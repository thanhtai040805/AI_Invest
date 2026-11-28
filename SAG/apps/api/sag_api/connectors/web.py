"""Bộ kết nối web — bộ kết nối động đầu tiên.

Lấy nội dung trang web do người dùng cung cấp, chuyển thành Markdown để engine xử lý. Minh họa cách
lớp thu thập dữ liệu tích hợp nguồn bên ngoài: `discover()` liệt kê URL, `fetch()` tải và trích xuất nội dung,
sau đó dùng lại pipeline ingest → extract. (MVP chỉ tải URL được chỉ định, không theo liên kết để thu thập.)
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import Any
from urllib.parse import urlparse

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

_MAX_HTML_BYTES = 8 * 1024 * 1024


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
        log.warning("Trích xuất bằng trafilatura thất bại, chuyển sang văn bản thuần: %s", e)
    return _strip_tags(html)


class WebConnector(Connector):
    meta = ConnectorMeta(
        kind=ConnectorKind.WEB,
        title="Trang web",
        description="Tải nội dung trang web, chuyển thành Markdown và lưu vào kho. Phù hợp với trang tài liệu, blog và trang tri thức công khai.",
        supports_sync=True,
        config_fields=[
            ConfigField(
                key="urls",
                label="Địa chỉ trang web",
                type="text",
                required=True,
                placeholder="https://example.com/docs\nhttps://example.com/faq",
                help="Mỗi dòng một URL; nhấn “Đồng bộ” để tải nội dung.",
            )
        ],
    )

    def validate_config(self, config: dict[str, Any]) -> None:
        urls = _parse_urls(config)
        if not urls:
            raise ValidationError("Vui lòng nhập ít nhất một địa chỉ trang web")
        for u in urls:
            p = urlparse(u)
            if p.scheme not in ("http", "https") or not p.netloc:
                raise ValidationError(f"Địa chỉ trang web không hợp lệ: {u}")

    async def discover(self, config: dict[str, Any]) -> list[DiscoveredDoc]:
        return [
            DiscoveredDoc(external_id=u, filename=_filename_for(u), content_type="text/markdown")
            for u in _parse_urls(config)
        ]

    async def fetch(self, config: dict[str, Any], doc: DiscoveredDoc) -> LocalFile:
        try:
            # Reuse the public-page fetcher's redirect, address, content-type,
            # and streaming byte guards for user-configured source URLs.
            from sag_api.tools.builtin import _download_public_web_page

            final_url, html = await _download_public_web_page(
                doc.external_id,
                max_bytes=_MAX_HTML_BYTES,
            )
        except Exception as e:  # noqa: BLE001
            raise UpstreamError(f"Tải trang thất bại {doc.external_id}: {e}") from e

        body = extract_web_markdown(html)
        if not body.strip():
            raise UpstreamError(f"Không trích xuất được nội dung từ trang: {doc.external_id}")

        title = extract_web_title(html) or doc.filename
        content = f"# {title}\n\n> Nguồn: {final_url}\n\n{body}\n"

        fd, path = tempfile.mkstemp(prefix="sag-web-", suffix=".md")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        return LocalFile(
            path=path,
            filename=doc.filename,
            content_type="text/markdown",
            size_bytes=len(content.encode("utf-8")),
        )
