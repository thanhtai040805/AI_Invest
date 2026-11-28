"""Zero-Buffer streaming download & verification for R2/S3 presigned object URLs.

Enforces HTTPS host allowlisting, DNS SSRF blocking, 64KB chunked disk streaming,
first-chunk magic-byte verification, incremental SHA-256 & byte-length checks, and
atomic snapshot placement.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
import weakref
from pathlib import Path
from urllib.parse import urlparse

import httpx

from sag_api.core.config import Settings
from sag_api.core.error_taxonomy import ErrorLayer, ErrorStage
from sag_api.core.errors import ServiceUnavailableError, UpstreamError, ValidationError
from sag_api.parsing.mineru import ParsePaused, PauseCallback, _assert_public_host
from sag_api.parsing.service import parsed_sidecar_paths

_DOWNLOAD_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def _download_lock_for(path: str) -> asyncio.Lock:
    lock = _DOWNLOAD_LOCKS.get(path)
    if lock is None:
        lock = asyncio.Lock()
        _DOWNLOAD_LOCKS[path] = lock
    return lock


def resolve_snapshot_target_path(
    effective_data_dir: str,
    payload_hash: str,
    original_filename: str,
) -> str:
    """Deterministic content-addressed snapshot path without writing file bytes yet."""
    safe_name = os.path.basename(original_filename) or "source.bin"
    normalized_hash = payload_hash.lower()
    base_dir = Path(effective_data_dir) / "snapshots" / normalized_hash[:2] / normalized_hash
    base_dir.mkdir(parents=True, exist_ok=True)
    return str((base_dir / safe_name).resolve())


def host_matches_allowlist(host: str, allowed_hosts: list[str]) -> bool:
    normalized_host = (host or "").strip().lower().rstrip(".")
    if not normalized_host:
        return False
    for raw_rule in allowed_hosts:
        rule = (raw_rule or "").strip().lower().rstrip(".")
        if not rule:
            continue
        if rule.startswith("."):
            if normalized_host.endswith(rule) and len(normalized_host) > len(rule):
                return True
        elif normalized_host == rule:
            return True
    return False


def validate_remote_object_url(source_url: str, settings: Settings) -> tuple[str, int]:
    """Fail-closed syntax and host allowlist validation for object storage URLs."""
    parsed = urlparse((source_url or "").strip())
    scheme = (parsed.scheme or "").lower()
    allow_dev_http = bool(
        settings.environment == "dev" and settings.object_ingest_allow_private_hosts_in_dev
    )
    allowed_schemes = {"https", "http"} if allow_dev_http else {"https"}
    if scheme not in allowed_schemes or not parsed.hostname:
        raise ValidationError(
            "Object source_url must be a valid HTTPS URL",
            layer=ErrorLayer.CLIENT,
            stage=ErrorStage.UPLOAD,
            code="INVALID_OBJECT_SOURCE_URL",
        )
    if parsed.username is not None or parsed.password is not None:
        raise ValidationError(
            "Object source_url must not embed credentials",
            layer=ErrorLayer.CLIENT,
            stage=ErrorStage.UPLOAD,
            code="INVALID_OBJECT_SOURCE_URL",
        )
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise ValidationError(
            "Object source_url has an invalid port",
            layer=ErrorLayer.CLIENT,
            stage=ErrorStage.UPLOAD,
            code="INVALID_OBJECT_SOURCE_URL",
        ) from exc

    host = parsed.hostname.lower().rstrip(".")
    if not host_matches_allowlist(host, settings.object_ingest_allowed_hosts):
        raise ValidationError(
            f"Object host '{host}' is not permitted by SAG_OBJECT_INGEST_ALLOWED_HOSTS",
            layer=ErrorLayer.CLIENT,
            stage=ErrorStage.UPLOAD,
            code="UNTRUSTED_OBJECT_HOST",
        )
    return host, port


def verify_mime_signature_prefix(filename: str, prefix_bytes: bytes) -> None:
    """Validate magic bytes against file extension on the first streamed chunk."""
    if not prefix_bytes:
        return
    name = (filename or "").lower()
    ext = ("." + name.rsplit(".", 1)[1]) if "." in name else ""

    if ext == ".pdf":
        if not prefix_bytes.startswith(b"%PDF-"):
            raise ValidationError(
                f"Định dạng nội dung không khớp phần mở rộng: tệp '{filename}' thiếu chữ ký PDF hợp lệ (%PDF-)",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
            )
    elif ext in {".docx", ".pptx", ".xlsx", ".epub", ".odt", ".ods", ".odp"}:
        if not (
            prefix_bytes.startswith(b"PK\x03\x04")
            or prefix_bytes.startswith(b"PK\x05\x06")
            or prefix_bytes.startswith(b"PK\x07\x08")
        ):
            raise ValidationError(
                f"Định dạng nội dung không khớp phần mở rộng: tệp '{filename}' ({ext}) "
                "thiếu chữ ký ZIP container hợp lệ (PK)",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
            )
    elif ext in {".xls", ".doc", ".ppt"}:
        is_ole2 = prefix_bytes.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
        is_rtf_doc = ext == ".doc" and prefix_bytes.lstrip().startswith(b"{\\rtf")
        if not (is_ole2 or is_rtf_doc):
            raise ValidationError(
                f"Định dạng nội dung không khớp phần mở rộng: tệp '{filename}' thiếu chữ ký OLE2 container hợp lệ",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
            )
    elif ext == ".rtf":
        if not prefix_bytes.lstrip().startswith(b"{\\rtf"):
            raise ValidationError(
                f"Định dạng nội dung không khớp phần mở rộng: tệp '{filename}' thiếu chữ ký RTF hợp lệ ({{\\rtf)",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
            )
    elif ext == ".png":
        if not prefix_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValidationError(
                f"Định dạng nội dung không khớp phần mở rộng: tệp '{filename}' thiếu chữ ký PNG hợp lệ",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
            )
    elif ext in {".jpg", ".jpeg"}:
        if not prefix_bytes.startswith(b"\xff\xd8\xff"):
            raise ValidationError(
                f"Định dạng nội dung không khớp phần mở rộng: tệp '{filename}' thiếu chữ ký JPEG hợp lệ",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
            )
    elif ext == ".webp":
        if not (
            len(prefix_bytes) >= 12
            and prefix_bytes.startswith(b"RIFF")
            and prefix_bytes[8:12] == b"WEBP"
        ):
            raise ValidationError(
                f"Định dạng nội dung không khớp phần mở rộng: tệp '{filename}' thiếu chữ ký WEBP hợp lệ",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
            )
    elif ext in {
        ".txt",
        ".md",
        ".markdown",
        ".text",
        ".csv",
        ".tsv",
        ".json",
        ".html",
        ".htm",
        ".yaml",
        ".yml",
        ".xml",
        ".drawio",
        ".sql",
    }:
        if (
            prefix_bytes.startswith(b"MZ")
            or prefix_bytes.startswith(b"\x7fELF")
            or prefix_bytes.startswith(b"\x89PNG\r\n\x1a\n")
            or prefix_bytes.startswith(b"\xff\xd8\xff")
            or (b"\x00" in prefix_bytes)
        ):
            raise ValidationError(
                f"Tệp nhị phân hoặc hình ảnh trá hình dưới phần mở rộng văn bản '{ext}' "
                f"không được phép cho '{filename}'",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
            )


async def stream_remote_object_to_snapshot(
    source_url: str,
    *,
    target_path: str,
    original_filename: str,
    expected_sha256: str,
    expected_size_bytes: int,
    settings: Settings,
    should_pause: PauseCallback | None = None,
) -> str:
    """Stream remote object in 64KB chunks directly to content-addressed snapshot disk path."""
    expected_hash = expected_sha256.strip().lower()
    max_bytes = getattr(settings, "max_upload_size_bytes", None) or (
        settings.max_upload_mb * 1024 * 1024
    )
    if expected_size_bytes <= 0 or expected_size_bytes > max_bytes:
        raise ValidationError(
            f"Object size {expected_size_bytes} exceeds maximum allowed ({max_bytes} bytes)",
            layer=ErrorLayer.CLIENT,
            stage=ErrorStage.UPLOAD,
        )

    async with _download_lock_for(target_path):
        # Fast reuse: if snapshot or parsed sidecar cache already exists on disk, skip download
        if os.path.isfile(target_path) and os.path.getsize(target_path) == expected_size_bytes:
            return target_path
        ext = os.path.splitext(target_path)[1].lower()
        if ext not in {".md", ".markdown"} and any(
            sidecar.endswith(".md") and os.path.isfile(sidecar) and os.path.getsize(sidecar) > 0
            for sidecar in parsed_sidecar_paths(target_path)
        ):
            return target_path

        host, port = validate_remote_object_url(source_url, settings)
        if not (
            settings.environment == "dev" and settings.object_ingest_allow_private_hosts_in_dev
        ):
            await _assert_public_host(host, port)

        parent_dir = os.path.dirname(target_path) or "."
        os.makedirs(parent_dir, exist_ok=True)
        tmp_path = os.path.join(parent_dir, f".tmp_r2_{uuid.uuid4().hex}")
        hasher = hashlib.sha256()
        received_bytes = 0
        header_buf = bytearray()
        signature_checked = False

        try:
            try:
                async with httpx.AsyncClient(
                    timeout=float(settings.object_ingest_timeout_seconds),
                    follow_redirects=False,
                ) as client:
                    async with client.stream("GET", source_url) as response:
                        if response.status_code in {401, 403, 404}:
                            raise UpstreamError(
                                f"Không thể tải tệp từ Object Storage (HTTP {response.status_code})",
                                layer=ErrorLayer.API,
                                stage=ErrorStage.UPLOAD,
                            )
                        if response.status_code == 429 or response.status_code >= 500:
                            raise ServiceUnavailableError(
                                f"Object Storage tạm thời không khả dụng (HTTP {response.status_code})"
                            )
                        if not response.is_success:
                            raise UpstreamError(
                                f"Tải tệp từ Object Storage thất bại (HTTP {response.status_code})"
                            )

                        declared_len = response.headers.get("content-length")
                        if declared_len and declared_len.isdigit():
                            declared_int = int(declared_len)
                            if declared_int > max_bytes or declared_int != expected_size_bytes:
                                raise ValidationError(
                                    f"Kích thước tệp trên Object Storage ({declared_int} B) "
                                    f"không khớp khai báo ({expected_size_bytes} B)",
                                    layer=ErrorLayer.CLIENT,
                                    stage=ErrorStage.UPLOAD,
                                    code="OBJECT_SIZE_MISMATCH",
                                )

                        with open(tmp_path, "wb") as out_file:
                            chunk_counter = 0
                            async for chunk in response.aiter_bytes(64 * 1024):
                                if not chunk:
                                    continue
                                chunk_counter += 1
                                if chunk_counter % 8 == 1 and should_pause is not None:
                                    if await should_pause():
                                        raise ParsePaused()

                                received_bytes += len(chunk)
                                if received_bytes > expected_size_bytes or received_bytes > max_bytes:
                                    raise ValidationError(
                                        "Luồng dữ liệu Object Storage vượt quá kích thước khai báo",
                                        layer=ErrorLayer.CLIENT,
                                        stage=ErrorStage.UPLOAD,
                                        code="OBJECT_SIZE_EXCEEDED",
                                    )

                                if not signature_checked:
                                    needed = 128 - len(header_buf)
                                    if needed > 0:
                                        header_buf.extend(chunk[:needed])
                                    if len(header_buf) >= 16 or received_bytes == expected_size_bytes:
                                        verify_mime_signature_prefix(
                                            original_filename, bytes(header_buf)
                                        )
                                        signature_checked = True

                                hasher.update(chunk)
                                await asyncio.to_thread(out_file.write, chunk)
            except httpx.TimeoutException as exc:
                raise ServiceUnavailableError("Tải tệp từ Object Storage quá thời gian chờ") from exc
            except httpx.RequestError as exc:
                raise ServiceUnavailableError("Lỗi kết nối khi tải tệp từ Object Storage") from exc

            if received_bytes == 0:
                raise ValidationError(
                    "Tệp trên Object Storage rỗng",
                    layer=ErrorLayer.CLIENT,
                    stage=ErrorStage.UPLOAD,
                )
            if not signature_checked:
                verify_mime_signature_prefix(original_filename, bytes(header_buf))

            if received_bytes != expected_size_bytes:
                raise ValidationError(
                    f"Kích thước tệp tải về ({received_bytes} B) không khớp expected_size_bytes ({expected_size_bytes} B)",
                    layer=ErrorLayer.CLIENT,
                    stage=ErrorStage.UPLOAD,
                    code="OBJECT_SIZE_MISMATCH",
                )

            actual_hash = hasher.hexdigest().lower()
            if actual_hash != expected_hash:
                raise ValidationError(
                    f"Mã băm SHA-256 của tệp không khớp khai báo (expected={expected_hash[:12]}..., actual={actual_hash[:12]}...)",
                    layer=ErrorLayer.CLIENT,
                    stage=ErrorStage.UPLOAD,
                    code="OBJECT_SHA256_MISMATCH",
                )

            await asyncio.to_thread(os.replace, tmp_path, target_path)
            return target_path
        except Exception:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise
