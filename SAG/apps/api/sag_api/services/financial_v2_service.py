from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.errors import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    UpstreamError,
    ValidationError,
)
from sag_api.db.models import (
    AssessmentRun,
    Document,
    DocumentAsset,
    DocumentFacet,
    DocumentTreeNode,
    Entity,
    EmbeddingChunk,
    EvidenceSpan,
    Fact,
    Issuer,
    Observation,
    ProcessingRun,
    Relation,
    ReviewQueueItem,
)
from sag_api.enums import DocumentRole, DocumentStatus, ProcessingStageStatus
from sag_api.schemas.v2 import DocumentCreateIn
from sag_api.services.document_structure_service import (
    ACTIVE_DOCUMENT_ROLES,
    hydrate_node_content,
    normalize_doc_role,
    parse_markdown_tree,
)

logger = logging.getLogger("sag.financial_v2")


async def assess_gil(session: AsyncSession, ticker: str) -> dict[str, Any]:
    """Compatibility entry point; GIL logic lives in gil_service."""
    from sag_api.services.gil_service import assess_gil as run_assessment

    return await run_assessment(session, ticker)


async def assess_moat(session: AsyncSession, ticker: str) -> dict[str, Any]:
    """Legacy name for the evidence-quality assessment; no MOAT score is invented."""
    from sag_api.services.analysis_v2_service import assess_financial_quality_by_ticker

    result = await assess_financial_quality_by_ticker(session, ticker)
    return {**result, "moat_score": None}


def _is_mineru_size_or_page_limit_error(error: Exception) -> bool:
    """Only explicit size/page-limit errors justify a local split fallback."""
    message = str(error).lower().replace("_", " ").replace("-", " ")
    markers = (
        "200mb", "200 mb", "file too large", "file size limit", "size limit",
        "600 pages", "600 page", "too many pages", "page limit", "page count limit",
        "maximum pages", "max pages",
        "retry limit", "failed to read file",
    )
    return any(marker in message for marker in markers) or (
        "exceed" in message and "page" in message
    )
from sag_api.services.embedding_v2_service import build_embeddings_for_document
from sag_api.services.extraction_v2_service import extract_and_persist_manifest
from sag_api.services.processing_run_service import (
    complete_processing_run,
    enqueue_processing_run,
    fail_processing_run,
)

PROCESSING_VERSION = 2
CANONICALIZATION_VERSION = "canonical-md-v1"

_VIETNAMESE_WORDS = frozenset(
    "báo cáo tài chính công ty mẹ hợp nhất quý năm tháng doanh thu chi phí tài sản nguồn vốn vốn chủ sở hữu phải thu phải trả".split()
)
_ENGLISH_WORDS = frozenset(
    "financial statements interim annual quarter company parent consolidated revenue expense assets liabilities equity receivables payables".split()
)


def _document_language(markdown: str) -> str:
    """Cheap post-OCR gate; unknown is retained for manual review."""
    sample = re.sub(r"[`|0-9_#*:/().,;\-]+", " ", markdown[:50000].casefold())
    vi = sum(sample.count(word) for word in _VIETNAMESE_WORDS)
    vi += sum(sample.count(char) for char in "ăâđêôơưáàảãạấầẩẫậếềểễệốồổỗộớờởỡợứừửữự")
    en = sum(sample.count(word) for word in _ENGLISH_WORDS)
    if en >= 4 and vi == 0:
        return "ENGLISH"
    if vi > en:
        return "VIETNAMESE"
    if en >= 4 and en > vi * 2:
        return "ENGLISH"
    return "UNKNOWN"


def _reject_english(markdown: str, filename: str) -> None:
    if _document_language(markdown) == "ENGLISH":
        raise ValidationError(f"OCR document is English; not persisted in SAG: {filename}")


def canonicalize_markdown(markdown: str) -> str:
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("\ufeff"):
        text = text[1:]
    return text if text.endswith("\n") else f"{text}\n"


def clean_ocr_markdown(markdown: str, filename: str, *, doc_role: str | None = None) -> str:
    """Remove OCR noise but keep statements in the canonical raw artifact.

    Role filtering happens later in ``analysis_markdown``.  Dropping statements
    here would make deterministic BS/IS/CF extraction impossible for PDF input.
    """
    from sag_api.parsing.markdown_noise_cleaner import clean_markdown

    cleaned, _stats = clean_markdown(markdown)
    if not cleaned.strip():
        raise ValidationError(f"Cleaner lĂ m rá»—ng Markdown OCR cho {filename}")
    canonical = canonicalize_markdown(cleaned)
    _reject_english(canonical, filename)
    return canonical


def analysis_markdown(markdown: str, *, doc_role: str | None = None) -> str:
    """Return the role-filtered Markdown used by structure/extraction/embeddings.

    The stored canonical asset remains the source artifact.  Financial
    statement roles use the strict cleaner so CDKT/KQKD/LCTT do not enter the
    analytical pipeline when a caller supplies Markdown directly (the PDF
    path has already gone through the same cleaner).
    """
    if str(doc_role or "").upper() not in {"ANNUAL_BACKBONE", "LATEST_QUARTER"}:
        return markdown
    from sag_api.parsing.markdown_noise_cleaner import clean_markdown

    cleaned, _stats = clean_markdown(markdown, doc_role=doc_role)
    return canonicalize_markdown(cleaned)


def _ticker(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "", value.upper().strip())
    if not cleaned:
        raise ValidationError("ticker khĂ´ng há»£p lá»‡")
    return cleaned


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cache_path_for(content_hash: str, suffix: str) -> Path:
    root = Path(settings.upload_dir).resolve() / "objects" / content_hash[:2]
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{content_hash}{suffix}"


def _cleanup_stale_pdf_parts(root: Path, max_age_seconds: int = 3600) -> None:
    """Remove interrupted streaming downloads left by a killed worker."""
    cutoff = time.time() - max_age_seconds
    for part in root.glob("sag-pdf-*.pdf"):
        try:
            if part.stat().st_mtime < cutoff:
                part.unlink(missing_ok=True)
        except OSError:
            continue


def _unlink_with_retry(path: Path, attempts: int = 8) -> None:
    """Remove transient PDFs after an upstream client releases its handle."""
    for attempt in range(max(1, attempts)):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(0.25)


def _extract_pdf_from_zip(path: Path, doc_role: str | None = None, scope: str | None = "SEPARATE") -> str:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".pdf")]
        if not names:
            raise ValidationError("ZIP nguồn không chứa PDF")
        role = str(doc_role or "").upper()
        scope_upper = str(scope or "SEPARATE").upper()

        def score(name: str) -> tuple[int, int]:
            lower = name.lower()
            value = 0

            # 1. Phạt nặng tài liệu phụ trợ, giải trình, tóm tắt
            if any(k in lower for k in ("giai_trinh", "giai-trinh", "giaitrinh", "bien_dong_ln", "explanation")):
                value -= 100
            if any(k in lower for k in ("tom_tat", "tomtat", "summary", "brief")):
                value -= 80

            # 2. Xử lý Governance Report
            if role == "GOVERNANCE_REPORT":
                if any(k in lower for k in ("quantri", "quan_tri", "governance")):
                    value += 100
                if any(k in lower for k in ("ban_day_du", "bandaydu", "full")):
                    value += 20
                if any(k in lower for k in ("report_on_corporate_governance", "bc_tinh_hinh_quan_tri")):
                    value += 50
                # Ưu tiên tiếng Việt hơn tiếng Anh
                if "bc_" in lower or "bcthqt" in lower or "quan_tri" in lower:
                    value += 30

            # 3. Xử lý BCTC (ANNUAL_BACKBONE & LATEST_QUARTER)
            if role in {"ANNUAL_BACKBONE", "LATEST_QUARTER"}:
                if any(k in lower for k in ("bctc", "financial", "interim", "statement", "baocaotaichinh")):
                    value += 100

                # Ưu tiên Scope: Mẹ / Riêng vs Hợp nhất
                if scope_upper == "SEPARATE":
                    if any(k in lower for k in ("congtyme", "cong_ty_me", "rieng", "parent", "separate")):
                        value += 60
                    if any(k in lower for k in ("hopnhat", "hop_nhat", "consolidated")):
                        value -= 50
                elif scope_upper == "CONSOLIDATED":
                    if any(k in lower for k in ("hopnhat", "hop_nhat", "consolidated")):
                        value += 60
                    if any(k in lower for k in ("congtyme", "cong_ty_me", "rieng")):
                        value -= 50

                # Ưu tiên tiếng Việt hơn tiếng Anh
                if any(k in lower for k in ("bctc", "baocaotaichinh", "soat_xet", "kiem_toan")):
                    value += 30
                if any(k in lower for k in ("interim_financial", "financial_statements", "english")):
                    value -= 25

            return value, -len(name)

        selected = max(names, key=score)
        path.write_bytes(archive.read(selected))
        return selected


async def get_or_create_issuer(session: AsyncSession, ticker: str) -> Issuer:
    ticker_clean = _ticker(ticker)
    issuer_id = await session.scalar(
        pg_insert(Issuer)
        .values(ticker=ticker_clean, legal_name=None, metadata_json={})
        .on_conflict_do_nothing(index_elements=[Issuer.ticker])
        .returning(Issuer.id)
    )
    if issuer_id is not None:
        issuer = await session.get(Issuer, issuer_id)
        if issuer is not None:
            return issuer
    issuer = await session.scalar(select(Issuer).where(Issuer.ticker == ticker_clean))
    if issuer is None:
        raise ConflictError(f"KhĂ´ng thá»ƒ táº¡o hoáº·c Ä‘á»c issuer {ticker_clean}")
    return issuer


async def _read_markdown_from_body(body: DocumentCreateIn) -> tuple[str, str, str, int, str]:
    if body.markdown is not None:
        raw_bytes = body.markdown.encode("utf-8")
        uri = body.object_uri or "inline://markdown"
    elif body.object_uri:
        raw_bytes = await _read_object_uri(body.object_uri)
        uri = body.object_uri
    else:
        raise ValidationError("API v2 cáº§n markdown trá»±c tiáº¿p hoáº·c object_uri trá» tá»›i Markdown trĂªn R2/local file")
    raw_hash = _hash_bytes(raw_bytes)
    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("Markdown object pháº£i lĂ  UTF-8") from exc
    canonical = canonicalize_markdown(raw_text)
    _reject_english(canonical, body.title or uri)
    data = canonical.encode("utf-8")
    content_hash = _hash_bytes(data)
    if body.content_sha256 and body.content_sha256 not in {raw_hash, content_hash}:
        raise ValidationError("content_sha256 khĂ´ng khá»›p raw hoáº·c canonical Markdown")
    if body.canonical_markdown_sha256 and body.canonical_markdown_sha256 != content_hash:
        raise ValidationError("canonical_markdown_sha256 khĂ´ng khá»›p Markdown canonical")
    return canonical, raw_hash, content_hash, len(data), uri


async def _read_object_uri(object_uri: str) -> bytes:
    uri = object_uri.strip()
    if uri.startswith("r2://"):
        from sag_api.services.r2_storage import SagR2StorageClient

        without_scheme = uri[len("r2://") :]
        if "/" not in without_scheme:
            raise ValidationError("R2 URI pháº£i cĂ³ dáº¡ng r2://bucket/key")
        bucket, key = without_scheme.split("/", 1)
        if not key:
            raise ValidationError("R2 URI thiáº¿u object key")
        digest = Path(key).stem
        if re.fullmatch(r"[0-9a-f]{64}", digest):
            cached = _cache_path_for(digest, ".md")
            if cached.is_file():
                return cached.read_bytes()
        client = SagR2StorageClient()
        if bucket:
            client.bucket_name = bucket
        return client.download_bctc_bytes(key)
    if uri.startswith("file://"):
        path = Path(uri[len("file://") :]).resolve()
    else:
        path = Path(uri).resolve()
    if not path.exists() or not path.is_file():
        raise ValidationError("object_uri local khĂ´ng tá»“n táº¡i hoáº·c khĂ´ng pháº£i file")
    return path.read_bytes()


async def parse_pdf_bytes_to_markdown(data: bytes, filename: str) -> str:
    from sag_api.core.errors import ConfigurationError
    from sag_api.parsing.mineru import MinerUClient

    if not settings.mineru_configured:
        raise ConfigurationError("PDF upload cáº§n MinerU Ä‘Æ°á»£c cáº¥u hĂ¬nh; khĂ´ng fallback sang parser khĂ¡c trong SAG v2")
    content_hash = _hash_bytes(data)
    pdf_path = _cache_path_for(content_hash, ".pdf")
    if not pdf_path.exists():
        pdf_path.write_bytes(data)
    needs_chunking = len(data) > int(getattr(settings, "mineru_chunk_max_mb", 20)) * 1024 * 1024
    if not needs_chunking:
        try:
            import fitz
            with fitz.open(str(pdf_path)) as pdf:
                needs_chunking = len(pdf) > int(getattr(settings, "mineru_chunk_trigger_pages", 200))
        except Exception:
            needs_chunking = False
    markdown = (
        await _parse_large_pdf_in_chunks(pdf_path, filename)
        if needs_chunking
        else await MinerUClient(settings).parse(str(pdf_path))
    )
    return clean_ocr_markdown(markdown, filename)


async def _parse_large_pdf_in_chunks(pdf_path: Path, filename: str) -> str:
    """Split oversized PDFs on page boundaries and OCR chunks concurrently."""
    import fitz

    from sag_api.parsing.mineru import MinerUClient

    chunk_limit = max(1, int(getattr(settings, "mineru_chunk_max_mb", 20))) * 1024 * 1024
    page_limit = max(1, int(getattr(settings, "mineru_chunk_max_pages", 200)))
    source = fitz.open(str(pdf_path))
    chunk_paths: list[tuple[int, int, int, Path]] = []
    chunk_doc = fitz.open()
    chunk_start = 0
    chunk_number = 0

    def flush_chunk(doc: fitz.Document, start: int, end: int) -> None:
        nonlocal chunk_number
        if len(doc) == 0:
            return
        chunk_number += 1
        fd, name = tempfile.mkstemp(prefix="sag-pdf-chunk-", suffix=".pdf", dir=str(pdf_path.parent))
        os.close(fd)
        chunk_path = Path(name)
        chunk_path.write_bytes(doc.tobytes(garbage=4, deflate=True))
        chunk_paths.append((chunk_number, start, end, chunk_path))

    try:
        for page_index in range(len(source)):
            candidate = fitz.open()
            if len(chunk_doc) > 0:
                candidate.insert_pdf(chunk_doc)
            candidate.insert_pdf(source, from_page=page_index, to_page=page_index)
            candidate_bytes = candidate.tobytes(garbage=4, deflate=True)
            candidate.close()

            if (len(candidate_bytes) > chunk_limit or len(chunk_doc) >= page_limit) and len(chunk_doc) > 0:
                flush_chunk(chunk_doc, chunk_start, page_index)
                chunk_doc.close()
                chunk_doc = fitz.open()
                chunk_start = page_index

            chunk_doc.insert_pdf(source, from_page=page_index, to_page=page_index)

        flush_chunk(chunk_doc, chunk_start, len(source))
    finally:
        chunk_doc.close()
        source.close()

    async def parse_chunk(item: tuple[int, int, int, Path]) -> tuple[int, str]:
        number, start, end, path = item
        logger.info("MinerU PDF chunk %s pages=%s-%s bytes=%s", number, start + 1, end, path.stat().st_size)
        try:
            markdown = await MinerUClient(settings).parse(str(path))
            text = markdown.strip()
            return number, (f"\n\n<!-- SAG PDF CHUNK {number}: pages {start + 1}-{end} -->\n\n{text}" if text else "")
        except Exception:
            logger.exception("MinerU PDF chunk failed %s pages=%s-%s", number, start + 1, end)
            raise
        finally:
            _unlink_with_retry(path)

    parsed = await asyncio.gather(*(parse_chunk(item) for item in chunk_paths))
    markdown_parts = [text for _, text in sorted(parsed) if text]
    if not markdown_parts:
        raise ValidationError(f"MinerU tráº£ vá» Markdown rá»—ng cho cĂ¡c chunk cá»§a {filename}")
    return "\n".join(markdown_parts).strip() + "\n"


async def parse_pdf_object_to_markdown(
    object_uri: str,
    filename: str,
    *,
    doc_role: str | None = None,
) -> str:
    """Parse an R2/local PDF or use MinerU direct URL ingest when safe."""
    from sag_api.core.errors import ConfigurationError
    from sag_api.parsing.mineru import MinerUClient

    if not settings.mineru_configured:
        raise ConfigurationError("PDF object cáº§n MinerU Ä‘Æ°á»£c cáº¥u hĂ¬nh")

    cache_root = (Path(settings.upload_dir).resolve() / "objects")
    cache_root.mkdir(parents=True, exist_ok=True)
    _cleanup_stale_pdf_parts(cache_root)
    source_uri = object_uri.strip()
    mineru_base = str(settings.mineru_base_url or "").lower()
    direct_supported = "mineru.net" in mineru_base or "opendatalab" in mineru_base
    if (
        source_uri.startswith(("http://", "https://"))
        and not source_uri.lower().split("?", 1)[0].endswith(".zip")
        and getattr(settings, "mineru_direct_url", True)
        and direct_supported
    ):
        declared_size: int | None = None
        try:
            timeout = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                headers={"User-Agent": "AIInvest-SAG/1.0"},
            ) as client:
                head = await client.head(source_uri)
                raw_length = head.headers.get("content-length")
                if head.status_code < 400 and raw_length and raw_length.isdigit():
                    declared_size = int(raw_length)
        except (httpx.TimeoutException, httpx.HTTPError):
            declared_size = None

        direct_limit = max(1, int(getattr(settings, "mineru_direct_url_max_mb", 200))) * 1024 * 1024
        # Content-Length is advisory. A missing header must not force a
        # duplicate local download; let MinerU inspect the URL first.
        if declared_size is None or declared_size <= direct_limit:
            try:
                markdown = await MinerUClient(settings).parse_url(source_uri, filename)
                return clean_ocr_markdown(markdown, filename, doc_role=doc_role)
            except (ServiceUnavailableError, UpstreamError) as exc:
                if not _is_mineru_size_or_page_limit_error(exc):
                    raise
                # The direct endpoint can reject a source after it inspects the
                # document (for example page count); use the stream/chunk path.
    temp_fd, temp_name = tempfile.mkstemp(prefix="sag-pdf-", suffix=".pdf", dir=cache_root)
    os.close(temp_fd)
    temp_path = Path(temp_name)
    try:
        stream_meta: dict[str, Any]
        if object_uri.strip().startswith("r2://"):
            from sag_api.services.r2_storage import SagR2StorageClient

            without_scheme = object_uri.strip()[len("r2://") :]
            if "/" not in without_scheme:
                raise ValidationError("R2 URI pháº£i cĂ³ dáº¡ng r2://bucket/key")
            bucket, key = without_scheme.split("/", 1)
            client = SagR2StorageClient()
            if bucket:
                client.bucket_name = bucket
            stream_meta = await asyncio.to_thread(client.download_bctc_to_file, key, temp_path)
        elif object_uri.strip().startswith(("http://", "https://")):
            source_url = object_uri.strip()
            digest = hashlib.sha256()
            size = 0
            max_single_file_bytes = max(1, int(getattr(settings, "mineru_chunk_max_mb", 20))) * 1024 * 1024
            timeout = httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=15.0)
            try:
                async with httpx.AsyncClient(
                    timeout=timeout,
                    follow_redirects=True,
                    headers={"User-Agent": "AIInvest-SAG/1.0"},
                ) as client:
                    # HEAD is advisory only: some financial hosts omit or
                    # falsify Content-Length. The streamed byte count remains
                    # authoritative for deciding whether to chunk.
                    declared_size: int | None = None
                    try:
                        head = await client.head(source_url)
                        raw_length = head.headers.get("content-length")
                        if head.status_code < 400 and raw_length and raw_length.isdigit():
                            declared_size = int(raw_length)
                    except (httpx.TimeoutException, httpx.HTTPError):
                        declared_size = None
                    async with client.stream("GET", source_url) as response:
                        if response.status_code >= 400:
                            raise ValidationError(
                                f"source fetch failed: HTTP {response.status_code} url={source_url}"
                            )
                        with temp_path.open("wb") as output:
                            async for chunk in response.aiter_bytes(1024 * 1024):
                                if not chunk:
                                    continue
                                output.write(chunk)
                                digest.update(chunk)
                                size += len(chunk)
            except ValidationError:
                raise
            except (httpx.TimeoutException, httpx.HTTPError) as exc:
                raise ValidationError(f"source fetch failed: url={source_url}: {exc}") from exc
            stream_meta = {
                "sha256": digest.hexdigest(),
                "size": size,
                "declared_size": declared_size,
                "chunked": size > max_single_file_bytes,
                "source_url": source_url,
            }
        else:
            source = Path(object_uri[len("file://") :] if object_uri.startswith("file://") else object_uri).resolve()
            if not source.is_file():
                raise ValidationError("object_uri local khĂ´ng tá»“n táº¡i hoáº·c khĂ´ng pháº£i file")
            digest = hashlib.sha256()
            size = 0
            with source.open("rb") as source_file, temp_path.open("wb") as output:
                for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            stream_meta = {"sha256": digest.hexdigest(), "size": size}

        if zipfile.is_zipfile(temp_path):
            _extract_pdf_from_zip(temp_path, doc_role)
        with temp_path.open("rb") as pdf_file:
            if pdf_file.read(4) != b"%PDF":
                raise ValidationError("object_uri pháº£i trá» tá»›i PDF há»£p lá»‡")
        # R2 is the durable source of truth. Keep this PDF only for the active
        # MinerU call; do not accumulate a second long-lived PDF cache in SAG.
        max_single_file_bytes = max(1, int(getattr(settings, "mineru_chunk_max_mb", 20))) * 1024 * 1024
        needs_chunking = temp_path.stat().st_size > max_single_file_bytes
        if not needs_chunking:
            try:
                import fitz
                with fitz.open(str(temp_path)) as pdf:
                    needs_chunking = len(pdf) > int(getattr(settings, "mineru_chunk_trigger_pages", 200))
            except Exception:
                # Some upstream/test streams expose a valid PDF header before
                # the full page tree is available.  Let MinerU validate those
                # small files instead of turning an advisory page check into a
                # local hard failure.
                needs_chunking = False
        if needs_chunking:
            markdown = await _parse_large_pdf_in_chunks(temp_path, filename)
        else:
            markdown = await MinerUClient(settings).parse(str(temp_path))
        return clean_ocr_markdown(markdown, filename, doc_role=doc_role)
    finally:
        _unlink_with_retry(temp_path)


async def _persist_canonical_markdown_uri(
    markdown: str,
    *,
    ticker: str,
    doc_role: str,
    content_hash: str,
    fallback_uri: str,
) -> str:
    if not settings.r2_write_canonical_markdown:
        return fallback_uri
    try:
        from sag_api.services.r2_storage import SagR2StorageClient

        client = SagR2StorageClient()
        if not client.is_configured:
            return fallback_uri
        safe_ticker = re.sub(r"[^A-Z0-9._-]+", "_", ticker.upper()).strip("_") or "UNKNOWN"
        safe_role = re.sub(r"[^A-Z0-9._-]+", "_", doc_role.upper()).strip("_") or "UNKNOWN"
        prefix = settings.r2_canonical_prefix.strip().strip("/")
        key = f"{prefix}/{safe_ticker}/{safe_role}/{content_hash}.md" if prefix else f"{safe_ticker}/{safe_role}/{content_hash}.md"
        if not client.file_exists(key):
            client.upload_parsed_markdown(key, markdown)
        return f"r2://{client.bucket_name}/{key}"
    except Exception as error:
        logger.warning("Canonical Markdown R2 persist failed for %s/%s: %s", ticker, doc_role, error)
        return fallback_uri


async def create_financial_document(
    session: AsyncSession,
    ticker: str,
    body: DocumentCreateIn,
) -> tuple[Document, bool]:
    if settings.environment == "prod" and body.processing_mode != "FULL":
        body = body.model_copy(update={"processing_mode": "FULL"})
    issuer = await get_or_create_issuer(session, ticker)
    role = normalize_doc_role(body.doc_role)
    if role is None:
        raise ValidationError("doc_role lĂ  báº¯t buá»™c")

    if role == DocumentRole.ANNUAL_BACKBONE.value and (body.fiscal_year is None or body.period_end is None):
        raise ValidationError("ANNUAL_BACKBONE báº¯t buá»™c fiscal_year vĂ  period_end")
    if role == DocumentRole.LATEST_QUARTER.value and (
        body.fiscal_year is None or body.fiscal_quarter is None or body.period_end is None
    ):
        raise ValidationError("LATEST_QUARTER báº¯t buá»™c fiscal_year, fiscal_quarter vĂ  period_end")
    if role == DocumentRole.GOVERNANCE_REPORT.value and (body.period_start is None or body.period_end is None):
        raise ValidationError("GOVERNANCE_REPORT báº¯t buá»™c period_start vĂ  period_end")

    canonical, raw_hash, content_hash, size_bytes, resolved_uri = await _read_markdown_from_body(body)

    conflict = await session.scalar(
        select(Document).where(
            Document.issuer_id == issuer.id,
            Document.content_sha256 == content_hash,
            Document.doc_role.is_not(None),
            Document.doc_role != role,
        )
    )
    if conflict is not None:
        raise ConflictError("ROLE_CONFLICT: cĂ¹ng hash Ä‘Ă£ Ä‘Æ°á»£c khai bĂ¡o báº±ng role khĂ¡c")

    existing_rows = (
        await session.execute(
            select(Document).where(
            Document.issuer_id == issuer.id,
            Document.doc_role == role,
            Document.fiscal_year == body.fiscal_year,
            Document.fiscal_quarter == body.fiscal_quarter,
            Document.content_sha256 == content_hash,
            )
        )
    ).scalars().all()
    # Historical retries can leave duplicate rows for the same canonical
    # artifact. A verified result is authoritative; never revive a failed or
    # cancelled duplicate and pay for another LLM run.
    existing = next(
        (
            row
            for row in existing_rows
            if _status_value(row.status) == DocumentStatus.READY.value
            and _document_ready_for_activation(row)
        ),
        existing_rows[0] if existing_rows else None,
    )
    if existing is not None:
        if body.processing_mode == "OCR_ONLY" and existing.asset_id:
            asset = await session.get(DocumentAsset, existing.asset_id)
            if asset is not None and not str(asset.object_uri or "").startswith("r2://"):
                r2_uri = await _persist_canonical_markdown_uri(
                    canonical,
                    ticker=issuer.ticker,
                    doc_role=role,
                    content_hash=content_hash,
                    fallback_uri="",
                )
                if r2_uri.startswith("r2://"):
                    asset.object_uri = r2_uri

        # A transport retry must be able to recover a failed inline run.  Keep
        # READY/PROCESSING documents idempotent. OCR_ONLY also repairs a stale
        # PROCESSING/PENDING row instead of waiting forever for an old job.
        needs_ocr_rebuild = (
            body.processing_mode == "OCR_ONLY"
            and existing.structure_status != ProcessingStageStatus.COMPLETE.value
        )
        if needs_ocr_rebuild or _status_value(existing.status) in {DocumentStatus.FAILED.value, DocumentStatus.CANCELLED.value}:
            existing.status = DocumentStatus.PROCESSING
            existing.progress = 0
            existing.error = None
            existing.is_active = False
            if settings.process_documents_inline and settings.environment != "prod":
                if body.processing_mode == "OCR_ONLY":
                    mark_document_ocr_ready(existing)
                else:
                    await rebuild_structure_and_embeddings(session, issuer, existing, canonical)
                if body.activate and _document_ready_for_activation(existing):
                    await activate_document(session, issuer.id, existing)
            else:
                await enqueue_document_processing(session, existing, processing_mode=body.processing_mode)
        return existing, True

    cache_path = _cache_path_for(content_hash, ".md")
    if not cache_path.exists():
        cache_path.write_bytes(canonical.encode("utf-8"))

    existing_asset_uri = await session.scalar(
        select(DocumentAsset.object_uri)
        .where(DocumentAsset.content_sha256 == content_hash)
        .limit(1)
    )
    asset_uri = existing_asset_uri if str(existing_asset_uri or "").startswith("r2://") else await _persist_canonical_markdown_uri(
        canonical,
        ticker=issuer.ticker,
        doc_role=role,
        content_hash=content_hash,
        fallback_uri=resolved_uri,
    )
    asset = DocumentAsset(
        issuer_id=issuer.id,
        asset_kind="canonical_markdown",
        object_uri=asset_uri,
        content_type="text/markdown; charset=utf-8",
        size_bytes=size_bytes,
        content_sha256=content_hash,
        canonical_markdown_sha256=content_hash,
        parser_version="provided-markdown",
        canonicalization_version=CANONICALIZATION_VERSION,
        metadata_json={**body.metadata, "local_cache_path": str(cache_path), "raw_content_sha256": raw_hash},
    )
    session.add(asset)
    await session.flush()

    document = Document(
        source_id=None,
        issuer_id=issuer.id,
        asset_id=asset.id,
        filename=body.title,
        content_type="text/markdown; charset=utf-8",
        size_bytes=size_bytes,
        storage_path=str(cache_path),
        status=DocumentStatus.PROCESSING,
        chunk_count=0,
        event_count=0,
        progress=0,
        token_usage=0,
        doc_role=role,
        is_active=False,
        activation_requested=body.activate,
        fiscal_year=body.fiscal_year,
        fiscal_quarter=body.fiscal_quarter,
        period_start=body.period_start,
        period_end=body.period_end,
        content_sha256=content_hash,
        processing_version=PROCESSING_VERSION,
        structure_status=ProcessingStageStatus.PENDING.value,
        extraction_status=ProcessingStageStatus.PENDING.value,
        embedding_status=ProcessingStageStatus.PENDING.value,
        fact_count=0,
        coverage={},
    )
    session.add(document)
    await session.flush()

    if settings.process_documents_inline and settings.environment != "prod":
        if body.processing_mode == "OCR_ONLY":
            mark_document_ocr_ready(document)
        else:
            await rebuild_structure_and_embeddings(session, issuer, document, canonical)
        if body.activate and _document_ready_for_activation(document):
            await activate_document(session, issuer.id, document)
    else:
        await enqueue_document_processing(session, document, processing_mode=body.processing_mode)
    await session.flush()
    return document, False


async def enqueue_document_processing(
    session: AsyncSession,
    document: Document,
    *,
    processing_mode: str = "FULL",
) -> None:
    document.status = DocumentStatus.QUEUED
    document.progress = 0
    document.error = None
    await enqueue_processing_run(
        session,
        document_id=document.id,
        stage="document",
        processing_version=PROCESSING_VERSION,
        idempotency_key=f"{document.id}:{PROCESSING_VERSION}:document:{document.content_sha256 or ''}",
        metadata={
            "document_id": document.id,
            "doc_role": document.doc_role,
            "content_sha256": document.content_sha256,
            "processing_mode": processing_mode,
        },
    )


def mark_document_ocr_ready(document: Document) -> None:
    """Stop after canonical Markdown persistence; tree belongs to analysis."""
    document.status = DocumentStatus.OCR_READY
    document.progress = 20
    document.structure_status = ProcessingStageStatus.PENDING.value
    document.extraction_status = ProcessingStageStatus.INCOMPLETE.value
    document.embedding_status = ProcessingStageStatus.INCOMPLETE.value
    document.error = None
    document.coverage = {
        "mode": "ocr_only",
        "extraction": {"status": "NOT_RUN", "reason": "ocr_only"},
        "embedding": {"status": "NOT_RUN", "reason": "ocr_only"},
    }


async def process_document_run(session: AsyncSession, run: ProcessingRun) -> None:
    document = await session.get(Document, run.document_id)
    if document is None:
        await fail_processing_run(session, run, error="document_not_found", retryable=False)
        return
    if _status_value(document.status) == DocumentStatus.CANCELLED.value:
        await complete_processing_run(session, run, status=ProcessingStageStatus.INCOMPLETE.value, metadata={"cancelled": True})
        return
    issuer = await session.get(Issuer, document.issuer_id) if document.issuer_id else None
    if issuer is None:
        document.status = DocumentStatus.FAILED
        document.error = "issuer_not_found"
        await fail_processing_run(session, run, error="issuer_not_found", retryable=False)
        return
    asset = await session.get(DocumentAsset, document.asset_id) if document.asset_id else None
    try:
        markdown = await hydrate_markdown(document, asset, session)
        document.status = DocumentStatus.PROCESSING
        document.progress = 5
        # Do not hold a database transaction while waiting on LLM/extraction.
        # Persist the state transition first so API polling and lease recovery
        # observe PROCESSING rather than a stale terminal state from a retry.
        await session.commit()
        await asyncio.wait_for(
            rebuild_structure_and_embeddings(session, issuer, document, markdown),
            timeout=settings.processing_run_timeout_seconds,
        )
        if document.activation_requested and _document_ready_for_activation(document):
            await activate_document(session, issuer.id, document)
        run_metadata = {
            "document_status": document.status.value if hasattr(document.status, "value") else document.status,
            "structure_status": document.structure_status,
            "extraction_status": document.extraction_status,
            "embedding_status": document.embedding_status,
        }
        if _document_ready_for_activation(document) or _status_value(document.status) == DocumentStatus.READY.value:
            await complete_processing_run(session, run, metadata=run_metadata)
        else:
            # A completed worker run must never mask a document that could not
            # produce a validated extraction and embedding set.
            document.status = DocumentStatus.FAILED
            await fail_processing_run(
                session,
                run,
                error=document.error or "document_processing_incomplete",
                retryable=False,
                metadata=run_metadata,
            )
    except Exception as exc:  # noqa: BLE001
        error = str(exc)[:2000] or type(exc).__name__
        document.status = DocumentStatus.FAILED
        document.error = error
        document.error_layer = "PROCESSING"
        document.error_stage = "DOCUMENT"
        # An outer timeout cancels the active extraction coroutine before it can
        # close its stage run. Close any remaining RUNNING telemetry so audits do
        # not mistake an abandoned substage for live work.
        await session.execute(
            update(ProcessingRun)
            .where(
                ProcessingRun.document_id == document.id,
                ProcessingRun.stage.in_(("structure", "extraction", "embedding")),
                ProcessingRun.status == ProcessingStageStatus.RUNNING.value,
            )
            .values(
                status=ProcessingStageStatus.FAILED.value,
                error=error,
                lease_expires_at=None,
                heartbeat_at=datetime.now(UTC),
            )
        )
        retryable = int(run.attempt or 0) < int(settings.processing_max_attempts)
        await fail_processing_run(
            session,
            run,
            error=error,
            retryable=retryable,
            metadata={"attempt_limit": settings.processing_max_attempts},
        )


async def rebuild_structure_and_embeddings(
    session: AsyncSession,
    issuer: Issuer,
    document: Document,
    markdown: str,
) -> None:
    raw_markdown = clean_ocr_markdown(markdown, document.filename)
    markdown = analysis_markdown(raw_markdown, doc_role=document.doc_role)
    structure_run = _stage_run(document, "structure", ProcessingStageStatus.RUNNING.value)
    session.add(structure_run)
    metadata = {
        "ticker": issuer.ticker,
        "title": document.filename,
        "doc_role": document.doc_role,
        "fiscal_year": document.fiscal_year,
        "fiscal_quarter": document.fiscal_quarter,
    }
    nodes, coverage = parse_markdown_tree(markdown, document_id=document.id, source_id=issuer.id, metadata=metadata)
    await session.execute(delete(DocumentTreeNode).where(DocumentTreeNode.document_id == document.id))
    await session.execute(delete(EmbeddingChunk).where(EmbeddingChunk.document_id == document.id))
    # Make the old node identities disappear before inserting deterministic
    # replacements with the same (document_id, node_id) unique key.
    await session.flush()
    for node in nodes:
        node_meta = {**node.metadata}
        excluded, reason = _excluded_policy(node.heading_path)
        node_meta["excluded_from_analysis"] = excluded
        if reason:
            node_meta["exclusion_reason"] = reason
        session.add(
            DocumentTreeNode(
                document_id=document.id,
                source_id=None,
                issuer_id=issuer.id,
                node_id=node.node_id,
                parent_id=node.parent_id,
                level=node.level,
                order_index=node.order,
                heading=node.heading,
                heading_path=node.heading_path,
                node_kind=node.node_kind,
                start_line=node.start_line,
                end_line=node.end_line,
                content_hash=node.content_hash,
                summary=None,
                relevance_json={"relevance": "NONE"},
                metadata_json=node_meta,
            )
        )
    await session.flush()

    document.structure_status = ProcessingStageStatus.COMPLETE.value
    _complete_stage(structure_run, coverage)
    node_rows = (
        await session.execute(
            select(DocumentTreeNode)
            .where(DocumentTreeNode.document_id == document.id)
            .order_by(DocumentTreeNode.order_index.asc())
        )
    ).scalars().all()

    extraction_error_metadata = None
    extraction_error: str | None = None
    try:
        extraction_run = _stage_run(document, "extraction", ProcessingStageStatus.RUNNING.value)
        session.add(extraction_run)
        # The LLM call can take minutes. Commit the structure and stage marker
        # first so the session returns its DB connection to the pool while
        # waiting on the external provider.
        await session.commit()
        extraction = await extract_and_persist_manifest(
            session, issuer, document, markdown, node_rows,
            statement_markdown=raw_markdown,
        )
    except Exception as exc:  # noqa: BLE001
        extraction = None
        extraction_error = str(exc)[:2000]
        extraction_error_metadata = {
            "mode": "llm_manifest",
            "error_type": type(exc).__name__,
            "error": extraction_error,
        }
        document.extraction_status = ProcessingStageStatus.FAILED.value
        document.error = extraction_error
        if "extraction_run" in locals():
            _complete_stage(extraction_run, extraction_error_metadata, status=ProcessingStageStatus.FAILED.value, error=extraction_error)
    else:
        document.extraction_status = extraction.status
        document.fact_count = extraction.fact_count
        document.token_usage = int(document.token_usage or 0) + int(extraction.token_usage or 0)
        _complete_stage(extraction_run, extraction.metadata or {}, status=extraction.status, error=extraction.error)
        if extraction.status != ProcessingStageStatus.COMPLETE.value:
            # Propagate the exact LLM failure reason instead of a generic placeholder.
            extraction_error = (extraction.error or "")[:2000] or extraction.status
            document.error = extraction_error

    # Persist extraction/stage state before starting embedding. The embedding
    # provider is also external and must not inherit the extraction session's
    # checked-out connection.
    await session.commit()

    embedding = None
    embedding_coverage: dict[str, Any] | None = None
    if settings.embedding_enabled and document.extraction_status == ProcessingStageStatus.COMPLETE.value:
        embedding_run = _stage_run(document, "embedding", ProcessingStageStatus.RUNNING.value)
        session.add(embedding_run)
        embedding = await build_embeddings_for_document(session, issuer, document, markdown)
        document.embedding_status = embedding.status
        _complete_stage(embedding_run, embedding.metadata or {}, status=embedding.status, error=embedding.error)
        embedding_coverage = embedding.metadata if embedding is not None else None
        if embedding.status != ProcessingStageStatus.COMPLETE.value:
            # Only report a provider/vector failure when embedding actually ran.
            embedding_error = (embedding.error or "")[:2000]
            if embedding_error:
                suffix = embedding_error
            else:
                suffix = "EMBEDDING_INCOMPLETE: chÆ°a cĂ³ embedding provider/vector há»£p lá»‡"
            document.error = f"{document.error}; {suffix}" if document.error else suffix
    else:
        document.embedding_status = ProcessingStageStatus.INCOMPLETE.value
        embedding_coverage = {
            "status": ProcessingStageStatus.INCOMPLETE.value,
            "reason": "disabled" if not settings.embedding_enabled else "skipped_because_extraction_incomplete",
            "extraction_status": document.extraction_status,
            "extraction_error": extraction_error,
        }
    if (
        document.extraction_status == ProcessingStageStatus.COMPLETE.value
        and (not settings.embedding_enabled or document.embedding_status == ProcessingStageStatus.COMPLETE.value)
    ):
        # Äá»§ Ä‘iá»u kiá»‡n pipeline: Ä‘á»ƒ caller quyáº¿t activate (READY) sau.
        # KhĂ´ng gĂ¡n FAILED á»Ÿ Ä‘Ă¢y Ä‘á»ƒ phĂ¢n biá»‡t "complete nhÆ°ng chÆ°a active"
        # vá»›i "fail tháº­t".
        document.status = DocumentStatus.PROCESSING
        document.error = None
        document.progress = 80
    else:
        document.status = DocumentStatus.FAILED
        document.progress = 60
    extraction_coverage: dict[str, Any] | None
    if extraction is not None:
        extraction_coverage = dict(extraction.metadata or {})
        extraction_coverage.setdefault("status", extraction.status)
        if extraction.error:
            extraction_coverage.setdefault("error", extraction.error)
    else:
        extraction_coverage = extraction_error_metadata
    document.coverage = {
        **coverage,
        "reference_validity": 1.0 if document.extraction_status == ProcessingStageStatus.COMPLETE.value else 0.0,
        "extraction": extraction_coverage,
        "embedding": embedding_coverage,
    }
    document.chunk_count = len(
        (await session.execute(select(EmbeddingChunk).where(EmbeddingChunk.document_id == document.id))).scalars().all()
    )


async def rebuild_document_structure_only(
    session: AsyncSession,
    issuer: Issuer,
    document: Document,
    markdown: str,
) -> None:
    """Build OCR/tree artifacts without invoking extraction or embeddings."""
    markdown = analysis_markdown(markdown, doc_role=document.doc_role)
    structure_run = _stage_run(document, "structure", ProcessingStageStatus.RUNNING.value)
    session.add(structure_run)
    metadata = {
        "ticker": issuer.ticker,
        "title": document.filename,
        "doc_role": document.doc_role,
        "fiscal_year": document.fiscal_year,
        "fiscal_quarter": document.fiscal_quarter,
    }
    nodes, coverage = parse_markdown_tree(markdown, document_id=document.id, source_id=issuer.id, metadata=metadata)
    await session.execute(delete(DocumentTreeNode).where(DocumentTreeNode.document_id == document.id))
    await session.execute(delete(EmbeddingChunk).where(EmbeddingChunk.document_id == document.id))
    await session.flush()
    for node in nodes:
        node_meta = {**node.metadata}
        excluded, reason = _excluded_policy(node.heading_path)
        node_meta["excluded_from_analysis"] = excluded
        if reason:
            node_meta["exclusion_reason"] = reason
        session.add(
            DocumentTreeNode(
                document_id=document.id,
                source_id=None,
                issuer_id=issuer.id,
                node_id=node.node_id,
                parent_id=node.parent_id,
                level=node.level,
                order_index=node.order,
                heading=node.heading,
                heading_path=node.heading_path,
                node_kind=node.node_kind,
                start_line=node.start_line,
                end_line=node.end_line,
                content_hash=node.content_hash,
                summary=None,
                relevance_json={"relevance": "NONE"},
                metadata_json=node_meta,
            )
        )
    await session.flush()
    document.structure_status = ProcessingStageStatus.COMPLETE.value
    document.extraction_status = ProcessingStageStatus.INCOMPLETE.value
    document.embedding_status = ProcessingStageStatus.INCOMPLETE.value
    document.status = DocumentStatus.OCR_READY
    document.progress = 40
    document.error = None
    document.coverage = {
        **coverage,
        "mode": "ocr_only",
        "extraction": {"status": "NOT_RUN", "reason": "ocr_only"},
        "embedding": {"status": "NOT_RUN", "reason": "ocr_only"},
    }
    _complete_stage(structure_run, coverage)


def _stage_run(document: Document, stage: str, status: str) -> ProcessingRun:
    now = datetime.now(UTC)
    return ProcessingRun(
        document_id=document.id,
        processing_version=PROCESSING_VERSION,
        stage=stage,
        status=status,
        attempt=1,
        idempotency_key=f"{document.id}:{PROCESSING_VERSION}:{stage}",
        heartbeat_at=now,
        metadata_json={"started_at": now.isoformat()},
    )


def _complete_stage(
    run: ProcessingRun,
    metadata: dict[str, Any],
    *,
    status: str = ProcessingStageStatus.COMPLETE.value,
    error: str | None = None,
) -> None:
    now = datetime.now(UTC)
    run.status = status
    run.heartbeat_at = now
    run.metadata_json = {**(run.metadata_json or {}), **metadata, "finished_at": now.isoformat()}
    run.error = error


def _fail_stage(run: ProcessingRun, error: str) -> None:
    _complete_stage(run, {}, status=ProcessingStageStatus.FAILED.value, error=error[:2000])


def _excluded_policy(heading_path: str) -> tuple[bool, str | None]:
    folded = heading_path.casefold()
    markers = (
        "báº£ng cĂ¢n Ä‘á»‘i káº¿ toĂ¡n",
        "bĂ¡o cĂ¡o káº¿t quáº£ hoáº¡t Ä‘á»™ng",
        "bĂ¡o cĂ¡o lÆ°u chuyá»ƒn tiá»n",
        "chá»¯ kĂ½",
        "ngÆ°á»i láº­p",
        "káº¿ toĂ¡n trÆ°á»Ÿng",
    )
    for marker in markers:
        if marker in folded:
            return True, marker
    return False, None


def _document_ready_for_activation(document: Document) -> bool:
    return (
        document.structure_status == ProcessingStageStatus.COMPLETE.value
        and document.extraction_status == ProcessingStageStatus.COMPLETE.value
        and (
            not settings.embedding_enabled
            or document.embedding_status == ProcessingStageStatus.COMPLETE.value
        )
    )


def _status_value(status: Any) -> str:
    return str(status.value if hasattr(status, "value") else status)


async def activate_document(session: AsyncSession, issuer_id: str, document: Document) -> None:
    if not _document_ready_for_activation(document):
        raise ConflictError("Document chÆ°a Ä‘á»§ Ä‘iá»u kiá»‡n activate")
    rows = (
        await session.execute(
            select(Document).where(
                Document.issuer_id == issuer_id,
                Document.doc_role == document.doc_role,
                Document.is_active.is_(True),
                Document.id != document.id,
            )
        )
    ).scalars()
    for row in rows:
        row.is_active = False
    # PostgreSQL's partial unique index permits only one active document for an
    # issuer/role. Flush the deactivations before activating a replacement so a
    # retry cannot transiently violate that invariant during autoflush.
    await session.flush()
    document.is_active = True
    document.status = DocumentStatus.READY
    document.progress = 100
    await session.execute(
        delete(EmbeddingChunk).where(
            EmbeddingChunk.issuer_id == issuer_id,
            EmbeddingChunk.doc_role == document.doc_role,
            EmbeddingChunk.active_version.is_(True),
            EmbeddingChunk.document_id != document.id,
        )
    )
    active_chunks = (
        await session.execute(select(EmbeddingChunk).where(EmbeddingChunk.document_id == document.id))
    ).scalars()
    for chunk in active_chunks:
        chunk.active_version = True
    # A retry can leave a failed duplicate for the same reporting period. Once
    # a replacement is verified READY, it has no analytical value and must not
    # pollute audits or future benchmark inventories.
    await session.execute(
        update(Document)
        .where(
            Document.issuer_id == issuer_id,
            Document.doc_role == document.doc_role,
            Document.fiscal_year == document.fiscal_year,
            Document.fiscal_quarter == document.fiscal_quarter,
            Document.status == DocumentStatus.FAILED,
            Document.id != document.id,
        )
        .values(
            status=DocumentStatus.CANCELLED,
            is_active=False,
            error="superseded_by_verified_document",
        )
    )


async def list_documents(session: AsyncSession, ticker: str) -> list[tuple[Document, DocumentAsset | None, Issuer]]:
    issuer = await get_or_create_issuer(session, ticker)
    docs = (
        await session.execute(
            select(Document).where(Document.issuer_id == issuer.id).order_by(Document.created_at.desc())
        )
    ).scalars().all()
    out = []
    for doc in docs:
        asset = await session.get(DocumentAsset, doc.asset_id) if doc.asset_id else None
        out.append((doc, asset, issuer))
    return out


async def get_document_for_ticker(session: AsyncSession, ticker: str, document_id: str) -> tuple[Issuer, Document, DocumentAsset | None]:
    issuer = await get_or_create_issuer(session, ticker)
    doc = await session.get(Document, document_id)
    if doc is None or doc.issuer_id != issuer.id:
        raise NotFoundError("TĂ i liá»‡u khĂ´ng tá»“n táº¡i")
    asset = await session.get(DocumentAsset, doc.asset_id) if doc.asset_id else None
    return issuer, doc, asset


async def active_documents(session: AsyncSession, ticker: str) -> tuple[Issuer, list[Document]]:
    issuer = await get_or_create_issuer(session, ticker)
    docs = (
        await session.execute(
            select(Document).where(Document.issuer_id == issuer.id, Document.is_active.is_(True))
        )
    ).scalars().all()
    return issuer, docs


def _period_label(document: Document) -> str | None:
    if document.fiscal_year and document.fiscal_quarter:
        return f"{document.fiscal_year}Q{document.fiscal_quarter}"
    if document.fiscal_year:
        return str(document.fiscal_year)
    if document.period_end:
        return str(document.period_end)
    return None


def document_to_out(document: Document, asset: DocumentAsset | None, issuer: Issuer, *, deduplicated: bool = False) -> dict[str, Any]:
    result = {
        "id": document.id,
        "ticker": issuer.ticker,
        "title": document.filename,
        "doc_role": document.doc_role or "",
        "fiscal_year": document.fiscal_year,
        "fiscal_quarter": document.fiscal_quarter,
        "period_start": document.period_start,
        "period_end": document.period_end,
        "is_active": document.is_active,
        "status": document.status.value if hasattr(document.status, "value") else str(document.status),
        "content_sha256": document.content_sha256,
        "canonical_markdown_sha256": asset.canonical_markdown_sha256 if asset else document.content_sha256,
        "object_uri": asset.object_uri if asset else document.storage_path,
        "processing_version": document.processing_version,
        "structure_status": document.structure_status,
        "extraction_status": document.extraction_status,
        "embedding_status": document.embedding_status,
        "fact_count": document.fact_count,
        "coverage": document.coverage or {},
        "deduplicated": deduplicated,
    }
    return result


async def hydrate_markdown(document: Document, asset: DocumentAsset | None, session: AsyncSession | None = None) -> str:
    cache_path = None
    if asset and asset.metadata_json:
        cache_path = asset.metadata_json.get("local_cache_path")
    path = Path(cache_path or document.storage_path)
    if path.exists():
        return path.read_text(encoding="utf-8")
    if asset and asset.object_uri:
        raw_bytes = await _read_object_uri(asset.object_uri)
        try:
            raw_markdown = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NotFoundError("Markdown canonical object khĂ´ng pháº£i UTF-8") from exc
        canonical = canonicalize_markdown(raw_markdown)
        canonical_hash = _hash_bytes(canonical.encode("utf-8"))
        if asset.canonical_markdown_sha256 and canonical_hash != asset.canonical_markdown_sha256:
            raise NotFoundError("Markdown canonical object hash khĂ´ng khá»›p document asset")
        cache_path = _cache_path_for(canonical_hash, ".md")
        if not cache_path.exists():
            cache_path.write_text(canonical, encoding="utf-8")
        asset.metadata_json = {**(asset.metadata_json or {}), "local_cache_path": str(cache_path)}
        document.storage_path = str(cache_path)
        if session is not None:
            await session.flush()
        return canonical
    raise NotFoundError("Markdown canonical khĂ´ng cĂ³ trong local cache hoáº·c object store")


async def assess_retired_quality_legacy(session: AsyncSession, ticker: str) -> dict[str, Any]:
    issuer, docs = await active_documents(session, ticker)
    active_roles = sorted({doc.doc_role for doc in docs if doc.doc_role})
    missing = [role for role in ACTIVE_DOCUMENT_ROLES if role not in active_roles]
    doc_ids = [doc.id for doc in docs if doc.extraction_status == ProcessingStageStatus.COMPLETE.value]
    # Retired: competitive-evidence signals are no longer an assessment input.
    signals = []
    evidence_digest = hashlib.sha256(
        json.dumps(
            {
                "documents": [
                    [doc.id, doc.content_sha256, doc.extraction_status, doc.embedding_status]
                    for doc in docs
                ],
                "signals": [
                    [signal.id, signal.document_id, signal.node_id, signal.updated_at.isoformat() if signal.updated_at else None]
                    for signal in signals
                ],
                "policy_version": "financial-quality-only-v1",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    cached = await session.scalar(
        select(AssessmentRun)
        .where(AssessmentRun.issuer_id == issuer.id, AssessmentRun.assessment_kind == "business_quality")
        .order_by(AssessmentRun.updated_at.desc())
    )
    if cached is not None and cached.evidence_digest == evidence_digest and cached.result_json:
        return cached.result_json
    evidence = []
    evidence_roles = set()
    for signal in signals:
        role = next((d.doc_role for d in docs if d.id == signal.document_id), None)
        if role:
            evidence_roles.add(role)
        evidence.append({
                "document_id": signal.document_id,
                "node_id": signal.node_id,
                "evidence_span_id": signal.evidence_span_id,
                "evidence_type": signal.evidence_type,
                "signal": signal.signal,
                "strength": signal.strength,
                "durability": signal.durability,
                "materiality": signal.materiality,
                "doc_role": role,
                "direction": signal.direction,
            })
    reasons = []
    if missing:
        reasons.append(f"Thiáº¿u tĂ i liá»‡u active: {', '.join(missing)}")
    if not evidence:
        reasons.append("ChÆ°a cĂ³ Ä‘á»§ evidence tĂ i chĂ­nh Ä‘Ă£ xĂ¡c minh")
    if len(evidence_roles) < 2:
        reasons.append("Evidence chÆ°a Ä‘áº¿n tá»« Ă­t nháº¥t 2 document roles")
    if any(doc.extraction_status != ProcessingStageStatus.COMPLETE.value for doc in docs):
        reasons.append("Extraction cá»§a bá»™ tĂ i liá»‡u active chÆ°a COMPLETE")
    if evidence and not any("chÆ°a" in reason.lower() for reason in reasons):
        status = "COMPLETE"
    elif signals:
        status = "PARTIAL"
    else:
        status = "INSUFFICIENT"
    result = {
        "ticker": issuer.ticker,
        "assessment_status": status,
        "quality_score": None,
        "evidence_status": "VERIFIED" if evidence else "INSUFFICIENT",
        "coverage_ratio": 1.0 if evidence else 0.0,
        "active_roles": active_roles,
        "missing_roles": missing,
        "evidence": evidence,
        "reasons": reasons,
        "policy_version": "financial-quality-only-v1",
    }
    if cached is None:
        cached = AssessmentRun(issuer_id=issuer.id, assessment_kind="business_quality")
        session.add(cached)
    cached.status = result["assessment_status"]
    cached.policy_version = "financial-quality-only-v1"
    cached.document_ids_json = doc_ids
    cached.result_json = result
    cached.evidence_digest = evidence_digest
    await session.flush()
    return result


def _score_pillar(evidence: list[dict[str, Any]], counter: list[dict[str, Any]]) -> tuple[str, float | None, float]:
    if not evidence and not counter:
        return "NO_EVIDENCE", None, 0.0
    if counter and not evidence:
        return "COUNTER_EVIDENCE", 30.0, 0.6
    if counter and evidence:
        return "MIXED", 50.0, 0.55
    strong = any(
        item.get("strength") == "strong"
        and item.get("durability") in {"long", "durable"}
        and item.get("materiality") in {"high", "material"}
        for item in evidence
    )
    roles = {item.get("doc_role") for item in evidence if item.get("doc_role")}
    if strong and len(roles) >= 2:
        return "SUPPORTED_STRONG", 90.0, 0.85
    if len(roles) >= 2 or len(evidence) >= 2:
        return "SUPPORTED_MULTI_SOURCE", 80.0, 0.75
    return "SUPPORTED_WEAK", 65.0, 0.55
