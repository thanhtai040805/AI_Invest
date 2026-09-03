from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.connectors import registry
from sag_api.core.db import get_session
from sag_api.core.deps import get_current_user, get_engine_manager, get_job_queue
from sag_api.db.models import User
from sag_api.jobs import JobQueue
from sag_api.mcp.server import MCP_TOOL_DETAILS, MCP_TOOL_NAMES
from sag_api.sag import EngineManager
from sag_api.schemas.common import Ok
from sag_api.schemas.job import JobOut
from sag_api.schemas.document import DocumentOut, IngestRequest
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

router = APIRouter(prefix="/sources", tags=["sources"])


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
    from sag_api.services.document_service import ingest_content

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
