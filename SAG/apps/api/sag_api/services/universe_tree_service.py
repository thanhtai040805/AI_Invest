from __future__ import annotations

import asyncio
from bisect import bisect_left, bisect_right
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from sag_api.core.errors import ServiceUnavailableError
from sag_api.db.models import Document, Job
from sag_api.db.models.routing_rag import CanonicalBlock, DocumentVersion, SearchUnit
from sag_api.enums import JobStatus, JobType
from sag_api.services.knowledge_candidate_service import build_project_routing_snapshot
from sag_api.services.routing_tree_service import TreeBuildConfig
from sag_api.services.tree_publish_service import (
    SearchUnitAssignment,
    TreePublishResult,
    publish_tree_candidate,
)

_LOCAL_SCHEDULE_LOCK = asyncio.Lock()


@asynccontextmanager
async def _schedule_lock(session: AsyncSession, key: int):
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(select(func.pg_advisory_xact_lock(key)))
        yield
    else:
        async with _LOCAL_SCHEDULE_LOCK:
            yield


async def enqueue_project_tree_rebuild(
    session: AsyncSession,
    job_queue,
    *,
    tenant_id: str,
    project_ids: list[str],
    reason: str,
    persist_only: bool = False,
) -> Job:
    if job_queue is None:
        raise ServiceUnavailableError("Background job queue is unavailable")
    projects = sorted(set(project_ids))
    if not tenant_id.strip() or not projects or any(not item.strip() for item in projects):
        raise ValueError("Universe tree rebuild requires a tenant and at least one project")

    scope = f"{tenant_id}:{','.join(projects)}"
    lock_key = int.from_bytes(sha256(scope.encode()).digest()[:8], "big", signed=True)
    try:
        async with _schedule_lock(session, lock_key):
            queued = (
                await session.scalars(
                    select(Job)
                    .where(
                        Job.type == JobType.REBUILD_UNIVERSE_TREE,
                        Job.status == JobStatus.QUEUED,
                    )
                    .order_by(Job.created_at)
                    .with_for_update()
                )
            ).all()
            job = next(
                (
                    item
                    for item in queued
                    if (item.payload or {}).get("tenant_id") == tenant_id
                    and sorted((item.payload or {}).get("project_ids") or []) == projects
                ),
                None,
            )
            created = False
            if job is None:
                created = True
                job = Job(
                    type=JobType.REBUILD_UNIVERSE_TREE,
                    source_id=None,
                    status=JobStatus.QUEUED,
                    payload={"tenant_id": tenant_id, "project_ids": projects, "reason": reason},
                )
                session.add(job)
                await session.flush()
            if not persist_only:
                await session.commit()
    except Exception:
        await session.rollback()
        raise

    if not persist_only and job.status == JobStatus.QUEUED:
        already_scheduled = (
            not created
            and (
                job.id in getattr(job_queue, "_queued_job_ids", ())
                or job.id in getattr(job_queue, "_retry_tasks_by_job", ())
            )
        )
        if not already_scheduled:
            from sag_api.services.document_service import _enqueue_persisted_job

            await _enqueue_persisted_job(job_queue, job.id)
    return job


async def enqueue_tree_rebuild_for_version(
    session: AsyncSession,
    job_queue,
    document_version_id: str,
    *,
    reason: str,
) -> Job | None:
    now = datetime.now(UTC)
    scope = await session.execute(
        select(Document.tenant_id, Document.project_id)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(
            DocumentVersion.id == document_version_id,
            Document.is_active.is_(True),
            DocumentVersion.knowledge_status == "DATA_READY",
            DocumentVersion.search_status.in_(("READY", "SEARCH_READY")),
            DocumentVersion.search_ready_at.is_not(None),
            DocumentVersion.valid_from <= now,
            DocumentVersion.valid_to > now,
        )
    )
    row = scope.first()
    if row is None:
        return None
    return await enqueue_project_tree_rebuild(
        session,
        job_queue,
        tenant_id=row.tenant_id,
        project_ids=[row.project_id],
        reason=reason,
    )


async def _search_unit_assignments(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    units,
) -> list[SearchUnitAssignment]:
    version_ids = sorted({unit.document_version_id for unit in units})
    block_rows = (
        await session.execute(
            select(CanonicalBlock.document_version_id, CanonicalBlock.id, CanonicalBlock.ordinal).where(
                CanonicalBlock.document_version_id.in_(version_ids)
            )
        )
    ).all()
    ordinal_by_block = {(version_id, block_id): ordinal for version_id, block_id, ordinal in block_rows}
    units_by_version: dict[str, list[tuple[int, str, str]]] = {}
    for unit in units:
        evidence_ids = unit.provenance_json.get("evidence_block_ids", [])
        if len(evidence_ids) != 1:
            raise ValueError("KnowledgeUnit must point to exactly one canonical block")
        ordinal = ordinal_by_block.get((unit.document_version_id, evidence_ids[0]))
        if ordinal is None:
            raise ValueError("KnowledgeUnit canonical block is missing")
        units_by_version.setdefault(unit.document_version_id, []).append(
            (ordinal, unit.id, unit.security_partition_id)
        )
    for version_units in units_by_version.values():
        version_units.sort()
    ordinals_by_version = {
        version_id: [item[0] for item in version_units]
        for version_id, version_units in units_by_version.items()
    }

    block_from = aliased(CanonicalBlock)
    block_to = aliased(CanonicalBlock)
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(
                SearchUnit,
                Document.tenant_id,
                Document.project_id,
                Document.source_id,
                block_from.ordinal,
                block_to.ordinal,
            )
            .join(DocumentVersion, DocumentVersion.id == SearchUnit.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .join(block_from, block_from.id == SearchUnit.block_from_id)
            .join(block_to, block_to.id == SearchUnit.block_to_id)
            .where(
                SearchUnit.document_version_id.in_(version_ids),
                Document.is_active.is_(True),
                DocumentVersion.knowledge_status == "DATA_READY",
                DocumentVersion.search_status.in_(("READY", "SEARCH_READY")),
                DocumentVersion.search_ready_at.is_not(None),
                DocumentVersion.valid_from <= now,
                DocumentVersion.valid_to > now,
            )
            .order_by(SearchUnit.document_version_id, SearchUnit.id)
        )
    ).all()

    assignments: list[SearchUnitAssignment] = []
    mapped_knowledge_ids: set[str] = set()
    for unit, actual_tenant, actual_project, source_id, first_ordinal, last_ordinal in rows:
        if actual_tenant != tenant_id or actual_project != project_id or first_ordinal > last_ordinal:
            raise ValueError("SearchUnit scope differs from the project tree candidate")
        version_units = units_by_version.get(unit.document_version_id, [])
        ordinals = ordinals_by_version.get(unit.document_version_id, [])
        first = bisect_left(ordinals, first_ordinal)
        last = bisect_right(ordinals, last_ordinal)
        knowledge_unit_ids = tuple(sorted({item[1] for item in version_units[first:last]}))
        if not knowledge_unit_ids:
            continue
        if any(
            partition_id != unit.security_partition_id
            for ordinal, _unit_id, partition_id in version_units[first:last]
        ):
            raise ValueError("SearchUnit spans multiple security partitions")
        assignments.append(
            SearchUnitAssignment(
                knowledge_unit_ids=knowledge_unit_ids,
                search_unit_id=unit.id,
                source_id=source_id,
                document_version_id=unit.document_version_id,
                partition_id=unit.security_partition_id,
            )
        )
        mapped_knowledge_ids.update(knowledge_unit_ids)

    if mapped_knowledge_ids != {unit.id for unit in units}:
        raise ValueError("Current KnowledgeUnits do not map completely to ready SearchUnits")
    return assignments


async def rebuild_project_universe_tree(
    session: AsyncSession,
    engine_manager,
    *,
    tenant_id: str,
    project_id: str,
) -> TreePublishResult | None:
    built = await build_project_routing_snapshot(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        queries=[],
        config=TreeBuildConfig(min_cluster_size=1, min_partition_units=1),
        allow_empty=True,
    )
    if built is None:
        from sag_api.db.models import ProjectSearchState, TreeManifest
        from sag_api.services.knowledge_service import knowledge_lock
        from sag_api.services.knowledge_candidate_service import current_units

        await knowledge_lock(session, "project-input:" + tenant_id + ":" + project_id)
        if await current_units(session, tenant_id, project_id, allow_empty=True):
            raise ServiceUnavailableError("Project changed while clearing its empty snapshot")
        state = await session.scalar(select(ProjectSearchState).where(
            ProjectSearchState.project_id == project_id).with_for_update())
        if state is not None and state.active_tree_version:
            previous = await session.get(TreeManifest, state.active_tree_version)
            if previous:
                previous.status = "INACTIVE"
            state.previous_tree_version = state.active_tree_version
            state.active_tree_version = None
            state.active_search_epoch += 1
            state.last_switched_at = datetime.now(UTC)
        await session.commit()
        return None
    snapshot, units, _graph, _benchmark = built
    assignments = await _search_unit_assignments(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        units=units,
    )
    await session.commit()
    qdrant_client = await engine_manager.get_search_unit_qdrant_client()
    return await publish_tree_candidate(
        snapshot,
        search_unit_assignments=assignments,
        qdrant_client=qdrant_client,
    )
