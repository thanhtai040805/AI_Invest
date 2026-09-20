"""Connector web — connector động đầu tiên.

Lấy nội dung trang web do người dùng cung cấp, chuyển thành Markdown giao cho engine xử lý. Minh họa cách "trừu tượng hóa tầng thu thập"
kết nối nguồn thông tin bên ngoài: `discover()` liệt kê URL, `fetch()` lấy và trích xuất nội dung → tái sử dụng cùng một
pipeline ingest → extract. (MVP chỉ lấy các URL được chỉ định, không theo link crawl.)
"""

from __future__ import annotations

import hashlib
import asyncio
import ipaddress
import os
import re
import socket
import tempfile
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from sag_api.connectors.base import (
    ConfigField,
    Connector,
    ConnectorMeta,
    DiscoveredDoc,
    LocalFile,
)
from sag_api.core.errors import UpstreamError, ValidationError
from sag_api.core.logging import get_logger
from sag_api.enums import ConnectorKind

log = get_logger("connectors.web")

_TIMEOUT = 20.0
_MAX_HTML_BYTES = 8 * 1024 * 1024
_MAX_REDIRECTS = 5


async def _public_addresses(url: str) -> tuple[str, list[str]]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValidationError(f"Địa chỉ trang web không hợp lệ: {url}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValidationError(f"Không phân giải được máy chủ: {parsed.hostname}") from exc
    addresses = sorted({info[4][0] for info in infos})
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValidationError("URL nội bộ hoặc địa chỉ mạng riêng không được phép")
    return parsed.hostname, addresses


def _parse_urls(config: dict[str, Any]) -> list[str]:
    raw = config.get("urls") or config.get("url") or ""
    if isinstance(raw, list):
        items = [str(u).strip() for u in raw]
    else:
        items = [u.strip() for u in re.split(r"[,\n]", str(raw))]
    return [u for u in items if u]


def _filename_for(url: str) -> str:
    p = urlparse(url)
    slug = (p.path.strip("/").replace("/", "-") or p.netloc) or "page"
    slug = re.sub(r"[^A-Za-z0-9._-]", "-", slug)[:60].strip("-") or "page"
    return f"{p.netloc}-{slug}.md" if p.netloc else f"{slug}.md"


def extract_web_title(html: str) -> str | None:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


def _strip_tags(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def extract_web_markdown(html: str) -> str:
    """Extract readable Markdown from an HTML page."""
    try:
        import trafilatura

        md = trafilatura.extract(
            html,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        )
        if md and md.strip():
            return md
    except Exception as e:  # noqa: BLE001
        log.warning("Trích xuất trafilatura thất bại, quay lại văn bản thô: %s", e)
    return _strip_tags(html)


class WebConnector(Connector):
    meta = ConnectorMeta(
        kind=ConnectorKind.WEB,
        title="Trang web",
        description="Lấy nội dung trang web và chuyển thành Markdown để lưu vào kho. Phù hợp với trang tài liệu, blog, trang kiến thức công khai.",
        supports_sync=True,
        config_fields=[
            ConfigField(
                key="urls",
                label="Địa chỉ trang web",
                type="text",
                required=True,
                placeholder="https://example.com/docs\nhttps://example.com/faq",
                help='Mỗi dòng một URL; nhấp "Đồng bộ" để lấy nội dung.',
            )
        ],
    )

    def validate_config(self, config: dict[str, Any]) -> None:
        urls = _parse_urls(config)
        if not urls:
            raise ValidationError("Vui lòng nhập ít nhất một địa chỉ trang web")
        for u in urls:
            p = urlparse(u)
            if p.scheme not in ("http", "https") or not p.netloc or p.username or p.password:
                raise ValidationError(f"Địa chỉ trang web không hợp lệ: {u}")
            host = (p.hostname or "").casefold().rstrip(".")
            try:
                if not ipaddress.ip_address(host).is_global:
                    raise ValidationError("URL nội bộ hoặc địa chỉ mạng riêng không được phép")
            except ValueError:
                if host == "localhost" or host.endswith((".localhost", ".local")):
                    raise ValidationError("URL nội bộ hoặc địa chỉ mạng riêng không được phép")

    async def discover(self, config: dict[str, Any]) -> list[DiscoveredDoc]:
        return [
            DiscoveredDoc(external_id=u, filename=_filename_for(u), content_type="text/markdown")
            for u in _parse_urls(config)
        ]

    async def fetch(self, config: dict[str, Any], doc: DiscoveredDoc) -> LocalFile:
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT,
                follow_redirects=False,
                trust_env=False,
                headers={"User-Agent": "sag-bot/0.1 (+https://github.com/Zleap-AI/SAG)"},
            ) as client:
                current_url = doc.external_id
                for redirect_count in range(_MAX_REDIRECTS + 1):
                    hostname, addresses = await _public_addresses(current_url)
                    request_url = httpx.URL(current_url).copy_with(host=addresses[0])
                    async with client.stream(
                        "GET",
                        request_url,
                        headers={"Host": httpx.URL(current_url).netloc.decode()},
                        extensions={"sni_hostname": hostname},
                    ) as resp:
                        if resp.is_redirect:
                            if redirect_count == _MAX_REDIRECTS or not resp.headers.get("location"):
                                raise UpstreamError("Chuỗi chuyển hướng quá dài hoặc không hợp lệ")
                            current_url = urljoin(current_url, resp.headers["location"])
                            continue
                        resp.raise_for_status()
                        declared = int(resp.headers.get("content-length", "0") or 0)
                        if declared > _MAX_HTML_BYTES:
                            raise ValidationError("Trang web vượt giới hạn 8MB")
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in resp.aiter_bytes():
                            size += len(chunk)
                            if size > _MAX_HTML_BYTES:
                                raise ValidationError("Trang web vượt giới hạn 8MB")
                            chunks.append(chunk)
                        html = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
                        break
        except Exception as e:  # noqa: BLE001
            if isinstance(e, ValidationError):
                raise
            raise UpstreamError(f"Không lấy được nội dung {doc.external_id}: {e}") from e

        body = extract_web_markdown(html)
        if not body.strip():
            raise UpstreamError(f"Không thể trích xuất nội dung chính từ trang: {doc.external_id}")

        title = extract_web_title(html) or doc.filename
        content = f"# {title}\n\n> Nguồn: {doc.external_id}\n\n{body}\n"

        digest = hashlib.md5(doc.external_id.encode()).hexdigest()[:12]
        path = os.path.join(tempfile.gettempdir(), f"sag-web-{digest}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return LocalFile(
            path=path,
            filename=doc.filename,
            content_type="text/markdown",
            size_bytes=len(content.encode("utf-8")),
        )
