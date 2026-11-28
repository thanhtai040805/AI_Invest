"""信源领域逻辑（单用户，扁平）。"""

from __future__ import annotations

import os
import shutil

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.connectors import registry
from sag_api.core.config import settings
from sag_api.core.error_taxonomy import ErrorCode
from sag_api.core.errors import (
    ApiError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from sag_api.core.logging import get_logger
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.db.base import new_id
from sag_api.db.models import AgentBinding, Document, DocumentVersion, Job, Source, SourceProjectMapping
from sag_api.enums import CONNECTOR_SOURCE_TYPE, BindingTargetType, DocumentStatus, JobStatus, JobType, SourceType
from sag_api.jobs import JobQueue
from sag_api.sag import EngineManager
from sag_api.schemas.source import SourceCreate, SourceUpdate

log = get_logger("services.source")
SOURCE_ID_LOOKUP_BATCH_SIZE = 500


def _is_project_source(source: Source) -> bool:
    return isinstance(source.config, dict) and bool(source.config.get("is_project_source"))


def _authorized_source_statement(principal: VerifiedPrincipal):
    return (
        select(Source)
        .join(SourceProjectMapping, SourceProjectMapping.source_id == Source.id)
        .where(
            SourceProjectMapping.state == "CONFIRMED",
            SourceProjectMapping.organization_id == principal.organization_id,
            SourceProjectMapping.project_id.in_(principal.allowed_project_ids),
        )
    )


async def list_sources(session: AsyncSession, *, principal: VerifiedPrincipal) -> list[Source]:
    if not principal.allowed_project_ids:
        return []
    try:
        rows = await session.execute(
            _authorized_source_statement(principal).order_by(Source.created_at.desc(), Source.id)
        )
    except SQLAlchemyError as error:
        log.exception("source scope resolution failed operation=list")
        raise ServiceUnavailableError("Source authorization mapping is unavailable") from error
    sources = list(rows.scalars().all())
    log.info("source scope resolved operation=list authorized_source_count=%d", len(sources))
    return [source for source in sources if not _is_project_source(source)]


async def search_source_candidates(
    session: AsyncSession,
    *,
    principal: VerifiedPrincipal,
    requested_source_ids: list[str] | None = None,
    include_project_sources: bool = False,
) -> list[Source]:
    """Select candidates only from confirmed Source mappings in the principal scope."""
    limit = settings.search_source_candidate_limit
    if not principal.allowed_project_ids:
        log.info("source scope resolved authorized_project_count=0 effective_source_count=0")
        return []
    if requested_source_ids is not None:
        ordered_ids = list(dict.fromkeys(source_id.strip() for source_id in requested_source_ids if source_id.strip()))
        if limit and len(ordered_ids) > limit:
            raise ValidationError(
                f"单次最多检索 {limit} 个信息源，请通过 @ 缩小范围",
                code=ErrorCode.TOO_MANY_SEARCH_SOURCES,
            )
        if not ordered_ids:
            return []
        statement = _authorized_source_statement(principal).where(Source.id.in_(ordered_ids))
        try:
            rows = await session.execute(statement)
        except SQLAlchemyError as error:
            log.exception("source scope resolution failed operation=search requested_count=%d", len(ordered_ids))
            raise ServiceUnavailableError("Source authorization mapping is unavailable") from error
        by_id = {source.id: source for source in rows.scalars().all()}
        sources = [
            by_id[source_id]
            for source_id in ordered_ids
            if source_id in by_id and (include_project_sources or not _is_project_source(by_id[source_id]))
        ]
    else:
        statement = _authorized_source_statement(principal).order_by(
            Source.chunk_count.desc(),
            Source.event_count.desc(),
            Source.updated_at.desc(),
            Source.id,
        )
        try:
            rows = await session.execute(statement)
        except SQLAlchemyError as error:
            log.exception("source scope resolution failed operation=search requested_count=0")
            raise ServiceUnavailableError("Source authorization mapping is unavailable") from error
        sources = [
            source
            for source in rows.scalars().all()
            if include_project_sources or not _is_project_source(source)
        ]
        if limit and len(sources) > limit:
            raise ValidationError(
                "Search source budget exceeded; narrow the requested source scope",
                code=ErrorCode.TOO_MANY_SEARCH_SOURCES,
            )
    log.info(
        "source scope resolved authorized_project_count=%d requested_source_count=%d effective_source_count=%d",
        len(principal.allowed_project_ids),
        len(requested_source_ids or []),
        len(sources),
    )
    return sources


async def get_authorized_source(
    session: AsyncSession,
    *,
    principal: VerifiedPrincipal,
    source_id: str,
) -> Source | None:
    """Resolve a single Source only when its active mapping is inside this principal's scope."""
    if not principal.allowed_project_ids:
        log.info("authorization denied reason=empty_project_scope operation=read")
        return None
    try:
        result = await session.execute(
            _authorized_source_statement(principal).where(Source.id == source_id).limit(1)
        )
    except SQLAlchemyError as error:
        log.exception("source scope resolution failed operation=read")
        raise ServiceUnavailableError("Source authorization mapping is unavailable") from error
    source = result.scalar_one_or_none()
    if source is None:
        log.info("authorization denied reason=source_mapping_not_visible operation=read")
    else:
        log.info("source scope resolved operation=read effective_source_count=1")
    return source


async def get_authorized_source_project(
    session: AsyncSession,
    *,
    principal: VerifiedPrincipal,
    source_id: str,
    project_id: str | None = None,
) -> str:
    """Resolve exactly one confirmed Project scope for a Source write."""
    if not principal.tenant_id or not principal.allowed_project_ids:
        raise ForbiddenError("A signed tenant and Project scope are required for Source writes")
    if project_id is not None and project_id not in principal.allowed_project_ids:
        raise ForbiddenError("The requested Project is outside the principal scope")
    stmt = select(SourceProjectMapping.project_id).where(
        SourceProjectMapping.source_id == source_id,
        SourceProjectMapping.organization_id == principal.organization_id,
        SourceProjectMapping.project_id.in_(principal.allowed_project_ids),
        SourceProjectMapping.state == "CONFIRMED",
    )
    if project_id is not None:
        stmt = stmt.where(SourceProjectMapping.project_id == project_id)
    stmt = (
        stmt
        .distinct()
        .order_by(SourceProjectMapping.project_id)
        .limit(2)
    )
    try:
        project_ids = list((await session.scalars(stmt)).all())
    except SQLAlchemyError as error:
        log.exception("source scope resolution failed operation=write")
        raise ServiceUnavailableError("Source authorization mapping is unavailable") from error
    if not project_ids:
        raise NotFoundError("信源不存在")
    if len(project_ids) != 1:
        raise ValidationError("project_id is required when the Source is mapped to multiple Projects")
    return project_ids[0]


async def versioned_source_ids(session: AsyncSession, source_ids: list[str]) -> set[str]:
    """Find Sources whose legacy readers cannot enforce version-level ACLs."""
    if not source_ids:
        return set()
    try:
        rows = await session.scalars(
            select(Document.source_id)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(Document.source_id.in_(source_ids))
            .distinct()
        )
        return set(rows.all())
    except SQLAlchemyError as error:
        log.exception("source partition inspection failed operation=read")
        raise ServiceUnavailableError("Source partition metadata is unavailable") from error


def _visible_document_clause(principal: VerifiedPrincipal):
    partition = DocumentVersion.metadata_json["security_partition_id"].as_string()
    has_version = select(DocumentVersion.id).where(DocumentVersion.document_id == Document.id).exists()
    has_allowed_version = select(DocumentVersion.id).where(
        DocumentVersion.document_id == Document.id,
        partition.in_(principal.allowed_partition_ids),
    ).exists()
    has_unavailable_version = select(DocumentVersion.id).where(
        DocumentVersion.document_id == Document.id,
        or_(partition.is_(None), partition.not_in(principal.allowed_partition_ids)),
    ).exists()
    source_mapped = select(SourceProjectMapping.id).where(
        SourceProjectMapping.source_id == Document.source_id,
        SourceProjectMapping.organization_id == principal.organization_id,
        SourceProjectMapping.project_id.in_(principal.allowed_project_ids),
        SourceProjectMapping.state == "CONFIRMED",
    ).exists()
    document_project_mapped = select(SourceProjectMapping.id).where(
        SourceProjectMapping.source_id == Document.source_id,
        SourceProjectMapping.organization_id == principal.organization_id,
        SourceProjectMapping.project_id == Document.project_id,
        SourceProjectMapping.project_id.in_(principal.allowed_project_ids),
        SourceProjectMapping.state == "CONFIRMED",
    ).exists()
    return and_(
        source_mapped,
        or_(
            and_(Document.project_id.is_(None), ~has_version),
            and_(
                Document.tenant_id == principal.tenant_id,
                Document.project_id.in_(principal.allowed_project_ids),
                document_project_mapped,
                has_allowed_version,
                ~has_unavailable_version,
            ),
        ),
    )


async def get_authorized_document(
    session: AsyncSession,
    *,
    principal: VerifiedPrincipal,
    source_ids: list[str],
    document_id: str,
) -> Document | None:
    if not source_ids:
        return None
    try:
        return await session.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.source_id.in_(source_ids),
                Document.is_active.is_(True),
                Document.status.not_in([DocumentStatus.DELETING, DocumentStatus.DELETE_FAILED]),
                _visible_document_clause(principal),
            )
        )
    except SQLAlchemyError as error:
        log.exception("document scope resolution failed operation=read")
        raise ServiceUnavailableError("Document authorization scope is unavailable") from error


async def list_authorized_documents(
    session: AsyncSession,
    *,
    principal: VerifiedPrincipal,
    source_ids: list[str],
    limit: int | None = None,
) -> list[Document]:
    if not source_ids:
        return []
    try:
        statement = (
            select(Document)
            .where(
                Document.source_id.in_(source_ids),
                Document.is_active.is_(True),
                Document.status.not_in([DocumentStatus.DELETING, DocumentStatus.DELETE_FAILED]),
                _visible_document_clause(principal),
            )
            .order_by(Document.created_at.desc(), Document.id.desc())
        )
        if limit is not None:
            statement = statement.limit(max(1, limit))
        rows = await session.scalars(statement)
        return list(rows.all())
    except SQLAlchemyError as error:
        log.exception("document scope resolution failed operation=list")
        raise ServiceUnavailableError("Document authorization scope is unavailable") from error


async def visible_source_counts(
    session: AsyncSession,
    *,
    principal: VerifiedPrincipal,
    source_ids: list[str],
) -> dict[str, tuple[int, int, int]]:
    if not source_ids:
        return {}
    rows = await session.execute(
        select(
            Document.source_id,
            func.count(Document.id),
            func.coalesce(func.sum(Document.chunk_count), 0),
            func.coalesce(func.sum(Document.event_count), 0),
        )
        .where(
            Document.source_id.in_(source_ids),
            Document.is_active.is_(True),
            Document.status.not_in([DocumentStatus.DELETING, DocumentStatus.DELETE_FAILED]),
            _visible_document_clause(principal),
        )
        .group_by(Document.source_id)
    )
    return {
        source_id: (int(documents), int(chunks), int(events))
        for source_id, documents, chunks, events in rows
        if source_id is not None
    }


async def get_authorized_source_ids(
    session: AsyncSession,
    *,
    principal: VerifiedPrincipal,
    requested_source_ids: list[str],
) -> set[str]:
    ordered_ids = list(dict.fromkeys(source_id.strip() for source_id in requested_source_ids if source_id.strip()))
    if not ordered_ids or not principal.allowed_project_ids:
        return set()
    authorized: set[str] = set()
    for start in range(0, len(ordered_ids), SOURCE_ID_LOOKUP_BATCH_SIZE):
        batch = ordered_ids[start : start + SOURCE_ID_LOOKUP_BATCH_SIZE]
        try:
            rows = await session.execute(
                _authorized_source_statement(principal)
                .where(Source.id.in_(batch))
                .with_only_columns(Source.id)
            )
        except SQLAlchemyError as error:
            log.exception("source scope resolution failed operation=validate requested_count=%d", len(ordered_ids))
            raise ServiceUnavailableError("Source authorization mapping is unavailable") from error
        authorized.update(rows.scalars().all())
    log.info(
        "source scope validated requested_source_count=%d effective_source_count=%d",
        len(ordered_ids),
        len(authorized),
    )
    return authorized


async def get_source(
    session: AsyncSession,
    source_id: str,
    *,
    allow_project_source: bool = False,
) -> Source:
    source = await session.get(Source, source_id)
    if source is None:
        raise NotFoundError("信源不存在")
    if not allow_project_source and isinstance(source.config, dict) and source.config.get("is_project_source"):
        raise ForbiddenError(
            "Nguồn dữ liệu dự án được bảo vệ và không thể truy cập qua route không có scope. "
            "Hãy sử dụng API /projects/{project_id}/documents."
        )
    return source


async def create_source(
    session: AsyncSession,
    data: SourceCreate,
    *,
    engine_manager: EngineManager,
    principal: VerifiedPrincipal | None = None,
) -> Source:
    project_id: str | None = None
    if principal is not None:
        if not principal.allowed_project_ids:
            raise ForbiddenError("A readable Project is required to request Source assignment")
        project_id = data.project_id
        if project_id is None:
            if len(principal.allowed_project_ids) != 1:
                raise ValidationError("project_id is required when the principal can access multiple Projects")
            project_id = next(iter(principal.allowed_project_ids))
        if project_id != project_id.strip():
            raise ValidationError("project_id must not contain leading or trailing whitespace")
        if project_id not in principal.allowed_project_ids:
            raise ForbiddenError("The requested Project is outside the principal scope")

    connector = registry.get(data.connector_kind)
    connector.validate_config(data.config)
    source_type = CONNECTOR_SOURCE_TYPE.get(data.connector_kind, SourceType.DOCUMENT)

    source = Source(
        name=data.name,
        description=data.description,
        source_type=source_type,
        connector_kind=data.connector_kind,
        sag_source_config_id=f"src_{new_id()[:16]}",
        config=data.config or {},
    )
    session.add(source)
    if principal is not None:
        await session.flush()
        session.add(
            SourceProjectMapping(
                source_id=source.id,
                organization_id=principal.organization_id,
                project_id=project_id,
                state="PENDING",
                mapping_version=1,
            )
        )
    await session.commit()
    await session.refresh(source)

    # Only the opt-in legacy Event pipeline needs SDK parent records provisioned here.
    try:
        if settings.document_event_extraction_enabled:
            await engine_manager.provision(source.sag_source_config_id, source)
    except ApiError as e:
        log.warning("信源引擎预建失败，回滚信源 %s：%s", source.sag_source_config_id, e.message)
        await session.execute(delete(SourceProjectMapping).where(SourceProjectMapping.source_id == source.id))
        await session.delete(source)
        await session.commit()
        raise
    return source


async def update_source(
    session: AsyncSession,
    source_id: str,
    data: SourceUpdate,
    *,
    job_queue: JobQueue | None = None,
) -> Source:
    source = await get_source(session, source_id)
    if data.name is not None:
        source.name = data.name
    if data.description is not None:
        source.description = data.description
    if data.status is not None:
        source.status = data.status
    await session.commit()
    await session.refresh(source)
    from sag_api.services.universe_service import schedule_universe_refresh

    await schedule_universe_refresh(
        session,
        job_queue,
        source_id=source.id,
        reason="source_updated",
    )
    return source


async def delete_source(
    session: AsyncSession,
    source_id: str,
    *,
    engine_manager: EngineManager,
    upload_dir: str,
    job_queue: JobQueue | None = None,
) -> None:
    """删除信源并收尾：移除悬挂绑定、关闭引擎槽、清理上传文件。"""
    source = await get_source(session, source_id)
    sag_id = source.sag_source_config_id
    tree_scopes = (await session.execute(select(
        SourceProjectMapping.organization_id, SourceProjectMapping.project_id,
    ).where(SourceProjectMapping.source_id == source_id, SourceProjectMapping.state == "CONFIRMED"))).all()

    # 悬挂绑定清理（target_id 为普通字符串，无 FK 级联）
    await session.execute(
        AgentBinding.__table__.delete().where(
            AgentBinding.target_type == BindingTargetType.SOURCE,
            AgentBinding.target_id == source.id,
        )
    )
    await session.delete(source)
    tree_jobs = []
    if job_queue is not None:
        from sag_api.services.universe_tree_service import enqueue_project_tree_rebuild

        for tenant, project in sorted(set(tree_scopes)):
            tree_jobs.append(await enqueue_project_tree_rebuild(
                session, job_queue, tenant_id=tenant, project_ids=[project],
                reason="source_deleted", persist_only=True))
    await session.commit()
    if tree_jobs:
        from sag_api.services.document_service import _enqueue_persisted_job

        for tree_job in tree_jobs:
            await _enqueue_persisted_job(job_queue, tree_job.id)

    # 引擎槽关闭 + 上传目录清理（尽力而为，不阻断删除）
    await engine_manager.release(sag_id)
    shutil.rmtree(os.path.join(upload_dir, source_id), ignore_errors=True)
    from sag_api.services.universe_service import schedule_universe_refresh

    await schedule_universe_refresh(
        session,
        job_queue,
        source_id=None,
        reason="source_deleted",
    )


async def sync_source(session: AsyncSession, source_id: str, *, job_queue: JobQueue) -> Job:
    """触发一次动态连接器同步（如网页抓取）。"""
    source = await get_source(session, source_id)
    connector = registry.get(source.connector_kind)
    if not connector.meta.supports_sync:
        raise ValidationError("该连接器不支持同步")
    job = Job(type=JobType.SYNC_SOURCE, source_id=source.id, status=JobStatus.QUEUED)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    from sag_api.services.document_service import _enqueue_persisted_job

    await _enqueue_persisted_job(job_queue, job.id)
    return job
