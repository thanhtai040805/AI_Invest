"""MinerU Official API v4 core utilities, SSRF guards, and disk-streamed ZIP page-marker extraction.

This module intentionally exposes only the MinerU Official API v4 client (`MinerUClient` ->
`OfficialMinerUClient`) alongside shared streaming ZIP extraction and `<!-- PAGE N -->`
restoration helpers. Legacy 302.AI MinerU endpoints have been removed.
"""

from __future__ import annotations

import asyncio
import html
import io
import ipaddress
import json
import logging
import os
import re
import socket
import tempfile
import zipfile
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, NoReturn
from urllib.parse import urljoin, urlparse

import httpx

from sag_api.core.config import Settings
from sag_api.core.errors import (
    ConfigurationError,
    ServiceUnavailableError,
    UpstreamError,
)

if TYPE_CHECKING:
    from sag_api.parsing.mineru_official import OfficialMinerUClient

StateCallback = Callable[[dict[str, Any]], Awaitable[None]]
PauseCallback = Callable[[], Awaitable[bool]]

log = logging.getLogger(__name__)


class ParsePaused(Exception):
    """Cooperative pause/yield signal raised during long-running MinerU polling."""


class MinerUClient:
    """Factory entrypoint returning the MinerU Official API v4 client exclusively."""

    def __new__(cls, settings: Settings) -> OfficialMinerUClient:
        from sag_api.parsing.mineru_official import OfficialMinerUClient

        return OfficialMinerUClient(settings)


async def download_mineru_result_markdown(
    url: str,
    *,
    request_timeout: float,
    result_limit: int,
) -> str:
    """Stream MinerU `full_zip_url` (or Markdown payload) to a temporary disk file in 64 KB
    chunks without forwarding API credentials, avoiding holding large image-heavy ZIPs in RAM.
    """
    temp_path: str | None = None
    try:
        try:
            async with httpx.AsyncClient(timeout=request_timeout) as client:
                current_url = url
                for _redirect_count in range(6):
                    await validate_public_result_url(current_url)
                    async with client.stream(
                        "GET", current_url, follow_redirects=False
                    ) as response:
                        if response.is_redirect and response.headers.get("location"):
                            current_url = urljoin(current_url, response.headers["location"])
                            continue
                        temp_path, magic_prefix = await _stream_result_to_temp_file(
                            response, result_limit=result_limit
                        )
                        content_type = response.headers.get("content-type", "").lower()
                        response_url = str(response.url)
                        encoding = response.encoding or "utf-8"
                        break
                else:
                    raise UpstreamError("MinerU Official 结果下载重定向次数过多")
        except httpx.TimeoutException as exc:
            raise ServiceUnavailableError("下载 MinerU Official 解析结果超时") from exc
        except httpx.RequestError as exc:
            raise ServiceUnavailableError(f"无法下载 MinerU Official 解析结果：{exc}") from exc

        assert temp_path is not None
        suffix = os.path.splitext(urlparse(response_url).path)[1].lower()
        if magic_prefix.startswith(b"PK\x03\x04") or suffix == ".zip" or "zip" in content_type:
            return await asyncio.to_thread(
                _markdown_from_zip_path, temp_path, result_limit
            )

        raw_bytes = await asyncio.to_thread(_read_file_bounded, temp_path, result_limit)
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass

    text = raw_bytes.decode(encoding, errors="replace")
    return _require_markdown(text)


async def _stream_result_to_temp_file(
    response: httpx.Response,
    *,
    result_limit: int,
) -> tuple[str, bytes]:
    if not response.is_success:
        error_body = bytearray()
        async for chunk in response.aiter_bytes():
            error_body.extend(chunk)
            if len(error_body) >= min(result_limit, 64 * 1024):
                break
        raise_mineru_http_status(
            response.status_code,
            "下载解析结果",
            _error_message_bytes(bytes(error_body)),
        )
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > result_limit:
        raise UpstreamError("MinerU Official 解析结果超过允许大小")

    fd, temp_path = tempfile.mkstemp(prefix=".mineru-res-", suffix=".bin")
    out_file = os.fdopen(fd, "wb")
    total = 0
    magic = bytearray()
    try:
        async for chunk in response.aiter_bytes(64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > result_limit:
                raise UpstreamError("MinerU Official 解析结果超过允许大小")
            if len(magic) < 16:
                needed = 16 - len(magic)
                magic.extend(chunk[:needed])
            await asyncio.to_thread(out_file.write, chunk)
        await asyncio.to_thread(out_file.close)
    except Exception:
        try:
            out_file.close()
        except OSError:
            pass
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise
    return temp_path, bytes(magic)


async def validate_public_result_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise UpstreamError("MinerU Official 返回了不安全的结果 URL")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise UpstreamError("MinerU Official 返回了不安全的结果 URL") from exc
    await _assert_public_host(host, port)


def check_mineru_response(response: httpx.Response, action: str) -> httpx.Response:
    if response.is_success:
        return response
    message = _error_message(response)
    raise_mineru_http_status(response.status_code, action, message)


def raise_mineru_http_status(status_code: int, action: str, message: str) -> NoReturn:
    if status_code in {401, 403}:
        raise ConfigurationError(
            f"{action}鉴权失败，请检查 SAG_MINERU_OFFICIAL_API_TOKEN"
        )
    if status_code == 429 or status_code >= 500:
        raise ServiceUnavailableError(f"{action}暂时不可用（{status_code}）：{message}")
    raise UpstreamError(f"{action}失败（{status_code}）：{message}")


def _read_file_bounded(path: str, limit: int) -> bytes:
    with open(path, "rb") as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise UpstreamError("MinerU Official 解析结果超过允许大小")
    return data


def _response_payload(response: httpx.Response) -> Any:
    try:
        payload: Any = response.json()
    except ValueError:
        payload = response.text
    return _unwrap_json(payload)


def _unwrap_json(payload: Any) -> Any:
    for _ in range(4):
        if not isinstance(payload, str):
            break
        value = payload.strip()
        if not value or value[0] not in "[{\"":
            break
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            break
        if decoded == payload:
            break
        payload = decoded
    return payload


def _find_error_message(payload: Any) -> str | None:
    for key in ("err_msg", "error", "detail", "message", "msg"):
        value = _find_key_string(payload, key)
        if value and value.lower() not in {"ok", "success", "succeeded"}:
            return value
    return None


def _find_key_string(payload: Any, key: str) -> str | None:
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()
        for child in payload.values():
            if isinstance(child, (dict, list)):
                found = _find_key_string(child, key)
                if found:
                    return found
    elif isinstance(payload, list):
        for child in payload:
            found = _find_key_string(child, key)
            if found:
                return found
    return None


_FAKE_IP_POOL = ipaddress.ip_network("198.18.0.0/15")


async def _assert_public_host(host: str, port: int) -> None:
    """Reject loopback, private, link-local, and reserved addresses to prevent SSRF."""
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise UpstreamError("MinerU 结果 URL 指向了本地或内网地址")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            records = await asyncio.to_thread(
                socket.getaddrinfo, host, port, type=socket.SOCK_STREAM
            )
        except OSError as exc:
            raise ServiceUnavailableError(f"无法解析 MinerU 结果域名：{host}") from exc
        addresses = {record[4][0] for record in records}
        if not addresses:
            raise ServiceUnavailableError(f"无法解析 MinerU 结果域名：{host}") from None
        resolved = [ipaddress.ip_address(address) for address in addresses]
    else:
        resolved = [literal]
    if any(not address.is_global and address not in _FAKE_IP_POOL for address in resolved):
        raise UpstreamError("MinerU 结果 URL 指向了本地或内网地址")


def _require_markdown(value: str) -> str:
    markdown = value.strip()
    if not markdown:
        raise UpstreamError("文档解析结果为空")
    return markdown + "\n"


def _markdown_from_zip_path(zip_path: str, size_limit: int) -> str:
    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            return _markdown_from_zip_archive(archive, size_limit)
    except zipfile.BadZipFile as exc:
        raise UpstreamError("MinerU 返回的结果压缩包已损坏") from exc


def _markdown_from_zip(content: bytes, size_limit: int) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            return _markdown_from_zip_archive(archive, size_limit)
    except zipfile.BadZipFile as exc:
        raise UpstreamError("MinerU 返回的结果压缩包已损坏") from exc


def _markdown_from_zip_archive(archive: zipfile.ZipFile, size_limit: int) -> str:
    infos = [info for info in archive.infolist() if not info.is_dir()]
    md_candidates = [
        info
        for info in infos
        if info.filename.lower().endswith((".md", ".markdown"))
    ]
    content_list_candidates = [
        info
        for info in infos
        if os.path.basename(info.filename).lower().endswith("content_list.json")
        and info.file_size <= min(size_limit, 32 * 1024 * 1024)
    ]
    content_list_candidates.sort(
        key=lambda info: (
            os.path.basename(info.filename).lower() != "content_list.json",
            -info.file_size,
        )
    )
    content_list: list[Any] | None = None
    if content_list_candidates:
        try:
            parsed_json = json.loads(
                archive.read(content_list_candidates[0]).decode("utf-8", errors="replace")
            )
            if isinstance(parsed_json, list):
                content_list = parsed_json
        except (ValueError, OSError):
            content_list = None

    if md_candidates:
        md_candidates.sort(
            key=lambda info: (
                os.path.basename(info.filename).lower() not in {"full.md", "full.markdown"},
                -info.file_size,
            )
        )
        chosen = md_candidates[0]
        if chosen.file_size > size_limit:
            raise UpstreamError("MinerU Markdown 结果超过允许大小")
        raw_md = archive.read(chosen).decode("utf-8", errors="replace")
        if content_list:
            raw_md = _inject_page_markers_from_content_list(raw_md, content_list)
        return _require_markdown(raw_md)

    if content_list:
        synthesized = _synthesize_markdown_from_content_list(content_list)
        if synthesized.strip():
            return _require_markdown(synthesized)

    raise UpstreamError("MinerU 结果压缩包中没有 Markdown 文件")


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MD_DECORATION_RE = re.compile(r"^[#>*\-\d.\s|`]+")


def _normalize_anchor_line(text: str) -> str:
    cleaned = _HTML_TAG_RE.sub(" ", text)
    cleaned = html.unescape(cleaned)
    cleaned = _MD_DECORATION_RE.sub("", cleaned.strip())
    return " ".join(cleaned.split()).casefold()


def _extract_item_snippets(item: dict[str, Any]) -> list[str]:
    snippets: list[str] = []
    for key in ("text", "table_caption", "img_caption", "table_body", "latex"):
        val = item.get(key)
        if isinstance(val, list):
            val = " ".join(str(x) for x in val if x)
        if not isinstance(val, str) or not val.strip():
            continue
        for raw_line in val.splitlines():
            norm = _normalize_anchor_line(raw_line)
            if len(norm) >= 4:
                snippets.append(norm[:96])
                if len(snippets) >= 3:
                    return snippets
    return snippets


def _inject_page_markers_from_content_list(
    raw_md: str, content_list: list[Any]
) -> str:
    """Monotonically inject `<!-- PAGE N -->` boundaries into `full.md` using MinerU's
    0-indexed `page_idx` from `content_list.json` while preserving 100% of `full.md` text.
    """
    if not raw_md.strip() or "<!-- PAGE " in raw_md.upper():
        return raw_md

    page_anchors: dict[int, list[str]] = {}
    ordered_pages: list[int] = []
    for item in content_list:
        if not isinstance(item, dict):
            continue
        raw_page = item.get("page_idx")
        if not isinstance(raw_page, int) or raw_page < 0:
            continue
        page_no = raw_page + 1
        if page_no not in page_anchors:
            page_anchors[page_no] = []
            ordered_pages.append(page_no)
        if len(page_anchors[page_no]) < 8:
            for snip in _extract_item_snippets(item):
                if snip not in page_anchors[page_no]:
                    page_anchors[page_no].append(snip)
                    if len(page_anchors[page_no]) >= 8:
                        break

    if not ordered_pages:
        return raw_md

    ordered_pages = sorted(set(ordered_pages))
    first_page = ordered_pages[0]
    if len(ordered_pages) == 1:
        return f"<!-- PAGE {first_page} -->\n\n{raw_md}"

    md_lines = raw_md.splitlines()
    norm_md_lines: list[str] = []
    safe_break_line: list[bool] = []
    in_fence = False
    prev_is_table = False

    for line in md_lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            safe_break_line.append(not in_fence)
            in_fence = not in_fence
            prev_is_table = False
            norm_md_lines.append("")
            continue
        is_table_row = stripped.startswith("|") and stripped.endswith("|")
        # Allow inserting a page marker right before the FIRST row of a table, never mid-table
        is_safe = (not in_fence) and (not (is_table_row and prev_is_table))
        safe_break_line.append(is_safe)
        prev_is_table = is_table_row
        norm_md_lines.append(_normalize_anchor_line(line) if stripped else "")

    insert_before: dict[int, list[int]] = {0: [first_page]}
    cursor = 0
    total_lines = len(md_lines)

    for page_no in ordered_pages[1:]:
        anchors = page_anchors.get(page_no) or []
        matched_idx: int | None = None
        for anchor in anchors:
            if not anchor:
                continue
            for idx in range(cursor, total_lines):
                if not safe_break_line[idx]:
                    continue
                target = norm_md_lines[idx]
                if not target:
                    continue
                if anchor in target or (len(target) >= 12 and target in anchor):
                    matched_idx = idx
                    break
            if matched_idx is not None:
                break
        if matched_idx is not None:
            insert_before.setdefault(matched_idx, []).append(page_no)
            cursor = matched_idx + 1

    output_lines: list[str] = []
    for idx, line in enumerate(md_lines):
        for p_no in insert_before.get(idx, ()):
            output_lines.append(f"<!-- PAGE {p_no} -->")
            output_lines.append("")
        output_lines.append(line)
    return "\n".join(output_lines)


def _synthesize_markdown_from_content_list(content_list: list[Any]) -> str:
    parts: list[str] = []
    current_page: int | None = None
    for item in content_list:
        if not isinstance(item, dict):
            continue
        raw_page = item.get("page_idx")
        if isinstance(raw_page, int) and raw_page >= 0:
            page_no = raw_page + 1
            if page_no != current_page:
                current_page = page_no
                parts.append(f"<!-- PAGE {page_no} -->")
        item_type = str(item.get("type") or "text").lower()
        text = str(item.get("text") or "").strip()
        if item_type == "text" and text:
            level = item.get("text_level")
            if isinstance(level, int) and 1 <= level <= 6:
                parts.append(f"{'#' * level} {text}")
            else:
                parts.append(text)
        elif item_type == "table":
            caption = item.get("table_caption")
            if isinstance(caption, list) and caption:
                parts.append(" ".join(str(c).strip() for c in caption if c))
            elif isinstance(caption, str) and caption.strip():
                parts.append(caption.strip())
            body = str(item.get("table_body") or "").strip()
            if body:
                parts.append(body)
        elif text:
            parts.append(text)
    return "\n\n".join(p for p in parts if p)


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return (response.text or response.reason_phrase).strip()[:300]
    if isinstance(payload, dict):
        message = _find_error_message(payload)
        if message:
            return message[:300]
    return str(payload)[:300]


def _error_message_bytes(content: bytes) -> str:
    text = content.decode("utf-8", errors="replace").strip()
    if not text:
        return "空响应"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[:300]
    if isinstance(payload, dict):
        message = _find_error_message(payload)
        if message:
            return message[:300]
    return str(payload)[:300]
