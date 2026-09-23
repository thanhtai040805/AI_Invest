from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.db import get_session
from sag_api.core.deps import get_job_queue, get_llm, require_service_or_admin
from sag_api.core.errors import NotFoundError, ValidationError
from sag_api.core.config import settings
from sag_api.core.uploads import read_upload_limited
from sag_api.db.models import DocumentFacet, DocumentTreeNode, EvidenceSpan, Job, Observation, ReviewQueueItem
from sag_api.enums import DocumentStatus, JobStatus, JobType
from sag_api.schemas.v2 import (
    DocumentCreateIn,
    DocumentObjectCreateIn,
    DocumentObjectSubmitOut,
    DocumentSourceCreateIn,
    DocumentOutV2,
    DocumentTreeOutV2,
    EvidenceSearchIn,
    EvidenceSearchOut,
    GILAssessmentOutV2,
    DocumentFacetOutV2,
    NodeContentOutV2,
    ObservationOutV2,
    ObservationSearchIn,
    ReviewQueueOut,
    TreeNodeOutV2,
)
from sag_api.services.financial_v2_service import (
    create_financial_document,
    document_to_out,
    enqueue_document_processing,
    get_document_for_ticker,
    hydrate_markdown,
    hydrate_node_content,
    list_documents,
    parse_pdf_bytes_to_markdown,
    parse_pdf_object_to_markdown,
)
from sag_api.services.document_structure_service import sha256_text
from sag_api.services.evidence_v2_service import heading_path_list, search_evidence
from sag_api.services.gil_service import assess_gil

router = APIRouter(tags=["sag-v2"], dependencies=[Depends(require_service_or_admin)])


@router.post("/tickers/{ticker}/documents", response_model=DocumentOutV2, status_code=201)
async def create_document(
    ticker: str,
    body: DocumentCreateIn,
    session: AsyncSession = Depends(get_session),
) -> DocumentOutV2:
    document, deduplicated = await create_financial_document(session, ticker, body)
    issuer, doc, asset = await get_document_for_ticker(session, ticker, document.id)
    await session.commit()
    return DocumentOutV2(**document_to_out(doc, asset, issuer, deduplicated=deduplicated))


@router.post("/tickers/{ticker}/documents/upload", response_model=DocumentOutV2, status_code=201)
async def upload_document(
    ticker: str,
    file: UploadFile = File(...),
    doc_role: str = Form(...),
    title: str | None = Form(None),
    fiscal_year: int | None = Form(None),
    fiscal_quarter: int | None = Form(None),
    period_start: str | None = Form(None),
    period_end: str | None = Form(None),
    report_scope: str | None = Form(None),
    is_active: bool = Form(True),
    processing_mode: str = Form("FULL"),
    session: AsyncSession = Depends(get_session),
) -> DocumentOutV2:
    data = await read_upload_limited(file, settings.max_upload_mb * 1024 * 1024)
    if not data:
        raise ValidationError("File rỗng")
    content_type = file.content_type or "application/octet-stream"
    filename = file.filename or "document"
    lower_name = filename.lower()
    if content_type == "text/markdown" or lower_name.endswith(".md"):
        try:
            markdown = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("Markdown phải là UTF-8") from exc
    elif content_type == "application/pdf" or lower_name.endswith(".pdf") or data[:4] == b"%PDF":
        markdown = await parse_pdf_bytes_to_markdown(data, filename)
    else:
        raise ValidationError("Chỉ hỗ trợ Markdown UTF-8 hoặc PDF")
    body = DocumentCreateIn(
        title=title or filename,
        markdown=markdown,
        doc_role=doc_role,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        period_start=period_start,
        period_end=period_end,
        activate=is_active if processing_mode.upper() == "FULL" else False,
        processing_mode=processing_mode.upper(),
        metadata={"report_scope": report_scope} if report_scope else {},
    )
    document, deduplicated = await create_financial_document(session, ticker, body)
    issuer, doc, asset = await get_document_for_ticker(session, ticker, document.id)
    await session.commit()
    return DocumentOutV2(**document_to_out(doc, asset, issuer, deduplicated=deduplicated))


@router.post("/tickers/{ticker}/documents/from-object", response_model=DocumentOutV2, status_code=201)
async def create_document_from_pdf_object(
    ticker: str,
    body: DocumentObjectCreateIn,
    session: AsyncSession = Depends(get_session),
) -> DocumentOutV2:
    """Read a pruned PDF from R2 inside SAG; never transfers PDF bytes through ai-engine."""
    markdown = await parse_pdf_object_to_markdown(
        body.object_uri, body.title, doc_role=body.doc_role.value
    )
    create_body = DocumentCreateIn(
        title=body.title,
        markdown=markdown,
        object_uri=body.object_uri,
        doc_role=body.doc_role,
        fiscal_year=body.fiscal_year,
        fiscal_quarter=body.fiscal_quarter,
        period_start=body.period_start,
        period_end=body.period_end,
        activate=body.activate,
        processing_mode=body.processing_mode,
        metadata=body.metadata,
    )
    document, deduplicated = await create_financial_document(session, ticker, create_body)
    issuer, doc, asset = await get_document_for_ticker(session, ticker, document.id)
    await session.commit()
    return DocumentOutV2(**document_to_out(doc, asset, issuer, deduplicated=deduplicated))


@router.post("/tickers/{ticker}/documents/from-object/submit", response_model=DocumentObjectSubmitOut, status_code=202)
async def submit_document_from_pdf_object(
    ticker: str,
    body: DocumentObjectCreateIn,
    session: AsyncSession = Depends(get_session),
    job_queue: Any = Depends(get_job_queue),
) -> DocumentObjectSubmitOut:
    """Queue R2 PDF OCR and return without holding the HTTP request for MinerU."""
    workflow_key = hashlib.sha256(
        "|".join(
            [
                ticker.upper().strip(),
                body.object_uri,
                body.doc_role.value,
                str(body.fiscal_year or ""),
                str(body.fiscal_quarter or ""),
                body.processing_mode,
            ]
        ).encode("utf-8")
    ).hexdigest()
    payload = {
        "workflow_key": workflow_key,
        "ticker": ticker.upper().strip(),
        "title": body.title,
        "object_uri": body.object_uri,
        "doc_role": body.doc_role.value,
        "fiscal_year": body.fiscal_year,
        "fiscal_quarter": body.fiscal_quarter,
        "period_start": body.period_start.isoformat() if body.period_start else None,
        "period_end": body.period_end.isoformat() if body.period_end else None,
        "activate": body.activate,
        "processing_mode": body.processing_mode,
        "metadata": body.metadata,
    }
    existing = await session.scalar(
        select(Job)
        .where(Job.type == JobType.OCR_FROM_OBJECT)
        .where(Job.payload["workflow_key"].as_string() == workflow_key)
        .order_by(Job.created_at.desc())
    )
    if existing is not None and existing.status != JobStatus.FAILED:
        return DocumentObjectSubmitOut(
            job_id=existing.id,
            status=existing.status.value,
            document_id=existing.document_id,
        )
    job = existing or Job(type=JobType.OCR_FROM_OBJECT, payload=payload, progress=0.0)
    job.status = JobStatus.QUEUED
    job.error = None
    job.payload = payload
    job.progress = 0.0
    session.add(job)
    await session.flush()
    await session.commit()
    await job_queue.enqueue(job.id)
    return DocumentObjectSubmitOut(job_id=job.id, status=job.status.value, document_id=None)


@router.post("/tickers/{ticker}/documents/from-url/submit", response_model=DocumentObjectSubmitOut, status_code=202)
async def submit_document_from_source_url(
    ticker: str,
    body: DocumentSourceCreateIn,
    session: AsyncSession = Depends(get_session),
    job_queue: Any = Depends(get_job_queue),
) -> DocumentObjectSubmitOut:
    """Queue OCR from the source URL; the PDF is never persisted in R2."""
    source_url = body.source_url.strip()
    if not source_url.lower().startswith(("http://", "https://")):
        raise ValidationError("source_url phải là HTTP(S) URL")
    workflow_key = hashlib.sha256(
        "|".join(
            [ticker.upper().strip(), source_url, body.doc_role.value,
             str(body.fiscal_year or ""), str(body.fiscal_quarter or ""), body.processing_mode]
        ).encode("utf-8")
    ).hexdigest()
    payload = {
        "workflow_key": workflow_key,
        "ticker": ticker.upper().strip(),
        "title": body.title,
        "source_url": source_url,
        "doc_role": body.doc_role.value,
        "fiscal_year": body.fiscal_year,
        "fiscal_quarter": body.fiscal_quarter,
        "period_start": body.period_start.isoformat() if body.period_start else None,
        "period_end": body.period_end.isoformat() if body.period_end else None,
        "activate": body.activate,
        "processing_mode": body.processing_mode,
        "metadata": {**body.metadata, "source_url": source_url},
    }
    existing = await session.scalar(
        select(Job).where(Job.type == JobType.OCR_FROM_URL)
        .where(Job.payload["workflow_key"].as_string() == workflow_key)
        .order_by(Job.created_at.desc())
    )
    if existing is not None and existing.status != JobStatus.FAILED:
        return DocumentObjectSubmitOut(job_id=existing.id, status=existing.status.value, document_id=existing.document_id)
    job = existing or Job(type=JobType.OCR_FROM_URL, payload=payload, progress=0.0)
    job.status = JobStatus.QUEUED
    job.error = None
    job.payload = payload
    job.progress = 0.0
    session.add(job)
    await session.flush()
    await session.commit()
    await job_queue.enqueue(job.id)
    return DocumentObjectSubmitOut(job_id=job.id, status=job.status.value, document_id=None)


@router.get("/tickers/{ticker}/documents", response_model=list[DocumentOutV2])
async def get_documents(ticker: str, session: AsyncSession = Depends(get_session)) -> list[DocumentOutV2]:
    rows = await list_documents(session, ticker)
    return [DocumentOutV2(**document_to_out(doc, asset, issuer)) for doc, asset, issuer in rows]


@router.get("/documents/{document_id}", response_model=DocumentOutV2)
async def get_document_by_id(document_id: str, session: AsyncSession = Depends(get_session)) -> DocumentOutV2:
    from sag_api.db.models import Document, DocumentAsset, Issuer

    doc = await session.get(Document, document_id)
    if doc is None or not doc.issuer_id:
        raise NotFoundError("Tài liệu không tồn tại")
    issuer = await session.get(Issuer, doc.issuer_id)
    if issuer is None:
        raise NotFoundError("Issuer không tồn tại")
    asset = await session.get(DocumentAsset, doc.asset_id) if doc.asset_id else None
    return DocumentOutV2(**document_to_out(doc, asset, issuer))


@router.get("/documents/{document_id}/extraction/raw")
async def get_extraction_raw_response(document_id: str, session: AsyncSession = Depends(get_session)) -> Response:
    """Return only the exact LLM response captured before server hydration."""
    from sag_api.db.models import Document

    doc = await session.get(Document, document_id)
    if doc is None:
        raise NotFoundError("Tài liệu không tồn tại")
    raw = (doc.coverage or {}).get("extraction", {}).get("audit_raw_llm_response")
    if not raw:
        raise NotFoundError("Tài liệu chưa có audit_raw_llm_response")
    return Response(content=str(raw), media_type="application/json; charset=utf-8")


@router.get("/jobs/{job_id}")
async def get_financial_job_status(job_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    job = await session.get(Job, job_id)
    if job is None:
        raise NotFoundError("Nhiệm vụ không tồn tại")
    return {
        "id": job.id,
        "type": job.type.value if hasattr(job.type, "value") else str(job.type),
        "status": job.status.value if hasattr(job.status, "value") else str(job.status),
        "document_id": job.document_id,
        "progress": job.progress,
        "attempts": job.attempts,
        "error": job.error,
    }


@router.get("/documents/{document_id}/markdown")
async def get_document_markdown(document_id: str, session: AsyncSession = Depends(get_session)) -> Response:
    from sag_api.db.models import Document, DocumentAsset

    doc = await session.get(Document, document_id)
    if doc is None:
        raise NotFoundError("Tài liệu không tồn tại")
    asset = await session.get(DocumentAsset, doc.asset_id) if doc.asset_id else None
    markdown = await hydrate_markdown(doc, asset, session)
    return Response(content=markdown, media_type="text/markdown; charset=utf-8")


@router.post("/documents/{document_id}/reprocess", response_model=DocumentOutV2)
async def reprocess_document(
    document_id: str,
    force: bool = Query(False, description="Run extraction again even when the document is READY"),
    session: AsyncSession = Depends(get_session),
) -> DocumentOutV2:
    from sag_api.db.models import Document, DocumentAsset, Issuer
    from sag_api.services.financial_v2_service import activate_document, rebuild_structure_and_embeddings

    doc = await session.get(Document, document_id)
    if doc is None or not doc.issuer_id:
        raise NotFoundError("Tài liệu không tồn tại")
    issuer = await session.get(Issuer, doc.issuer_id)
    asset = await session.get(DocumentAsset, doc.asset_id) if doc.asset_id else None
    if issuer is None:
        raise NotFoundError("Issuer không tồn tại")
    from sag_api.core.config import settings

    if settings.process_documents_inline and settings.environment != "prod":
        markdown = await hydrate_markdown(doc, asset, session)
        # A normal reprocess repairs incomplete documents.  ``force`` is the
        # explicit benchmark path that reruns the current extraction prompt
        # for an already READY document.
        if force:
            doc.status = DocumentStatus.PROCESSING
            doc.is_active = False
            doc.error = None
            doc.progress = 0
        if force or doc.status != DocumentStatus.READY:
            await rebuild_structure_and_embeddings(session, issuer, doc, markdown)
        if doc.extraction_status == "COMPLETE" and (
            not settings.embedding_enabled or doc.embedding_status == "COMPLETE"
        ):
            await activate_document(session, issuer.id, doc)
    else:
        await enqueue_document_processing(session, doc)
    await session.commit()
    return DocumentOutV2(**document_to_out(doc, asset, issuer))


@router.post("/documents/{document_id}/cancel", response_model=DocumentOutV2)
async def cancel_document(document_id: str, session: AsyncSession = Depends(get_session)) -> DocumentOutV2:
    from sag_api.db.models import Document, DocumentAsset, Issuer
    from sag_api.enums import DocumentStatus

    doc = await session.get(Document, document_id)
    if doc is None or not doc.issuer_id:
        raise NotFoundError("Tài liệu không tồn tại")
    doc.status = DocumentStatus.CANCELLED
    doc.is_active = False
    issuer = await session.get(Issuer, doc.issuer_id)
    asset = await session.get(DocumentAsset, doc.asset_id) if doc.asset_id else None
    await session.commit()
    if issuer is None:
        raise NotFoundError("Issuer không tồn tại")
    return DocumentOutV2(**document_to_out(doc, asset, issuer))


@router.get("/documents/{document_id}/tree", response_model=DocumentTreeOutV2)
async def get_document_tree(document_id: str, session: AsyncSession = Depends(get_session)) -> DocumentTreeOutV2:
    from sag_api.db.models import Document, Issuer

    doc = await session.get(Document, document_id)
    if doc is None or not doc.issuer_id:
        raise NotFoundError("Tài liệu không tồn tại")
    issuer = await session.get(Issuer, doc.issuer_id)
    if issuer is None:
        raise NotFoundError("Issuer không tồn tại")
    nodes = (
        await session.execute(
            select(DocumentTreeNode).where(DocumentTreeNode.document_id == doc.id).order_by(DocumentTreeNode.order_index)
        )
    ).scalars().all()
    return DocumentTreeOutV2(
        document_id=doc.id,
        ticker=issuer.ticker,
        doc_role=doc.doc_role or "",
        processing_version=doc.processing_version,
        structure_status=doc.structure_status,
        coverage=doc.coverage or {},
        nodes=[_node_out(node) for node in nodes],
    )


@router.get("/documents/{document_id}/nodes/{node_id}/content", response_model=NodeContentOutV2)
async def get_node_content(
    document_id: str,
    node_id: str,
    session: AsyncSession = Depends(get_session),
) -> NodeContentOutV2:
    from sag_api.db.models import Document, DocumentAsset

    doc = await session.get(Document, document_id)
    if doc is None:
        raise NotFoundError("Tài liệu không tồn tại")
    asset = await session.get(DocumentAsset, doc.asset_id) if doc.asset_id else None
    node = await session.scalar(select(DocumentTreeNode).where(DocumentTreeNode.document_id == doc.id, DocumentTreeNode.node_id == node_id))
    if node is None:
        raise NotFoundError("Node không tồn tại")
    markdown = await hydrate_markdown(doc, asset, session)
    content = hydrate_node_content(markdown, node)
    return NodeContentOutV2(
        document_id=doc.id,
        node_id=node.node_id,
        heading=node.heading,
        heading_path=heading_path_list(node.heading_path),
        start_line=node.start_line,
        end_line=node.end_line,
        content_hash=sha256_text(content),
        content=content,
    )


@router.post("/tickers/{ticker}/search", response_model=EvidenceSearchOut)
async def search_ticker_evidence(
    ticker: str,
    body: EvidenceSearchIn,
    session: AsyncSession = Depends(get_session),
) -> EvidenceSearchOut:
    hits = await search_evidence(session, ticker, body.query, top_k=body.top_k, include_historical=body.include_historical)
    return EvidenceSearchOut(ticker=ticker.upper().strip(), hits=hits)


@router.get("/documents/{document_id}/observations", response_model=list[ObservationOutV2])
async def get_document_observations(
    document_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[ObservationOutV2]:
    rows = (
        await session.execute(select(Observation).where(Observation.document_id == document_id).order_by(Observation.created_at))
    ).scalars().all()
    return await _observation_outs(session, rows)


@router.get("/tickers/{ticker}/facets", response_model=list[DocumentFacetOutV2])
async def get_ticker_facets(
    ticker: str,
    session: AsyncSession = Depends(get_session),
) -> list[DocumentFacetOutV2]:
    from sag_api.services.financial_v2_service import active_documents

    _issuer, docs = await active_documents(session, ticker)
    doc_ids = [doc.id for doc in docs]
    if not doc_ids:
        return []
    rows = (
        await session.execute(
            select(DocumentFacet)
            .where(DocumentFacet.document_id.in_(doc_ids))
            .order_by(DocumentFacet.confidence.desc(), DocumentFacet.facet)
        )
    ).scalars().all()
    spans = await _spans_by_id(session, [row.evidence_span_id for row in rows])
    return [
        DocumentFacetOutV2(
            id=row.id,
            document_id=row.document_id,
            node_id=row.node_id,
            facet=row.facet,
            confidence=row.confidence,
            source=row.source,
            evidence=_evidence_payload(spans.get(row.evidence_span_id)),
        )
        for row in rows
    ]


@router.post("/tickers/{ticker}/observations/search", response_model=list[ObservationOutV2])
async def search_ticker_observations(
    ticker: str,
    body: ObservationSearchIn,
    session: AsyncSession = Depends(get_session),
) -> list[ObservationOutV2]:
    """Retrieve open observations for an analysis lens without a sector-specific schema."""
    from sag_api.db.models import Document, Issuer
    from sag_api.services.financial_v2_service import active_documents

    issuer, active_docs = await active_documents(session, ticker)
    if body.include_historical:
        docs = (
            await session.execute(
                select(Document).where(Document.issuer_id == issuer.id, Document.extraction_status == "COMPLETE")
            )
        ).scalars().all()
    else:
        docs = active_docs
    doc_ids = [doc.id for doc in docs]
    if not doc_ids:
        return []
    stmt = (
        select(Observation)
        .where(Observation.document_id.in_(doc_ids), Observation.confidence >= body.min_confidence)
        .order_by(Observation.confidence.desc(), Observation.period_end.desc().nullslast(), Observation.created_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    wanted_predicates = {value.strip().casefold() for value in body.predicates if value.strip()}
    wanted_topics = {value.strip().casefold() for value in body.topic_tags if value.strip()}
    wanted_facets = {value.strip().casefold() for value in body.facets if value.strip()}
    if wanted_facets:
        facet_doc_ids = set(
            (await session.execute(select(DocumentFacet.document_id).where(DocumentFacet.document_id.in_(doc_ids), DocumentFacet.facet.in_(wanted_facets)))).scalars().all()
        )
        rows = [row for row in rows if row.document_id in facet_doc_ids]
    rows = [
        row for row in rows
        if (not wanted_predicates or row.predicate.casefold() in wanted_predicates)
        and (not wanted_topics or wanted_topics.intersection({str(tag).casefold() for tag in (row.topic_tags_json or [])}))
    ][: body.top_k]
    return await _observation_outs(session, rows)


@router.get("/tickers/{ticker}/evidence-graph")
async def evidence_graph(ticker: str, session: AsyncSession = Depends(get_session)) -> dict:
    from sag_api.db.models import Fact, Relation
    from sag_api.services.financial_v2_service import active_documents

    issuer, docs = await active_documents(session, ticker)
    doc_ids = [doc.id for doc in docs]
    facts = (await session.execute(select(Fact).where(Fact.document_id.in_(doc_ids)))).scalars().all() if doc_ids else []
    relations = (await session.execute(select(Relation).where(Relation.document_id.in_(doc_ids)))).scalars().all() if doc_ids else []
    observations = (await session.execute(select(Observation).where(Observation.document_id.in_(doc_ids)))).scalars().all() if doc_ids else []
    facets = (await session.execute(select(DocumentFacet).where(DocumentFacet.document_id.in_(doc_ids)))).scalars().all() if doc_ids else []
    return {
        "ticker": issuer.ticker,
        "documents": [{"document_id": doc.id, "doc_role": doc.doc_role, "period_end": doc.period_end} for doc in docs],
        "facts": [{"id": fact.id, "type": fact.fact_type, "semantic_key": fact.semantic_key, "node_id": fact.node_id} for fact in facts],
        "relations": [
            {"id": rel.id, "type": rel.relation_type, "subject": rel.subject, "object": rel.object, "validation_status": rel.validation_status}
            for rel in relations
        ],
        "facets": [
            {"id": facet.id, "facet": facet.facet, "document_id": facet.document_id, "node_id": facet.node_id, "confidence": facet.confidence}
            for facet in facets
        ],
        "observations": [
            {"id": observation.id, "statement": observation.statement, "predicate": observation.predicate, "topic_tags": observation.topic_tags_json or [], "document_id": observation.document_id, "node_id": observation.node_id, "confidence": observation.confidence}
            for observation in observations
        ],
    }


@router.get("/tickers/{ticker}/assessments/gil", response_model=GILAssessmentOutV2)
async def gil_assessment(ticker: str, session: AsyncSession = Depends(get_session)) -> GILAssessmentOutV2:
    return GILAssessmentOutV2(**await assess_gil(session, ticker))


@router.get("/review-queue", response_model=list[ReviewQueueOut])
async def review_queue(session: AsyncSession = Depends(get_session)) -> list[ReviewQueueOut]:
    """Đánh giá Business Quality từ evidence đã extract; không chấm điểm lợi thế cạnh tranh."""
    from sag_api.db.models import Issuer

    rows = (await session.execute(select(ReviewQueueItem).order_by(ReviewQueueItem.created_at.desc()).limit(200))).scalars().all()
    out = []
    for row in rows:
        issuer = await session.get(Issuer, row.issuer_id) if row.issuer_id else None
        out.append(
            ReviewQueueOut(
                id=row.id,
                ticker=issuer.ticker if issuer else None,
                document_id=row.document_id,
                item_type=row.item_type,
                status=row.status,
                reason=row.reason,
                payload=row.payload_json or {},
            )
        )
    return out


@router.post("/relations/{relation_id}/review")
async def review_relation(relation_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    from sag_api.db.models import Relation

    relation = await session.get(Relation, relation_id)
    if relation is None:
        raise NotFoundError("Relation không tồn tại")
    relation.validation_status = "VALIDATED"
    await session.commit()
    return {"ok": True, "relation_id": relation_id, "validation_status": relation.validation_status}


def _node_out(node: DocumentTreeNode) -> TreeNodeOutV2:
    metadata = node.metadata_json or {}
    return TreeNodeOutV2(
        node_id=node.node_id,
        parent_id=node.parent_id,
        level=node.level,
        order=node.order_index,
        heading=node.heading,
        heading_path=heading_path_list(node.heading_path),
        node_kind=node.node_kind,
        start_line=node.start_line,
        end_line=node.end_line,
        content_hash=node.content_hash,
        excluded_from_analysis=bool(metadata.get("excluded_from_analysis")),
        exclusion_reason=metadata.get("exclusion_reason"),
        summary=node.summary,
        relevance=node.relevance_json or {},
        metadata=metadata,
    )


async def _spans_by_id(session: AsyncSession, ids: list[str | None]) -> dict[str, EvidenceSpan]:
    span_ids = [span_id for span_id in ids if span_id]
    if not span_ids:
        return {}
    rows = (await session.execute(select(EvidenceSpan).where(EvidenceSpan.id.in_(span_ids)))).scalars().all()
    return {row.id: row for row in rows}


def _evidence_payload(span: EvidenceSpan | None) -> dict:
    if span is None:
        return {}
    metadata = span.metadata_json or {}
    return {
        "document_id": span.document_id,
        "node_id": span.node_id,
        "line_start": span.start_line,
        "line_end": span.end_line,
        "quote": metadata.get("quote", ""),
    }


async def _observation_outs(session: AsyncSession, rows: list[Observation]) -> list[ObservationOutV2]:
    spans = await _spans_by_id(session, [row.evidence_span_id for row in rows])
    return [
        ObservationOutV2(
            id=row.id,
            document_id=row.document_id,
            node_id=row.node_id,
            statement=row.statement,
            subject=row.subject,
            predicate=row.predicate,
            object=row.object,
            topic_tags=row.topic_tags_json or [],
            attributes=row.attributes_json or {},
            period_start=row.period_start,
            period_end=row.period_end,
            as_of=row.as_of,
            confidence=row.confidence,
            normalization=row.normalization_json or {},
            evidence=_evidence_payload(spans.get(row.evidence_span_id)),
        )
        for row in rows
    ]
