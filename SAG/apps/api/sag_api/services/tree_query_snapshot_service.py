"""Request-scoped routing snapshot capture backed by durable slot leases."""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections import OrderedDict, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.orm import load_only

from sag_api.core.config import settings
from sag_api.core.db import SessionLocal
from sag_api.db.models.routing_rag import (
    ProjectSearchState,
    TreeManifest,
    TreeRoutingProfile,
    TreeSnapshotLease,
)
from sag_api.services.query_routing_service import (
    GroupRoutingSnapshot,
    scope_fingerprint,
)
from sag_api.services.query_routing_service import (
    NodeProfile as QueryNodeProfile,
)
from sag_api.services.query_routing_service import (
    RoutingSnapshot as QueryRoutingSnapshot,
)
from sag_api.sag.search_unit_store import _expand_continuous_script_tokens
from sag_api.services.search_index_service import COMMON_STOPWORDS
from sag_api.services.tree_publish_service import (
    _checksum_manifest,
    _database_now,
    _scoped_profile_checksum,
)

_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)
_QUERY_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_AGGREGATED_SCOPE_NODE_CACHE: OrderedDict[
    tuple[Any, ...],
    list[tuple[str, str | None, bool, int, list[list[Any]], list[str]]],
] = OrderedDict()
_AGGREGATED_SCOPE_NODE_CACHE_MAX = 128

# Keyed by (project_id, tree_version, checksum) -> {(partition_id, node_id): (unit_vec | None, (year_from, year_to) | None)}
_MANIFEST_NODE_FEATURES_CACHE: OrderedDict[
    tuple[str, str, str],
    dict[tuple[str, str], tuple[tuple[float, ...] | None, tuple[int, int] | None]],
] = OrderedDict()
_MANIFEST_NODE_FEATURES_CACHE_MAX = 64


def _normalize_unit_vector(values: Sequence[float] | None) -> tuple[float, ...] | None:
    if not values:
        return None
    try:
        floats = [float(v) for v in values]
    except (TypeError, ValueError):
        return None
    if not floats or any(not math.isfinite(v) for v in floats):
        return None
    scale = max(abs(v) for v in floats)
    if scale <= 0.0:
        return None
    scaled = [v / scale for v in floats]
    norm = math.sqrt(math.sumprod(scaled, scaled))
    if norm <= 0.0 or not math.isfinite(norm):
        return None
    return tuple(v / norm for v in scaled)


def _parse_iso_year(value: object) -> int | None:
    if not isinstance(value, str) or len(value) < 4:
        return None
    prefix = value[:4]
    if prefix.isdigit():
        year = int(prefix)
        if 1900 <= year <= 2100:
            return year
    return None


def _extract_manifest_node_features(
    manifest_json: object,
) -> dict[tuple[str, str], tuple[tuple[float, ...] | None, tuple[int, int] | None]]:
    if not isinstance(manifest_json, Mapping):
        return {}
    features: dict[tuple[str, str], tuple[tuple[float, ...] | None, tuple[int, int] | None]] = {}
    routing_profiles = manifest_json.get("routing_profiles")
    if isinstance(routing_profiles, list):
        for item in routing_profiles:
            if not isinstance(item, Mapping):
                continue
            partition_id = item.get("partition_id")
            node_id = item.get("node_id")
            if not isinstance(partition_id, str) or not isinstance(node_id, str):
                continue
            raw_medoid = item.get("dense_medoid")
            norm_medoid = _normalize_unit_vector(raw_medoid) if isinstance(raw_medoid, Sequence) and not isinstance(raw_medoid, (str, bytes)) else None
            y_from = _parse_iso_year(item.get("temporal_from"))
            y_to = _parse_iso_year(item.get("temporal_to"))
            y_range = (
                (min(y_from, y_to), max(y_from, y_to))
                if y_from is not None and y_to is not None
                else (y_from, y_from)
                if y_from is not None
                else (y_to, y_to)
                if y_to is not None
                else None
            )
            features[(partition_id, node_id)] = (norm_medoid, y_range)
        if features:
            return features

    # Fallback for incremental tree manifests storing hierarchical `roots`
    roots = manifest_json.get("roots")
    if isinstance(roots, list):
        stack = list(roots)
        while stack:
            node = stack.pop()
            if not isinstance(node, Mapping):
                continue
            partition_id = node.get("security_partition_id")
            node_id = node.get("node_id")
            prof = node.get("profile")
            if isinstance(partition_id, str) and isinstance(node_id, str) and isinstance(prof, Mapping):
                raw_medoid = prof.get("dense_medoid")
                norm_medoid = _normalize_unit_vector(raw_medoid) if isinstance(raw_medoid, Sequence) and not isinstance(raw_medoid, (str, bytes)) else None
                y_from = _parse_iso_year(prof.get("temporal_from"))
                y_to = _parse_iso_year(prof.get("temporal_to"))
                y_range = (
                    (min(y_from, y_to), max(y_from, y_to))
                    if y_from is not None and y_to is not None
                    else (y_from, y_from)
                    if y_from is not None
                    else (y_to, y_to)
                    if y_to is not None
                    else None
                )
                features[(partition_id, node_id)] = (norm_medoid, y_range)
            children = node.get("children")
            if isinstance(children, list):
                stack.extend(children)
    return features


def _query_signal_scores(
    query: str,
    profile: dict[str, Any],
    *,
    query_terms: set[str] | None = None,
    folded_query: str | None = None,
    query_years: tuple[int, ...] = (),
) -> dict[str, float]:
    if query_terms is None:
        raw_query_terms = set(_expand_continuous_script_tokens(_TOKEN_PATTERN.findall(query.casefold())))
        informative_terms = {
            t for t in raw_query_terms if t not in COMMON_STOPWORDS and (len(t) >= 2 or t.isdigit())
        }
        query_terms = informative_terms or raw_query_terms
    if folded_query is None:
        folded_query = query.casefold()
    sparse = profile.get("sparse")
    weights: list[tuple[str, float]] = []
    if isinstance(sparse, list):
        for item in sparse:
            if (
                isinstance(item, list)
                and len(item) == 2
                and isinstance(item[0], str)
                and isinstance(item[1], (int, float))
                and not isinstance(item[1], bool)
                and math.isfinite(float(item[1]))
                and float(item[1]) >= 0.0
            ):
                weights.append((item[0].casefold(), float(item[1])))
    weight_map: dict[str, float] = {}
    for term, weight in weights:
        weight_map[term] = weight_map.get(term, 0.0) + weight
    matched_terms = [t for t in query_terms if t in weight_map and weight_map[t] > 0.0]
    if matched_terms and query_terms and weights:
        query_cov = len(matched_terms) / len(query_terms)
        matched_mass = math.fsum(weight_map[t] for t in matched_terms)
        top_k_weights = sorted(weight_map.values(), reverse=True)[: max(1, len(query_terms))]
        top_k_mass = math.fsum(top_k_weights)
        weight_conc = min(1.0, matched_mass / top_k_mass) if top_k_mass > 0.0 else 0.0
        sparse_score = min(1.0, 0.65 * query_cov + 0.35 * weight_conc)
    else:
        sparse_score = 0.0
    scores: dict[str, float] = {"sparse": sparse_score}
    entities = profile.get("entities")
    if isinstance(entities, list) and all(isinstance(entity, str) for entity in entities):
        if any(
            entity.strip()
            and (
                (len(entity.strip()) >= 4 or not entity.isascii())
                and entity.casefold() in folded_query
            )
            for entity in entities
        ):
            scores["entity"] = 1.0
        else:
            best_overlap = 0.0
            for entity in entities:
                ent_tokens = {
                    t
                    for t in _TOKEN_PATTERN.findall(entity.casefold())
                    if t not in COMMON_STOPWORDS and (len(t) >= 2 or t.isdigit())
                }
                if len(ent_tokens) >= 2 and query_terms:
                    overlap = len(ent_tokens & query_terms) / len(ent_tokens)
                    if overlap >= 0.5 and overlap > best_overlap:
                        best_overlap = overlap
            scores["entity"] = round(0.85 * best_overlap, 4) if best_overlap > 0.0 else 0.0
    temporal_range = profile.get("temporal_year_range")
    if query_years and isinstance(temporal_range, tuple) and len(temporal_range) == 2:
        y_min, y_max = temporal_range
        if any(y_min <= y <= y_max for y in query_years):
            scores["temporal"] = 1.0
        elif any((y_min - 1) <= y <= (y_max + 1) for y in query_years):
            scores["temporal"] = 0.5
        else:
            scores["temporal"] = 0.1
    return scores


def _source_profile_payload(record: TreeRoutingProfile) -> dict[str, Any]:
    return {
        "project_id": record.project_id,
        "tenant_id": record.tenant_id,
        "source_id": record.source_id,
        "document_version_id": record.document_version_id,
        "partition_id": record.partition_id,
        "node_id": record.node_id,
        "parent_id": record.parent_id,
        "is_leaf": record.is_leaf,
        "accessible_unit_count": record.accessible_unit_count,
        "sparse": record.sparse_json,
        "entities": record.entities_json,
    }


def _validate_and_cache_scope_nodes(
    records: list[TreeRoutingProfile],
    *,
    project_id: str,
    tenant_id: str,
    tree_version: str,
    source_ids: tuple[str, ...],
    partition_id: str,
    document_version_ids: tuple[str, ...],
    fingerprint: str,
) -> list[tuple[str, str | None, bool, int, list[list[Any]], list[str]]] | None:
    cache_sig = (
        project_id,
        tenant_id,
        tree_version,
        fingerprint,
        tuple(
            (
                r.node_id,
                r.document_version_id,
                r.profile_checksum,
                r.accessible_unit_count,
            )
            for r in records
        ),
    )
    cached_nodes = _AGGREGATED_SCOPE_NODE_CACHE.get(cache_sig)
    if cached_nodes is not None:
        return cached_nodes

    by_node: dict[str, dict[str, Any]] = {}
    for record in records:
        profile = _source_profile_payload(record)
        if (
            record.project_id != project_id
            or record.tenant_id != tenant_id
            or record.tree_version != tree_version
            or record.source_id not in source_ids
            or record.document_version_id not in document_version_ids
            or record.partition_id != partition_id
            or record.profile_checksum != _scoped_profile_checksum(tree_version, profile)
            or not isinstance(record.node_id, str)
            or not record.node_id
            or (record.parent_id is not None and not isinstance(record.parent_id, str))
            or not isinstance(record.is_leaf, bool)
            or isinstance(record.accessible_unit_count, bool)
            or not isinstance(record.accessible_unit_count, int)
            or record.accessible_unit_count <= 0
            or not isinstance(record.sparse_json, list)
            or not isinstance(record.entities_json, list)
            or any(not isinstance(entity, str) for entity in record.entities_json)
        ):
            return None
        aggregate = by_node.setdefault(
            record.node_id,
            {
                "parent_id": record.parent_id,
                "is_leaf": record.is_leaf,
                "accessible_unit_count": 0,
                "sparse": defaultdict(list),
                "entities": set(),
            },
        )
        if aggregate["parent_id"] != record.parent_id or aggregate["is_leaf"] != record.is_leaf:
            return None
        aggregate["accessible_unit_count"] += record.accessible_unit_count
        for item in record.sparse_json:
            if (
                not isinstance(item, list)
                or len(item) != 2
                or not isinstance(item[0], str)
                or isinstance(item[1], bool)
                or not isinstance(item[1], (int, float))
                or not math.isfinite(float(item[1]))
                or float(item[1]) < 0.0
            ):
                return None
            aggregate["sparse"][item[0]].append(float(item[1]))
        aggregate["entities"].update(record.entities_json)

    cached_nodes = []
    for node_id, aggregate in sorted(by_node.items()):
        sparse = sorted(
            ((term, math.fsum(weights)) for term, weights in aggregate["sparse"].items()),
            key=lambda item: (-item[1], item[0]),
        )
        cached_nodes.append(
            (
                node_id,
                aggregate["parent_id"],
                aggregate["is_leaf"],
                aggregate["accessible_unit_count"],
                [[term, weight] for term, weight in sparse],
                sorted(aggregate["entities"]),
            )
        )
    if len(_AGGREGATED_SCOPE_NODE_CACHE) >= _AGGREGATED_SCOPE_NODE_CACHE_MAX:
        _AGGREGATED_SCOPE_NODE_CACHE.pop(next(iter(_AGGREGATED_SCOPE_NODE_CACHE)))
    _AGGREGATED_SCOPE_NODE_CACHE[cache_sig] = cached_nodes
    return cached_nodes


def _build_query_node_profiles(
    cached_nodes: list[tuple[str, str | None, bool, int, list[list[Any]], list[str]]],
    *,
    project_id: str,
    tenant_id: str,
    tree_version: str,
    source_ids: tuple[str, ...],
    partition_id: str,
    document_version_ids: tuple[str, ...],
    fingerprint: str,
    query: str,
    manifest_features: Mapping[tuple[str, str], tuple[tuple[float, ...] | None, tuple[int, int] | None]] | None = None,
    query_unit_vector: tuple[float, ...] | None = None,
    query_terms: set[str] | None = None,
    folded_query: str | None = None,
    query_years: tuple[int, ...] | None = None,
) -> tuple[QueryNodeProfile, ...]:
    if not cached_nodes:
        return ()
    if query_terms is None:
        raw_query_terms = set(_expand_continuous_script_tokens(_TOKEN_PATTERN.findall(query.casefold())))
        informative_terms = {
            t for t in raw_query_terms if t not in COMMON_STOPWORDS and (len(t) >= 2 or t.isdigit())
        }
        query_terms = informative_terms or raw_query_terms
    if folded_query is None:
        folded_query = query.casefold()
    if query_years is None:
        query_years = tuple(
            sorted({int(m) for m in _QUERY_YEAR_RE.findall(query) if 1900 <= int(m) <= 2100})
        )

    raw_scores_by_node: dict[str, dict[str, float]] = {}
    raw_cos_by_node: dict[str, float] = {}
    for node_id, _parent_id, _is_leaf, _unit_count, sparse_list, entities_list in cached_nodes:
        norm_medoid, temporal_range = (
            manifest_features.get((partition_id, node_id), (None, None))
            if manifest_features is not None
            else (None, None)
        )
        raw_scores_by_node[node_id] = _query_signal_scores(
            query,
            {
                "sparse": sparse_list,
                "entities": entities_list,
                "temporal_year_range": temporal_range,
            },
            query_terms=query_terms,
            folded_query=folded_query,
            query_years=query_years,
        )
        if (
            query_unit_vector is not None
            and norm_medoid is not None
            and len(query_unit_vector) == len(norm_medoid)
        ):
            cos_val = math.sumprod(query_unit_vector, norm_medoid)
            if math.isfinite(cos_val):
                raw_cos_by_node[node_id] = cos_val

    has_dense = len(raw_cos_by_node) == len(cached_nodes)
    dense_by_node: dict[str, float] = {}
    if has_dense:
        min_cos = min(raw_cos_by_node.values())
        max_cos = max(raw_cos_by_node.values())
        if len(raw_cos_by_node) >= 2 and (max_cos - min_cos) >= 0.06:
            # Calibrate out the anisotropic cosine baseline floor while preserving uncertainty when flat
            floor = max(0.0, min(0.60, min_cos * 0.95))
            denom = max(1e-6, 1.0 - floor)
            for node_id, cos_val in raw_cos_by_node.items():
                dense_by_node[node_id] = max(0.0, min(1.0, (cos_val - floor) / denom))
        else:
            for node_id, cos_val in raw_cos_by_node.items():
                dense_by_node[node_id] = max(0.0, min(1.0, cos_val))

    max_entity = max((s.get("entity", 0.0) for s in raw_scores_by_node.values()), default=0.0)
    max_sparse = max((s.get("sparse", 0.0) for s in raw_scores_by_node.values()), default=0.0)
    has_temporal_match = bool(query_years) and any(
        s.get("temporal", 0.0) >= 1.0 for s in raw_scores_by_node.values()
    )

    profiles: list[QueryNodeProfile] = []
    for node_id, parent_id, is_leaf, unit_count, _sparse_list, _entities_list in cached_nodes:
        node_raw = raw_scores_by_node[node_id]
        final_scores: dict[str, float] = {}
        if has_dense:
            final_scores["dense"] = dense_by_node[node_id]
        if max_sparse > 0.0 or not has_dense:
            final_scores["sparse"] = node_raw.get("sparse", 0.0)
        if max_entity > 0.0:
            final_scores["entity"] = node_raw.get("entity", 0.0)
        if has_temporal_match:
            final_scores["temporal"] = node_raw.get("temporal", 0.5)
        profiles.append(
            QueryNodeProfile(
                node_id=node_id,
                parent_id=parent_id,
                is_leaf=is_leaf,
                accessible_unit_count=unit_count,
                project_id=project_id,
                source_ids=source_ids,
                document_version_ids=document_version_ids,
                tenant_id=tenant_id,
                partition_id=partition_id,
                tree_version=tree_version,
                scope_fingerprint=fingerprint,
                signal_scores=final_scores,
            )
        )
    return tuple(profiles)


def _aggregate_source_profiles(
    records: list[TreeRoutingProfile],
    *,
    project_id: str,
    tenant_id: str,
    tree_version: str,
    source_ids: tuple[str, ...],
    partition_id: str,
    document_version_ids: tuple[str, ...],
    fingerprint: str,
    query: str,
    manifest_features: Mapping[tuple[str, str], tuple[tuple[float, ...] | None, tuple[int, int] | None]] | None = None,
    query_unit_vector: tuple[float, ...] | None = None,
) -> tuple[QueryNodeProfile, ...] | None:
    cached_nodes = _validate_and_cache_scope_nodes(
        records,
        project_id=project_id,
        tenant_id=tenant_id,
        tree_version=tree_version,
        source_ids=source_ids,
        partition_id=partition_id,
        document_version_ids=document_version_ids,
        fingerprint=fingerprint,
    )
    if cached_nodes is None:
        return None
    return _build_query_node_profiles(
        cached_nodes,
        project_id=project_id,
        tenant_id=tenant_id,
        tree_version=tree_version,
        source_ids=source_ids,
        partition_id=partition_id,
        document_version_ids=document_version_ids,
        fingerprint=fingerprint,
        query=query,
        manifest_features=manifest_features,
        query_unit_vector=query_unit_vector,
    )


async def _resolve_scope_query_vector(
    raw_task_or_vec: Any,
    *,
    wait_seconds: float | None = None,
) -> tuple[float, ...] | None:
    if raw_task_or_vec is None:
        return None
    try:
        if hasattr(raw_task_or_vec, "__await__"):
            shielded = (
                asyncio.shield(raw_task_or_vec)
                if asyncio.isfuture(raw_task_or_vec)
                else raw_task_or_vec
            )
            if wait_seconds is not None:
                if wait_seconds <= 0.01:
                    return None
                vec = await asyncio.wait_for(shielded, timeout=wait_seconds)
            else:
                vec = await shielded
        else:
            vec = raw_task_or_vec
    except (TimeoutError, Exception):  # noqa: BLE001 - fall back to sparse/entity/temporal signals on slow/failed embed
        return None
    if isinstance(vec, Sequence) and not isinstance(vec, (str, bytes)):
        return _normalize_unit_vector(vec)
    return None


async def acquire_tree_query_snapshot(
    *,
    query: str,
    scopes: list[dict[str, object]],
    session_factory=SessionLocal,
    query_vector_tasks_by_scope: Mapping[str, Any] | None = None,
    timeout_seconds: float | None = None,
) -> QueryRoutingSnapshot | None:
    """Capture project pointers and scope-indexed profiles with durable slot leases."""
    if not scopes or len(scopes) > 256:
        return None
    projects = sorted({str(scope.get("project_id") or "") for scope in scopes})
    if any(not project for project in projects):
        return None

    started_monotonic = time.monotonic()
    request_snapshot_id = str(uuid4())
    prepared_groups: list[dict[str, Any]] = []
    manifest_features_by_project: dict[
        str, dict[tuple[str, str], tuple[tuple[float, ...] | None, tuple[int, int] | None]]
    ] = {}
    captured_at: datetime | None = None

    async with session_factory() as session:
        async with session.begin():
            rows = (
                await session.execute(
                    select(ProjectSearchState, TreeManifest, func.now())
                    .options(
                        load_only(
                            TreeManifest.tree_version,
                            TreeManifest.project_id,
                            TreeManifest.config_version,
                            TreeManifest.status,
                            TreeManifest.checksum,
                        )
                    )
                    .join(
                        TreeManifest,
                        (TreeManifest.tree_version == ProjectSearchState.active_tree_version)
                        & (TreeManifest.project_id == ProjectSearchState.project_id),
                    )
                    .where(
                        ProjectSearchState.project_id.in_(projects),
                        ProjectSearchState.active_tree_version.is_not(None),
                    )
                    .order_by(ProjectSearchState.project_id)
                    .with_for_update(read=True, of=ProjectSearchState)
                )
            ).all()
            by_project = {state.project_id: (state, manifest_record) for state, manifest_record, _ in rows}
            if set(by_project) != set(projects):
                return None
            raw_now = rows[0][2]
            captured_at = (
                raw_now.replace(tzinfo=UTC)
                if isinstance(raw_now, datetime) and raw_now.tzinfo is None
                else raw_now.astimezone(UTC)
                if isinstance(raw_now, datetime)
                else await _database_now(session)
            )

            scoped_requests: list[dict[str, Any]] = []
            leases: dict[str, tuple[str, str, int]] = {}
            missing_manifest_keys: list[tuple[str, str]] = []
            for scope in scopes:
                project_id = str(scope["project_id"])
                state, record = by_project[project_id]
                if (
                    not state.active_tree_version
                    or state.active_routing_slot not in {"SLOT_A", "SLOT_B"}
                    or record.status != "ACTIVE"
                    or record.project_id != project_id
                    or record.tree_version != state.active_tree_version
                    or not isinstance(record.checksum, str)
                    or len(record.checksum) != 64
                    or any(character not in "0123456789abcdef" for character in record.checksum)
                    or state.active_tree_version != f"tree-{record.checksum[:24]}"
                    or isinstance(state.active_search_epoch, bool)
                    or state.active_search_epoch < 1
                ):
                    return None
                active_slot_version = (
                    state.slot_a_tree_version if state.active_routing_slot == "SLOT_A" else state.slot_b_tree_version
                )
                if active_slot_version != state.active_tree_version:
                    return None

                manifest_cache_key = (project_id, state.active_tree_version, record.checksum)
                if manifest_cache_key in _MANIFEST_NODE_FEATURES_CACHE:
                    _MANIFEST_NODE_FEATURES_CACHE.move_to_end(manifest_cache_key)
                    manifest_features_by_project[project_id] = _MANIFEST_NODE_FEATURES_CACHE[manifest_cache_key]
                elif (project_id, state.active_tree_version) not in missing_manifest_keys:
                    missing_manifest_keys.append((project_id, state.active_tree_version))

                tenant_id = str(scope.get("tenant_id") or "")
                partition_id = str(scope.get("partition_id") or "")
                raw_source_ids = scope.get("source_ids")
                raw_version_ids = scope.get("document_version_ids")
                if (
                    not tenant_id
                    or not partition_id
                    or not isinstance(raw_source_ids, (list, tuple))
                    or not raw_source_ids
                    or any(not isinstance(value, str) or not value.strip() for value in raw_source_ids)
                    or not isinstance(raw_version_ids, (list, tuple))
                    or not raw_version_ids
                    or any(not isinstance(value, str) or not value.strip() for value in raw_version_ids)
                ):
                    return None
                fingerprint = scope_fingerprint(scope)
                source_ids = tuple(sorted(set(raw_source_ids)))
                version_ids = tuple(sorted(set(raw_version_ids)))
                scoped_requests.append(
                    {
                        "project_id": project_id,
                        "tree_version": state.active_tree_version,
                        "tree_config_version": record.config_version,
                        "routing_slot": state.active_routing_slot,
                        "search_epoch": state.active_search_epoch,
                        "manifest_status": record.status,
                        "manifest_checksum": record.checksum,
                        "source_ids": source_ids,
                        "document_version_ids": version_ids,
                        "tenant_id": tenant_id,
                        "partition_id": partition_id,
                        "scope_fingerprint": fingerprint,
                    }
                )
                leases[project_id] = (
                    state.active_routing_slot,
                    state.active_tree_version,
                    state.active_search_epoch,
                )

            if missing_manifest_keys:
                manifest_rows = (
                    await session.execute(
                        select(
                            TreeManifest.project_id,
                            TreeManifest.tree_version,
                            TreeManifest.checksum,
                            TreeManifest.manifest_json,
                        ).where(
                            tuple_(TreeManifest.project_id, TreeManifest.tree_version).in_(
                                missing_manifest_keys
                            )
                        )
                    )
                ).all()
                for m_proj, m_ver, m_checksum, m_json in manifest_rows:
                    if isinstance(m_json, Mapping) and _checksum_manifest(m_json) == m_checksum:
                        extracted = _extract_manifest_node_features(m_json)
                    else:
                        extracted = {}
                    m_key = (m_proj, m_ver, m_checksum)
                    _MANIFEST_NODE_FEATURES_CACHE[m_key] = extracted
                    _MANIFEST_NODE_FEATURES_CACHE.move_to_end(m_key)
                    while len(_MANIFEST_NODE_FEATURES_CACHE) > _MANIFEST_NODE_FEATURES_CACHE_MAX:
                        _MANIFEST_NODE_FEATURES_CACHE.popitem(last=False)
                    manifest_features_by_project[m_proj] = extracted

            header_by_scope: dict[tuple[str, str, str, str, str], list[tuple[str, str, str, int]]] = defaultdict(list)
            total_profile_count = 0
            for project_id in projects:
                project_requests = [
                    request for request in scoped_requests if request["project_id"] == project_id
                ]
                state = by_project[project_id][0]
                scope_triples = sorted(
                    {
                        (source_id, version_id, request["partition_id"])
                        for request in project_requests
                        for source_id in request["source_ids"]
                        for version_id in request["document_version_ids"]
                    }
                )
                if not scope_triples or len(scope_triples) > settings.search_tree_profile_limit:
                    return None
                remaining_profile_count = settings.search_tree_profile_limit - total_profile_count
                if remaining_profile_count < 0:
                    return None
                scope_filter = tuple_(
                    TreeRoutingProfile.source_id,
                    TreeRoutingProfile.document_version_id,
                    TreeRoutingProfile.partition_id,
                ).in_(scope_triples)
                header_rows = (
                    await session.execute(
                        select(
                            TreeRoutingProfile.project_id,
                            TreeRoutingProfile.tree_version,
                            TreeRoutingProfile.source_id,
                            TreeRoutingProfile.document_version_id,
                            TreeRoutingProfile.partition_id,
                            TreeRoutingProfile.node_id,
                            TreeRoutingProfile.profile_checksum,
                            TreeRoutingProfile.accessible_unit_count,
                        )
                        .where(
                            TreeRoutingProfile.project_id == project_id,
                            TreeRoutingProfile.tree_version == state.active_tree_version,
                            scope_filter,
                        )
                        .order_by(
                            TreeRoutingProfile.source_id,
                            TreeRoutingProfile.document_version_id,
                            TreeRoutingProfile.partition_id,
                            TreeRoutingProfile.node_id,
                        )
                        .limit(remaining_profile_count + 1)
                    )
                ).all()
                if len(header_rows) > remaining_profile_count:
                    return None
                total_profile_count += len(header_rows)
                for h_proj, h_ver, h_src, h_doc_ver, h_part, h_node, h_csum, h_cnt in header_rows:
                    header_by_scope[(h_proj, h_ver, h_src, h_doc_ver, h_part)].append(
                        (h_node, h_doc_ver, h_csum, h_cnt)
                    )

            uncached_requests: list[dict[str, Any]] = []
            uncached_triples_by_project: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
            for request in scoped_requests:
                project_id = request["project_id"]
                tree_version = request["tree_version"]
                source_ids = request["source_ids"]
                partition_id = request["partition_id"]
                matching_headers = [
                    hdr
                    for source_id in source_ids
                    for version_id in request["document_version_ids"]
                    for hdr in header_by_scope.get(
                        (project_id, tree_version, source_id, version_id, partition_id),
                        [],
                    )
                ]
                if len(matching_headers) > settings.search_tree_profile_limit:
                    return None
                cache_sig = (
                    project_id,
                    request["tenant_id"],
                    tree_version,
                    request["scope_fingerprint"],
                    tuple(matching_headers),
                )
                cached_nodes = _AGGREGATED_SCOPE_NODE_CACHE.get(cache_sig)
                if cached_nodes is not None:
                    _AGGREGATED_SCOPE_NODE_CACHE.move_to_end(cache_sig)
                    prepared_groups.append({**request, "cached_nodes": cached_nodes})
                else:
                    uncached_requests.append(request)
                    for source_id in source_ids:
                        for version_id in request["document_version_ids"]:
                            uncached_triples_by_project[project_id].add(
                                (source_id, version_id, partition_id)
                            )

            if uncached_requests:
                profiles_by_scope: dict[tuple[str, str, str, str, str], list[TreeRoutingProfile]] = defaultdict(list)
                for project_id, triples_set in uncached_triples_by_project.items():
                    state = by_project[project_id][0]
                    scope_filter = tuple_(
                        TreeRoutingProfile.source_id,
                        TreeRoutingProfile.document_version_id,
                        TreeRoutingProfile.partition_id,
                    ).in_(sorted(triples_set))
                    profile_rows = (
                        await session.scalars(
                            select(TreeRoutingProfile)
                            .where(
                                TreeRoutingProfile.project_id == project_id,
                                TreeRoutingProfile.tree_version == state.active_tree_version,
                                scope_filter,
                            )
                            .order_by(
                                TreeRoutingProfile.source_id,
                                TreeRoutingProfile.document_version_id,
                                TreeRoutingProfile.partition_id,
                                TreeRoutingProfile.node_id,
                            )
                        )
                    ).all()
                    for profile in profile_rows:
                        profiles_by_scope[
                            (
                                profile.project_id,
                                profile.tree_version,
                                profile.source_id,
                                profile.document_version_id,
                                profile.partition_id,
                            )
                        ].append(profile)

                for request in uncached_requests:
                    project_id = request["project_id"]
                    tree_version = request["tree_version"]
                    source_ids = request["source_ids"]
                    partition_id = request["partition_id"]
                    matching_profiles = [
                        profile
                        for source_id in source_ids
                        for version_id in request["document_version_ids"]
                        for profile in profiles_by_scope.get(
                            (
                                project_id,
                                tree_version,
                                source_id,
                                version_id,
                                partition_id,
                            ),
                            [],
                        )
                    ]
                    if len(matching_profiles) > settings.search_tree_profile_limit:
                        return None
                    cached_nodes = _validate_and_cache_scope_nodes(
                        matching_profiles,
                        project_id=project_id,
                        tenant_id=request["tenant_id"],
                        tree_version=tree_version,
                        source_ids=source_ids,
                        partition_id=partition_id,
                        document_version_ids=request["document_version_ids"],
                        fingerprint=request["scope_fingerprint"],
                    )
                    if cached_nodes is None:
                        return None
                    prepared_groups.append({**request, "cached_nodes": cached_nodes})

            database_now = captured_at
            expires_at = database_now + timedelta(
                seconds=max(30.0, float(settings.search_source_timeout) + 15.0)
            )
            for project_id, (slot, tree_version, epoch) in leases.items():
                session.add(
                    TreeSnapshotLease(
                        request_snapshot_id=request_snapshot_id,
                        project_id=project_id,
                        routing_slot=slot,
                        tree_version=tree_version,
                        search_epoch=epoch,
                        expires_at=expires_at,
                    )
                )

    # Compute dense/sparse/entity/temporal scores outside the DB transaction so locks are never held during I/O
    try:
        raw_query_terms = set(_expand_continuous_script_tokens(_TOKEN_PATTERN.findall(query.casefold())))
        informative_terms = {
            t for t in raw_query_terms if t not in COMMON_STOPWORDS and (len(t) >= 2 or t.isdigit())
        }
        query_terms = informative_terms or raw_query_terms
        folded_query = query.casefold()
        query_years = tuple(
            sorted({int(m) for m in _QUERY_YEAR_RE.findall(query) if 1900 <= int(m) <= 2100})
        )

        unit_vectors_by_scope: dict[str, tuple[float, ...] | None] = {}
        if query_vector_tasks_by_scope:
            # Deduplicate underlying awaitables so shared embedding configs are awaited once
            unique_tasks: dict[int, tuple[Any, list[str]]] = {}
            for fp, task_obj in query_vector_tasks_by_scope.items():
                if task_obj is None:
                    continue
                obj_id = id(task_obj)
                if obj_id not in unique_tasks:
                    unique_tasks[obj_id] = (task_obj, [fp])
                else:
                    unique_tasks[obj_id][1].append(fp)
            for task_obj, fps in unique_tasks.values():
                rem_wait: float | None = None
                if timeout_seconds is not None:
                    rem_wait = float(timeout_seconds) - (time.monotonic() - started_monotonic) - 0.12
                norm_vec = await _resolve_scope_query_vector(task_obj, wait_seconds=rem_wait)
                for fp in fps:
                    unit_vectors_by_scope[fp] = norm_vec

        assert captured_at is not None
        group_rows: list[GroupRoutingSnapshot] = []
        for item in prepared_groups:
            fp = item["scope_fingerprint"]
            project_id = item["project_id"]
            query_profiles = _build_query_node_profiles(
                item["cached_nodes"],
                project_id=project_id,
                tenant_id=item["tenant_id"],
                tree_version=item["tree_version"],
                source_ids=item["source_ids"],
                partition_id=item["partition_id"],
                document_version_ids=item["document_version_ids"],
                fingerprint=fp,
                query=query,
                manifest_features=manifest_features_by_project.get(project_id),
                query_unit_vector=unit_vectors_by_scope.get(fp),
                query_terms=query_terms,
                folded_query=folded_query,
                query_years=query_years,
            )
            group_rows.append(
                GroupRoutingSnapshot(
                    snapshot_id=str(uuid4()),
                    captured_at=captured_at,
                    project_id=project_id,
                    source_ids=item["source_ids"],
                    document_version_ids=item["document_version_ids"],
                    tenant_id=item["tenant_id"],
                    partition_id=item["partition_id"],
                    scope_fingerprint=fp,
                    tree_version=item["tree_version"],
                    tree_config_version=item["tree_config_version"],
                    routing_slot=item["routing_slot"],
                    search_epoch=item["search_epoch"],
                    manifest_status=item["manifest_status"],
                    manifest_checksum=item["manifest_checksum"],
                    manifest_verified=True,
                    profiles=query_profiles,
                )
            )
        return QueryRoutingSnapshot(
            snapshot_id=request_snapshot_id,
            captured_at=captured_at,
            groups=tuple(group_rows),
        )
    except BaseException:
        try:
            await release_tree_query_snapshot(request_snapshot_id, session_factory=session_factory)
        except Exception:  # noqa: BLE001 - preserve original exception
            pass
        raise


async def release_tree_query_snapshot(request_snapshot_id: str, *, session_factory=SessionLocal) -> None:
    if not request_snapshot_id:
        return
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                delete(TreeSnapshotLease).where(
                    TreeSnapshotLease.request_snapshot_id == request_snapshot_id
                )
            )
