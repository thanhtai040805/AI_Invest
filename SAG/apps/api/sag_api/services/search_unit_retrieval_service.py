"""Global retrieval over ACL-scoped, manifest-verified Phase 2C SearchUnits."""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from weakref import WeakKeyDictionary

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from sag_api.core.config import settings
from sag_api.core.db import SessionLocal
from sag_api.core.errors import ServiceUnavailableError
from sag_api.core.logging import get_logger
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.db.models import (
    CanonicalBlock,
    Document,
    DocumentVersion,
    IngestionRun,
    Job,
    KnowledgeGraphBuild,
    KnowledgeGraphEdge,
    KnowledgeUnit,
    KnowledgeUnitEdge,
    ProjectSearchState,
    SearchUnit,
    SourceProjectMapping,
    StageRun,
    TreeManifest,
)
from sag_api.enums import DocumentStatus, JobStatus, JobType
from sag_api.sag import RetrievedSection, SearchOutcome
from sag_api.sag.search_unit_store import (
    SearchIndexUnavailable,
    SearchUnitHit,
    SearchUnitQdrantStore,
    build_search_filter,
    build_sparse_query_vector,
)
from sag_api.services.knowledge_quality_persistence import record_query_demand
from sag_api.services.query_routing_service import (
    RouteDecision,
    capture_routing_decisions,
    routing_scope_for_group,
    scope_fingerprint,
)
from sag_api.services.query_strategy_planner import QueryStrategyPlan, plan_query, promote_multi_hop

log = get_logger("search_units")
_routed_query_demand_tasks: set[asyncio.Task[None]] = set()
_selection_slots = WeakKeyDictionary()


async def _select_diverse_sections(
    query,
    candidates,
    *,
    limit,
    deadline,
    per_document_limit: int | None = None,
):
    """Keep evidence scoring off the API loop and stop it when its request ends."""
    from sag_api.services.retrieval_service import select_diverse_sections

    loop = asyncio.get_running_loop()
    slot = _selection_slots.setdefault(loop, asyncio.Semaphore(max(1, settings.search_source_concurrency)))
    async with asyncio.timeout_at(deadline):
        await slot.acquire()
        try:
            cancelled = threading.Event()
            task = asyncio.create_task(asyncio.to_thread(
                select_diverse_sections,
                query,
                candidates,
                limit=limit,
                per_document_limit=per_document_limit,
                _cancelled=cancelled,
            ))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled.set()
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not task.cancelled():
                    task.exception()
                raise
        finally:
            slot.release()


_CONTINUUM_PUBLIC_DOC_ID_RE = re.compile(
    r"^(?:continuum:project_document:|project-document:)?([0-9a-fA-F]{24})\Z"
)


def _resolve_public_document_id(logical_source_id: object) -> str | None:
    if isinstance(logical_source_id, str) and logical_source_id.strip():
        matched = _CONTINUUM_PUBLIC_DOC_ID_RE.fullmatch(logical_source_id.strip())
        if matched:
            return matched.group(1).lower()
    return None


def _verify_selection_scope(version_ids, previous, refreshed):
    fields = ("source_id", "project_id", "tenant_id", "partition_id", "acl_mapping_version",
              "ingestion_run_id", "index_stage_run_id", "search_manifest_checksum")
    if any(vid not in previous or vid not in refreshed or any(
        getattr(previous[vid], key) != getattr(refreshed[vid], key) for key in fields
    ) for vid in version_ids):
        raise ServiceUnavailableError("Search scope changed during evidence selection")


class SearchableSource(Protocol):
    id: str
    name: str
    sag_source_config_id: str


@dataclass(frozen=True, slots=True)
class _ReadyVersion:
    document_version_id: str
    source_id: str
    project_id: str
    tenant_id: str
    partition_id: str
    source: SearchableSource
    acl_mapping_version: int = 0
    ingestion_run_id: str = ""
    index_stage_run_id: str = ""
    search_manifest_checksum: str = ""


@dataclass(frozen=True, slots=True)
class _SearchGroup:
    project_id: str
    tenant_id: str
    partition_id: str
    source: SearchableSource
    versions: tuple[str, ...]
    acl_mapping_version: int = 0
    search_index_versions: tuple[tuple[str, str, str, str], ...] = ()

    @property
    def scope_key(self) -> tuple[str, str, str, str]:
        return (self.project_id, self.source.id, self.tenant_id, self.partition_id)


async def _record_routed_query_demand(
    groups: list[_SearchGroup],
    decisions: dict[tuple[str, str, str, str], RouteDecision],
) -> None:
    """Persist selected node counts without retaining query text or mismatched scopes."""
    by_scope: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    for group in groups:
        route = decisions.get(group.scope_key)
        if (
            route is None
            or not route.routed
            or not route.selected_nodes
            or route.project_id != group.project_id
            or not route.tree_version
            or route.acl_scope_fingerprint != scope_fingerprint(routing_scope_for_group(group))
        ):
            continue
        scope = (
            group.tenant_id,
            group.project_id,
            group.partition_id,
            route.tree_version,
        )
        by_scope[scope].update(node_id for node_id in route.selected_nodes if node_id)
    if not by_scope:
        return
    try:
        async with asyncio.timeout(settings.search_source_timeout):
            async with SessionLocal() as session:
                occurred_at = datetime.now(UTC)
                for (tenant_id, project_id, partition_id, tree_version), node_ids in sorted(by_scope.items()):
                    await record_query_demand(
                        session,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        security_partition_id=partition_id,
                        tree_version=tree_version,
                        node_ids=tuple(sorted(node_ids)),
                        occurred_at=occurred_at,
                    )
                await session.commit()
    except Exception as error:  # noqa: BLE001 - demand recording must never break retrieval
        log.warning("knowledge query demand write failed error_type=%s", type(error).__name__)


def _finish_routed_query_demand(task: asyncio.Task[None]) -> None:
    _routed_query_demand_tasks.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        log.warning("knowledge query demand task failed error_type=%s", type(error).__name__)


def _schedule_routed_query_demand(
    groups: list[_SearchGroup],
    decisions: dict[tuple[str, str, str, str], RouteDecision],
) -> None:
    """Write approximate demand off the search response path."""
    task = asyncio.create_task(
        _record_routed_query_demand(groups, decisions),
        name="sag-routed-query-demand",
    )
    _routed_query_demand_tasks.add(task)
    task.add_done_callback(_finish_routed_query_demand)


def _verified_manifest(metrics: Any, *, project_id: str) -> bool:
    if not isinstance(metrics, dict) or metrics.get("manifest_verified") is not True:
        return False
    expected_collection = f"search_units_{project_id}"
    if metrics.get("collection_name") != expected_collection:
        return False
    count_names = (
        "search_unit_count",
        "pg_count",
        "qdrant_count",
        "qdrant_indexed_count",
    )
    counts = [metrics.get(name) for name in count_names]
    if any(type(value) is not int or value < 0 for value in counts):
        return False
    unit_count, pg_count, qdrant_count, indexed_count = counts
    checksum = metrics.get("manifest_checksum")
    qdrant_checksum = metrics.get("qdrant_checksum")
    checksums_are_sha256 = all(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
        for value in (checksum, qdrant_checksum)
    )
    return (
        unit_count > 0
        and unit_count == pg_count == qdrant_count == indexed_count
        and checksums_are_sha256
        and checksum == qdrant_checksum
    )


def _stage_belongs_to_current_attempt(
    attempt_started_at: datetime | None,
    stage: StageRun,
) -> bool:
    """A retry invalidates older SUCCESS manifests as soon as its run starts."""
    return attempt_started_at is not None and stage.created_at >= attempt_started_at


async def _load_current_ready_versions(
    session: AsyncSession,
    sources: list[SearchableSource],
    principal: VerifiedPrincipal,
    *,
    version_ids: set[str] | list[str] | tuple[str, ...] | None = None,
) -> dict[str, _ReadyVersion]:
    try:
        return await _load_current_ready_versions_checked(
            session,
            sources,
            principal,
            version_ids=version_ids,
        )
    except SQLAlchemyError as error:
        log.warning("canonical scope lookup unavailable error_type=%s", type(error).__name__)
        raise ServiceUnavailableError("Canonical search authorization is unavailable") from error


async def grep_search_unit_blocks(
    session: AsyncSession,
    sources: list[SearchableSource],
    pattern: str,
    *,
    principal: VerifiedPrincipal,
    limit: int,
) -> list[CanonicalBlock]:
    """Find literal text in every currently searchable, ACL-scoped canonical block."""
    versions = await _load_current_ready_versions(session, sources, principal)
    if not versions:
        return []

    escaped = pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    try:
        rows = await session.scalars(
            select(CanonicalBlock)
            .where(
                CanonicalBlock.document_version_id.in_(versions),
                CanonicalBlock.normalized_text.ilike(f"%{escaped}%", escape="\\"),
            )
            .order_by(CanonicalBlock.document_version_id, CanonicalBlock.ordinal)
            .limit(limit)
        )
        return list(rows.all())
    except SQLAlchemyError as error:
        log.warning("canonical literal search unavailable error_type=%s", type(error).__name__)
        raise ServiceUnavailableError("Canonical literal search is unavailable") from error


async def _load_current_ready_versions_checked(
    session: AsyncSession,
    sources: list[SearchableSource],
    principal: VerifiedPrincipal,
    *,
    version_ids: set[str] | list[str] | tuple[str, ...] | None = None,
) -> dict[str, _ReadyVersion]:
    """Recheck mappings and collect only versions from each version's latest verified attempt."""
    if not sources or not principal.tenant_id or not principal.allowed_partition_ids:
        return {}
    if version_ids is not None and not version_ids:
        return {}

    source_by_id = {source.id: source for source in sources}
    source_ids = sorted(source_by_id)
    partition_ids = sorted(principal.allowed_partition_ids)
    partition_expression = DocumentVersion.metadata_json["security_partition_id"].as_string()
    current_time = datetime.now(UTC)
    hidden_document_ids = await _hidden_reprocess_document_ids(session, source_ids)

    unit_exists_clause = (
        select(SearchUnit.id)
        .where(
            SearchUnit.document_version_id == DocumentVersion.id,
            SearchUnit.security_partition_id.in_(partition_ids),
            SearchUnit.security_partition_id == partition_expression,
        )
        .exists()
    )
    scoped_rows = (
        await session.execute(
            select(
                DocumentVersion.id.label("document_version_id"),
                Document.source_id,
                Document.project_id,
                Document.tenant_id,
                partition_expression.label("security_partition_id"),
                SourceProjectMapping.mapping_version,
            )
            .join(Document, Document.id == DocumentVersion.document_id)
            .join(SourceProjectMapping, SourceProjectMapping.source_id == Document.source_id)
            .where(
                DocumentVersion.id.in_(sorted(version_ids)) if version_ids is not None else True,
                Document.source_id.in_(source_ids),
                Document.project_id.in_(principal.allowed_project_ids),
                Document.project_id == SourceProjectMapping.project_id,
                Document.tenant_id == principal.tenant_id,
                SourceProjectMapping.organization_id == principal.organization_id,
                SourceProjectMapping.state == "CONFIRMED",
                Document.is_active.is_(True),
                Document.status.notin_(
                    [
                        DocumentStatus.DELETING,
                        DocumentStatus.DELETE_FAILED,
                        DocumentStatus.PAUSING,
                        DocumentStatus.PAUSED,
                        DocumentStatus.FAILED,
                    ]
                ),
                Document.id.notin_(hidden_document_ids) if hidden_document_ids else True,
                DocumentVersion.search_status.in_(("READY", "SEARCH_READY")),
                DocumentVersion.valid_from <= current_time,
                DocumentVersion.search_ready_at.is_not(None),
                DocumentVersion.valid_to > current_time,
                partition_expression.in_(partition_ids),
                unit_exists_clause,
            )
        )
    ).all()
    if not scoped_rows:
        return {}

    matched_version_ids = sorted({str(row.document_version_id) for row in scoped_rows})
    ranked_runs = (
        select(
            IngestionRun.id.label("run_id"),
            IngestionRun.document_version_id.label("document_version_id"),
            IngestionRun.project_id.label("project_id"),
            IngestionRun.tenant_id.label("tenant_id"),
            IngestionRun.started_at.label("started_at"),
            func.row_number()
            .over(
                partition_by=IngestionRun.document_version_id,
                order_by=(IngestionRun.created_at.desc(), IngestionRun.id.desc()),
            )
            .label("attempt_rank"),
        )
        .where(IngestionRun.document_version_id.in_(matched_version_ids))
        .subquery()
    )
    latest_runs = (
        await session.execute(
            select(
                ranked_runs.c.run_id,
                ranked_runs.c.document_version_id,
                ranked_runs.c.project_id,
                ranked_runs.c.tenant_id,
                ranked_runs.c.started_at,
            ).where(ranked_runs.c.attempt_rank == 1)
        )
    ).all()
    run_by_version = {str(row.document_version_id): row for row in latest_runs}
    run_ids = [row.run_id for row in latest_runs]
    if not run_ids:
        return {}

    stage_rows = (
        await session.execute(
            select(StageRun)
            .where(StageRun.run_id.in_(run_ids), StageRun.stage == "INDEX_SEARCH")
            .order_by(StageRun.created_at.desc(), StageRun.id.desc())
        )
    ).scalars().all()
    stage_by_run: dict[str, StageRun] = {}
    for stage in stage_rows:
        stage_by_run.setdefault(stage.run_id, stage)

    versions: dict[str, _ReadyVersion] = {}
    for row in scoped_rows:
        version_id = str(row.document_version_id)
        run = run_by_version.get(version_id)
        source = source_by_id.get(str(row.source_id))
        if run is None or source is None:
            continue
        if str(run.project_id) != str(row.project_id) or str(run.tenant_id) != str(row.tenant_id):
            continue
        stage = stage_by_run.get(run.run_id)
        if (
            stage is None
            or stage.status != "SUCCESS"
            or not _stage_belongs_to_current_attempt(run.started_at, stage)
            or not _verified_manifest(stage.metrics_json, project_id=str(row.project_id))
        ):
            continue
        versions[version_id] = _ReadyVersion(
            document_version_id=version_id,
            source_id=str(row.source_id),
            project_id=str(row.project_id),
            tenant_id=str(row.tenant_id),
            partition_id=str(row.security_partition_id),
            acl_mapping_version=int(row.mapping_version),
            ingestion_run_id=str(run.run_id),
            index_stage_run_id=str(stage.id),
            search_manifest_checksum=str(stage.metrics_json["manifest_checksum"]),
            source=source,
        )
    return versions


async def _hidden_reprocess_document_ids(session: AsyncSession, source_ids: list[str]) -> set[str]:
    """Honor the delete/reprocess visibility barrier used by legacy retrieval in 1 query."""
    if not source_ids:
        return set()
    control_types = [JobType.DELETE_DOCUMENT, JobType.REPROCESS_DOCUMENT]
    rows = (
        await session.scalars(
            select(Job.document_id)
            .outerjoin(Document, Document.id == Job.document_id)
            .where(
                Job.source_id.in_(source_ids),
                Job.type.in_(control_types),
                Job.document_id.is_not(None),
                (
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])
                    | (
                        (Job.status == JobStatus.FAILED)
                        & Document.status.in_([DocumentStatus.FAILED, DocumentStatus.DELETE_FAILED])
                    )
                ),
            )
        )
    ).all()
    return {str(doc_id) for doc_id in rows if doc_id}


def _groups(versions: dict[str, _ReadyVersion]) -> list[_SearchGroup]:
    grouped: dict[tuple[str, str, str, str], list[_ReadyVersion]] = defaultdict(list)
    for version in versions.values():
        key = (version.project_id, version.source_id, version.tenant_id, version.partition_id)
        grouped[key].append(version)
    return [
        _SearchGroup(
            project_id=key[0],
            tenant_id=key[2],
            partition_id=key[3],
            source=group_versions[0].source,
            versions=tuple(sorted({version.document_version_id for version in group_versions})),
            acl_mapping_version=group_versions[0].acl_mapping_version,
            search_index_versions=tuple(
                sorted(
                    (
                        version.document_version_id,
                        version.ingestion_run_id,
                        version.index_stage_run_id,
                        version.search_manifest_checksum,
                    )
                    for version in group_versions
                )
            ),
        )
        for key, group_versions in sorted(grouped.items())
        if len({version.acl_mapping_version for version in group_versions}) == 1
    ]


def _interleave_hit_lists(hit_lists: list[list[SearchUnitHit]], *, limit: int) -> list[SearchUnitHit]:
    non_empty = [lst for lst in hit_lists if lst]
    if not non_empty:
        return []
    if len(non_empty) == 1:
        return non_empty[0][:limit]
    list_maxes = [max((max(0.0, float(h.score)) for h in lst), default=0.0) for lst in non_empty]
    base_scale = max(list_maxes, default=1.0) or 1.0
    by_point: dict[str, tuple[int, float, SearchUnitHit]] = {}
    max_len = max(len(lst) for lst in non_empty)
    out_order = 0
    for rank_idx in range(max_len):
        for lst_idx, lst in enumerate(non_empty):
            if rank_idx >= len(lst):
                continue
            hit = lst[rank_idx]
            l_max = list_maxes[lst_idx]
            scaled_score = (max(0.0, float(hit.score)) / l_max) * base_scale if l_max > 0.0 else 0.0
            scaled_hit = (
                SearchUnitHit(point_id=hit.point_id, score=scaled_score, payload=hit.payload)
                if scaled_score != hit.score
                else hit
            )
            prev = by_point.get(hit.point_id)
            if prev is None:
                by_point[hit.point_id] = (out_order, scaled_score, scaled_hit)
                out_order += 1
            elif scaled_score > prev[1]:
                by_point[hit.point_id] = (prev[0], scaled_score, scaled_hit)
    merged = [item[2] for item in sorted(by_point.values(), key=lambda entry: (entry[0], -entry[1]))]
    return merged[: max(limit, min(limit * 2, len(merged)))]


async def _query_group(
    group: _SearchGroup,
    *,
    query: str,
    limit: int,
    store: SearchUnitQdrantStore,
    query_vector: list[float],
    route: RouteDecision | None = None,
    sub_sparse_queries: tuple[dict[str, list[int] | list[float]], ...] = (),
) -> tuple[_SearchGroup, list[SearchUnitHit], list[SearchUnitHit]]:
    collection = f"search_units_{group.project_id}"
    use_tree_route = bool(
        route
        and route.routed
        and route.acl_scope_fingerprint
        == scope_fingerprint(routing_scope_for_group(group))
    )
    search_filter = build_search_filter(
        project_id=group.project_id,
        tenant_id=group.tenant_id,
        partition_id=group.partition_id,
        document_version_ids=list(group.versions),
        tree_version=route.tree_version if use_tree_route and route else None,
        routing_slot=route.routing_slot if use_tree_route and route else None,
        membership_node_ids=list(route.membership_node_ids) if use_tree_route and route else None,
    )
    try:
        vector = [float(value) for value in query_vector]
        if not vector or any(not math.isfinite(value) for value in vector):
            raise ValueError("invalid embedding vector")
        sparse_query = build_sparse_query_vector(query)
        if hasattr(store, "search_batch"):
            sub_limit = max(8, limit // 2)
            searches: list[dict[str, Any]] = [
                {
                    "vector_name": "content_vector",
                    "query": vector,
                    "search_filter": search_filter,
                    "limit": limit,
                },
                {
                    "vector_name": "bm25_sparse",
                    "query": sparse_query,
                    "search_filter": search_filter,
                    "limit": limit,
                },
            ]
            for sub_sparse in sub_sparse_queries:
                searches.append(
                    {
                        "vector_name": "bm25_sparse",
                        "query": sub_sparse,
                        "search_filter": search_filter,
                        "limit": sub_limit,
                    }
                )
            batch_res = await store.search_batch(collection, searches=searches)
            semantic = batch_res[0]
            lexical = _interleave_hit_lists([batch_res[1], *batch_res[2:]], limit=limit)
        else:
            semantic_task = asyncio.create_task(
                store.search(
                    collection,
                    vector_name="content_vector",
                    query=vector,
                    search_filter=search_filter,
                    limit=limit,
                ),
            )
            lexical_task = asyncio.create_task(
                store.search(
                    collection,
                    vector_name="bm25_sparse",
                    query=sparse_query,
                    search_filter=search_filter,
                    limit=limit,
                ),
            )
            try:
                semantic, lexical = await asyncio.gather(semantic_task, lexical_task)
            except BaseException:
                semantic_task.cancel()
                lexical_task.cancel()
                await asyncio.gather(semantic_task, lexical_task, return_exceptions=True)
                raise
    except asyncio.CancelledError:
        raise
    except SearchIndexUnavailable:
        raise
    except Exception as error:  # noqa: BLE001 - upstream details may contain private URLs or credentials
        log.warning("canonical hybrid query failed error_type=%s", type(error).__name__)
        raise ServiceUnavailableError("Canonical search query failed") from error
    return group, semantic, lexical


async def _query_routed_group_batch(
    group: _SearchGroup,
    *,
    query: str,
    limit: int,
    store: SearchUnitQdrantStore,
    query_vector: list[float],
    route: RouteDecision,
    escape_limit: int | None = None,
    sub_sparse_queries: tuple[dict[str, list[int] | list[float]], ...] = (),
) -> tuple[list[SearchUnitHit], list[SearchUnitHit], list[SearchUnitHit], list[SearchUnitHit]]:
    """Execute branch_local + global_escape hybrid queries in 1 Qdrant batch round-trip."""
    collection = f"search_units_{group.project_id}"
    local_filter = build_search_filter(
        project_id=group.project_id,
        tenant_id=group.tenant_id,
        partition_id=group.partition_id,
        document_version_ids=list(group.versions),
        tree_version=route.tree_version,
        routing_slot=route.routing_slot,
        membership_node_ids=list(route.membership_node_ids),
    )
    escape_filter = build_search_filter(
        project_id=group.project_id,
        tenant_id=group.tenant_id,
        partition_id=group.partition_id,
        document_version_ids=list(group.versions),
    )
    vector = [float(value) for value in query_vector]
    if not vector or any(not math.isfinite(value) for value in vector):
        raise ValueError("invalid embedding vector")
    sparse_query = build_sparse_query_vector(query)
    eff_escape_limit = escape_limit if escape_limit is not None else limit
    searches: list[dict[str, Any]] = [
        {"vector_name": "content_vector", "query": vector, "search_filter": local_filter, "limit": limit},
        {"vector_name": "bm25_sparse", "query": sparse_query, "search_filter": local_filter, "limit": limit},
        {"vector_name": "content_vector", "query": vector, "search_filter": escape_filter, "limit": eff_escape_limit},
        {"vector_name": "bm25_sparse", "query": sparse_query, "search_filter": escape_filter, "limit": eff_escape_limit},
    ]
    local_sub_cnt = 0
    if sub_sparse_queries:
        sub_local_lim = max(8, limit // 2)
        sub_esc_lim = max(8, eff_escape_limit // 2)
        for sub_sparse in sub_sparse_queries:
            searches.append(
                {"vector_name": "bm25_sparse", "query": sub_sparse, "search_filter": local_filter, "limit": sub_local_lim}
            )
            local_sub_cnt += 1
        for sub_sparse in sub_sparse_queries:
            searches.append(
                {"vector_name": "bm25_sparse", "query": sub_sparse, "search_filter": escape_filter, "limit": sub_esc_lim}
            )
    batch = await store.search_batch(collection, searches=searches)
    if not sub_sparse_queries:
        return batch[0], batch[1], batch[2], batch[3]
    local_sub_hits = batch[4 : 4 + local_sub_cnt]
    esc_sub_hits = batch[4 + local_sub_cnt :]
    return (
        batch[0],
        _interleave_hit_lists([batch[1], *local_sub_hits], limit=limit),
        batch[2],
        _interleave_hit_lists([batch[3], *esc_sub_hits], limit=eff_escape_limit),
    )


_QWEN3_QUERY_INSTRUCT = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: "
)


async def _query_vector(engine_manager: Any, group: _SearchGroup, query: str) -> list[float]:
    from sag_api.services.search_index_service import (
        get_cached_text_embedding,
        put_cached_text_embedding,
    )

    model_name = (getattr(settings, "embedding_model", "") or "").lower()
    if "qwen3-embedding" in model_name and not query.startswith("Instruct:"):
        embed_query = f"{_QWEN3_QUERY_INSTRUCT}{query}"
    else:
        embed_query = query

    from sag_api.core.token_usage import (
        extract_embedding_usage,
        record_embedding_cache_hit,
        record_embedding_usage,
        token_usage_scope,
    )

    cached = get_cached_text_embedding(embed_query)
    if cached is not None:
        record_embedding_cache_hit(
            model=getattr(settings, "embedding_model", None),
            count=1,
            component="query_embedding",
            project_id=group.project_id,
        )
        return cached
    if cached is not None:
        return cached
    try:
        embedding = await engine_manager.get_sag_embedding(group.source.sag_source_config_id, group.source)
        with token_usage_scope(
            component="query_embedding",
            project_id=group.project_id,
            tenant_id=group.tenant_id,
        ):

            vector = [float(value) for value in await embedding.generate(embed_query)]
            if not getattr(embedding, "_sag_token_metered_cache_wrapped", False):
                record_embedding_usage(
                    model=getattr(settings, "embedding_model", None),
                    usage=extract_embedding_usage(None, embed_query),
                )
        if not vector or any(not math.isfinite(value) for value in vector):
            raise ValueError("invalid embedding vector")
        put_cached_text_embedding(embed_query, vector)
        return vector
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 - upstream details may contain private URLs or credentials
        log.warning("canonical query embedding unavailable error_type=%s", type(error).__name__)
        raise ServiceUnavailableError("Search embedding is unavailable") from error


def _fair_rank_merge(lists: list[list[RetrievedSection]]) -> list[RetrievedSection]:
    """Merge incomparable per-scope scores by deterministic rank interleaving."""
    result: list[RetrievedSection] = []
    seen: set[tuple[str, str]] = set()
    max_length = max((len(items) for items in lists), default=0)
    for rank in range(max_length):
        for items in lists:
            if rank >= len(items):
                continue
            section = items[rank]
            key = (section.source_config_id or "", section.chunk_id or "")
            if key in seen:
                continue
            seen.add(key)
            result.append(section)
    return result


def _bounded_candidate_unit_ids(
    hits: dict[
        tuple[str, str, str, str, str, str],
        tuple[_SearchGroup, SearchUnitHit, float, int, str],
    ],
    *,
    limit: int,
) -> list[str]:
    """Apply a stable RRF/rank candidate budget with round-robin scope coverage.

    This only bounds canonical hydration and reranking work; its preliminary RRF
    ordering is not an answerability or confidence signal.
    """
    if limit <= 0 or not hits:
        return []
    by_scope: dict[tuple[str, str, str, str], dict[str, float]] = defaultdict(dict)
    for key, (_group, _hit, _score, rank, _origin) in hits.items():
        _channel, project_id, source_id, tenant_id, partition_id, unit_id = key
        scope = (project_id, source_id, tenant_id, partition_id)
        rrf = 1.0 / (60 + rank + 1)
        by_scope[scope][unit_id] = by_scope[scope].get(unit_id, 0.0) + rrf

    ranked_by_scope = {
        scope: sorted(units.items(), key=lambda item: (-item[1], item[0]))
        for scope, units in sorted(by_scope.items())
    }
    selected: list[str] = []
    selected_ids: set[str] = set()
    cursors = {scope: 0 for scope in ranked_by_scope}
    while len(selected) < limit:
        advanced = False
        for scope, candidates in ranked_by_scope.items():
            cursor = cursors[scope]
            if cursor >= len(candidates):
                continue
            unit_id = candidates[cursor][0]
            cursors[scope] = cursor + 1
            advanced = True
            if unit_id not in selected_ids:
                selected.append(unit_id)
                selected_ids.add(unit_id)
                if len(selected) >= limit:
                    break
        if not advanced:
            break
    return selected


def _failure_code(error: BaseException) -> str:
    return "QUERY_TIMEOUT" if isinstance(error, TimeoutError) else "QUERY_PATH_FAILED"


def _structural_coverage_status(
    selected_count: int,
    diversity_stats: dict[str, Any],
) -> str:
    if selected_count <= 0:
        return "empty_evidence"
    if not diversity_stats.get("required_anchor_count"):
        return "unknown"
    if diversity_stats.get("anchor_coverage") == "missing_required_anchor":
        return "weak"
    return "structurally_sufficient"


def _routing_trace(
    groups: list[_SearchGroup],
    decisions: dict[tuple[str, str, str, str], RouteDecision],
    verified_origins: dict[tuple[str, str, str, str], dict[str, set[str]]],
    *,
    strategy_plan: QueryStrategyPlan,
    local_failures: dict[tuple[str, str, str, str], str],
    escape_failures: dict[tuple[str, str, str, str], str],
    coverage: dict[str, Any],
    graph_skip_reason: str | None,
    selection_fallback_reason: str | None = None,
    graph_expansion_attempted: bool = False,
    graph_expanded_candidates: int = 0,
) -> dict[str, Any]:
    routed_groups: list[dict[str, Any]] = []
    request_snapshot_ids: set[str] = set()
    fallback_used = selection_fallback_reason is not None
    for group in groups:
        route = decisions.get(group.scope_key)
        origins = verified_origins.get(group.scope_key, {})
        local_ids = origins.get("branch_local", set())
        escape_ids = origins.get("global_escape", set())
        global_ids = escape_ids | origins.get("global_only", set())
        fallback_used = fallback_used or (
            not (route and route.routed)
            or group.scope_key in local_failures
            or bool(escape_ids - local_ids)
            or group.scope_key in escape_failures
        )
        if route and route.request_snapshot_id:
            request_snapshot_ids.add(route.request_snapshot_id)
        selected_nodes = list(route.selected_nodes) if route else []
        routed_groups.append(
            {
                "project_id": group.project_id,
                "source_id": group.source.id,
                "partition_id": group.partition_id,
                "snapshot_id": route.snapshot_id if route else None,
                "request_snapshot_id": route.request_snapshot_id if route else None,
                "acl_scope_fingerprint": route.acl_scope_fingerprint if route else None,
                "tree_version": route.tree_version if route else None,
                "routing_slot": route.routing_slot if route else None,
                "search_epoch": route.search_epoch if route else None,
                "selected_nodes": selected_nodes,
                "membership_count": len(route.membership_node_ids) if route else 0,
                "entropy": route.entropy if route else None,
                "margin": route.margin if route else None,
                "broad_route": route.broad_route if route else False,
                "reason_code": route.reason_code if route else "TREE_SNAPSHOT_UNAVAILABLE",
                "fallback_reason": route.fallback_reason if route else "routing_snapshot_unavailable",
                "branch_local_candidates": len(local_ids),
                "global_candidates": len(global_ids),
                "escape_recovered_candidates": len(escape_ids - local_ids),
                "escape_attempted": True,
                "escape_trigger": (
                    "tree_route_unavailable"
                    if not route or not route.routed
                    else "local_path_failed"
                    if group.scope_key in local_failures
                    else "local_path_empty"
                    if not local_ids
                    else "reserved_parallel_global_escape"
                ),
                "local_failure": local_failures.get(group.scope_key),
                "escape_failure": escape_failures.get(group.scope_key),
                # This is a retrieval-path event, not a corpus-level recall metric.
                "blackhole_detected": bool(
                    route and route.routed and group.scope_key not in local_failures and not local_ids and global_ids
                ),
            }
        )
    return {
        "planner": strategy_plan.as_trace(),
        "request_snapshot_id": next(iter(request_snapshot_ids)) if len(request_snapshot_ids) == 1 else None,
        "groups": routed_groups,
        "coverage": coverage,
        "selection_fallback_reason": selection_fallback_reason,
        "multi_hop": {
            "eligible": strategy_plan.multi_hop_eligible,
            "effective": strategy_plan.primary_strategy.value,
            "graph_expansion_attempted": graph_expansion_attempted,
            "graph_expanded_candidates": graph_expanded_candidates,
            "graph_expansion_skip_reason": graph_skip_reason,
        },
        "fallback_used": fallback_used,
    }


_ARTIFACT_CODE_RE = re.compile(
    r"\b([a-z]{2,8})[\s_#-]+(\d{2,6}[a-z0-9]*)\b",
    re.IGNORECASE,
)
_NON_ARTIFACT_PREFIXES = frozenset(
    {
        "in", "on", "at", "to", "by", "of", "or", "as", "is", "be", "do", "no",
        "up", "if", "so", "an", "we", "he", "it", "us", "vs",
        "the", "and", "for", "from", "with", "into", "over", "after", "under", "between",
        "year", "years", "month", "months", "week", "weeks", "day", "days", "hour", "hours",
        "page", "pages", "step", "steps", "tier", "tiers", "part", "parts", "item", "items",
        "line", "lines", "table", "tables", "figure", "figures", "note", "notes", "top", "max", "min",
        "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
        "ngay", "thang", "nam", "tuan", "gio", "phut", "giay", "trang", "buoc", "lan", "phan", "muc",
    }
)
_INTERNAL_URI_SLUG_RE = re.compile(
    r"(?:[a-z][a-z0-9+.-]*:\/\/|(?<!https:)(?<!http:)\/\/)[^\s<>\]\)`\"']+\/([a-z0-9][a-z0-9_-]{5,120})",
    re.IGNORECASE,
)


def _extract_artifact_codes(text: str) -> set[str]:
    if not text:
        return set()
    codes: set[str] = set()
    for match in _ARTIFACT_CODE_RE.finditer(text):
        prefix = match.group(1).lower()
        if prefix in _NON_ARTIFACT_PREFIXES:
            continue
        num = match.group(2).lower()
        codes.add(f"{prefix} {num}")
    return codes


def _extract_uri_slugs(text: str) -> set[str]:
    if not text:
        return set()
    slugs: set[str] = set()
    for match in _INTERNAL_URI_SLUG_RE.finditer(text):
        slug_norm = re.sub(r"[\s_-]+", " ", match.group(1).lower()).strip()
        if " " in slug_norm and len(slug_norm) >= 8:
            slugs.add(slug_norm)
    return slugs


@dataclass(frozen=True, slots=True)
class _CompactKU:
    id: str
    document_version_id: str
    ordinal: int
    sec_path_norm: str
    entities_raw: tuple[str, ...]
    entities_norm: frozenset[str]


@dataclass(slots=True)
class _ScopeKnowledgeIndex:
    ku_by_version: dict[str, list[_CompactKU]]
    ku_by_id: dict[str, _CompactKU]
    edges_by_ku: dict[str, list[tuple[str, float]]]
    ent_exact_versions: dict[str, set[str]]
    hydrated_su_by_version: dict[str, list[tuple[RetrievedSection, int, int]]]
    # Maps (seed_version_id, seed_ordinal) -> [(target_version_id, target_s_ord, target_e_ord, weight, relation)]
    lineage_by_version_ordinal: dict[tuple[str, int], list[tuple[str, int, int, float, str]]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.lineage_by_version_ordinal is None:
            self.lineage_by_version_ordinal = {}


_SCOPE_KNOWLEDGE_INDEX_CACHE: dict[str, _ScopeKnowledgeIndex] = {}
_SCOPE_KNOWLEDGE_INDEX_CACHE_MAX = 16


def _scope_knowledge_digest(current_versions: dict[str, _ReadyVersion]) -> str:
    payload = [
        (
            v.document_version_id,
            v.search_manifest_checksum,
            v.partition_id,
            v.tenant_id,
            v.project_id,
            v.source_id,
        )
        for v in sorted(current_versions.values(), key=lambda item: item.document_version_id)
    ]
    return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()


async def _get_or_build_scope_knowledge_index(
    session: AsyncSession,
    current_versions: dict[str, _ReadyVersion],
    allowed_partitions: list[str],
) -> _ScopeKnowledgeIndex:
    version_ids = sorted(current_versions)
    checksum_rows = (await session.execute(
        select(KnowledgeUnit.id, KnowledgeUnit.checksum, KnowledgeUnit.project_id).where(
            KnowledgeUnit.document_version_id.in_(version_ids), KnowledgeUnit.is_current.is_(True),
            KnowledgeUnit.security_partition_id.in_(allowed_partitions),
        )
    )).all()
    checksums = {uid: value for uid, value, _project in checksum_rows}
    projects = {v.project_id for v in current_versions.values()}
    active_rows = (await session.execute(select(
        TreeManifest.project_id, TreeManifest.manifest_json["knowledge_graph_build_id"].as_string(),
    ).join(ProjectSearchState, ProjectSearchState.active_tree_version == TreeManifest.tree_version)
        .where(TreeManifest.project_id.in_(projects), TreeManifest.status == "ACTIVE"))).all()
    pinned = {project: build for project, build in active_rows if build}
    active_graphs = set(pinned.values())
    graph_rows = (await session.execute(select(
        KnowledgeGraphBuild.id, KnowledgeGraphBuild.project_id, KnowledgeGraphBuild.manifest_json["units"],
    ).where(KnowledgeGraphBuild.project_id.in_(projects),
            (KnowledgeGraphBuild.id.in_(active_graphs)) | KnowledgeGraphBuild.input_fingerprint.is_(None)))).all()
    graph_ids = []
    for project in projects:
        wanted = {uid: value for uid, value, scope_project in checksum_rows if scope_project == project}
        candidates = []
        for build_id, build_project, pairs in graph_rows:
            if build_project != project:
                continue
            if project in pinned and build_id != pinned[project]:
                continue
            inventory = dict(pairs or [])
            relevant = {uid: value for uid, value in wanted.items() if uid in inventory}
            if wanted and relevant == wanted and all(inventory[uid] == value for uid, value in wanted.items()):
                candidates.append((build_id in active_graphs, len(relevant), build_id))
        if candidates:
            graph_ids.append(max(candidates)[2])
    kg_edge_ids = sorted(
        str(eid)
        for (eid,) in (
            await session.execute(
                select(KnowledgeGraphEdge.id).where(
                    KnowledgeGraphEdge.project_id.in_(projects),
                    KnowledgeGraphEdge.edge_type.in_(("CONTRADICTS", "SUPERSEDES")),
                )
            )
        ).all()
    )
    digest = hashlib.sha256(repr((
        _scope_knowledge_digest(current_versions),
        sorted(checksums.items()),
        sorted(graph_ids),
        kg_edge_ids,
    )).encode()).hexdigest()
    cached = _SCOPE_KNOWLEDGE_INDEX_CACHE.get(digest)
    if cached is not None:
        return cached

    ku_rows = (
        await session.execute(
            select(
                KnowledgeUnit.id,
                KnowledgeUnit.document_version_id,
                KnowledgeUnit.ordinal,
                KnowledgeUnit.tenant_id,
                KnowledgeUnit.project_id,
                KnowledgeUnit.security_partition_id,
                KnowledgeUnit.provenance_json["section_path"],
                KnowledgeUnit.features_json["entities"],
                KnowledgeUnit.text,
            ).where(
                KnowledgeUnit.document_version_id.in_(version_ids),
                KnowledgeUnit.is_current.is_(True),
                KnowledgeUnit.security_partition_id.in_(allowed_partitions),
            )
        )
    ).all()

    ku_by_version: dict[str, list[_CompactKU]] = defaultdict(list)
    ku_by_id: dict[str, _CompactKU] = {}
    ent_exact_versions: dict[str, set[str]] = defaultdict(set)

    for ku_id, ver_id, ordinal, tenant_id, project_id, partition_id, sec_path_val, ents_val, ku_text in ku_rows:
        ver_id_str = str(ver_id)
        ver = current_versions.get(ver_id_str)
        if (
            ver is None
            or tenant_id != ver.tenant_id
            or project_id != ver.project_id
            or partition_id != ver.partition_id
        ):
            continue
        sec_path_raw = (
            sec_path_val.get("section_path")
            if isinstance(sec_path_val, dict)
            else sec_path_val
        )
        sec_path_str = str(sec_path_raw or "")
        sec_path_norm = re.sub(
            r"[\s_-]+",
            " ",
            sec_path_str.lower(),
        )
        ents_iter = (
            ents_val.get("entities", [])
            if isinstance(ents_val, dict)
            else (ents_val if isinstance(ents_val, (list, tuple)) else [])
        )
        artifact_codes = _extract_artifact_codes(f"{sec_path_str}\n{ku_text or ''}")
        uri_slugs = _extract_uri_slugs(ku_text or "")
        for raw_e in ents_iter:
            artifact_codes.update(_extract_artifact_codes(str(raw_e)))
            uri_slugs.update(_extract_uri_slugs(str(raw_e)))
        raw_ents = tuple(
            dict.fromkeys(
                [
                    *(str(e).strip() for e in ents_iter if str(e).strip()),
                    *sorted(artifact_codes),
                    *sorted(uri_slugs),
                ]
            )
        )
        norm_ents = frozenset(
            {
                *(re.sub(r"[\s_-]+", " ", e.lower()) for e in raw_ents),
                *artifact_codes,
                *uri_slugs,
            }
        )
        compact = _CompactKU(
            id=str(ku_id),
            document_version_id=ver_id_str,
            ordinal=int(ordinal),
            sec_path_norm=sec_path_norm,
            entities_raw=raw_ents,
            entities_norm=norm_ents,
        )
        ku_by_version[ver_id_str].append(compact)
        ku_by_id[compact.id] = compact
        for e_norm in norm_ents:
            ent_exact_versions[e_norm].add(ver_id_str)

    edges_by_ku: dict[str, list[tuple[str, float]]] = defaultdict(list)
    if ku_by_id and graph_ids:
        edge_rows = (
            await session.execute(
                select(
                    KnowledgeUnitEdge.source_unit_id,
                    KnowledgeUnitEdge.target_unit_id,
                    KnowledgeUnitEdge.weight,
                ).where(
                    KnowledgeUnitEdge.weight >= 0.55,
                    KnowledgeUnitEdge.build_id.in_(graph_ids),
                    KnowledgeUnitEdge.security_partition_id.in_(allowed_partitions),
                )
            )
        ).all()
        best_pair_weights: dict[tuple[str, str], float] = {}
        for src_id, tgt_id, weight in edge_rows:
            s_str, t_str = str(src_id), str(tgt_id)
            if s_str == t_str or s_str not in ku_by_id or t_str not in ku_by_id:
                continue
            w_val = float(weight or 0.0)
            pair = (s_str, t_str) if s_str < t_str else (t_str, s_str)
            if w_val > best_pair_weights.get(pair, 0.0):
                best_pair_weights[pair] = w_val
        for (s_str, t_str), w_val in best_pair_weights.items():
            edges_by_ku[s_str].append((t_str, w_val))
            edges_by_ku[t_str].append((s_str, w_val))
        for k_id in edges_by_ku:
            edges_by_ku[k_id].sort(key=lambda item: (-item[1], item[0]))

    lineage_by_version_ordinal: dict[tuple[str, int], list[tuple[str, int, int, float, str]]] = defaultdict(list)
    ver_meta_rows = (
        await session.execute(
            select(DocumentVersion.id, DocumentVersion.metadata_json).where(
                DocumentVersion.id.in_(version_ids)
            )
        )
    ).all()
    raw_block_cands: list[tuple[str, str, str, str, str]] = []
    cand_block_ids: set[str] = set()
    for v_id_raw, meta_json in ver_meta_rows:
        if not isinstance(meta_json, dict):
            continue
        dedup_cands = meta_json.get("dedup_candidates")
        if not isinstance(dedup_cands, list):
            continue
        for cand in dedup_cands:
            if not isinstance(cand, dict):
                continue
            rel_type = str(cand.get("relation_type") or cand.get("relation") or "")
            if rel_type not in {"CONTRADICTS", "SUPERSEDES"}:
                continue
            sb_id = str(cand.get("source_block_id") or "")
            tb_id = str(cand.get("target_block_id") or "")
            sv_id = str(cand.get("source_version_id") or v_id_raw or "")
            tv_id = str(cand.get("target_version_id") or "")
            if (
                not sb_id
                or not tb_id
                or sv_id not in current_versions
                or tv_id not in current_versions
                or current_versions[sv_id].tenant_id != current_versions[tv_id].tenant_id
                or current_versions[sv_id].project_id != current_versions[tv_id].project_id
            ):
                continue
            cand_block_ids.add(sb_id)
            cand_block_ids.add(tb_id)
            raw_block_cands.append((sb_id, tb_id, sv_id, tv_id, rel_type))

    if cand_block_ids:
        blk_ord_rows = (
            await session.execute(
                select(
                    CanonicalBlock.id,
                    CanonicalBlock.document_version_id,
                    CanonicalBlock.ordinal,
                ).where(CanonicalBlock.id.in_(sorted(cand_block_ids)))
            )
        ).all()
        ord_by_block = {
            (str(b_id), str(b_ver)): int(b_ord)
            for b_id, b_ver, b_ord in blk_ord_rows
        }
        for sb_id, tb_id, sv_id, tv_id, rel_type in raw_block_cands:
            s_ord_val = ord_by_block.get((sb_id, sv_id))
            t_ord_val = ord_by_block.get((tb_id, tv_id))
            if s_ord_val is None or t_ord_val is None:
                continue
            if rel_type == "CONTRADICTS":
                lineage_by_version_ordinal[(sv_id, s_ord_val)].append(
                    (tv_id, t_ord_val, t_ord_val, 0.92, "CONTRADICTS")
                )
                lineage_by_version_ordinal[(tv_id, t_ord_val)].append(
                    (sv_id, s_ord_val, s_ord_val, 0.92, "CONTRADICTS")
                )
            elif rel_type == "SUPERSEDES":
                lineage_by_version_ordinal[(tv_id, t_ord_val)].append(
                    (sv_id, s_ord_val, s_ord_val, 0.94, "SUPERSEDED_BY")
                )
                lineage_by_version_ordinal[(sv_id, s_ord_val)].append(
                    (tv_id, t_ord_val, t_ord_val, 0.88, "SUPERSEDES")
                )

    if kg_edge_ids:
        src_su = aliased(SearchUnit)
        tgt_su = aliased(SearchUnit)
        src_b_from = aliased(CanonicalBlock)
        src_b_to = aliased(CanonicalBlock)
        tgt_b_from = aliased(CanonicalBlock)
        tgt_b_to = aliased(CanonicalBlock)
        kg_rows = (
            await session.execute(
                select(
                    KnowledgeGraphEdge.edge_type,
                    src_su.document_version_id,
                    src_su.security_partition_id,
                    src_b_from.ordinal,
                    src_b_to.ordinal,
                    tgt_su.document_version_id,
                    tgt_su.security_partition_id,
                    tgt_b_from.ordinal,
                    tgt_b_to.ordinal,
                )
                .join(src_su, src_su.id == KnowledgeGraphEdge.source_unit_id)
                .join(tgt_su, tgt_su.id == KnowledgeGraphEdge.target_unit_id)
                .join(src_b_from, src_b_from.id == src_su.block_from_id)
                .join(src_b_to, src_b_to.id == src_su.block_to_id)
                .join(tgt_b_from, tgt_b_from.id == tgt_su.block_from_id)
                .join(tgt_b_to, tgt_b_to.id == tgt_su.block_to_id)
                .where(
                    KnowledgeGraphEdge.id.in_(kg_edge_ids),
                    src_su.document_version_id.in_(version_ids),
                    tgt_su.document_version_id.in_(version_ids),
                    src_su.security_partition_id.in_(allowed_partitions),
                    tgt_su.security_partition_id.in_(allowed_partitions),
                )
            )
        ).all()
        for (
            edge_type_val,
            sv_raw,
            s_part,
            s_from_ord,
            s_to_ord,
            tv_raw,
            t_part,
            t_from_ord,
            t_to_ord,
        ) in kg_rows:
            sv_id, tv_id = str(sv_raw), str(tv_raw)
            if (
                sv_id not in current_versions
                or tv_id not in current_versions
                or current_versions[sv_id].partition_id != str(s_part)
                or current_versions[tv_id].partition_id != str(t_part)
                or current_versions[sv_id].tenant_id != current_versions[tv_id].tenant_id
                or current_versions[sv_id].project_id != current_versions[tv_id].project_id
            ):
                continue
            ss_lo, ss_hi = min(int(s_from_ord), int(s_to_ord)), max(int(s_from_ord), int(s_to_ord))
            tt_lo, tt_hi = min(int(t_from_ord), int(t_to_ord)), max(int(t_from_ord), int(t_to_ord))
            e_type = str(edge_type_val)
            if e_type == "CONTRADICTS":
                for o_val in range(ss_lo, ss_hi + 1):
                    lineage_by_version_ordinal[(sv_id, o_val)].append(
                        (tv_id, tt_lo, tt_hi, 0.92, "CONTRADICTS")
                    )
                for o_val in range(tt_lo, tt_hi + 1):
                    lineage_by_version_ordinal[(tv_id, o_val)].append(
                        (sv_id, ss_lo, ss_hi, 0.92, "CONTRADICTS")
                    )
            elif e_type == "SUPERSEDES":
                for o_val in range(tt_lo, tt_hi + 1):
                    lineage_by_version_ordinal[(tv_id, o_val)].append(
                        (sv_id, ss_lo, ss_hi, 0.94, "SUPERSEDED_BY")
                    )
                for o_val in range(ss_lo, ss_hi + 1):
                    lineage_by_version_ordinal[(sv_id, o_val)].append(
                        (tv_id, tt_lo, tt_hi, 0.88, "SUPERSEDES")
                    )

    index = _ScopeKnowledgeIndex(
        ku_by_version=dict(ku_by_version),
        ku_by_id=ku_by_id,
        edges_by_ku=dict(edges_by_ku),
        ent_exact_versions=dict(ent_exact_versions),
        hydrated_su_by_version={},
        lineage_by_version_ordinal=dict(lineage_by_version_ordinal),
    )
    if len(_SCOPE_KNOWLEDGE_INDEX_CACHE) >= _SCOPE_KNOWLEDGE_INDEX_CACHE_MAX:
        _SCOPE_KNOWLEDGE_INDEX_CACHE.pop(next(iter(_SCOPE_KNOWLEDGE_INDEX_CACHE)))
    _SCOPE_KNOWLEDGE_INDEX_CACHE[digest] = index
    return index


async def _ensure_versions_hydrated(
    session: AsyncSession,
    k_index: _ScopeKnowledgeIndex,
    version_ids: list[str],
    current_versions: dict[str, _ReadyVersion],
    sources: list[SearchableSource],
) -> None:
    unhydrated_version_ids = [
        v_id for v_id in version_ids if v_id in current_versions and v_id not in k_index.hydrated_su_by_version
    ]
    if not unhydrated_version_ids:
        return
    start_block = aliased(CanonicalBlock)
    end_block = aliased(CanonicalBlock)
    su_rows = (
        await session.execute(
            select(
                SearchUnit,
                Document.id,
                Document.filename,
                Document.source_id,
                DocumentVersion.version_no,
                start_block.source_anchor,
                start_block.ordinal,
                end_block.ordinal,
                Document.logical_source_id,
            )
            .join(DocumentVersion, DocumentVersion.id == SearchUnit.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .join(start_block, start_block.id == SearchUnit.block_from_id)
            .join(end_block, end_block.id == SearchUnit.block_to_id)
            .where(
                SearchUnit.document_version_id.in_(unhydrated_version_ids),
                start_block.document_version_id == SearchUnit.document_version_id,
                end_block.document_version_id == SearchUnit.document_version_id,
                Document.source_id.in_([source.id for source in sources]),
                Document.is_active.is_(True),
            )
        )
    ).all()
    blocks_by_version: dict[str, list[tuple[int, str]]] = defaultdict(list)
    if su_rows:
        blk_rows = (
            await session.execute(
                select(
                    CanonicalBlock.document_version_id,
                    CanonicalBlock.ordinal,
                    CanonicalBlock.normalized_text,
                )
                .where(CanonicalBlock.document_version_id.in_(unhydrated_version_ids))
                .order_by(CanonicalBlock.document_version_id, CanonicalBlock.ordinal)
            )
        ).all()
        for b_ver_id, b_ord, b_text in blk_rows:
            blocks_by_version[str(b_ver_id)].append((int(b_ord), str(b_text or "")))

    for v_id in unhydrated_version_ids:
        k_index.hydrated_su_by_version[v_id] = []
    for raw_su_row in su_rows:
        row_tuple = tuple(raw_su_row)
        unit, doc_id, filename, source_id, version_no, anchor, s_ord, e_ord = row_tuple[:8]
        logical_source_id = row_tuple[8] if len(row_tuple) > 8 else None
        v_id_str = str(unit.document_version_id)
        ver = current_versions.get(v_id_str)
        if (
            ver is None
            or ver.source_id != str(source_id)
            or ver.partition_id != str(unit.security_partition_id)
            or s_ord > e_ord
            or int(unit.page_from or 0) < 1
            or int(unit.page_to or 0) < int(unit.page_from or 0)
            or not isinstance(anchor, str)
            or not anchor.strip()
        ):
            continue
        unit_texts = [
            b_text
            for b_ord, b_text in blocks_by_version.get(v_id_str, ())
            if int(s_ord) <= b_ord <= int(e_ord)
        ]
        combined_text = "\n\n".join(unit_texts)
        if (
            not combined_text.strip()
            or hashlib.sha256(combined_text.encode("utf-8")).hexdigest() != unit.content_hash
        ):
            continue
        sec_template = RetrievedSection(
            chunk_id=unit.id,
            search_unit_id=unit.id,
            block_from_id=unit.block_from_id,
            block_to_id=unit.block_to_id,
            section_path=unit.section_path,
            heading=unit.section_path or str(filename or ""),
            content=combined_text,
            score=0.0,
            rank=0,
            source_id=ver.source.id,
            source_config_id=ver.source.sag_source_config_id,
            content_hash=unit.content_hash,
            canonical_evidence_verified=True,
            project_id=ver.project_id,
            document_id=str(doc_id),
            public_document_id=_resolve_public_document_id(logical_source_id),
            document_version_id=v_id_str,
            document_name=str(filename or ""),
            version_no=int(version_no),
            page_from=int(unit.page_from),
            page_to=int(unit.page_to),
            anchor=anchor.strip(),
        )
        k_index.hydrated_su_by_version[v_id_str].append((sec_template, int(s_ord), int(e_ord)))


async def _hydrate_sibling_sections_for_top_versions(
    *,
    sections: list[RetrievedSection],
    current_versions: dict[str, _ReadyVersion],
    sources: list[SearchableSource],
    limit: int,
    index_holder: list[_ScopeKnowledgeIndex] | None = None,
) -> list[RetrievedSection]:
    """Hydrate sibling SearchUnits of top admitted documents so context completion stays coherent."""
    if not sections or not current_versions:
        return []
    top_version_scores: dict[str, float] = {}
    for sec in sections:
        v_id = str(sec.document_version_id or "")
        if not v_id or v_id not in current_versions:
            continue
        if v_id not in top_version_scores:
            if len(top_version_scores) >= limit:
                continue
            top_version_scores[v_id] = float(sec.score or 0.0)
    if not top_version_scores:
        return []

    existing_ids = {str(s.search_unit_id or s.chunk_id or "") for s in sections}
    allowed_partitions = sorted({v.partition_id for v in current_versions.values()})
    try:
        async with SessionLocal() as session:
            k_index = await _get_or_build_scope_knowledge_index(
                session,
                current_versions,
                allowed_partitions,
            )
            if index_holder is not None:
                index_holder.append(k_index)
            await _ensure_versions_hydrated(
                session,
                k_index,
                list(top_version_scores),
                current_versions,
                sources,
            )
            siblings: list[RetrievedSection] = []
            for v_id, parent_score in top_version_scores.items():
                for idx_su, (sec_template, _s_ord, _e_ord) in enumerate(
                    k_index.hydrated_su_by_version.get(v_id, ())
                ):
                    if len(siblings) >= limit:
                        return siblings
                    u_id = str(sec_template.search_unit_id or sec_template.chunk_id or "")
                    if not u_id or u_id in existing_ids:
                        continue
                    existing_ids.add(u_id)
                    decay = max(0.55, 0.72 - 0.03 * idx_su)
                    siblings.append(
                        sec_template.model_copy(
                            update={
                                "score": round(parent_score * decay, 6),
                                "rank": len(sections) + len(siblings),
                            }
                        )
                    )
            return siblings
    except Exception:  # noqa: BLE001 - sibling hydration is best-effort
        return []


_CONTINUOUS_ENTITY_CHAR_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\u0e00-\u0eff\u1780-\u17ff\u1000-\u109f]"
)
_CONFLICT_LINEAGE_CUE_RE = re.compile(
    r"(?<!\w)(?:contradict\w*|conflict\w*|discrepanc\w*|mismatch\w*|inconsisten\w*|"
    r"differ\w*|versus|vs\.?|compare|comparison|reconcil\w*|"
    r"mâu\s+thuẫn|xung\s+đột|khác\s+nhau|sai\s+lệch|đối\s+chiếu|so\s+sánh)(?!\w)",
    flags=re.IGNORECASE | re.UNICODE,
)


def _is_cjk_or_continuous_entity(text: str) -> bool:
    return bool(_CONTINUOUS_ENTITY_CHAR_RE.search(text))


async def _expand_multi_hop_sections(
    *,
    seed_sections: list[RetrievedSection],
    unit_ordinals: dict[str, tuple[int, int]],
    current_versions: dict[str, _ReadyVersion],
    existing_unit_ids: set[str],
    sources: list[SearchableSource],
    existing_pool_scores: dict[str, float] | None = None,
    limit: int,
    lineage_only: bool = False,
    query: str = "",
    allow_contradiction_lineage: bool = True,
    preloaded_k_index: _ScopeKnowledgeIndex | None = None,
) -> list[RetrievedSection]:
    """HippoRAG 2 associative passage expansion over ACL-scoped KnowledgeUnitEdge + Artifact Bridges + Stage 2B Lineage."""
    if not seed_sections or not current_versions:
        return []
    if lineage_only and preloaded_k_index is not None and not preloaded_k_index.lineage_by_version_ordinal:
        return []
    pool_scores = existing_pool_scores or {}
    seed_ranges: list[tuple[str, int, int, float]] = []
    ordered_seeds = sorted(seed_sections, key=lambda s: -float(s.score or 0.0))
    top_anchor_score = max((float(s.score or 0.5) for s in ordered_seeds[:1]), default=0.5)
    for idx_s, section in enumerate(ordered_seeds[:limit]):
        s_val = max(0.05, float(section.score or 0.5))
        if idx_s == 1 and s_val < 0.82 * top_anchor_score:
            continue
        if idx_s >= 2 and s_val < 0.90 * top_anchor_score:
            continue
        unit_id = str(section.search_unit_id or section.chunk_id or "")
        version_id = str(section.document_version_id or "")
        ordinals = unit_ordinals.get(unit_id)
        if not unit_id or not version_id or ordinals is None or version_id not in current_versions:
            continue
        seed_ranges.append((version_id, ordinals[0], ordinals[1], s_val))
    if not seed_ranges:
        return []

    seed_version_ids = {str(item[0]) for item in seed_ranges}
    allowed_partitions = sorted({v.partition_id for v in current_versions.values()})
    query_folded = re.sub(r"[\s_-]+", " ", (query or "").lower()).strip()
    query_single_words = set(re.findall(r"[^\W_]+", query_folded, flags=re.UNICODE))
    allowed_lineage_kinds = (
        {"SUPERSEDED_BY", "CONTRADICTS"}
        if (not lineage_only or allow_contradiction_lineage or bool(_CONFLICT_LINEAGE_CUE_RE.search(query or "")))
        else {"SUPERSEDED_BY"}
    )
    try:
        k_index = preloaded_k_index
        if k_index is None:
            async with SessionLocal() as session:
                k_index = await _get_or_build_scope_knowledge_index(
                    session,
                    current_versions,
                    allowed_partitions,
                )
        valid_neighbors: dict[str, list[tuple[int, float]]] = defaultdict(list)

        # 1. Stage 2B / KnowledgeGraphEdge Lineage Expansion (CONTRADICTS & SUPERSEDES)
        if k_index.lineage_by_version_ordinal:
            for v_id, s_ord, e_ord, s_score in seed_ranges:
                for ord_cursor in range(s_ord, e_ord + 1):
                    for tgt_ver, t_lo, t_hi, rel_weight, rel_kind in k_index.lineage_by_version_ordinal.get(
                        (v_id, ord_cursor), ()
                    ):
                        if lineage_only and rel_kind not in allowed_lineage_kinds:
                            continue
                        if tgt_ver not in current_versions:
                            continue
                        if tgt_ver == v_id and not (t_hi < s_ord or t_lo > e_ord):
                            continue
                        lin_score = s_score * rel_weight
                        for t_ord in range(t_lo, t_hi + 1):
                            valid_neighbors[tgt_ver].append((t_ord, lin_score))

        if not lineage_only:
            seed_ku_scores: dict[str, float] = {}
            top_seed_entities: dict[str, float] = {}
            max_seed_score = max((item[3] for item in seed_ranges), default=0.5)
            for idx_r, (v_id, s_ord, e_ord, s_score) in enumerate(seed_ranges):
                for ku in k_index.ku_by_version.get(v_id, ()):
                    if s_ord <= ku.ordinal <= e_ord:
                        seed_ku_scores[ku.id] = max(seed_ku_scores.get(ku.id, 0.0), s_score)
                        if idx_r < 2 or s_score >= 0.85 * max_seed_score:
                            for ent in ku.entities_raw:
                                ent_clean = re.sub(r"[\s_-]+", " ", ent.lower()).strip()
                                is_cont = _is_cjk_or_continuous_entity(ent_clean)
                                if len(ent_clean) < (2 if is_cont else 3):
                                    continue
                                if re.fullmatch(r"\d+(?:\.\d+)?%?(?:\s*[a-z]{1,6})?", ent_clean):
                                    continue
                                # Single non-digit words already in the user query are direct lexical terms, not Hop-1->Hop-2 bridges
                                if (
                                    not is_cont
                                    and " " not in ent_clean
                                    and not any(c.isdigit() for c in ent_clean)
                                    and ent_clean in query_single_words
                                ):
                                    continue
                                if (
                                    not is_cont
                                    and " " not in ent_clean
                                    and not any(c.isdigit() for c in ent_clean)
                                    and not any(c.isupper() for c in ent[1:])
                                    and len(ent_clean) < 6
                                ):
                                    continue
                                top_seed_entities[ent_clean] = max(
                                    top_seed_entities.get(ent_clean, 0.0), s_score
                                )
                    elif idx_r == 0:
                        # Harvest explicit cross-reference codes and URI slugs from sibling sections of the #1 seed doc
                        doc_ref_score = s_score * 0.88
                        for ent_norm in ku.entities_norm:
                            if _ARTIFACT_CODE_RE.fullmatch(ent_norm) or (
                                ent_norm.count(" ") >= 2 and len(ent_norm) >= 12
                            ):
                                top_seed_entities[ent_norm] = max(
                                    top_seed_entities.get(ent_norm, 0.0), doc_ref_score
                                )
            if seed_ku_scores:
                neighbor_scores: dict[str, float] = {}
                seed_edges = sorted(
                    (
                        (s_score, tgt_id, w)
                        for s_ku_id, s_score in seed_ku_scores.items()
                        for tgt_id, w in k_index.edges_by_ku.get(s_ku_id, ())
                        if tgt_id not in seed_ku_scores
                    ),
                    key=lambda item: -item[2],
                )[:limit]
                for s_score, tgt_id, w in seed_edges:
                    tgt_ku = k_index.ku_by_id.get(tgt_id)
                    if tgt_ku is None or tgt_ku.document_version_id in seed_version_ids:
                        continue
                    neighbor_scores[tgt_id] = max(neighbor_scores.get(tgt_id, 0.0), s_score * w)

                # HippoRAG 2 Bridge Entity Activation using pre-indexed scope knowledge units
                if top_seed_entities:
                    ent_doc_versions: dict[str, set[str]] = defaultdict(set)
                    cont_entities = {
                        b_ent for b_ent in top_seed_entities if _is_cjk_or_continuous_entity(b_ent)
                    }
                    path_patterns: dict[str, re.Pattern[str]] = {
                        b_ent: re.compile(rf"\b{re.escape(b_ent)}\b")
                        for b_ent in top_seed_entities
                        if b_ent not in cont_entities and len(b_ent) >= 5
                    }
                    for v_id, ku_list in k_index.ku_by_version.items():
                        if v_id in seed_version_ids:
                            continue
                        for ku in ku_list:
                            for b_ent in top_seed_entities:
                                is_cont = b_ent in cont_entities
                                if (
                                    is_cont or " " in b_ent or any(c.isdigit() for c in b_ent) or len(b_ent) >= 6
                                ) and b_ent in ku.entities_norm:
                                    ent_doc_versions[b_ent].add(v_id)
                                elif is_cont and len(b_ent) >= 2 and b_ent in ku.sec_path_norm:
                                    ent_doc_versions[b_ent].add(v_id)
                                else:
                                    pat = path_patterns.get(b_ent)
                                    if (
                                        pat is not None
                                        and b_ent in ku.sec_path_norm
                                        and pat.search(ku.sec_path_norm)
                                    ):
                                        ent_doc_versions[b_ent].add(v_id)

                    max_bridge_df = max(3, min(10, math.ceil(0.08 * max(1, len(current_versions)))))
                    specific_seed_entities = {
                        b_ent: b_score
                        for b_ent, b_score in top_seed_entities.items()
                        if 1
                        <= len(ent_doc_versions.get(b_ent, ()))
                        <= (
                            max_bridge_df
                            if (b_ent in cont_entities or " " in b_ent or any(c.isdigit() for c in b_ent))
                            else 1
                        )
                    }
                    if specific_seed_entities:
                        candidate_ver_ids = {
                            v_id
                            for b_ent in specific_seed_entities
                            for v_id in ent_doc_versions.get(b_ent, ())
                            if v_id not in seed_version_ids
                        }
                        for v_id in candidate_ver_ids:
                            for ku in k_index.ku_by_version.get(v_id, ()):
                                if ku.id in seed_ku_scores:
                                    continue
                                matched_scores: list[float] = []
                                path_matched = False
                                for b_ent, b_score in specific_seed_entities.items():
                                    is_cont = b_ent in cont_entities
                                    is_code = _ARTIFACT_CODE_RE.fullmatch(b_ent) is not None
                                    ent_hit = (
                                        (is_cont or " " in b_ent or any(c.isdigit() for c in b_ent) or len(b_ent) >= 6)
                                        and b_ent in ku.entities_norm
                                    )
                                    if is_cont:
                                        path_hit = len(b_ent) >= 2 and b_ent in ku.sec_path_norm
                                    else:
                                        pat = path_patterns.get(b_ent)
                                        path_hit = (
                                            pat is not None
                                            and b_ent in ku.sec_path_norm
                                            and bool(pat.search(ku.sec_path_norm))
                                        )
                                    if is_code and (ent_hit or path_hit):
                                        path_matched = True
                                    elif (
                                        path_hit
                                        and (is_cont or " " in b_ent or any(c.isdigit() for c in b_ent))
                                        and (not query_folded or b_ent not in query_folded)
                                    ):
                                        path_matched = True
                                    if ent_hit or path_hit:
                                        matched_scores.append(b_score)
                                if matched_scores:
                                    best_b = max(matched_scores)
                                    mult = (
                                        0.88
                                        if path_matched
                                        else min(0.85, 0.72 + 0.04 * (len(matched_scores) - 1))
                                    )
                                    neighbor_scores[ku.id] = max(neighbor_scores.get(ku.id, 0.0), best_b * mult)

                # Bounded 2-hop propagation over canonical artifact codes (e.g. Confluence -> PR-19422 -> SUP-18510)
                if neighbor_scores:
                    hop2_artifact_entities: dict[str, float] = {}
                    for ku_id, n_score in list(neighbor_scores.items()):
                        if n_score < 0.65 * max_seed_score:
                            continue
                        ku = k_index.ku_by_id.get(ku_id)
                        if ku is None:
                            continue
                        for ent_norm in ku.entities_norm:
                            if _ARTIFACT_CODE_RE.fullmatch(ent_norm) and ent_norm not in top_seed_entities:
                                hop2_artifact_entities[ent_norm] = max(
                                    hop2_artifact_entities.get(ent_norm, 0.0), n_score
                                )
                    for art_code, h1_score in hop2_artifact_entities.items():
                        target_vers = k_index.ent_exact_versions.get(art_code, set()) - seed_version_ids
                        if 1 <= len(target_vers) <= 3:
                            for v_id in target_vers:
                                for ku in k_index.ku_by_version.get(v_id, ()):
                                    if art_code in ku.entities_norm or art_code in ku.sec_path_norm:
                                        neighbor_scores[ku.id] = max(
                                            neighbor_scores.get(ku.id, 0.0),
                                            h1_score * 0.92,
                                        )

                for ku_id, n_score in neighbor_scores.items():
                    ku = k_index.ku_by_id.get(ku_id)
                    if ku is None or ku.document_version_id in seed_version_ids:
                        continue
                    valid_neighbors[ku.document_version_id].append((ku.ordinal, n_score))

        if not valid_neighbors:
            return []

        neighbor_version_ids = sorted(valid_neighbors)
        if any(v not in k_index.hydrated_su_by_version for v in neighbor_version_ids):
            async with SessionLocal() as session:
                await _ensure_versions_hydrated(
                    session,
                    k_index,
                    neighbor_version_ids,
                    current_versions,
                    sources,
                )

        per_version_cap = max(2, limit // 4)
        expanded: list[RetrievedSection] = []
        for v_id_str in neighbor_version_ids:
            ord_scores = valid_neighbors.get(v_id_str, ())
            ver_candidates: list[RetrievedSection] = []
            for sec_template, s_ord, e_ord in k_index.hydrated_su_by_version.get(v_id_str, ()):
                if sec_template.search_unit_id in existing_unit_ids:
                    continue
                best_score = max(
                    (score for ku_ord, score in ord_scores if s_ord <= ku_ord <= e_ord),
                    default=0.0,
                )
                if best_score <= 0.0:
                    continue
                ver_candidates.append(
                    sec_template.model_copy(
                        update={
                            "score": round(best_score, 4),
                            "rank": len(seed_sections) + len(expanded),
                        }
                    )
                )
            ver_candidates.sort(
                key=lambda s: (
                    -s.score,
                    -pool_scores.get(str(s.search_unit_id or s.chunk_id or ""), 0.0),
                    s.chunk_id or "",
                )
            )
            expanded.extend(ver_candidates[:per_version_cap])
        expanded.sort(
            key=lambda s: (
                -s.score,
                -pool_scores.get(str(s.search_unit_id or s.chunk_id or ""), 0.0),
                s.chunk_id or "",
            )
        )
        return expanded[:limit]
    except Exception as error:  # noqa: BLE001 - associative expansion is best-effort within ACL bounds
        log.warning("multi-hop knowledge graph expansion skipped error_type=%s", type(error).__name__)
        return []


def _empty_routing_trace(strategy_plan: QueryStrategyPlan, reason: str) -> dict[str, Any]:
    return {
        "planner": strategy_plan.as_trace(),
        "request_snapshot_id": None,
        "groups": [],
        "coverage": {"status": "unknown", "reason": reason},
        "multi_hop": {
            "eligible": strategy_plan.multi_hop_eligible,
            "effective": strategy_plan.primary_strategy.value,
            "graph_expansion_attempted": False,
            "graph_expansion_skip_reason": "phase5_adapter_unavailable" if strategy_plan.multi_hop_eligible else None,
        },
        "fallback_used": True,
    }


async def retrieve_search_unit_sections(
    engine_manager: Any,
    sources: list[SearchableSource],
    query: str,
    *,
    principal: VerifiedPrincipal | None,
    top_k: int | None = None,
    query_strategy_plan: QueryStrategyPlan | None = None,
) -> SearchOutcome:
    """Return verified sections according to the 7-axis RetrievalPlan, with a bounded candidate pool."""
    import unicodedata

    query = unicodedata.normalize("NFC", query or "")
    if top_k is not None and top_k < 1:
        raise ValueError("top_k must be positive")
    plan = query_strategy_plan or plan_query(query, requested_top_k=top_k)
    if top_k is not None and plan.retrieval_plan.cardinality.requested_n != top_k:
        from dataclasses import replace

        from sag_api.services.retrieval_plan import build_retrieval_plan

        plan = replace(
            plan,
            retrieval_plan=build_retrieval_plan(
                query,
                requested_top_k=top_k,
                strategy_mode=plan.primary_strategy.value,
                clauses=plan.retrieval_plan.decomposition.subqueries,
                multi_hop=plan.multi_hop_eligible,
                exact_lookup=plan.primary_strategy.value == "EXACT",
                constraints=plan.retrieval_plan.constraints,
            ),
        )
    effective_top_k = plan.retrieval_plan.cardinality.effective_n
    budget = max(1, settings.search_unit_candidate_limit)
    seed = max(settings.search_top_k, effective_top_k)
    candidate_limit = min(budget, max(seed * 4, seed + 1))
    started = time.perf_counter()
    deadline = asyncio.get_running_loop().time() + settings.search_source_timeout
    timings: dict[str, float] = defaultdict(float)
    waves = 0
    while True:
        wave_start = time.perf_counter()
        outcome = await _retrieve_search_unit_sections(
            engine_manager, sources, query, principal=principal, top_k=effective_top_k,
            query_strategy_plan=plan, candidate_limit=candidate_limit, deadline=deadline,
        )
        waves += 1
        for key, value in outcome.stats.get("stage_latencies_ms", {}).items():
            timings[key] += value
        saturated = bool(outcome.stats.get("candidate_pool_saturated"))
        hydration_truncated = bool(outcome.stats.get("candidate_units_truncated"))
        coverage = outcome.stats.get("coverage") or {}
        unique_candidates = (
            int(coverage.get("candidate_count", 0)) - int(coverage.get("deduplicated_count", 0))
            if isinstance(coverage, dict) and "candidate_count" in coverage and "deduplicated_count" in coverage
            else len(outcome.sections)
        )
        anchor_cov = coverage.get("anchor_coverage") if isinstance(coverage, dict) else None
        precision_pruned = (
            bool(outcome.sections)
            and unique_candidates >= effective_top_k
            and anchor_cov in ("complete", "not_required")
        )
        needs_more = len(outcome.sections) < effective_top_k and not precision_pruned
        remaining = deadline - asyncio.get_running_loop().time()
        stop = (
            "deadline" if remaining <= 0 else
            "candidate_budget" if hydration_truncated else
            "candidate_pool_exhausted" if not saturated else
            "requested_top_k_reached" if not needs_more else
            "candidate_budget" if candidate_limit >= budget else
            "deadline" if remaining <= 2 * (time.perf_counter() - wave_start) else None
        )
        if stop:
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            timings["total_retrieval"] = elapsed
            return SearchOutcome(query=outcome.query, sections=outcome.sections, stats={
                **outcome.stats,
                "automatic_top_k": top_k is None and plan.retrieval_plan.cardinality.count_source.value != "DEFAULT",
                "requested_top_k": effective_top_k,
                "effective_top_k": len(outcome.sections),
                "retrieval_plan": plan.retrieval_plan.as_trace(),
                "adaptive_waves": waves,
                "retrieval_stop_reason": stop, "budget_exhausted": stop in {"candidate_budget", "deadline"},
                "has_more": False,
                "stage_latencies_ms": dict(timings), "latency_total_ms": elapsed,
            })
        candidate_limit = min(budget, candidate_limit * 2)


async def _retrieve_search_unit_sections(
    engine_manager: Any,
    sources: list[SearchableSource],
    query: str,
    *,
    principal: VerifiedPrincipal | None,
    top_k: int | None = None,
    query_strategy_plan: QueryStrategyPlan | None = None,
    candidate_limit: int,
    deadline: float,
) -> SearchOutcome:
    """Query only currently authorized versions with a verified Phase 2C manifest."""
    import unicodedata

    from sag_api.services.retrieval_service import rerank_sections

    query = unicodedata.normalize("NFC", query or "")
    requested_limit = min(top_k, settings.search_unit_candidate_limit) if top_k is not None else candidate_limit
    selection_limit = requested_limit if top_k is not None else None
    strategy_plan = query_strategy_plan or plan_query(query, requested_top_k=top_k)
    per_doc_limit = strategy_plan.retrieval_plan.per_document_limit
    retrieval_stats = {
        "requested_top_k": requested_limit,
        "candidate_top_k": candidate_limit,
        "candidate_unit_pool_limit": settings.search_unit_candidate_limit,
    }
    t_start = time.perf_counter()
    t_auth_start = time.perf_counter()
    if principal is None or not sources or not principal.tenant_id or not principal.allowed_partition_ids:
        total_ms = round((time.perf_counter() - t_start) * 1000, 2)
        return SearchOutcome(
            query=query,
            sections=[],
            stats={
                "canonical_index": True,
                "ready_versions": 0,
                **retrieval_stats,
                "routing": _empty_routing_trace(strategy_plan, "no_authorized_scope"),
                "stage_latencies_ms": {
                    "auth_scope": total_ms,
                    "tree_route": 0.0,
                    "candidate_retrieval": 0.0,
                    "rank_fusion": 0.0,
                    "diversity_selection": 0.0,
                    "total_retrieval": total_ms,
                },
                "latency_total_ms": total_ms,
            },
        )

    loop = asyncio.get_running_loop()
    try:
        async with asyncio.timeout_at(deadline):
            async with SessionLocal() as session:
                ready_versions = await _load_current_ready_versions(session, sources, principal)
    except TimeoutError as error:
        log.warning("canonical search authorization exceeded query budget")
        raise ServiceUnavailableError("Canonical search exceeded the query budget") from error
    t_auth_ms = round((time.perf_counter() - t_auth_start) * 1000, 2)
    groups = _groups(ready_versions)
    if not groups:
        total_ms = round((time.perf_counter() - t_start) * 1000, 2)
        return SearchOutcome(
            query=query,
            sections=[],
            stats={
                "canonical_index": True,
                "ready_versions": 0,
                **retrieval_stats,
                "routing": _empty_routing_trace(strategy_plan, "no_verified_search_ready_group"),
                "stage_latencies_ms": {
                    "auth_scope": t_auth_ms,
                    "tree_route": 0.0,
                    "candidate_retrieval": 0.0,
                    "rank_fusion": 0.0,
                    "diversity_selection": 0.0,
                    "total_retrieval": total_ms,
                },
                "latency_total_ms": total_ms,
            },
        )

    t_route_ms = 0.0
    t_retrieval_ms = 0.0

    decisions: dict[tuple[str, str, str, str], RouteDecision] = {}
    local_failures: dict[tuple[str, str, str, str], str] = {}
    escape_failures: dict[tuple[str, str, str, str], str] = {}
    vector_tasks: dict[str, asyncio.Task[list[float]]] = {}
    try:
        async with asyncio.timeout_at(deadline):
            client = await engine_manager.get_search_unit_qdrant_client()
            store = SearchUnitQdrantStore(client)
            semaphore = asyncio.Semaphore(max(1, settings.search_source_concurrency))
            from sag_api.services.tree_query_snapshot_service import _MANIFEST_NODE_FEATURES_CACHE

            base_snapshot_timeout = float(settings.search_tree_snapshot_timeout)
            if not _MANIFEST_NODE_FEATURES_CACHE:
                base_snapshot_timeout = max(base_snapshot_timeout, 4.5)
            snapshot_budget = min(
                base_snapshot_timeout,
                max(
                    0.05,
                    deadline - loop.time() - float(settings.search_tree_escape_reserve_seconds),
                ),
            )
            t_route_start = time.perf_counter()
            scope_vector_tasks: dict[str, asyncio.Task[list[float]]] = {}
            for group in groups:
                config_key = group.source.sag_source_config_id or group.source.id
                if config_key not in vector_tasks:
                    vector_tasks[config_key] = asyncio.create_task(
                        _query_vector(engine_manager, group, query)
                    )
                scope_vector_tasks[scope_fingerprint(routing_scope_for_group(group))] = vector_tasks[
                    config_key
                ]
            raw_subqueries = strategy_plan.retrieval_plan.decomposition.subqueries
            sub_sparse_queries: tuple[dict[str, list[int] | list[float]], ...] = (
                tuple(
                    sq_vec
                    for sq in raw_subqueries[:3]
                    if sq.strip() and sq.strip() != query.strip() and (sq_vec := build_sparse_query_vector(sq))["indices"]
                )
                if len(raw_subqueries) >= 2
                else ()
            )
            decisions = await capture_routing_decisions(
                engine_manager,
                groups,
                query=query,
                planner_trace=strategy_plan.as_trace(),
                principal=principal,
                timeout_seconds=snapshot_budget,
                query_vector_tasks_by_scope=scope_vector_tasks,
            )
            t_route_ms = round((time.perf_counter() - t_route_start) * 1000, 2)

            async def bounded(group: _SearchGroup):
                async with semaphore:
                    route = decisions.get(group.scope_key)
                    if (
                        route is not None
                        and route.routed
                        and route.acl_scope_fingerprint
                        != scope_fingerprint(routing_scope_for_group(group))
                    ):
                        route = route.model_copy(
                            update={
                                "selected_nodes": (),
                                "membership_node_ids": (),
                                "fallback_reason": "acl_scope_snapshot_mismatch",
                                "reason_code": "ROUTING_ACL_SCOPE_MISMATCH",
                            }
                        )
                    config_key = group.source.sag_source_config_id or group.source.id
                    vector = await vector_tasks[config_key]
                    if route is None or not route.routed:
                        result = await _query_group(
                            group,
                            query=query,
                            limit=candidate_limit,
                            store=store,
                            query_vector=vector,
                            sub_sparse_queries=sub_sparse_queries,
                        )
                        return [(group, route, "global_only", result[1], result[2])], None, None

                    # Each bounded path gets the same candidate cap. The tree path has a
                    # shorter deadline so it cannot consume the reserved global-escape time.
                    now = loop.time()
                    remaining = max(0.1, deadline - now)
                    local_reserve = min(
                        float(settings.search_tree_escape_reserve_seconds),
                        remaining / 2,
                    )
                    local_deadline = deadline - local_reserve
                    escape_deadline = deadline - 0.05
                    confident_local = (
                        not route.broad_route
                        and route.reason_code == "ROUTE_DESCENDED"
                        and float(route.margin or 0.0) >= 0.22
                        and not strategy_plan.multi_hop_eligible
                        and strategy_plan.primary_strategy.value not in {"MULTI_HOP", "GLOBAL_SUMMARY"}
                        and strategy_plan.retrieval_plan.target.value != "DOCUMENT"
                    )
                    eff_escape_limit = max(8, candidate_limit // 2) if confident_local else candidate_limit

                    if hasattr(store, "search_batch"):
                        try:
                            async with asyncio.timeout_at(local_deadline):
                                local_sem, local_lex, esc_sem, esc_lex = await _query_routed_group_batch(
                                    group,
                                    query=query,
                                    limit=candidate_limit,
                                    store=store,
                                    query_vector=vector,
                                    route=route,
                                    escape_limit=eff_escape_limit,
                                    sub_sparse_queries=sub_sparse_queries,
                                )
                            return (
                                [
                                    (group, route, "branch_local", local_sem, local_lex),
                                    (group, route, "global_escape", esc_sem, esc_lex),
                                ],
                                None,
                                None,
                            )
                        except asyncio.CancelledError:
                            raise
                        except BaseException as batch_error:
                            local_failure = _failure_code(batch_error)
                            async with asyncio.timeout_at(escape_deadline):
                                esc_res = await _query_group(
                                    group,
                                    query=query,
                                    limit=candidate_limit,
                                    store=store,
                                    query_vector=vector,
                                    sub_sparse_queries=sub_sparse_queries,
                                )
                            return [(group, route, "global_escape", esc_res[1], esc_res[2])], local_failure, None

                    async def query_local():
                        async with asyncio.timeout_at(local_deadline):
                            return await _query_group(
                                group,
                                query=query,
                                limit=candidate_limit,
                                store=store,
                                query_vector=vector,
                                route=route,
                            )

                    async def query_escape():
                        async with asyncio.timeout_at(escape_deadline):
                            return await _query_group(
                                group,
                                query=query,
                                limit=candidate_limit,
                                store=store,
                                query_vector=vector,
                            )

                    escape_task = asyncio.create_task(query_escape())
                    local_task = asyncio.create_task(query_local())
                    local_result, escape_result = await asyncio.gather(local_task, escape_task, return_exceptions=True)
                    local_failure = _failure_code(local_result) if isinstance(local_result, BaseException) else None
                    escape_failure = _failure_code(escape_result) if isinstance(escape_result, BaseException) else None
                    if isinstance(local_result, asyncio.CancelledError):
                        raise local_result
                    if isinstance(escape_result, asyncio.CancelledError):
                        raise escape_result
                    if isinstance(local_result, BaseException) and isinstance(escape_result, BaseException):
                        raise escape_result
                    path_results = []
                    if isinstance(local_result, BaseException):
                        local_result = None
                    else:
                        path_results.append((group, route, "branch_local", local_result[1], local_result[2]))
                    if isinstance(escape_result, BaseException):
                        escape_result = None
                    else:
                        path_results.append((group, route, "global_escape", escape_result[1], escape_result[2]))
                    return path_results, local_failure, escape_failure

            t_retrieval_start = time.perf_counter()
            group_tasks = [asyncio.create_task(bounded(group)) for group in groups]
            try:
                path_groups = await asyncio.gather(*group_tasks)
            except BaseException:
                for task in group_tasks:
                    task.cancel()
                await asyncio.gather(*group_tasks, return_exceptions=True)
                raise
            channel_results = [item for paths, _local_error, _escape_error in path_groups for item in paths]
            if not any(item[3] or item[4] for item in channel_results):
                # A restored metadata database may reference versions absent from
                # its vector accelerator. Do not report that outage as no evidence.
                for group in groups:
                    available = await store.count(
                        f"search_units_{group.project_id}",
                        search_filter=build_search_filter(
                            project_id=group.project_id,
                            tenant_id=group.tenant_id,
                            partition_id=group.partition_id,
                            document_version_ids=list(group.versions),
                        ),
                    )
                    if available == 0:
                        raise SearchIndexUnavailable("Search index is missing current document versions")
            t_retrieval_ms = round((time.perf_counter() - t_retrieval_start) * 1000, 2)
        retrieval_stats["candidate_pool_saturated"] = any(
            len(item[3]) >= candidate_limit or len(item[4]) >= candidate_limit for item in channel_results
        )
        local_failures = {
            group.scope_key: local_failure
            for group, (_paths, local_failure, _escape_failure) in zip(groups, path_groups, strict=True)
            if local_failure is not None
        }
        escape_failures = {
            group.scope_key: escape_failure
            for group, (_paths, _local_failure, escape_failure) in zip(groups, path_groups, strict=True)
            if escape_failure is not None
        }
    except asyncio.CancelledError:
        raise
    except TimeoutError as error:
        log.warning("canonical routed retrieval exhausted request budget")
        raise ServiceUnavailableError("Canonical search exceeded the query budget") from error
    except SearchIndexUnavailable as error:
        log.warning("canonical search index unavailable error_type=%s", type(error).__name__)
        raise ServiceUnavailableError("Canonical search index is unavailable") from error
    except httpx.HTTPError as error:
        log.warning("canonical search client unavailable error_type=%s", type(error).__name__)
        raise ServiceUnavailableError("Canonical search index is unavailable") from error
    finally:
        for v_task in vector_tasks.values():
            if not v_task.done():
                v_task.cancel()
        if vector_tasks:
            await asyncio.gather(*vector_tasks.values(), return_exceptions=True)
        request_snapshot_ids = {
            route.request_snapshot_id
            for route in decisions.values()
            if route.request_snapshot_id
        }
        release = getattr(engine_manager, "release_routing_snapshot", None)
        if callable(release):
            for request_snapshot_id in request_snapshot_ids:
                try:
                    await release(request_snapshot_id)
                except Exception as error:  # noqa: BLE001 - lease expiry remains the recovery path
                    log.warning(
                        "could not release request tree snapshot error_type=%s",
                        type(error).__name__,
                    )

    hits: dict[
        tuple[str, str, str, str, str, str],
        tuple[_SearchGroup, SearchUnitHit, float, int, str],
    ] = {}
    hit_variants: dict[
        tuple[str, str, str, str, str, str],
        list[tuple[str, SearchUnitHit]],
    ] = defaultdict(list)
    max_dense_raw_score = 0.0
    max_sparse_raw_score = 0.0
    for group, _route, origin, semantic_hits, lexical_hits in channel_results:
        for channel_hits, channel in ((semantic_hits, "dense"), (lexical_hits, "sparse")):
            max_score = max((max(0.0, hit.score) for hit in channel_hits), default=0.0)
            if channel == "dense":
                max_dense_raw_score = max(max_dense_raw_score, max_score)
            else:
                max_sparse_raw_score = max(max_sparse_raw_score, max_score)
            for rank, hit in enumerate(channel_hits):
                unit_id = str(hit.payload.get("search_unit_id") or "")
                if (
                    not unit_id
                    or hit.payload.get("project_id") != group.project_id
                    or hit.payload.get("tenant_id") != group.tenant_id
                    or hit.payload.get("security_partition_id") != group.partition_id
                    or hit.payload.get("document_version_id") not in group.versions
                ):
                    continue
                key = (group.project_id, group.source.id, group.tenant_id, group.partition_id, unit_id)
                normalized_score = max(0.0, hit.score) / max_score if max_score > 0.0 else 0.0
                hit_key = (channel, *key)
                hit_variants[hit_key].append((origin, hit))
                candidate = (group, hit, normalized_score, rank, origin)
                previous = hits.get(hit_key)
                if previous is None or (rank, -normalized_score, origin) < (
                    previous[3],
                    -previous[2],
                    previous[4],
                ):
                    # A unit seen by both paths retains the best rank in this channel;
                    # it does not receive a second RRF vote.
                    hits[hit_key] = candidate

    candidate_units_before_cap = len({key[-1] for key in hits})
    candidate_unit_ids = _bounded_candidate_unit_ids(
        hits,
        limit=int(settings.search_unit_candidate_limit),
    )
    selected_candidate_ids = set(candidate_unit_ids)
    hits = {key: value for key, value in hits.items() if key[-1] in selected_candidate_ids}
    retrieval_stats.update(
        {
            "candidate_units_before_cap": candidate_units_before_cap,
            "candidate_units_after_cap": len(candidate_unit_ids),
            "candidate_units_truncated": candidate_units_before_cap - len(candidate_unit_ids),
        }
    )
    if not candidate_unit_ids:
        effective_plan = promote_multi_hop(
            strategy_plan,
            coverage_reason="NO_BRANCH_EVIDENCE" if strategy_plan.multi_hop_eligible else None,
        )
        routing = _routing_trace(
            groups,
            decisions,
            {},
            strategy_plan=effective_plan,
            local_failures=local_failures,
            escape_failures=escape_failures,
            coverage={"status": "unknown", "reason": "no_retrieval_candidates"},
            graph_skip_reason="phase5_adapter_unavailable"
            if effective_plan.primary_strategy.value == "MULTI_HOP"
            else None,
        )
        total_ms = round((time.perf_counter() - t_start) * 1000, 2)
        return SearchOutcome(
            query=query,
            sections=[],
            stats={
                "canonical_index": True,
                "ready_versions": len(ready_versions),
                **retrieval_stats,
                "routing": routing,
                "stage_latencies_ms": {
                    "auth_scope": t_auth_ms,
                    "tree_route": t_route_ms,
                    "candidate_retrieval": t_retrieval_ms,
                    "rank_fusion": 0.0,
                    "diversity_selection": 0.0,
                    "total_retrieval": total_ms,
                },
                "latency_total_ms": total_ms,
            },
        )

    # Recheck Source mappings, active documents, partitions, and latest attempts after Qdrant returns.
    t_hydration_start = time.perf_counter()
    candidate_version_ids = {
        str(hit.payload.get("document_version_id") or "")
        for (_g, hit, _s, _r, _o) in hits.values()
        if hit.payload.get("document_version_id")
    }
    try:
        async with asyncio.timeout_at(deadline):
            async with SessionLocal() as session:
                try:
                    verified_candidate_versions = await _load_current_ready_versions(
                        session,
                        sources,
                        principal,
                        version_ids=candidate_version_ids,
                    )
                    current_versions = {
                        vid: ver
                        for vid, ver in ready_versions.items()
                        if vid not in candidate_version_ids
                    } | verified_candidate_versions
                except TypeError:
                    current_versions = await _load_current_ready_versions(session, sources, principal)
                start_block = aliased(CanonicalBlock)
                end_block = aliased(CanonicalBlock)
                rows = (
                    await session.execute(
                        select(
                            SearchUnit,
                            Document.id,
                            Document.filename,
                            Document.source_id,
                            DocumentVersion.version_no,
                            start_block.source_anchor,
                            start_block.ordinal,
                            end_block.ordinal,
                            Document.logical_source_id,
                        )
                        .join(DocumentVersion, DocumentVersion.id == SearchUnit.document_version_id)
                        .join(Document, Document.id == DocumentVersion.document_id)
                        .join(start_block, start_block.id == SearchUnit.block_from_id)
                        .join(end_block, end_block.id == SearchUnit.block_to_id)
                        .where(
                            SearchUnit.id.in_(candidate_unit_ids),
                            start_block.document_version_id == SearchUnit.document_version_id,
                            end_block.document_version_id == SearchUnit.document_version_id,
                            Document.source_id.in_([source.id for source in sources]),
                            Document.is_active.is_(True),
                        )
                    )
                ).all()
    except SQLAlchemyError as error:
        log.warning("canonical evidence lookup unavailable error_type=%s", type(error).__name__)
        raise ServiceUnavailableError("Canonical search evidence is unavailable") from error
    except TimeoutError as error:
        log.warning("canonical evidence hydration exceeded query budget")
        raise ServiceUnavailableError("Canonical search exceeded the query budget") from error
    t_hydration_ms = round((time.perf_counter() - t_hydration_start) * 1000, 2)

    unit_by_id: dict[str, tuple[SearchUnit, dict[str, Any]]] = {}
    unit_ordinals: dict[str, tuple[int, int]] = {}
    for raw_row in rows:
        row_tuple = tuple(raw_row)
        unit, document_id, filename, source_id, version_no, anchor, start_ordinal, end_ordinal = row_tuple[:8]
        logical_source_id = row_tuple[8] if len(row_tuple) > 8 else None
        version = current_versions.get(str(unit.document_version_id))
        if (
            version is None
            or version.source_id != str(source_id)
            or version.partition_id != str(unit.security_partition_id)
            or start_ordinal > end_ordinal
            or int(unit.page_from or 0) < 1
            or int(unit.page_to or 0) < int(unit.page_from or 0)
            or not isinstance(anchor, str)
            or not anchor.strip()
        ):
            continue
        unit_ordinals[str(unit.id)] = (int(start_ordinal), int(end_ordinal))
        unit_by_id[str(unit.id)] = (
            unit,
            {
                "document_id": str(document_id),
                "public_document_id": _resolve_public_document_id(logical_source_id),
                "document_version_id": str(unit.document_version_id),
                "document_name": str(filename or ""),
                "version_no": int(version_no),
                "source_id": str(source_id),
                "source_name": version.source.name,
                "page_from": int(unit.page_from),
                "page_to": int(unit.page_to),
                "anchor": anchor.strip(),
                "section_path": unit.section_path,
                "block_from_id": unit.block_from_id,
                "block_to_id": unit.block_to_id,
                "search_unit_id": unit.id,
                "content_hash": unit.content_hash,
                "project_id": version.project_id,
                "tenant_id": version.tenant_id,
                "security_partition_id": version.partition_id,
            },
        )

    sections_by_key: dict[tuple[str, str, str, str, str], RetrievedSection] = {}
    verified_origins: dict[tuple[str, str, str, str], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    ranked_by_channel_and_scope: dict[
        tuple[str, tuple[str, str, str, str]], list[RetrievedSection]
    ] = defaultdict(list)
    semantic_candidate_count = 0
    lexical_candidate_count = 0
    for channel, project_id, source_id, tenant_id, partition_id, unit_id in sorted(hits):
        hit_key = (channel, project_id, source_id, tenant_id, partition_id, unit_id)
        group, hit, score, rank, origin = hits[hit_key]
        resolved = unit_by_id.get(unit_id)
        if resolved is None:
            continue
        unit, locator = resolved
        payload = hit.payload
        if (
            payload.get("search_unit_id") != unit.id
            or payload.get("document_version_id") != unit.document_version_id
            or payload.get("project_id") != locator["project_id"]
            or payload.get("tenant_id") != locator["tenant_id"]
            or payload.get("security_partition_id") != locator["security_partition_id"]
            or payload.get("content_hash") != unit.content_hash
        ):
            continue
        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != unit.content_hash:
            continue
        from sag_api.services.search_index_service import generate_search_unit_point_id

        if hit.point_id != generate_search_unit_point_id(f"search_units_{project_id}", unit.id):
            continue
        section_key = (project_id, source_id, tenant_id, partition_id, unit_id)
        scope_key = (project_id, source_id, tenant_id, partition_id)
        for variant_origin, variant in hit_variants.get(hit_key, []):
            variant_payload = variant.payload
            variant_content = variant_payload.get("content")
            if (
                variant.point_id == generate_search_unit_point_id(f"search_units_{project_id}", unit.id)
                and variant_payload.get("search_unit_id") == unit.id
                and variant_payload.get("document_version_id") == unit.document_version_id
                and variant_payload.get("project_id") == locator["project_id"]
                and variant_payload.get("tenant_id") == locator["tenant_id"]
                and variant_payload.get("security_partition_id") == locator["security_partition_id"]
                and variant_payload.get("content_hash") == unit.content_hash
                and isinstance(variant_content, str)
                and hashlib.sha256(variant_content.encode("utf-8")).hexdigest() == unit.content_hash
            ):
                verified_origins[scope_key][variant_origin].add(unit_id)
        section = sections_by_key.get(section_key)
        if section is None:
            scope_source = group.source
            section = RetrievedSection(
                chunk_id=unit.id,
                search_unit_id=unit.id,
                block_from_id=unit.block_from_id,
                block_to_id=unit.block_to_id,
                section_path=unit.section_path,
                heading=unit.section_path or locator["document_name"],
                content=content,
                score=0.0,
                rank=0,
                source_id=scope_source.id,
                source_config_id=scope_source.sag_source_config_id,
                content_hash=unit.content_hash,
                canonical_evidence_verified=True,
                **{
                    name: value
                    for name, value in locator.items()
                    if name
                    not in {
                        "source_id",
                        "source_name",
                        "tenant_id",
                        "security_partition_id",
                        "search_unit_id",
                        "content_hash",
                        "block_from_id",
                        "block_to_id",
                        "section_path",
                    }
                },
            )
            sections_by_key[section_key] = section
        interleaved_rank = rank if origin == "global_only" else rank * 2 + int(origin != "branch_local")
        ranked_by_channel_and_scope[(channel, scope_key)].append(
            section.model_copy(update={"score": score, "rank": interleaved_rank})
        )
        if channel == "dense":
            semantic_candidate_count += 1
        else:
            lexical_candidate_count += 1

    semantic_lists = [
        sorted(
            ranked_by_channel_and_scope.get(("dense", group.scope_key), []),
            key=lambda section: (section.rank, section.chunk_id or ""),
        )
        for group in groups
    ]
    lexical_lists = [
        sorted(
            ranked_by_channel_and_scope.get(("sparse", group.scope_key), []),
            key=lambda section: (section.rank, section.chunk_id or ""),
        )
        for group in groups
    ]
    semantic = _fair_rank_merge(semantic_lists)
    lexical = _fair_rank_merge(lexical_lists)
    selection_fallback_reason = None
    selection_deadline = deadline - max(
        settings.search_rerank_min_remaining_seconds, min(1.0, settings.search_source_timeout * 0.1),
    )
    rerank_remaining = deadline - loop.time()
    t_rerank_start = time.perf_counter()
    scope_k_index_holder: list[_ScopeKnowledgeIndex] = []
    diversity_stats: dict[str, Any] = {}
    initial_pool: list[RetrievedSection] = []
    if rerank_remaining <= settings.search_rerank_min_remaining_seconds:
        selection_fallback_reason = "rerank_budget_exhausted"
        fallback_pool = _fair_rank_merge([semantic, lexical])
        initial_pool = fallback_pool
        if per_doc_limit == 1 and selection_limit is not None:
            from sag_api.services.retrieval_plan import group_sections_by_document

            selected = group_sections_by_document(fallback_pool, limit=selection_limit)
        else:
            selected = fallback_pool[:selection_limit]
        rerank_stats = {
            "candidates": len(fallback_pool),
            "relevant": None,
            "filtered_irrelevant": None,
        }
        coverage = {
            "status": "unknown",
            "reason": selection_fallback_reason,
            "canonical_locator_verified": bool(selected)
            and all(
                section.canonical_evidence_verified
                and section.document_id
                and section.document_version_id
                and section.page_from is not None
                and section.anchor
                for section in selected
            ),
            "semantic_answerability": "unknown",
        }
        selection_method = "latency_fallback_rank_interleave"
        t_fusion_ms = 0.0
        t_diversity_ms = round((time.perf_counter() - t_rerank_start) * 1000, 2)
    else:
        t_fusion_start = time.perf_counter()
        fused = rerank_sections(query, semantic, lexical=lexical, limit=None)
        t_fusion_ms = round((time.perf_counter() - t_fusion_start) * 1000, 2)
        rerank_stats = {
            "candidates": fused.candidate_count,
            "relevant": fused.relevant_count,
            "filtered_irrelevant": fused.filtered_count,
        }
        t_div_start = time.perf_counter()
        is_multi_hop_query = (
            strategy_plan.primary_strategy.value == "MULTI_HOP"
            or "MULTI_HOP_CANDIDATE" in strategy_plan.reason_codes
        )
        sibling_sections = (
            await _hydrate_sibling_sections_for_top_versions(
                sections=fused.sections,
                current_versions=current_versions,
                sources=sources,
                limit=requested_limit,
                index_holder=scope_k_index_holder,
            )
            if not is_multi_hop_query and requested_limit > 1 and fused.sections and per_doc_limit != 1
            else []
        )
        initial_pool = fused.sections + sibling_sections
        try:
            selected, diversity_stats = await _select_diverse_sections(
                query,
                initial_pool,
                limit=selection_limit,
                deadline=selection_deadline,
                per_document_limit=per_doc_limit,
            )
        except TimeoutError:
            selection_fallback_reason = "diversity_budget_exhausted"
            if per_doc_limit == 1 and selection_limit is not None:
                from sag_api.services.retrieval_plan import group_sections_by_document

                selected = group_sections_by_document(fused.sections, limit=selection_limit)
            else:
                selected = fused.sections[:selection_limit]
            diversity_stats = {"candidate_count": len(initial_pool), "deduplicated_count": 0,
                               "anchor_coverage": "unknown", "reason": selection_fallback_reason}
        t_diversity_ms = round((time.perf_counter() - t_div_start) * 1000, 2)
        coverage = {
            **diversity_stats,
            "status": _structural_coverage_status(len(selected), diversity_stats),
            "canonical_locator_verified": bool(selected)
            and all(
                section.canonical_evidence_verified
                and section.document_id
                and section.document_version_id
                and section.page_from is not None
                and section.anchor
                for section in selected
            ),
            "semantic_answerability": "unknown",
        }
        selection_method = "latency_fallback_rrf" if selection_fallback_reason else "authorized_content_dedup_mmr"
    coverage_reason = (
        "NO_BRANCH_EVIDENCE"
        if coverage["status"] == "empty_evidence"
        else "MISSING_REQUIRED_QUERY_ANCHOR"
        if coverage["status"] == "weak"
        else None
    )
    effective_plan = promote_multi_hop(strategy_plan, coverage_reason=coverage_reason)
    graph_expansion_attempted = False
    graph_expanded_candidates = 0
    graph_skip_reason: str | None = None
    t_graph_expansion_ms = 0.0
    is_multi_hop_mode = (
        effective_plan.primary_strategy.value == "MULTI_HOP" or effective_plan.multi_hop_eligible
    )
    if is_multi_hop_mode or requested_limit > 1:
        if selection_fallback_reason is not None or (deadline - loop.time()) <= 0.08:
            if is_multi_hop_mode:
                graph_skip_reason = "budget_exhausted"
        else:
            if is_multi_hop_mode:
                graph_expansion_attempted = True
            seed_pool = selected if selected else (fused.sections if not selection_fallback_reason else [])
            # Keep unselected candidates available for bridge re-scoring within the current breadth.
            existing_ids = {
                str(s.search_unit_id or s.chunk_id or "")
                for s in list(seed_pool)[:requested_limit]
                if (s.search_unit_id or s.chunk_id)
            }
            existing_pool_scores = {
                str(s.search_unit_id or s.chunk_id or ""): float(s.score or 0.0)
                for s in initial_pool
                if (s.search_unit_id or s.chunk_id)
            }
            has_required_anchors = int((diversity_stats or {}).get("required_anchor_count", 0) or 0) > 0
            t_graph_start = time.perf_counter()
            expanded_sections = await _expand_multi_hop_sections(
                seed_sections=list(seed_pool),
                unit_ordinals=unit_ordinals,
                current_versions=current_versions,
                existing_unit_ids=existing_ids,
                sources=sources,
                existing_pool_scores=existing_pool_scores,
                limit=min(settings.search_unit_candidate_limit, max(8, requested_limit * 2)),
                lineage_only=not is_multi_hop_mode,
                query=query,
                allow_contradiction_lineage=has_required_anchors,
                preloaded_k_index=(scope_k_index_holder[0] if scope_k_index_holder else None),
            )
            t_graph_expansion_ms = round((time.perf_counter() - t_graph_start) * 1000, 2)
            graph_expanded_candidates = len(expanded_sections)
            if expanded_sections:
                graph_expansion_attempted = True
                t_div_start = time.perf_counter()
                top_seed_score = max((float(s.score or 0.0) for s in list(seed_pool)[:1]), default=1.0)
                seed_doc_ids = {str(s.document_id or "") for s in list(seed_pool) if s.document_id}
                hop2_cap = (
                    max(0.05, top_seed_score - 0.005)
                    if (is_multi_hop_query or has_required_anchors or not is_multi_hop_mode)
                    else max(0.05, top_seed_score * 0.88)
                )
                expanded_by_id = {str(s.search_unit_id or s.chunk_id or ""): s for s in expanded_sections}
                boosted_fused: list[RetrievedSection] = []
                seen_exp_ids: set[str] = set()
                for f_sec in initial_pool:
                    f_id = str(f_sec.search_unit_id or f_sec.chunk_id or "")
                    exp_match = expanded_by_id.get(f_id)
                    if exp_match is not None:
                        seen_exp_ids.add(f_id)
                        exp_score = float(exp_match.score or 0.0)
                        is_cross_doc_multi_hop = bool(seed_doc_ids) and str(f_sec.document_id or "") not in seed_doc_ids
                        is_strong_bridge = exp_score >= 0.82 * top_seed_score
                        bonus = 0.08 if (is_cross_doc_multi_hop and is_strong_bridge) else 0.0
                        raw_boost = max(float(f_sec.score or 0.0), exp_score + bonus)
                        boosted_score = max(float(f_sec.score or 0.0), min(hop2_cap, raw_boost))
                        boosted_fused.append(f_sec.model_copy(update={"score": round(boosted_score, 6)}))
                    else:
                        boosted_fused.append(f_sec)
                unconfirmed_cap = min(
                    hop2_cap,
                    max(0.05, top_seed_score * (0.90 if not is_multi_hop_mode else 0.85)),
                )
                new_expanded = [
                    s.model_copy(update={"score": round(min(unconfirmed_cap, float(s.score or 0.0)), 6)})
                    for s in expanded_sections
                    if str(s.search_unit_id or s.chunk_id or "") not in seen_exp_ids
                    and float(s.score or 0.0) >= 0.78 * top_seed_score
                ]
                combined_pool = boosted_fused + new_expanded
                try:
                    selected, diversity_stats = await _select_diverse_sections(
                        query,
                        combined_pool,
                        limit=selection_limit,
                        deadline=selection_deadline,
                        per_document_limit=per_doc_limit,
                    )
                except TimeoutError:
                    diversity_stats = {**diversity_stats, "reason": "diversity_budget_exhausted"}
                    selection_fallback_reason = "diversity_budget_exhausted"
                t_diversity_ms = round(t_diversity_ms + (time.perf_counter() - t_div_start) * 1000, 2)
                coverage = {
                    **diversity_stats,
                    "status": _structural_coverage_status(len(selected), diversity_stats),
                    "canonical_locator_verified": bool(selected)
                    and all(
                        section.canonical_evidence_verified
                        and section.document_id
                        and section.document_version_id
                        and section.page_from is not None
                        and section.anchor
                        for section in selected
                    ),
                    "semantic_answerability": "unknown",
                }
    routing = _routing_trace(
        groups,
        decisions,
        verified_origins,
        strategy_plan=effective_plan,
        local_failures=local_failures,
        escape_failures=escape_failures,
        coverage=coverage,
        graph_skip_reason=graph_skip_reason,
        selection_fallback_reason=selection_fallback_reason,
        graph_expansion_attempted=graph_expansion_attempted,
        graph_expanded_candidates=graph_expanded_candidates,
    )
    from sag_api.services.retrieval_plan import OutputMode, is_unfiltered_scope_catalog_query

    max_catalog_slots = selection_limit if selection_limit is not None else requested_limit
    if (
        per_doc_limit == 1
        and strategy_plan.retrieval_plan.output in {OutputMode.LIST, OutputMode.COUNT}
        and len(selected) < max_catalog_slots
        and is_unfiltered_scope_catalog_query(query, [s.name for s in sources])
    ):
        represented_vids = {str(section.document_version_id or "") for section in selected}
        missing_vids = [
            vid
            for vid in sorted(
                current_versions,
                key=lambda v: (str(getattr(current_versions[v], "document_name", "") or ""), v),
            )
            if vid not in represented_vids
        ][: max(0, max_catalog_slots - len(selected))]
        if missing_vids and (deadline - loop.time()) > 0.15:
            try:
                from sag_api.services.search_index_service import generate_search_unit_point_id

                allowed_partitions = sorted({v.partition_id for v in current_versions.values()})
                async with asyncio.timeout_at(deadline):
                    async with SessionLocal() as session:
                        k_index = await _get_or_build_scope_knowledge_index(
                            session,
                            current_versions,
                            allowed_partitions,
                        )
                        await _ensure_versions_hydrated(
                            session,
                            k_index,
                            missing_vids,
                            current_versions,
                            sources,
                        )
                    qdrant_client = await engine_manager.get_search_unit_qdrant_client()
                    verify_store = SearchUnitQdrantStore(qdrant_client)
                    min_existing_score = min((float(s.score or 0.35) for s in selected), default=0.35)
                    for vid in missing_vids:
                        if len(selected) >= max_catalog_slots:
                            break
                        hydrated_items = k_index.hydrated_su_by_version.get(vid, ())
                        if not hydrated_items:
                            continue
                        sec_template = hydrated_items[0][0]
                        ver = current_versions[vid]
                        expected_point_id = generate_search_unit_point_id(
                            f"search_units_{ver.project_id}",
                            str(sec_template.search_unit_id or sec_template.chunk_id or ""),
                        )
                        point = await verify_store.get_point(
                            f"search_units_{ver.project_id}",
                            expected_point_id,
                        )
                        if point is None:
                            continue
                        p_load = point.payload
                        if (
                            point.point_id != expected_point_id
                            or p_load.get("search_unit_id") != sec_template.search_unit_id
                            or p_load.get("document_version_id") != vid
                            or p_load.get("project_id") != ver.project_id
                            or p_load.get("tenant_id") != ver.tenant_id
                            or p_load.get("security_partition_id") != ver.partition_id
                            or p_load.get("content_hash") != sec_template.content_hash
                        ):
                            continue
                        selected.append(
                            sec_template.model_copy(
                                update={
                                    "score": round(max(0.25, min_existing_score - 0.001), 6),
                                    "rank": len(selected),
                                }
                            )
                        )
            except (TimeoutError, SQLAlchemyError, SearchIndexUnavailable):
                pass
    selected_version_ids = {str(section.document_version_id or "") for section in selected}
    if selected_version_ids:
        try:
            async with asyncio.timeout_at(deadline):
                async with SessionLocal() as session:
                    refreshed = await _load_current_ready_versions(
                        session, sources, principal, version_ids=selected_version_ids,
                    )
            _verify_selection_scope(selected_version_ids, current_versions, refreshed)
        except (TimeoutError, SQLAlchemyError) as error:
            raise ServiceUnavailableError("Search scope could not be revalidated") from error
    _schedule_routed_query_demand(groups, decisions)
    total_retrieval_ms = round((time.perf_counter() - t_start) * 1000, 2)
    return SearchOutcome(
        query=query,
        sections=selected,
        stats={
            "canonical_index": True,
            "ready_versions": len(ready_versions),
            **retrieval_stats,
            "semantic_candidates": semantic_candidate_count,
            "lexical_candidates": lexical_candidate_count,
                **rerank_stats,
            "fusion_method": (
                "rank_interleave_fallback" if selection_fallback_reason == "rerank_budget_exhausted" else "rrf"
            ),
            "selection_method": selection_method,
            "cross_scope_merge": "rank_interleave",
            "coverage": coverage,
            "routing": routing,
            "selection_fallback_reason": selection_fallback_reason,
            "stage_latencies_ms": {
                "auth_scope": t_auth_ms,
                "tree_route": t_route_ms,
                "candidate_retrieval": t_retrieval_ms,
                "evidence_hydration": t_hydration_ms,
                "rank_fusion": t_fusion_ms,
                "graph_expansion": t_graph_expansion_ms,
                "diversity_selection": t_diversity_ms,
                "total_retrieval": total_retrieval_ms,
            },
            "latency_total_ms": total_retrieval_ms,
        },
    )


async def get_search_unit_citation(
    session: AsyncSession,
    *,
    source: SearchableSource,
    principal: VerifiedPrincipal,
    search_unit_id: str,
    engine_manager: Any,
) -> dict[str, Any] | None:
    try:
        return await _get_search_unit_citation_checked(
            session,
            source=source,
            principal=principal,
            search_unit_id=search_unit_id,
            engine_manager=engine_manager,
        )
    except SQLAlchemyError as error:
        log.warning("canonical citation authorization unavailable error_type=%s", type(error).__name__)
        raise ServiceUnavailableError("Canonical citation authorization is unavailable") from error


async def _get_search_unit_citation_checked(
    session: AsyncSession,
    *,
    source: SearchableSource,
    principal: VerifiedPrincipal,
    search_unit_id: str,
    engine_manager: Any,
) -> dict[str, Any] | None:
    """Read an exact canonical citation target, rechecking ACL and index provenance.

    ``None`` means the identifier is not a SearchUnit and may be handled by the
    existing legacy chunk reader. A known SearchUnit outside this caller's
    scope raises a generic 404 so it can never fall through to legacy lookup.
    Historical versions remain readable while retained and authorized; their
    immutable version and latest verified search manifest are checked here.
    """
    from sag_api.core.errors import NotFoundError

    exists = await session.scalar(select(SearchUnit.id).where(SearchUnit.id == search_unit_id).limit(1))
    if exists is None:
        return None
    if not principal.tenant_id or not principal.allowed_partition_ids or not principal.allowed_project_ids:
        raise NotFoundError("原文分块不存在")

    start_block = aliased(CanonicalBlock)
    end_block = aliased(CanonicalBlock)
    partition_expression = DocumentVersion.metadata_json["security_partition_id"].as_string()
    row = (
        await session.execute(
            select(
                SearchUnit,
                Document.id,
                Document.filename,
                Document.source_id,
                Document.project_id,
                Document.tenant_id,
                DocumentVersion.version_no,
                start_block.source_anchor,
                start_block.ordinal,
                end_block.ordinal,
            )
            .join(DocumentVersion, DocumentVersion.id == SearchUnit.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .join(start_block, start_block.id == SearchUnit.block_from_id)
            .join(end_block, end_block.id == SearchUnit.block_to_id)
            .join(SourceProjectMapping, SourceProjectMapping.source_id == Document.source_id)
            .where(
                SearchUnit.id == search_unit_id,
                Document.source_id == source.id,
                Document.project_id.in_(principal.allowed_project_ids),
                Document.tenant_id == principal.tenant_id,
                SourceProjectMapping.project_id == Document.project_id,
                SourceProjectMapping.organization_id == principal.organization_id,
                SourceProjectMapping.state == "CONFIRMED",
                Document.is_active.is_(True),
                Document.status.notin_([DocumentStatus.DELETING, DocumentStatus.DELETE_FAILED]),
                DocumentVersion.search_status.in_(("READY", "SEARCH_READY")),
                DocumentVersion.search_ready_at.is_not(None),
                SearchUnit.security_partition_id.in_(principal.allowed_partition_ids),
                SearchUnit.security_partition_id == partition_expression,
                start_block.document_version_id == SearchUnit.document_version_id,
                end_block.document_version_id == SearchUnit.document_version_id,
            )
            .limit(1)
        )
    ).first()
    if row is None:
        raise NotFoundError("原文分块不存在")

    unit, document_id, filename, source_id, project_id, tenant_id, version_no, anchor, start_ordinal, end_ordinal = row
    if document_id in await _hidden_reprocess_document_ids(session, [source.id]):
        raise NotFoundError("原文分块不存在")
    if (
        start_ordinal > end_ordinal
        or int(unit.page_from or 0) < 1
        or int(unit.page_to or 0) < int(unit.page_from or 0)
        or not isinstance(anchor, str)
        or not anchor.strip()
    ):
        raise NotFoundError("原文分块不存在")

    latest_run = (
        await session.execute(
            select(IngestionRun)
            .where(IngestionRun.document_version_id == unit.document_version_id)
            .order_by(IngestionRun.created_at.desc(), IngestionRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest_run is None or latest_run.project_id != project_id or latest_run.tenant_id != tenant_id:
        raise NotFoundError("原文分块不存在")
    latest_index_stage = (
        await session.execute(
            select(StageRun)
            .where(StageRun.run_id == latest_run.id, StageRun.stage == "INDEX_SEARCH")
            .order_by(StageRun.created_at.desc(), StageRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if (
        latest_index_stage is None
        or latest_index_stage.status != "SUCCESS"
        or not _stage_belongs_to_current_attempt(
            latest_run.started_at,
            latest_index_stage,
        )
        or not _verified_manifest(latest_index_stage.metrics_json, project_id=project_id)
    ):
        raise NotFoundError("原文分块不存在")

    collection = f"search_units_{project_id}"
    try:
        client = await engine_manager.get_search_unit_qdrant_client()
        point = await SearchUnitQdrantStore(client).get_point(
            collection,
            _point_id(collection, unit.id),
        )
    except SearchIndexUnavailable as error:
        log.warning("canonical citation index unavailable error_type=%s", type(error).__name__)
        raise ServiceUnavailableError("Canonical citation index is unavailable") from error
    if point is None:
        raise NotFoundError("原文分块不存在")

    payload = point.payload
    content = payload.get("content")
    if (
        point.point_id != _point_id(collection, unit.id)
        or payload.get("search_unit_id") != unit.id
        or payload.get("document_version_id") != unit.document_version_id
        or payload.get("project_id") != project_id
        or payload.get("tenant_id") != tenant_id
        or payload.get("security_partition_id") != unit.security_partition_id
        or payload.get("content_hash") != unit.content_hash
        or not isinstance(content, str)
        or not content.strip()
        or hashlib.sha256(content.encode("utf-8")).hexdigest() != unit.content_hash
    ):
        raise NotFoundError("原文分块不存在")

    return {
        "chunk_id": unit.id,
        "search_unit_id": unit.id,
        "block_from_id": unit.block_from_id,
        "block_to_id": unit.block_to_id,
        "document_id": str(document_id),
        "document_version_id": str(unit.document_version_id),
        "document_name": str(filename or ""),
        "version_no": int(version_no),
        "page_from": int(unit.page_from),
        "page_to": int(unit.page_to),
        "anchor": anchor.strip(),
        "section_path": unit.section_path,
        "heading": unit.section_path or str(filename or ""),
        "content": content,
        "rank": 0,
        "source_id": source_id,
        "source_name": source.name,
    }


def _point_id(collection: str, unit_id: str) -> str:
    from sag_api.services.search_index_service import generate_search_unit_point_id

    return generate_search_unit_point_id(collection, unit_id)
