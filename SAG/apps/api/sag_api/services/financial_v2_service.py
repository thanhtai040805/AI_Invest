from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.errors import ConflictError, NotFoundError, ValidationError
from sag_api.db.models import (
    AssessmentRun,
    Document,
    DocumentAsset,
    DocumentTreeNode,
    EmbeddingChunk,
    EvidenceSpan,
    Fact,
    Issuer,
    MoatSignal,
    ProcessingRun,
    Relation,
    ReviewQueueItem,
)
from sag_api.enums import DocumentRole, DocumentStatus, FactType, MoatPillar, ProcessingStageStatus, RelationType
from sag_api.schemas.v2 import DocumentCreateIn
from sag_api.services.document_structure_service import (
    ACTIVE_DOCUMENT_ROLES,
    hydrate_node_content,
    normalize_doc_role,
    parse_markdown_tree,
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


def canonicalize_markdown(markdown: str) -> str:
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("\ufeff"):
        text = text[1:]
    return text if text.endswith("\n") else f"{text}\n"


def _ticker(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "", value.upper().strip())
    if not cleaned:
        raise ValidationError("ticker không hợp lệ")
    return cleaned


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cache_path_for(content_hash: str, suffix: str) -> Path:
    root = Path(settings.upload_dir).resolve() / "objects" / content_hash[:2]
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{content_hash}{suffix}"


async def get_or_create_issuer(session: AsyncSession, ticker: str) -> Issuer:
    ticker_clean = _ticker(ticker)
    issuer = await session.scalar(select(Issuer).where(Issuer.ticker == ticker_clean))
    if issuer is not None:
        return issuer
    issuer = Issuer(ticker=ticker_clean, legal_name=None, metadata_json={})
    session.add(issuer)
    await session.flush()
    return issuer


async def _read_markdown_from_body(body: DocumentCreateIn) -> tuple[str, str, str, int, str]:
    if body.markdown is not None:
        raw_bytes = body.markdown.encode("utf-8")
        uri = "inline://markdown"
    elif body.object_uri:
        raw_bytes = await _read_object_uri(body.object_uri)
        uri = body.object_uri
    else:
        raise ValidationError("API v2 cần markdown trực tiếp hoặc object_uri trỏ tới Markdown trên R2/local file")
    raw_hash = _hash_bytes(raw_bytes)
    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("Markdown object phải là UTF-8") from exc
    canonical = canonicalize_markdown(raw_text)
    data = canonical.encode("utf-8")
    content_hash = _hash_bytes(data)
    if body.content_sha256 and body.content_sha256 not in {raw_hash, content_hash}:
        raise ValidationError("content_sha256 không khớp raw hoặc canonical Markdown")
    if body.canonical_markdown_sha256 and body.canonical_markdown_sha256 != content_hash:
        raise ValidationError("canonical_markdown_sha256 không khớp Markdown canonical")
    return canonical, raw_hash, content_hash, len(data), uri


async def _read_object_uri(object_uri: str) -> bytes:
    uri = object_uri.strip()
    if uri.startswith("r2://"):
        from sag_api.services.r2_storage import SagR2StorageClient

        without_scheme = uri[len("r2://") :]
        if "/" not in without_scheme:
            raise ValidationError("R2 URI phải có dạng r2://bucket/key")
        bucket, key = without_scheme.split("/", 1)
        if not key:
            raise ValidationError("R2 URI thiếu object key")
        client = SagR2StorageClient()
        if bucket:
            client.bucket_name = bucket
        return client.download_bctc_bytes(key)
    if uri.startswith("file://"):
        path = Path(uri[len("file://") :]).resolve()
    else:
        path = Path(uri).resolve()
    if not path.exists() or not path.is_file():
        raise ValidationError("object_uri local không tồn tại hoặc không phải file")
    return path.read_bytes()


async def parse_pdf_bytes_to_markdown(data: bytes, filename: str) -> str:
    from sag_api.core.errors import ConfigurationError
    from sag_api.parsing.mineru import MinerUClient

    if not settings.mineru_configured:
        raise ConfigurationError("PDF upload cần MinerU được cấu hình; không fallback sang parser khác trong SAG v2")
    content_hash = _hash_bytes(data)
    pdf_path = _cache_path_for(content_hash, ".pdf")
    if not pdf_path.exists():
        pdf_path.write_bytes(data)
    markdown = await MinerUClient(settings).parse(str(pdf_path))
    if not markdown.strip():
        raise ValidationError(f"MinerU trả về Markdown rỗng cho {filename}")
    return markdown


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
    except Exception:
        return fallback_uri


async def create_financial_document(
    session: AsyncSession,
    ticker: str,
    body: DocumentCreateIn,
) -> tuple[Document, bool]:
    issuer = await get_or_create_issuer(session, ticker)
    role = normalize_doc_role(body.doc_role)
    if role is None:
        raise ValidationError("doc_role là bắt buộc")

    if role == DocumentRole.ANNUAL_BACKBONE.value and (body.fiscal_year is None or body.period_end is None):
        raise ValidationError("ANNUAL_BACKBONE bắt buộc fiscal_year và period_end")
    if role == DocumentRole.LATEST_QUARTER.value and (
        body.fiscal_year is None or body.fiscal_quarter is None or body.period_end is None
    ):
        raise ValidationError("LATEST_QUARTER bắt buộc fiscal_year, fiscal_quarter và period_end")
    if role == DocumentRole.GOVERNANCE_REPORT.value and (body.period_start is None or body.period_end is None):
        raise ValidationError("GOVERNANCE_REPORT bắt buộc period_start và period_end")

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
        raise ConflictError("ROLE_CONFLICT: cùng hash đã được khai báo bằng role khác")

    existing = await session.scalar(
        select(Document).where(
            Document.issuer_id == issuer.id,
            Document.doc_role == role,
            Document.fiscal_year == body.fiscal_year,
            Document.fiscal_quarter == body.fiscal_quarter,
            Document.content_sha256 == content_hash,
        )
    )
    if existing is not None:
        return existing, True

    cache_path = _cache_path_for(content_hash, ".md")
    if not cache_path.exists():
        cache_path.write_bytes(canonical.encode("utf-8"))

    asset_uri = await _persist_canonical_markdown_uri(
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
        await rebuild_structure_and_embeddings(session, issuer, document, canonical)
        if body.activate and _document_ready_for_activation(document):
            await activate_document(session, issuer.id, document)
    else:
        await enqueue_document_processing(session, document)
    await session.flush()
    return document, False


async def enqueue_document_processing(session: AsyncSession, document: Document) -> None:
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
        },
    )


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
        await rebuild_structure_and_embeddings(session, issuer, document, markdown)
        if document.activation_requested and _document_ready_for_activation(document):
            await activate_document(session, issuer.id, document)
        status = (
            ProcessingStageStatus.COMPLETE.value
            if _document_ready_for_activation(document) or _status_value(document.status) == DocumentStatus.READY.value
            else ProcessingStageStatus.INCOMPLETE.value
        )
        await complete_processing_run(
            session,
            run,
            status=status,
            metadata={
                "document_status": document.status.value if hasattr(document.status, "value") else document.status,
                "structure_status": document.structure_status,
                "extraction_status": document.extraction_status,
                "embedding_status": document.embedding_status,
            },
        )
    except Exception as exc:  # noqa: BLE001
        document.status = DocumentStatus.FAILED
        document.error = str(exc)[:2000]
        await fail_processing_run(session, run, error=str(exc)[:2000], retryable=True)


async def rebuild_structure_and_embeddings(
    session: AsyncSession,
    issuer: Issuer,
    document: Document,
    markdown: str,
) -> None:
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
    try:
        extraction_run = _stage_run(document, "extraction", ProcessingStageStatus.RUNNING.value)
        session.add(extraction_run)
        extraction = await extract_and_persist_manifest(session, issuer, document, markdown, node_rows)
    except Exception as exc:  # noqa: BLE001
        extraction = None
        extraction_error_metadata = {
            "mode": "llm_manifest",
            "error_type": type(exc).__name__,
            "error": str(exc)[:2000],
        }
        document.extraction_status = ProcessingStageStatus.FAILED.value
        document.error = f"EXTRACTION_FAILED: {str(exc)[:2000]}"
        if "extraction_run" in locals():
            _complete_stage(extraction_run, extraction_error_metadata, status=ProcessingStageStatus.FAILED.value, error=str(exc))
    else:
        document.extraction_status = extraction.status
        document.fact_count = extraction.fact_count
        document.token_usage = int(document.token_usage or 0) + int(extraction.token_usage or 0)
        _complete_stage(extraction_run, extraction.metadata or {}, status=extraction.status, error=extraction.error)

    embedding = None
    if document.extraction_status == ProcessingStageStatus.COMPLETE.value:
        embedding_run = _stage_run(document, "embedding", ProcessingStageStatus.RUNNING.value)
        session.add(embedding_run)
        embedding = await build_embeddings_for_document(session, issuer, document, markdown)
        document.embedding_status = embedding.status
        _complete_stage(embedding_run, embedding.metadata or {}, status=embedding.status, error=embedding.error)
    else:
        document.embedding_status = ProcessingStageStatus.INCOMPLETE.value
    document.status = DocumentStatus.FAILED
    if document.extraction_status != ProcessingStageStatus.COMPLETE.value and not document.error:
        document.error = "EXTRACTION_INCOMPLETE: chưa có full-document LLM manifest hợp lệ"
    if document.embedding_status != ProcessingStageStatus.COMPLETE.value:
        suffix = "EMBEDDING_INCOMPLETE: chưa có embedding provider/vector hợp lệ"
        document.error = f"{document.error}; {suffix}" if document.error else suffix
    document.coverage = {
        **coverage,
        "reference_validity": 1.0 if document.extraction_status == ProcessingStageStatus.COMPLETE.value else 0.0,
        "extraction": extraction.metadata if extraction is not None else extraction_error_metadata,
        "embedding": embedding.metadata if embedding is not None else None,
    }
    document.chunk_count = len(
        (await session.execute(select(EmbeddingChunk).where(EmbeddingChunk.document_id == document.id))).scalars().all()
    )
    document.progress = 80 if document.extraction_status == ProcessingStageStatus.COMPLETE.value else 60


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
        "bảng cân đối kế toán",
        "báo cáo kết quả hoạt động",
        "báo cáo lưu chuyển tiền",
        "chữ ký",
        "người lập",
        "kế toán trưởng",
    )
    for marker in markers:
        if marker in folded:
            return True, marker
    return False, None


def _document_ready_for_activation(document: Document) -> bool:
    return (
        document.structure_status == ProcessingStageStatus.COMPLETE.value
        and document.extraction_status == ProcessingStageStatus.COMPLETE.value
        and document.embedding_status == ProcessingStageStatus.COMPLETE.value
    )


def _status_value(status: Any) -> str:
    return str(status.value if hasattr(status, "value") else status)


async def activate_document(session: AsyncSession, issuer_id: str, document: Document) -> None:
    if not _document_ready_for_activation(document):
        raise ConflictError("Document chưa đủ điều kiện activate")
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
        raise NotFoundError("Tài liệu không tồn tại")
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
    return {
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
            raise NotFoundError("Markdown canonical object không phải UTF-8") from exc
        canonical = canonicalize_markdown(raw_markdown)
        canonical_hash = _hash_bytes(canonical.encode("utf-8"))
        if asset.canonical_markdown_sha256 and canonical_hash != asset.canonical_markdown_sha256:
            raise NotFoundError("Markdown canonical object hash không khớp document asset")
        cache_path = _cache_path_for(canonical_hash, ".md")
        if not cache_path.exists():
            cache_path.write_text(canonical, encoding="utf-8")
        asset.metadata_json = {**(asset.metadata_json or {}), "local_cache_path": str(cache_path)}
        document.storage_path = str(cache_path)
        if session is not None:
            await session.flush()
        return canonical
    raise NotFoundError("Markdown canonical không có trong local cache hoặc object store")


def heading_path_list(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(">") if part.strip()]


async def search_evidence(session: AsyncSession, ticker: str, query: str, *, top_k: int, include_historical: bool) -> list[dict[str, Any]]:
    issuer = await get_or_create_issuer(session, ticker)
    dense_hits = await _search_evidence_pgvector(session, issuer.id, query, top_k=top_k, include_historical=include_historical)
    if dense_hits:
        return dense_hits
    return await _search_evidence_lexical(session, issuer.id, query, top_k=top_k, include_historical=include_historical)


async def _search_evidence_pgvector(
    session: AsyncSession,
    issuer_id: str,
    query: str,
    *,
    top_k: int,
    include_historical: bool,
) -> list[dict[str, Any]]:
    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect != "postgresql" or not settings.effective_embedding_api_key:
        return []
    try:
        from sag_api.services.embedding_v2_service import _embed_texts

        query_vector = (await _embed_texts([query]))[0]
        vector_literal = "[" + ",".join(f"{float(value):.9g}" for value in query_vector) + "]"
        active_filter = "" if include_historical else "AND active_version = true"
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT
                        document_id,
                        doc_role,
                        node_id,
                        start_line,
                        end_line,
                        content_hash,
                        metadata_json,
                        1 - (embedding_vector <=> CAST(:query_vector AS vector)) AS dense_score
                    FROM sag.embedding_chunks
                    WHERE issuer_id = :issuer_id
                      AND embedding_vector IS NOT NULL
                      {active_filter}
                    ORDER BY embedding_vector <=> CAST(:query_vector AS vector)
                    LIMIT :limit
                    """
                ),
                {"issuer_id": issuer_id, "query_vector": vector_literal, "limit": max(top_k * 4, top_k)},
            )
        ).mappings().all()
    except Exception:
        return []

    folded_terms = [term for term in query.casefold().split() if term]
    ranked: list[tuple[float, Any]] = []
    for row in rows:
        metadata = row.get("metadata_json") or {}
        text_value = str(metadata.get("text") or "")
        lexical = _lexical_score(text_value, folded_terms)
        dense = float(row.get("dense_score") or 0.0)
        role_bonus = 1.0 if row.get("doc_role") in ACTIVE_DOCUMENT_ROLES else 0.0
        ranked.append((0.55 * dense + 0.30 * lexical + 0.15 * role_bonus, row))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "document_id": row["document_id"],
            "doc_role": row["doc_role"],
            "node_id": row["node_id"],
            "heading_path": heading_path_list((row.get("metadata_json") or {}).get("heading_path")),
            "line_span": (int(row["start_line"]), int(row["end_line"])),
            "score": score,
            "content": str((row.get("metadata_json") or {}).get("text") or ""),
            "quote_hash": row["content_hash"],
        }
        for score, row in ranked[:top_k]
    ]


async def _search_evidence_lexical(
    session: AsyncSession,
    issuer_id: str,
    query: str,
    *,
    top_k: int,
    include_historical: bool,
) -> list[dict[str, Any]]:
    folded_terms = [term for term in query.casefold().split() if term]
    stmt = select(EmbeddingChunk).where(EmbeddingChunk.issuer_id == issuer_id)
    if not include_historical:
        stmt = stmt.where(EmbeddingChunk.active_version.is_(True))
    chunks = (await session.execute(stmt)).scalars().all()
    ranked = []
    for chunk in chunks:
        text = str((chunk.metadata_json or {}).get("text") or "")
        folded = text.casefold()
        score = _lexical_score(text, folded_terms)
        if score:
            ranked.append((float(score), chunk, text))
    ranked.sort(key=lambda item: item[0], reverse=True)
    hits = []
    for score, chunk, text in ranked[:top_k]:
        hits.append(
            {
                "document_id": chunk.document_id,
                "doc_role": chunk.doc_role,
                "node_id": chunk.node_id,
                "heading_path": heading_path_list((chunk.metadata_json or {}).get("heading_path")),
                "line_span": (chunk.start_line, chunk.end_line),
                "score": score,
                "content": text,
                "quote_hash": chunk.content_hash,
            }
        )
    return hits


def _lexical_score(text_value: str, folded_terms: list[str]) -> float:
    if not folded_terms:
        return 0.0
    folded = text_value.casefold()
    raw = sum(folded.count(term) for term in folded_terms)
    return min(float(raw) / max(len(folded_terms), 1), 1.0)


async def assess_moat(session: AsyncSession, ticker: str) -> dict[str, Any]:
    issuer, docs = await active_documents(session, ticker)
    active_roles = sorted({doc.doc_role for doc in docs if doc.doc_role})
    missing = [role for role in ACTIVE_DOCUMENT_ROLES if role not in active_roles]
    doc_ids = [doc.id for doc in docs if doc.extraction_status == ProcessingStageStatus.COMPLETE.value]
    signals = []
    if doc_ids:
        signals = (
            await session.execute(
                select(MoatSignal).where(
                    MoatSignal.issuer_id == issuer.id,
                    MoatSignal.document_id.in_(doc_ids),
                    MoatSignal.validation_status == "VALIDATED",
                )
            )
        ).scalars().all()
    pillars = {}
    eligible = 0
    evidence_roles = set()
    for pillar in MoatPillar:
        pillar_signals = [s for s in signals if s.pillar == pillar.value]
        evidence = []
        counter = []
        for signal in pillar_signals:
            role = next((d.doc_role for d in docs if d.id == signal.document_id), None)
            if role:
                evidence_roles.add(role)
            item = {
                "document_id": signal.document_id,
                "node_id": signal.node_id,
                "evidence_span_id": signal.evidence_span_id,
                "signal": signal.signal,
                "strength": signal.strength,
                "durability": signal.durability,
                "materiality": signal.materiality,
                "doc_role": role,
            }
            if signal.direction == "counter":
                counter.append(item)
            else:
                evidence.append(item)
        verdict, score, confidence = _score_pillar(evidence, counter)
        if score is not None:
            eligible += 1
        pillars[pillar.value] = {
            "verdict": verdict,
            "score": score,
            "confidence": confidence,
            "evidence": evidence,
            "counter_evidence": counter,
        }
    reasons = []
    if missing:
        reasons.append(f"Thiếu tài liệu active: {', '.join(missing)}")
    if eligible < 3:
        reasons.append("Chưa đủ ít nhất 3 trụ có evidence/counter-evidence validated")
    if len(evidence_roles) < 2:
        reasons.append("Evidence chưa đến từ ít nhất 2 document roles")
    if any(doc.extraction_status != ProcessingStageStatus.COMPLETE.value for doc in docs):
        reasons.append("Extraction của bộ tài liệu active chưa COMPLETE")
    moat_score = None
    multiplier = None
    if not reasons:
        scores = [p["score"] for p in pillars.values() if p["score"] is not None]
        moat_score = round(sum(scores) / len(scores), 2)
        multiplier = round(max(0.85, min(1.15, 0.85 + (moat_score / 100.0) * 0.30)), 3)
        status = "COMPLETE"
    elif signals:
        status = "PARTIAL"
    else:
        status = "INSUFFICIENT"
    return {
        "ticker": issuer.ticker,
        "assessment_status": status,
        "moat_score": moat_score,
        "multiplier": multiplier,
        "coverage_ratio": round(eligible / 5, 4),
        "active_roles": active_roles,
        "missing_roles": missing,
        "pillars": pillars,
        "reasons": reasons,
        "policy_version": "moat-policy-v2",
    }


def _score_pillar(evidence: list[dict[str, Any]], counter: list[dict[str, Any]]) -> tuple[str, float | None, float]:
    if not evidence and not counter:
        return "NO_EVIDENCE", None, 0.0
    if counter and not evidence:
        return "COUNTER_EVIDENCE", 30.0, 0.6
    if counter and evidence:
        return "MIXED", 50.0, 0.55
    strong = any(
        item.get("strength") == "strong" and item.get("durability") == "durable" and item.get("materiality") == "material"
        for item in evidence
    )
    roles = {item.get("doc_role") for item in evidence if item.get("doc_role")}
    if strong and len(roles) >= 2:
        return "SUPPORTED_STRONG", 90.0, 0.85
    if len(roles) >= 2 or len(evidence) >= 2:
        return "SUPPORTED_MULTI_SOURCE", 80.0, 0.75
    return "SUPPORTED_WEAK", 65.0, 0.55


async def assess_gil(session: AsyncSession, ticker: str) -> dict[str, Any]:
    issuer, docs = await active_documents(session, ticker)
    active_roles = sorted({doc.doc_role for doc in docs if doc.doc_role})
    missing = [role for role in ACTIVE_DOCUMENT_ROLES if role not in active_roles]
    doc_ids = [doc.id for doc in docs if doc.extraction_status == ProcessingStageStatus.COMPLETE.value]
    relations = []
    facts = []
    if doc_ids:
        relations = (
            await session.execute(
                select(Relation).where(
                    Relation.issuer_id == issuer.id,
                    Relation.document_id.in_(doc_ids),
                    Relation.validation_status == "VALIDATED",
                )
            )
        ).scalars().all()
        facts = (
            await session.execute(
                select(Fact).where(
                    Fact.issuer_id == issuer.id,
                    Fact.document_id.in_(doc_ids),
                    Fact.validation_status == "VALIDATED",
                )
            )
        ).scalars().all()
    equity = _latest_equity(facts)
    reasons = []
    if missing:
        reasons.append(f"Thiếu tài liệu active: {', '.join(missing)}")
    if not equity:
        reasons.append("Thiếu vốn chủ sở hữu validated")
    if any(doc.extraction_status != ProcessingStageStatus.COMPLETE.value for doc in docs):
        reasons.append("Extraction của bộ tài liệu active chưa COMPLETE")
    if reasons:
        return _gil_insufficient(issuer.ticker, reasons, equity)
    capital_edges = [
        rel for rel in relations if rel.relation_type in {RelationType.OWNS.value, RelationType.INVESTS_IN.value, RelationType.LENDS_TO.value, RelationType.CREDITOR_OF.value}
    ]
    cycles = _detect_cycles(capital_edges)
    exposure = sum(float(rel.amount_vnd or 0) for rel in relations if rel.relation_type in {RelationType.TRANSACTS_WITH.value, RelationType.GUARANTEES_FOR.value})
    ratio = exposure / equity if equity else None
    flag = "PASS"
    risk = "LOW"
    if cycles:
        flag = "CATASTROPHIC"
        risk = "CATASTROPHIC"
        reasons.append("Phát hiện verified capital-flow cycle")
    elif ratio is not None and ratio > 0.50:
        flag = "CATASTROPHIC"
        risk = "HIGH"
        reasons.append("Related-party/guarantee exposure vượt 50% equity")
    elif ratio is not None and ratio > 0.25:
        flag = "WARNING"
        risk = "MEDIUM"
        reasons.append("Related-party/guarantee exposure vượt 25% equity")
    else:
        reasons.append("Không phát hiện capital-flow cycle hoặc exposure vượt ngưỡng trong evidence validated")
    return {
        "ticker": issuer.ticker,
        "analysis_status": "COMPLETE",
        "gil_flag": flag,
        "risk_level": risk,
        "rpt_ratio": ratio,
        "total_rpt_exposure_vnd": exposure,
        "equity_vnd": equity,
        "cycles_detected": len(cycles),
        "cycle_paths": cycles,
        "reasons": reasons,
        "nodes_count": len({rel.subject for rel in relations} | {rel.object for rel in relations}),
        "edges_count": len(relations),
        "policy_version": "gil-policy-v2",
    }


def _gil_insufficient(ticker: str, reasons: list[str], equity: float | None) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "analysis_status": "DATA_INSUFFICIENT",
        "gil_flag": "DATA_INSUFFICIENT",
        "risk_level": "UNKNOWN",
        "rpt_ratio": None,
        "total_rpt_exposure_vnd": 0.0,
        "equity_vnd": equity,
        "cycles_detected": 0,
        "cycle_paths": [],
        "reasons": reasons,
        "nodes_count": 0,
        "edges_count": 0,
        "policy_version": "gil-policy-v2",
    }


def _latest_equity(facts: list[Fact]) -> float | None:
    equities = [fact for fact in facts if fact.fact_type == FactType.EQUITY.value and fact.value_numeric]
    if not equities:
        return None
    equities.sort(key=lambda fact: (fact.as_of or fact.period_end or fact.period_start or ""), reverse=True)
    return float(equities[0].value_numeric or 0)


def _detect_cycles(relations: list[Relation]) -> list[list[str]]:
    graph: dict[str, set[str]] = {}
    for rel in relations:
        graph.setdefault(rel.subject, set()).add(rel.object)
    cycles: list[list[str]] = []
    for start in graph:
        stack = [(start, [start])]
        while stack:
            current, path = stack.pop()
            for nxt in graph.get(current, set()):
                if nxt == start and len(path) > 1:
                    cycles.append([*path, start])
                elif nxt not in path and len(path) < 8:
                    stack.append((nxt, [*path, nxt]))
    unique = []
    seen = set()
    for cycle in cycles:
        key = tuple(cycle)
        if key not in seen:
            seen.add(key)
            unique.append(cycle)
    return unique
