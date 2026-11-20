from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.connectors import registry
from sag_api.core.db import SessionLocal, get_session
from sag_api.core.deps import (
    get_current_user,
    get_current_user_or_connector,
    get_engine_manager,
    get_job_queue,
    require_path_source_access,
)
from sag_api.core.principal_assertion import (
    VerifiedPrincipal,
    require_principal_assertion,
    require_project_read_principal,
)
from sag_api.db.models import Source, User
from sag_api.jobs import JobQueue
from sag_api.mcp.server import MCP_TOOL_DETAILS, MCP_TOOL_NAMES
from sag_api.sag import EngineManager
from sag_api.schemas.common import Ok
from sag_api.schemas.job import JobOut
from sag_api.schemas.source import ConnectorOut, SourceCreate, SourceCreateOut, SourceOut, SourceUpdate
from sag_api.services.source_operation_service import acquire_source_exclusive_lease
from sag_api.services.source_service import (
    create_source,
    delete_source,
    list_sources,
    sync_source,
    update_source,
    versioned_source_ids,
    visible_source_counts,
)

router = APIRouter(prefix="/sources", tags=["sources"])


async def _source_out(session: AsyncSession, source: Source, principal: VerifiedPrincipal) -> SourceOut:
    counts = (await visible_source_counts(session, principal=principal, source_ids=[source.id])).get(
        source.id, (0, 0, 0)
    )
    return SourceOut.model_validate(source).model_copy(
        update={"document_count": counts[0], "chunk_count": counts[1], "event_count": counts[2]}
    )


# 注意：静态路由须在 /{source_id} 之前声明
@router.get("/connectors", response_model=list[ConnectorOut])
async def list_connectors() -> list[ConnectorOut]:
    return [ConnectorOut(**c.meta.to_public()) for c in registry.all()]


@router.get("", response_model=list[SourceOut])
async def list_(
    _user: User = Depends(get_current_user_or_connector),
    principal: VerifiedPrincipal = Depends(require_project_read_principal),
    session: AsyncSession = Depends(get_session),
) -> list[SourceOut]:
    sources = await list_sources(session, principal=principal)
    counts = await visible_source_counts(session, principal=principal, source_ids=[source.id for source in sources])
    return [
        SourceOut.model_validate(source).model_copy(
            update={
                "document_count": counts.get(source.id, (0, 0, 0))[0],
                "chunk_count": counts.get(source.id, (0, 0, 0))[1],
                "event_count": counts.get(source.id, (0, 0, 0))[2],
            }
        )
        for source in sources
    ]


@router.post("", response_model=SourceCreateOut, status_code=201)
async def create(
    body: SourceCreate,
    _user: User = Depends(get_current_user_or_connector),
    principal: VerifiedPrincipal = Depends(require_principal_assertion),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
) -> SourceCreateOut:
    """Request a Source assignment; reads and uploads require owner confirmation."""
    source = await create_source(session, body, principal=principal, engine_manager=engine_manager)
    return SourceCreateOut.model_validate(source)


@router.get("/{source_id}", response_model=SourceOut)
async def get_(
    source_id: str,
    _user: User = Depends(get_current_user),
    source: Source = Depends(require_path_source_access),
    principal: VerifiedPrincipal = Depends(require_project_read_principal),
    session: AsyncSession = Depends(get_session),
) -> SourceOut:
    return await _source_out(session, source, principal)


@router.patch("/{source_id}", response_model=SourceOut)
async def update_(
    source_id: str,
    body: SourceUpdate,
    _user: User = Depends(get_current_user),
    _authorized_source: Source = Depends(require_path_source_access),
    principal: VerifiedPrincipal = Depends(require_principal_assertion),
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue = Depends(get_job_queue),
) -> SourceOut:
    source = await update_source(session, source_id, body, job_queue=job_queue)
    return await _source_out(session, source, principal)


@router.delete("/{source_id}", response_model=Ok)
async def delete_(
    source_id: str,
    _user: User = Depends(get_current_user),
    _authorized_source: Source = Depends(require_path_source_access),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
    job_queue: JobQueue = Depends(get_job_queue),
) -> Ok:
    from sag_api.core.config import settings

    # 先请求在途处理任务尽快让路：解析/抽取会在下一个安全检查点释放处理租约，
    # 使独占租约的 drain 能在 HTTP 窗口内完成，而非被动等待任务自然结束。
    job_queue.request_source_stop(source_id)
    try:
        async with acquire_source_exclusive_lease(SessionLocal, source_id, "source-delete"):
            await delete_source(
                session,
                source_id,
                engine_manager=engine_manager,
                upload_dir=settings.upload_dir,
                job_queue=job_queue,
            )
    finally:
        job_queue.clear_source_stop(source_id)
    return Ok(detail="信源已删除")


@router.get("/{source_id}/chunks/{chunk_id}")
async def get_chunk(
    source_id: str,
    chunk_id: str,
    _user: User = Depends(get_current_user_or_connector),
    source: Source = Depends(require_path_source_access),
    principal: VerifiedPrincipal = Depends(require_project_read_principal),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
) -> dict:
    """引用溯源：读取某分块的完整原文。"""
    from sag_api.core.errors import NotFoundError
    from sag_api.services.search_unit_retrieval_service import get_search_unit_citation

    canonical = await get_search_unit_citation(
        session,
        source=source,
        principal=principal,
        search_unit_id=chunk_id,
        engine_manager=engine_manager,
    )
    if canonical is not None:
        return canonical

    partitioned = source.id in await versioned_source_ids(session, [source.id])
    chunk = None
    if not partitioned:
        chunk = await engine_manager.get_chunk(source.sag_source_config_id, chunk_id, source=source)
    if chunk is None and not partitioned:
        from datetime import UTC, datetime

        from sag_api.db.models import OctxInstallation
        from sag_api.enums import OctxInstallationStatus

        retained = (
            (
                await session.execute(
                    select(OctxInstallation)
                    .where(
                        OctxInstallation.source_id == source.id,
                        OctxInstallation.status == OctxInstallationStatus.RETAINED,
                        OctxInstallation.retain_until > datetime.now(UTC),
                    )
                    .order_by(OctxInstallation.activated_at.desc())
                )
            )
            .scalars()
            .all()
        )
        for installation in retained:
            chunk = await engine_manager.get_chunk(installation.sag_source_config_id, chunk_id, source=None)
            if chunk is not None:
                break
    if chunk is None:
        raise NotFoundError("原文分块不存在")
    return {**chunk.model_dump(), "source_id": source.id, "source_name": source.name}


@router.get("/{source_id}/mcp")
async def mcp_descriptor(
    source_id: str,
    request: Request,
    _user: User = Depends(get_current_user),
    source: Source = Depends(require_path_source_access),
) -> dict:
    """信源即 MCP：返回把该信源挂进外部宿主（Claude Desktop / Cursor）的连接信息。"""
    base = str(request.base_url).rstrip("/")
    return {
        "source_id": source.id,
        "source_name": source.name,
        "tools": list(MCP_TOOL_NAMES),
        "tool_details": list(MCP_TOOL_DETAILS),
        "http": {
            "transport": "streamable-http",
            "url": f"{base}/mcp/?source_id={source.id}",
            "headers": {
                "Authorization": "Bearer <SAG_TOKEN>",
                "X-SAG-Principal-Assertion": "<short-lived-continuum-assertion>",
            },
            "note": (
                "此端点要求 SAG 应用令牌和 BE/Continuum 签发的短时主体断言。"
                "断言应由受信任的服务端代理注入，不要放入浏览器存储。"
            ),
        },
        "stdio": {
            "available": False,
            "note": "stdio 已停用，直到每次工具调用都能携带并重新校验当前 BE/Continuum 主体断言。",
        },
    }


@router.post("/{source_id}/sync", response_model=JobOut)
async def sync(
    source_id: str,
    _user: User = Depends(get_current_user),
    _authorized_source: Source = Depends(require_path_source_access),
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue = Depends(get_job_queue),
) -> JobOut:
    job = await sync_source(session, source_id, job_queue=job_queue)
    return JobOut.model_validate(job)
