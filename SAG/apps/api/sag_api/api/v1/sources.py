from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.connectors import registry
from sag_api.core.db import get_session
from sag_api.core.deps import get_current_user, get_engine_manager, get_job_queue
from sag_api.db.models import User
from sag_api.db.models import DocumentTreeNode
from sag_api.jobs import JobQueue
from sag_api.mcp.server import MCP_TOOL_DETAILS, MCP_TOOL_NAMES
from sag_api.sag import EngineManager
from sag_api.schemas.common import Ok
from sag_api.schemas.job import JobOut
from sag_api.schemas.document import DocumentOut, IngestRequest
from sag_api.schemas.document_v2 import DocumentTreeOut, NodeContentOut, TreeNodeOut
from sag_api.schemas.source import ConnectorOut, SourceCreate, SourceOut, SourceUpdate
from sag_api.services.source_service import (
    create_source,
    delete_source,
    get_or_create_source_by_ticker,
    get_source,
    list_sources,
    sync_source,
    update_source,
)
from sag_api.services.document_structure_service import hydrate_node_content

router = APIRouter(prefix="/sources", tags=["sources"])


def _heading_path(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(">") if part.strip()]


# Lưu ý: route tĩnh phải được khai báo trước /{source_id}
@router.get("/connectors", response_model=list[ConnectorOut])
async def list_connectors() -> list[ConnectorOut]:
    return [ConnectorOut(**c.meta.to_public()) for c in registry.all()]


@router.post("/by-ticker/{ticker}/documents/ingest", response_model=DocumentOut, status_code=201)
async def ingest_by_ticker(
    ticker: str,
    body: IngestRequest,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
    job_queue: JobQueue = Depends(get_job_queue),
) -> DocumentOut:
    """Nạp tài liệu tự động theo mã cổ phiếu. Tự động khởi tạo Nguồn BCTC_{TICKER} nếu chưa có."""
    from sag_api.core.config import settings
    from sag_api.core.errors import ValidationError
    from sag_api.services.document_service import ingest_content

    if body.doc_role is None:
        raise ValidationError("doc_role là bắt buộc khi nạp tài liệu theo ticker")
    source = await get_or_create_source_by_ticker(session, ticker, engine_manager=engine_manager)
    document = await ingest_content(
        session,
        source,
        text=body.text,
        title=body.title,
        messages=[m.model_dump() for m in body.messages] if body.messages else None,
        upload_dir=settings.upload_dir,
        job_queue=job_queue,
        doc_role=body.doc_role,
        is_active=body.is_active,
        fiscal_year=body.fiscal_year,
        fiscal_quarter=body.fiscal_quarter,
    )
    return DocumentOut.model_validate(document)


@router.post("/by-ticker/{ticker}/documents/upload", response_model=DocumentOut, status_code=201)
async def upload_by_ticker(
    ticker: str,
    file: UploadFile = File(...),
    doc_role: str = Form("LATEST_QUARTER"),
    is_active: bool = Form(True),
    fiscal_year: int | None = Form(None),
    fiscal_quarter: int | None = Form(None),
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
    job_queue: JobQueue = Depends(get_job_queue),
) -> DocumentOut:
    """Tải lên file PDF trực tiếp theo mã cổ phiếu để chạy MinerU OCR và xây dựng cây tài liệu."""
    from sag_api.core.config import settings
    from sag_api.core.errors import ValidationError
    from sag_api.services.document_service import create_document_from_upload

    source = await get_or_create_source_by_ticker(session, ticker, engine_manager=engine_manager)
    from sag_api.core.uploads import read_upload_limited
    data = await read_upload_limited(file, settings.max_upload_mb * 1024 * 1024)
    if not data:
        raise ValidationError("Nội dung file rỗng")

    document, _job = await create_document_from_upload(
        session,
        source,
        filename=file.filename or "bctc.pdf",
        content_type=file.content_type or "application/pdf",
        data=data,
        upload_dir=settings.upload_dir,
        job_queue=job_queue,
        doc_role=doc_role,
        is_active=is_active,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
    )
    return DocumentOut.model_validate(document)


@router.get("/by-ticker/{ticker}/documents/{document_id}/tree", response_model=DocumentTreeOut)
async def get_document_tree_by_ticker(
    ticker: str,
    document_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
) -> DocumentTreeOut:
    """Cây kiến trúc deterministic của một tài liệu v2, không phụ thuộc output LLM."""
    from sag_api.core.errors import ConflictError, NotFoundError
    from sag_api.services.document_service import get_document

    source = await get_or_create_source_by_ticker(session, ticker, engine_manager=engine_manager)
    document = await get_document(session, source, document_id)
    if document.structure_status != "COMPLETE":
        raise ConflictError("Cây tài liệu chưa sẵn sàng")
    rows = (
        await session.execute(
            select(DocumentTreeNode)
            .where(DocumentTreeNode.document_id == document.id)
            .order_by(DocumentTreeNode.order_index)
        )
    ).scalars().all()
    if not rows:
        raise NotFoundError("Cây tài liệu không tồn tại")
    return DocumentTreeOut(
        document_id=document.id,
        source_id=source.id,
        ticker=ticker.upper().strip(),
        doc_role=document.doc_role,
        processing_version=document.processing_version,
        structure_status=document.structure_status,
        coverage=document.coverage or {},
        nodes=[
            TreeNodeOut(
                node_id=row.node_id,
                parent_id=row.parent_id,
                level=row.level,
                order=row.order_index,
                heading=row.heading,
                heading_path=_heading_path(row.heading_path),
                node_kind=row.node_kind,
                start_line=row.start_line,
                end_line=row.end_line,
                content_hash=row.content_hash,
                summary=row.summary,
                relevance=row.relevance_json or {},
                metadata=row.metadata_json or {},
            )
            for row in rows
        ],
    )


@router.get("/by-ticker/{ticker}/documents/{document_id}/nodes/{node_id}/content", response_model=NodeContentOut)
async def get_document_node_content_by_ticker(
    ticker: str,
    document_id: str,
    node_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
) -> NodeContentOut:
    """Hydrate nội dung node nguyên văn từ Markdown bằng line span."""
    from sag_api.core.errors import NotFoundError
    from sag_api.parsing.text import read_text_file
    from sag_api.services.document_service import get_document
    from sag_api.services.document_structure_service import sha256_text

    source = await get_or_create_source_by_ticker(session, ticker, engine_manager=engine_manager)
    document = await get_document(session, source, document_id)
    node = await session.scalar(
        select(DocumentTreeNode).where(
            DocumentTreeNode.document_id == document.id,
            DocumentTreeNode.node_id == node_id,
        )
    )
    if node is None:
        raise NotFoundError("Node tài liệu không tồn tại")
    markdown = None
    if document.sag_source_id:
        markdown = await engine_manager.get_document_markdown(
            source.sag_source_config_id,
            document.sag_source_id,
            source=source,
        )
    if markdown is None:
        markdown = read_text_file(document.storage_path).text
    content = hydrate_node_content(markdown, node)
    return NodeContentOut(
        document_id=document.id,
        node_id=node.node_id,
        heading=node.heading,
        heading_path=_heading_path(node.heading_path),
        start_line=node.start_line,
        end_line=node.end_line,
        content_hash=sha256_text(content),
        content=content,
    )



@router.get("", response_model=list[SourceOut])
async def list_(
    _user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> list[SourceOut]:
    return [SourceOut.model_validate(s) for s in await list_sources(session)]


@router.post("", response_model=SourceOut, status_code=201)
async def create(
    body: SourceCreate,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
) -> SourceOut:
    source = await create_source(session, body, engine_manager=engine_manager)
    return SourceOut.model_validate(source)


@router.get("/{source_id}", response_model=SourceOut)
async def get_(
    source_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SourceOut:
    return SourceOut.model_validate(await get_source(session, source_id))


@router.patch("/{source_id}", response_model=SourceOut)
async def update_(
    source_id: str,
    body: SourceUpdate,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue = Depends(get_job_queue),
) -> SourceOut:
    return SourceOut.model_validate(
        await update_source(session, source_id, body, job_queue=job_queue)
    )


@router.delete("/{source_id}", response_model=Ok)
async def delete_(
    source_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
    job_queue: JobQueue = Depends(get_job_queue),
) -> Ok:
    from sag_api.core.config import settings

    await delete_source(
        session,
        source_id,
        engine_manager=engine_manager,
        upload_dir=settings.upload_dir,
        job_queue=job_queue,
    )
    return Ok(detail="Nguồn đã xóa")


@router.get("/{source_id}/chunks/{chunk_id}")
async def get_chunk(
    source_id: str,
    chunk_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
) -> dict:
    """Truy vết trích dẫn: đọc toàn văn bản gốc của một chunk."""
    from sag_api.core.errors import NotFoundError

    source = await get_source(session, source_id)
    chunk = await engine_manager.get_chunk(source.sag_source_config_id, chunk_id, source=source)
    if chunk is None:
        raise NotFoundError("Chunk văn bản gốc không tồn tại")
    return {**chunk.model_dump(), "source_id": source.id, "source_name": source.name}


@router.get("/{source_id}/mcp")
async def mcp_descriptor(
    source_id: str,
    request: Request,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Nguồn chính là MCP: trả về thông tin kết nối để gắn nguồn này vào host bên ngoài (Claude Desktop / Cursor)."""
    source = await get_source(session, source_id)
    base = str(request.base_url).rstrip("/")
    return {
        "source_id": source.id,
        "source_name": source.name,
        "tools": list(MCP_TOOL_NAMES),
        "tool_details": list(MCP_TOOL_DETAILS),
        "http": {
            "transport": "streamable-http",
            "url": f"{base}/mcp/?source_id={source.id}",
            "headers": {"Authorization": "Bearer <SAG_TOKEN>"},
            "note": (
                "Điền URL này vào host hỗ trợ Streamable HTTP MCP; "
                "cấu hình Dify có thể dùng transport=streamable_http và mang Bearer <token> trong header Authorization."
            ),
        },
        "stdio": {
            "command": "python",
            "args": ["-m", "sag_api.mcp.server"],
            "env": {"SAG_MCP_SOURCE_ID": source.id},
            "note": "Dành cho host chỉ hỗ trợ stdio; cần chạy trong môi trường Python của apps/api.",
        },
    }


@router.post("/{source_id}/sync", response_model=JobOut)
async def sync(
    source_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue = Depends(get_job_queue),
) -> JobOut:
    job = await sync_source(session, source_id, job_queue=job_queue)
    return JobOut.model_validate(job)
