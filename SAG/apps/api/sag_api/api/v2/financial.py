from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.db import get_session
from sag_api.core.deps import require_service_or_admin
from sag_api.core.errors import NotFoundError, ValidationError
from sag_api.db.models import DocumentTreeNode, ReviewQueueItem
from sag_api.schemas.v2 import (
    DocumentCreateIn,
    DocumentOutV2,
    DocumentTreeOutV2,
    EvidenceSearchIn,
    EvidenceSearchOut,
    GILAssessmentOutV2,
    MoatAssessmentOutV2,
    NodeContentOutV2,
    ReviewQueueOut,
    TreeNodeOutV2,
)
from sag_api.services.financial_v2_service import (
    assess_gil,
    assess_moat,
    create_financial_document,
    document_to_out,
    enqueue_document_processing,
    get_document_for_ticker,
    heading_path_list,
    hydrate_markdown,
    hydrate_node_content,
    list_documents,
    parse_pdf_bytes_to_markdown,
    search_evidence,
)
from sag_api.services.document_structure_service import sha256_text

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
    session: AsyncSession = Depends(get_session),
) -> DocumentOutV2:
    data = await file.read()
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
    )
    document, deduplicated = await create_financial_document(session, ticker, body)
    issuer, doc, asset = await get_document_for_ticker(session, ticker, document.id)
    await session.commit()
    return DocumentOutV2(**document_to_out(doc, asset, issuer, deduplicated=deduplicated))


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


@router.post("/documents/{document_id}/reprocess", response_model=DocumentOutV2)
async def reprocess_document(document_id: str, session: AsyncSession = Depends(get_session)) -> DocumentOutV2:
    from sag_api.db.models import Document, DocumentAsset, Issuer
    from sag_api.services.financial_v2_service import rebuild_structure_and_embeddings

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
        await rebuild_structure_and_embeddings(session, issuer, doc, markdown)
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


@router.get("/tickers/{ticker}/evidence-graph")
async def evidence_graph(ticker: str, session: AsyncSession = Depends(get_session)) -> dict:
    from sag_api.db.models import Fact, Relation
    from sag_api.services.financial_v2_service import active_documents

    issuer, docs = await active_documents(session, ticker)
    doc_ids = [doc.id for doc in docs]
    facts = (await session.execute(select(Fact).where(Fact.document_id.in_(doc_ids)))).scalars().all() if doc_ids else []
    relations = (await session.execute(select(Relation).where(Relation.document_id.in_(doc_ids)))).scalars().all() if doc_ids else []
    return {
        "ticker": issuer.ticker,
        "documents": [{"document_id": doc.id, "doc_role": doc.doc_role, "period_end": doc.period_end} for doc in docs],
        "facts": [{"id": fact.id, "type": fact.fact_type, "semantic_key": fact.semantic_key, "node_id": fact.node_id} for fact in facts],
        "relations": [
            {"id": rel.id, "type": rel.relation_type, "subject": rel.subject, "object": rel.object, "validation_status": rel.validation_status}
            for rel in relations
        ],
    }


@router.get("/tickers/{ticker}/assessments/moat", response_model=MoatAssessmentOutV2)
async def moat_assessment(ticker: str, session: AsyncSession = Depends(get_session)) -> MoatAssessmentOutV2:
    return MoatAssessmentOutV2(**await assess_moat(session, ticker))


@router.get("/tickers/{ticker}/assessments/gil", response_model=GILAssessmentOutV2)
async def gil_assessment(ticker: str, session: AsyncSession = Depends(get_session)) -> GILAssessmentOutV2:
    return GILAssessmentOutV2(**await assess_gil(session, ticker))


@router.get("/review-queue", response_model=list[ReviewQueueOut])
async def review_queue(session: AsyncSession = Depends(get_session)) -> list[ReviewQueueOut]:
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
