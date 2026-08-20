from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.db import get_session
from sag_api.core.deps import get_current_user, get_engine_manager, get_job_queue
from sag_api.db.models import User
from sag_api.jobs import JobQueue
from sag_api.sag import EngineManager
from sag_api.schemas.job import JobOut
from sag_api.schemas.universe import (
    ExplorationDetailOut,
    ExplorationSessionOut,
    ExplorationStepOut,
    UniverseExpandIn,
    UniverseGraphPatchOut,
    UniverseManifestOut,
    UniverseNodeDetailOut,
    UniversePartitionOut,
    UniverseTimelineIn,
    UniverseTimelineSliceOut,
)
from sag_api.services.universe_service import (
    enqueue_universe_rebuild,
    get_exploration,
    list_explorations,
    universe_expand,
    universe_manifest,
    universe_node_detail,
    universe_timeline,
)

router = APIRouter(prefix="/universe", tags=["universe"])


def _partition_out(value) -> UniversePartitionOut:
    def read(name: str, default=None):
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

    return UniversePartitionOut(
        id=read("id"),
        source_id=read("source_id"),
        parent_id=read("parent_id"),
        kind=read("kind"),
        key=read("key"),
        label=read("label"),
        x=read("x", 0.0),
        y=read("y", 0.0),
        z=read("z", 0.0),
        radius=read("radius", 120.0),
        node_count=read("node_count", 0),
        event_count=read("event_count", 0),
        entity_count=read("entity_count", 0),
        relation_count=read("relation_count", 0),
        density=read("density", 0.0),
        time_buckets=read("time_buckets", []) or [],
        importance=read("importance", 0.0),
    )


@router.get("/manifest", response_model=UniverseManifestOut)
async def manifest(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UniverseManifestOut:
    value = await universe_manifest(session, user.id)
    return UniverseManifestOut(
        version=value["version"],
        status=value["status"],
        stale=value["stale"],
        as_of=value.get("as_of"),
        bounds=value["bounds"],
        partitions=[_partition_out(item) for item in value["partitions"]],
        counts=value["counts"],
        policy=value["policy"],
    )


@router.post("/expand", response_model=UniverseGraphPatchOut)
async def expand(
    body: UniverseExpandIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
) -> UniverseGraphPatchOut:
    value = await universe_expand(
        session,
        engine_manager,
        user_id=user.id,
        source_id=body.source_id,
        node_kind=body.node_kind,
        node_id=body.node_id,
        limit=body.limit,
        cursor=body.cursor,
        snapshot_id=body.snapshot_id,
        after=body.after,
        before=body.before,
    )
    return UniverseGraphPatchOut(epoch=body.epoch, **value)


@router.post("/timeline", response_model=UniverseTimelineSliceOut)
async def timeline(
    body: UniverseTimelineIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
) -> UniverseTimelineSliceOut:
    value = await universe_timeline(
        session,
        engine_manager,
        user_id=user.id,
        source_id=body.source_id,
        limit=body.limit,
        direction=body.direction,
        cursor=body.cursor,
        snapshot_id=body.snapshot_id,
    )
    return UniverseTimelineSliceOut(epoch=body.epoch, **value)


@router.get("/nodes/{node_kind}/{node_id}", response_model=UniverseNodeDetailOut)
async def node_detail(
    node_kind: Literal["event", "entity"],
    node_id: str,
    source_id: str = Query(min_length=1, max_length=64),
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
) -> UniverseNodeDetailOut:
    value = await universe_node_detail(
        session,
        engine_manager,
        node_kind,
        node_id,
        source_id=source_id,
    )
    return UniverseNodeDetailOut(**value)

@router.post("/rebuild", response_model=JobOut, status_code=202)
async def rebuild(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue = Depends(get_job_queue),
) -> JobOut:
    job = await enqueue_universe_rebuild(
        session,
        job_queue,
        user_id=user.id,
    )
    return JobOut.model_validate(job)


@router.get("/explorations", response_model=list[ExplorationSessionOut])
async def explorations(
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ExplorationSessionOut]:
    rows = await list_explorations(session, user.id, limit=limit)
    return [
        ExplorationSessionOut(
            id=item.id,
            title=item.title,
            source_ids=item.source_ids or [],
            created_at=item.created_at,
            updated_at=item.updated_at,
            step_count=count,
        )
        for item, count in rows
    ]


@router.get("/explorations/{exploration_id}", response_model=ExplorationDetailOut)
async def exploration_detail(
    exploration_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ExplorationDetailOut:
    item, steps = await get_exploration(session, user.id, exploration_id)
    return ExplorationDetailOut(
        session=ExplorationSessionOut(
            id=item.id,
            title=item.title,
            source_ids=item.source_ids or [],
            created_at=item.created_at,
            updated_at=item.updated_at,
            step_count=len(steps),
        ),
        steps=[
            ExplorationStepOut(
                id=step.id,
                session_id=step.session_id,
                query=step.query,
                summary=step.summary,
                source_ids=step.source_ids or [],
                event_refs=step.event_refs or [],
                entity_refs=step.entity_refs or [],
                relation_refs=step.relation_refs or [],
                evidence_refs=step.evidence_refs or [],
                camera=step.camera or {},
                created_at=step.created_at,
            )
            for step in steps
        ],
    )
