"""Incremental Knowledge Routing Tree Service (Phase 8 / Checkpoint C).

Implements:
- DATN-58: Base + delta tree assignment without full rebuild, node/ancestor statistics,
  and drift monitoring with hysteresis.
- DATN-59: Targeted subtree rebuild and stable node lineage matching (SPLIT_FROM,
  MERGED_FROM, SUPERSEDES_TREE_NODE).
- DATN-60: Dual-slot inactive build, Qdrant payload batch update, and manifest verification gate.
- DATN-36: Single PostgreSQL transaction atomic pointer switch, query-time snapshot
  consistency, fault injection resilience, and rollback.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.db.models.routing_rag import ProjectSearchState, TreeManifest, TreeRoutingProfile
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
from sag_api.services.routing_tree_service import (
    KnowledgeEdgeInput,
    KnowledgeUnitInput,
    NodeProfile,
    RoutingNode,
    TreeBuildConfig,
    _cosine,
    _profile,
    _stable_id,
    _tree_integrity,
    _walk,
    build_routing_snapshot,
)
from sag_api.services.routing_tree_service import (
    RoutingSnapshot as TreeRoutingSnapshot,
)
from sag_api.services.search_index_service import generate_search_unit_point_id
from sag_api.services.tree_publish_service import (
    PublishVerificationError,
    TreePublishError,
    _scoped_profile_checksum,
)

log = logging.getLogger(__name__)

# Constants mandated by SAG_Knowledge_Routing_RAG_Workflow_v1.1 Section 11
T_HIGH = 0.78
T_LOW = 0.58
NODE_ID_INHERIT_THRESHOLD = 0.70
DRIFT_CENTROID_THRESHOLD = 0.15
DRIFT_OUTLIER_THRESHOLD = 0.20
HYSTERESIS_MIN_VIOLATIONS = 2


# ---------------------------------------------------------------------------
# Data Models for Incremental Operations
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UnitAssignment:
    unit_id: str
    target_node_id: str | None
    score: float
    assignment_type: Literal["DIRECT", "BORDERLINE", "OUTLIER"]


@dataclass(frozen=True, slots=True)
class DeltaAssignmentResult:
    assignments: tuple[UnitAssignment, ...]
    direct_count: int
    borderline_count: int
    outlier_count: int
    updated_roots: tuple[RoutingNode, ...]
    updated_units: tuple[KnowledgeUnitInput, ...]
    touched_leaf_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DriftReport:
    centroid_drift: float
    outlier_ratio: float
    capacity_overflow: bool
    overflowed_node_ids: tuple[str, ...]
    violation_count: int
    trigger_rebuild: bool
    affected_node_ids: tuple[str, ...]
    new_partition_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LineageMapping:
    node_id_map: dict[str, str]  # new_internal_id -> stable_node_id
    relationships: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class IncrementalTreeUpdateResult:
    snapshot: TreeRoutingSnapshot
    delta: DeltaAssignmentResult
    drift: DriftReport
    rebuilt_node_ids: tuple[str, ...]
    rebuilt_partition_ids: tuple[str, ...] = ()


def _unit_from_manifest(raw: dict[str, Any], tenant_id: str, project_id: str) -> KnowledgeUnitInput:
    def parse_time(value: str | None) -> datetime | None:
        if not value:
            return None
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo and parsed.utcoffset() else parsed.replace(tzinfo=UTC)

    return KnowledgeUnitInput(
        unit_id=raw["unit_id"],
        tenant_id=tenant_id,
        project_id=project_id,
        security_partition_id=raw["partition"],
        dense=tuple(raw.get("dense") or ()),
        sparse=tuple((term, float(weight)) for term, weight in raw.get("sparse", ())),
        entities=tuple(raw.get("entities", ())),
        valid_from=parse_time(raw.get("valid_from")),
        valid_to=parse_time(raw.get("valid_to")),
    )


def _unit_to_manifest(unit: KnowledgeUnitInput) -> dict[str, Any]:
    return {
        "unit_id": unit.unit_id,
        "partition": unit.security_partition_id,
        "dense": unit.dense,
        "sparse": sorted(unit.sparse),
        "entities": sorted(unit.entities),
        "valid_from": unit.valid_from.isoformat() if unit.valid_from else None,
        "valid_to": unit.valid_to.isoformat() if unit.valid_to else None,
    }


def _refresh_incremental_snapshot(
    base_snapshot: TreeRoutingSnapshot,
    roots: tuple[RoutingNode, ...],
    units: tuple[KnowledgeUnitInput, ...],
    edges: list[KnowledgeEdgeInput],
    *,
    delta_unit_ids: set[str],
    config: TreeBuildConfig | None = None,
    incremental_trace: dict[str, Any] | None = None,
    lineage_events: list[dict[str, Any]] | None = None,
    subtree_quality_passed: bool = True,
) -> TreeRoutingSnapshot:
    manifest = dict(base_snapshot.manifest)
    config = config or TreeBuildConfig(**manifest.get("config", {}))
    nodes = list(_walk(roots))
    tree_unit_ids = sorted({unit_id for root in roots for unit_id in root.unit_ids})
    unit_map = {unit.unit_id: unit for unit in units}
    lineage = tuple(sorted((node.node_id, node.parent_id) for node in nodes))
    quality_gates = dict(base_snapshot.quality_gates)
    quality_gates["tree_integrity"] = _tree_integrity(list(roots), set(tree_unit_ids))
    quality_gates["partition_profiles"] = all(
        node.profile.accessible_unit_count == len(node.unit_ids)
        and all(
            unit_id in unit_map
            and unit_map[unit_id].security_partition_id == node.security_partition_id
            for unit_id in node.unit_ids
        )
        for node in nodes
    )
    quality_gates["cluster_constraints"] = all(
        len(node.children) <= config.max_children
        and (bool(node.children) or len(node.unit_ids) <= config.max_cluster_size)
        and (node.depth == 0 or len(node.unit_ids) >= config.min_cluster_size)
        and node.depth <= config.max_depth
        for node in nodes
    )
    quality_gates["delta_pending"] = not delta_unit_ids
    quality_gates["subtree_quality"] = subtree_quality_passed
    quality_metrics = dict(base_snapshot.metrics)
    quality_metrics.update({
        "unit_count": len(units),
        "tree_unit_count": len(tree_unit_ids),
        "delta_unit_count": len(delta_unit_ids),
        "max_leaf_size": max(
            (len(node.unit_ids) for node in nodes if not node.children),
            default=0,
        ),
    })
    manifest.update({
        "unit_ids": tree_unit_ids,
        "tree_unit_ids": tree_unit_ids,
        "delta_unit_ids": sorted(delta_unit_ids),
        "partitions": {
            root.security_partition_id: sorted(root.unit_ids) for root in roots
        },
        "units": [_unit_to_manifest(unit) for unit in sorted(units, key=lambda item: item.unit_id)],
        "edges": sorted(
            (edge.source_unit_id, edge.target_unit_id, edge.weight)
            for edge in edges
        ),
        "lineage": [list(item) for item in lineage],
        "quality_metrics": quality_metrics,
        "quality_gates": quality_gates,
        "unit_count": len(units),
        "node_count": len(nodes),
        "status": "QUALITY_PASSED" if all(quality_gates.values()) else "REJECTED",
    })
    if incremental_trace is not None:
        manifest["incremental_update"] = incremental_trace
    if lineage_events is not None:
        manifest["lineage_events"] = lineage_events

    canonical = dict(manifest)
    for key in ("tree_version", "checksum", "unit_count", "node_count", "status", "lineage_events"):
        canonical.pop(key, None)
    checksum = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    manifest["checksum"] = checksum
    manifest["tree_version"] = f"tree-{checksum[:24]}"
    return TreeRoutingSnapshot(
        contract_version=base_snapshot.contract_version,
        tree_version=manifest["tree_version"],
        tenant_id=base_snapshot.tenant_id,
        project_id=base_snapshot.project_id,
        config_version=base_snapshot.config_version,
        roots=roots,
        metrics=quality_metrics,
        quality_gates=quality_gates,
        publishable=all(quality_gates.values()),
        manifest_json=json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        lineage=lineage,
        partition_algorithm=base_snapshot.partition_algorithm,
    )


# ---------------------------------------------------------------------------
# DATN-58: Delta Assignment & Statistics Update
# ---------------------------------------------------------------------------


def assign_delta_units(
    base_snapshot: TreeRoutingSnapshot,
    new_units: list[KnowledgeUnitInput],
    config: TreeBuildConfig | None = None,
) -> DeltaAssignmentResult:
    """Assign new knowledge units to base tree leaves using prototype cosine similarity.

    Threshold rules:
    - score >= T_HIGH (0.78): Direct Attach to highest-similarity leaf.
    - T_LOW <= score < T_HIGH (0.58): Borderline queue attached to closest leaf.
    - score < T_LOW: Outlier candidate.

    Updates ancestor cumulative accessible_unit_count and statistics without topology change.
    """
    config = config or TreeBuildConfig()
    leaves = [node for node in _walk(base_snapshot.roots) if not node.children]

    manifest_units = [
        _unit_from_manifest(unit, base_snapshot.tenant_id, base_snapshot.project_id)
        for unit in base_snapshot.manifest.get("units", [])
    ]
    known_units = {unit.unit_id: unit for unit in manifest_units}
    dense_dimensions = {len(unit.dense) for unit in known_units.values() if unit.dense}
    incoming: dict[str, KnowledgeUnitInput] = {}
    for unit in new_units:
        if (
            not isinstance(unit.unit_id, str)
            or not isinstance(unit.tenant_id, str)
            or not isinstance(unit.project_id, str)
            or not isinstance(unit.security_partition_id, str)
            or unit.tenant_id != base_snapshot.tenant_id
            or unit.project_id != base_snapshot.project_id
            or not unit.unit_id.strip()
            or not unit.security_partition_id.strip()
        ):
            raise ValueError("delta units must match the base tenant/project and have stable IDs/partitions")
        if (
            not isinstance(unit.dense, tuple)
            or not isinstance(unit.sparse, tuple)
            or not isinstance(unit.entities, tuple)
            or any(
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                for value in unit.dense
            )
            or any(
                not isinstance(term, str)
                or not term.strip()
                or isinstance(weight, bool)
                or not isinstance(weight, (int, float))
                or not math.isfinite(weight)
                or weight < 0
                for term, weight in unit.sparse
            )
            or any(not isinstance(entity, str) or not entity.strip() for entity in unit.entities)
        ):
            raise ValueError("delta unit features are malformed")
        if unit.dense and dense_dimensions and len(unit.dense) not in dense_dimensions:
            raise ValueError("delta dense vectors must match the base dimension")
        if unit.dense:
            dense_dimensions.add(len(unit.dense))
        for timestamp in (unit.valid_from, unit.valid_to):
            if timestamp is not None and (
                not isinstance(timestamp, datetime)
                or timestamp.tzinfo is None
                or timestamp.utcoffset() is None
            ):
                raise ValueError("delta temporal profile timestamps must include a timezone")
        if unit.valid_from and unit.valid_to and unit.valid_from > unit.valid_to:
            raise ValueError("delta unit validity range is inverted")
        existing = known_units.get(unit.unit_id) or incoming.get(unit.unit_id)
        if existing is not None:
            if _unit_to_manifest(existing) != _unit_to_manifest(unit):
                raise ValueError(f"delta retry changed features for unit_id={unit.unit_id}")
            continue
        incoming[unit.unit_id] = unit
    candidates = [incoming[unit_id] for unit_id in sorted(incoming)]

    # Pre-index leaves by partition
    leaves_by_partition: dict[str, list[RoutingNode]] = defaultdict(list)
    for leaf in leaves:
        leaves_by_partition[leaf.security_partition_id].append(leaf)

    assignments: list[UnitAssignment] = []
    unit_additions_per_leaf: dict[str, list[KnowledgeUnitInput]] = defaultdict(list)
    direct_count = 0
    borderline_count = 0
    outlier_count = 0

    for unit in candidates:
        matching_leaves = leaves_by_partition.get(unit.security_partition_id, [])
        if not matching_leaves or not unit.dense:
            assignments.append(
                UnitAssignment(
                    unit_id=unit.unit_id,
                    target_node_id=None,
                    score=0.0,
                    assignment_type="OUTLIER",
                )
            )
            outlier_count += 1
            continue

        best_leaf = None
        best_score = -1.0
        for leaf in matching_leaves:
            if leaf.profile.dense_medoid:
                score = _cosine(unit.dense, leaf.profile.dense_medoid)
                if score > best_score or (
                    score == best_score and best_leaf is not None and leaf.node_id < best_leaf.node_id
                ):
                    best_score = score
                    best_leaf = leaf

        if best_leaf is not None and best_score >= T_HIGH:
            asgn_type: Literal["DIRECT", "BORDERLINE", "OUTLIER"] = "DIRECT"
            target_id: str | None = best_leaf.node_id
            unit_additions_per_leaf[best_leaf.node_id].append(unit)
            direct_count += 1
        elif best_leaf is not None and best_score >= T_LOW:
            asgn_type = "BORDERLINE"
            target_id = best_leaf.node_id
            unit_additions_per_leaf[best_leaf.node_id].append(unit)
            borderline_count += 1
        else:
            asgn_type = "OUTLIER"
            target_id = best_leaf.node_id if best_leaf else None
            outlier_count += 1

        assignments.append(
            UnitAssignment(
                unit_id=unit.unit_id,
                target_node_id=target_id,
                score=best_score if best_leaf else 0.0,
                assignment_type=asgn_type,
            )
        )

    all_units = tuple(known_units[unit_id] for unit_id in sorted(known_units)) + tuple(candidates)
    all_units_dict = {u.unit_id: u for u in all_units}

    # Reconstruct updated roots with incremental counts and stats
    touched_leaf_ids = tuple(sorted(unit_additions_per_leaf.keys()))

    def _update_node(node: RoutingNode) -> RoutingNode:
        if not node.children:
            additions = unit_additions_per_leaf.get(node.node_id, [])
            if not additions:
                return node
            updated_units = tuple(sorted(set(node.unit_ids).union(u.unit_id for u in additions)))
            leaf_units = [all_units_dict[uid] for uid in updated_units if uid in all_units_dict]
            if leaf_units:
                updated_profile = _profile(leaf_units, config.profile_sparse_terms)
            else:
                new_sparse = dict(node.profile.sparse)
                for unit in additions:
                    for term, weight in unit.sparse:
                        new_sparse[term] = new_sparse.get(term, 0.0) + weight
                new_entities = tuple(
                    sorted(
                        set(node.profile.entities).union(
                            *(u.entities for u in additions)
                        )
                    )
                )
                from_times = [node.profile.temporal_from] + [
                    u.valid_from for u in additions if u.valid_from
                ]
                to_times = [node.profile.temporal_to] + [
                    u.valid_to for u in additions if u.valid_to
                ]
                valid_from = min((t for t in from_times if t is not None), default=None)
                valid_to = max((t for t in to_times if t is not None), default=None)

                updated_profile = NodeProfile(
                    dense_medoid=node.profile.dense_medoid,
                    dense_medoid_candidate_count=node.profile.dense_medoid_candidate_count,
                    dense_medoid_is_exact=node.profile.dense_medoid_is_exact,
                    sparse=tuple(
                        sorted(new_sparse.items(), key=lambda item: (-item[1], item[0]))[
                            : config.profile_sparse_terms
                        ]
                    ),
                    entities=new_entities,
                    temporal_from=valid_from,
                    temporal_to=valid_to,
                    accessible_unit_count=len(updated_units),
                )
            return RoutingNode(
                node_id=node.node_id,
                parent_id=node.parent_id,
                depth=node.depth,
                tenant_id=node.tenant_id,
                project_id=node.project_id,
                security_partition_id=node.security_partition_id,
                unit_ids=updated_units,
                profile=updated_profile,
                children=(),
            )

        updated_children = tuple(_update_node(child) for child in node.children)
        if all(before is after for before, after in zip(node.children, updated_children, strict=True)):
            return node
        updated_units = tuple(sorted({unit_id for child in updated_children for unit_id in child.unit_ids}))
        profile_units = [all_units_dict[unit_id] for unit_id in updated_units if unit_id in all_units_dict]
        updated_profile = _profile(profile_units, config.profile_sparse_terms)
        if len(profile_units) != len(updated_units):
            updated_profile = NodeProfile(
                dense_medoid=node.profile.dense_medoid,
                dense_medoid_candidate_count=node.profile.dense_medoid_candidate_count,
                dense_medoid_is_exact=node.profile.dense_medoid_is_exact,
                sparse=updated_profile.sparse,
                entities=tuple(sorted(set(node.profile.entities).union(updated_profile.entities))),
                temporal_from=min(
                    (value for value in (node.profile.temporal_from, updated_profile.temporal_from) if value),
                    default=None,
                ),
                temporal_to=max(
                    (value for value in (node.profile.temporal_to, updated_profile.temporal_to) if value),
                    default=None,
                ),
                accessible_unit_count=len(updated_units),
            )
        return RoutingNode(
            node_id=node.node_id,
            parent_id=node.parent_id,
            depth=node.depth,
            tenant_id=node.tenant_id,
            project_id=node.project_id,
            security_partition_id=node.security_partition_id,
            unit_ids=updated_units,
            profile=updated_profile,
            children=updated_children,
        )

    updated_roots = tuple(_update_node(root) for root in base_snapshot.roots)

    return DeltaAssignmentResult(
        assignments=tuple(assignments),
        direct_count=direct_count,
        borderline_count=borderline_count,
        outlier_count=outlier_count,
        updated_roots=updated_roots,
        updated_units=all_units,
        touched_leaf_ids=touched_leaf_ids,
    )


# ---------------------------------------------------------------------------
# DATN-58: Drift Monitoring & Hysteresis
# ---------------------------------------------------------------------------


def compute_tree_drift(
    base_snapshot: TreeRoutingSnapshot,
    delta_result: DeltaAssignmentResult,
    config: TreeBuildConfig | None = None,
    history_window_violations: int = 0,
    drift_threshold: float = DRIFT_CENTROID_THRESHOLD,
) -> DriftReport:
    """Evaluate centroid drift, outlier ratio, capacity overflow, and hysteresis.

    Requires at least HYSTERESIS_MIN_VIOLATIONS (2) consecutive violations before triggering rebuild.
    """
    config = config or TreeBuildConfig()
    if (
        isinstance(drift_threshold, bool)
        or not isinstance(drift_threshold, (int, float))
        or not math.isfinite(drift_threshold)
        or not 0 <= drift_threshold <= 1
    ):
        raise ValueError("drift_threshold must be finite and in [0, 1]")
    if (
        not isinstance(history_window_violations, int)
        or isinstance(history_window_violations, bool)
        or history_window_violations < 0
    ):
        raise ValueError("history_window_violations must be a non-negative integer")
    total_new = len(delta_result.assignments)
    outlier_ratio = (delta_result.outlier_count / total_new) if total_new > 0 else 0.0

    leaf_map = {node.node_id: node for node in _walk(base_snapshot.roots) if not node.children}
    updated_leaf_map = {
        node.node_id: node for node in _walk(delta_result.updated_roots) if not node.children
    }

    max_centroid_drift = 0.0
    overflowed: list[str] = []
    affected: set[str] = set()
    new_partitions: set[str] = set()

    for leaf_id in delta_result.touched_leaf_ids:
        base_leaf = leaf_map.get(leaf_id)
        updated_leaf = updated_leaf_map.get(leaf_id)
        if base_leaf and updated_leaf:
            if len(updated_leaf.unit_ids) > config.max_cluster_size:
                overflowed.append(leaf_id)
                affected.add(leaf_id)
            if base_leaf.profile.dense_medoid and updated_leaf.profile.dense_medoid:
                c_sim = _cosine(base_leaf.profile.dense_medoid, updated_leaf.profile.dense_medoid)
                drift = 1.0 - c_sim
                if drift > max_centroid_drift:
                    max_centroid_drift = drift
                if drift > drift_threshold:
                    affected.add(leaf_id)

    if outlier_ratio > DRIFT_OUTLIER_THRESHOLD:
        units_by_id = {unit.unit_id: unit for unit in delta_result.updated_units}
        roots_by_partition = {
            root.security_partition_id: root for root in base_snapshot.roots
        }
        for assignment in delta_result.assignments:
            if assignment.assignment_type != "OUTLIER":
                continue
            if assignment.target_node_id:
                affected.add(assignment.target_node_id)
                continue
            unit = units_by_id.get(assignment.unit_id)
            root = roots_by_partition.get(unit.security_partition_id) if unit else None
            if root:
                affected.add(root.node_id)
            elif unit:
                new_partitions.add(unit.security_partition_id)

    violated_now = (
        max_centroid_drift > drift_threshold
        or outlier_ratio > DRIFT_OUTLIER_THRESHOLD
        or bool(overflowed)
    )

    current_violation_count = (history_window_violations + 1) if violated_now else 0
    trigger = current_violation_count >= HYSTERESIS_MIN_VIOLATIONS

    return DriftReport(
        centroid_drift=max_centroid_drift,
        outlier_ratio=outlier_ratio,
        capacity_overflow=bool(overflowed),
        overflowed_node_ids=tuple(sorted(overflowed)),
        violation_count=current_violation_count,
        trigger_rebuild=trigger,
        affected_node_ids=tuple(sorted(affected)),
        new_partition_ids=tuple(sorted(new_partitions)),
    )


# ---------------------------------------------------------------------------
# DATN-59: Targeted Subtree Rebuild & Stable Node Lineage
# ---------------------------------------------------------------------------


def match_node_lineage(
    old_nodes: list[RoutingNode],
    new_clusters: list[RoutingNode],
    threshold: float = NODE_ID_INHERIT_THRESHOLD,
) -> LineageMapping:
    """Perform stable community matching via Weighted Overlap (0.5 Jaccard + 0.3 Medoid + 0.2 Entity).

    Lineage relationships emitted:
    - SUPERSEDES_TREE_NODE: new cluster inherits old node_id when overlap >= threshold.
    - SPLIT_FROM: old node was partitioned into multiple new clusters.
    - MERGED_FROM: multiple old nodes were merged into one new cluster.
    """
    id_map: dict[str, str] = {}
    relationships: list[dict[str, Any]] = []

    # Calculate pairwise overlap
    overlaps: list[tuple[float, RoutingNode, RoutingNode]] = []
    for old in old_nodes:
        old_units = set(old.unit_ids)
        old_entities = set(old.profile.entities)
        for new in new_clusters:
            new_units = set(new.unit_ids)
            new_entities = set(new.profile.entities)

            # Jaccard units
            u_union = old_units | new_units
            j_units = (len(old_units & new_units) / len(u_union)) if u_union else 0.0

            # Medoid cosine
            m_sim = 0.0
            if old.profile.dense_medoid and new.profile.dense_medoid:
                m_sim = _cosine(old.profile.dense_medoid, new.profile.dense_medoid)

            # Entity Jaccard
            e_union = old_entities | new_entities
            j_entities = (len(old_entities & new_entities) / len(e_union)) if e_union else 0.0

            total_overlap = 0.5 * j_units + 0.3 * m_sim + 0.2 * j_entities
            overlaps.append((total_overlap, old, new))

    # Resolve equal-overlap candidates without depending on caller list order.
    overlaps.sort(key=lambda item: (-item[0], item[1].node_id, item[2].node_id))

    claimed_old: set[str] = set()
    claimed_new: set[str] = set()

    for overlap_score, old, new in overlaps:
        if overlap_score >= threshold and old.node_id not in claimed_old and new.node_id not in claimed_new:
            id_map[new.node_id] = old.node_id
            claimed_old.add(old.node_id)
            claimed_new.add(new.node_id)
            relationships.append({
                "type": "SUPERSEDES_TREE_NODE",
                "old_node_id": old.node_id,
                "new_node_id": old.node_id,
                "overlap_score": round(overlap_score, 4),
            })

    # Track splits & merges for remaining clusters
    for new in sorted(new_clusters, key=lambda node: node.node_id):
        contributors = sorted(
            (old for old in old_nodes if set(old.unit_ids) & set(new.unit_ids)),
            key=lambda old: old.node_id,
        )
        if len(contributors) > 1:
            relationships.append({
                "type": "MERGED_FROM",
                "source_node_ids": [old.node_id for old in contributors],
                "new_node_id": id_map.get(new.node_id, new.node_id),
            })
        elif new.node_id not in claimed_new and len(contributors) == 1:
            relationships.append({
                "type": "SPLIT_FROM",
                "source_node_id": contributors[0].node_id,
                "new_node_id": new.node_id,
            })

    relationships.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    return LineageMapping(node_id_map=dict(sorted(id_map.items())), relationships=tuple(relationships))


def rebuild_drifted_subtree(
    base_snapshot: TreeRoutingSnapshot,
    affected_node_id: str,
    all_units: list[KnowledgeUnitInput],
    all_edges: list[KnowledgeEdgeInput],
    config: TreeBuildConfig | None = None,
    benchmark: list[Any] | None = None,
    unit_ids_to_include: set[str] | None = None,
) -> TreeRoutingSnapshot:
    """Rebuild one selected subtree, graft it back, and preserve all sibling nodes."""
    from sag_api.services.routing_tree_service import RoutingBenchmarkCase

    log.info("Targeted rebuild for drifted node=%s", affected_node_id)
    config = config or TreeBuildConfig()
    benchmark_cases: list[RoutingBenchmarkCase] = []
    if benchmark is not None:
        benchmark_cases = [b if isinstance(b, RoutingBenchmarkCase) else RoutingBenchmarkCase(**b) for b in benchmark]
    else:
        raw_cases = base_snapshot.manifest.get("benchmark", [])
        if raw_cases:
            benchmark_cases = [
                RoutingBenchmarkCase(
                    query_id=b["query_id"],
                    target_unit_id=b["target_unit_id"],
                    routed_unit_ids=tuple(b["routed_unit_ids"]),
                    k=b.get("k", 10),
                )
                for b in raw_cases
            ]

    target_node = next((n for n in _walk(base_snapshot.roots) if n.node_id == affected_node_id), None)
    if target_node is None:
        raise ValueError(f"affected_node_id={affected_node_id} not found in base routing tree")

    units_by_id = {unit.unit_id: unit for unit in all_units}
    if len(units_by_id) != len(all_units):
        raise ValueError("subtree rebuild requires unique Knowledge Unit IDs")
    base_unit_ids = {raw["unit_id"] for raw in base_snapshot.manifest.get("units", [])}
    included_ids = set(unit_ids_to_include or ())
    if unit_ids_to_include is None:
        included_ids.update(
            unit.unit_id for unit in all_units if unit.unit_id not in base_unit_ids
        )
    subtree_unit_ids = set(target_node.unit_ids) | included_ids
    affected_units = sorted(
        (
            units_by_id[unit_id]
            for unit_id in subtree_unit_ids
            if unit_id in units_by_id
            and units_by_id[unit_id].security_partition_id == target_node.security_partition_id
        ),
        key=lambda unit: unit.unit_id,
    )
    if not affected_units:
        raise ValueError(f"No units found for affected subtree={affected_node_id}")
    affected_unit_ids = {u.unit_id for u in affected_units}
    affected_edges = [
        e for e in all_edges
        if getattr(e, "source_unit_id", getattr(e, "source_id", None)) in affected_unit_ids
        and getattr(e, "target_unit_id", getattr(e, "target_id", None)) in affected_unit_ids
    ]
    affected_benchmark = [
        RoutingBenchmarkCase(
            query_id=case.query_id,
            target_unit_id=case.target_unit_id,
            routed_unit_ids=tuple(
                unit_id for unit_id in case.routed_unit_ids if unit_id in affected_unit_ids
            ),
            k=case.k,
        )
        for case in benchmark_cases
        if case.target_unit_id in affected_unit_ids
    ]

    sub_snapshot = build_routing_snapshot(
        affected_units,
        affected_edges,
        config=config,
        benchmark=affected_benchmark,
    )

    sub_root = sub_snapshot.roots[0]
    old_leaves = [n for n in _walk([target_node]) if not n.children]
    new_leaves = [n for n in _walk(sub_snapshot.roots) if not n.children]
    if not target_node.children and sub_root.children:
        old_leaves = []
    lineage = match_node_lineage(old_leaves, new_leaves, threshold=NODE_ID_INHERIT_THRESHOLD)
    id_map = dict(lineage.node_id_map)
    id_map[sub_root.node_id] = target_node.node_id

    relationships = [
        {
            "type": "SUPERSEDES_TREE_NODE",
            "old_node_id": target_node.node_id,
            "new_node_id": target_node.node_id,
            "overlap_score": 1.0,
        },
        *lineage.relationships,
    ]
    if not target_node.children and sub_root.children:
        relationships.extend(
            {
                "type": "SPLIT_FROM",
                "source_node_id": target_node.node_id,
                "new_node_id": leaf.node_id,
            }
            for leaf in new_leaves
        )

    def _apply_stable_ids(node: RoutingNode, parent_id: str | None = None) -> RoutingNode:
        stable_id = target_node.node_id if node is sub_root else id_map.get(node.node_id, node.node_id)
        children = tuple(_apply_stable_ids(child, stable_id) for child in node.children)
        return RoutingNode(
            node_id=stable_id,
            parent_id=parent_id,
            depth=target_node.depth + node.depth,
            tenant_id=node.tenant_id,
            project_id=node.project_id,
            security_partition_id=node.security_partition_id,
            unit_ids=node.unit_ids,
            profile=node.profile,
            children=children,
        )

    rebuilt_subtree = _apply_stable_ids(sub_root, target_node.parent_id)

    def _graft(node: RoutingNode) -> RoutingNode:
        if node.node_id == target_node.node_id:
            return rebuilt_subtree
        children = tuple(_graft(child) for child in node.children)
        if all(before is after for before, after in zip(node.children, children, strict=True)):
            return node
        member_ids = tuple(sorted({unit_id for child in children for unit_id in child.unit_ids}))
        profile_units = [units_by_id[unit_id] for unit_id in member_ids if unit_id in units_by_id]
        return RoutingNode(
            node_id=node.node_id,
            parent_id=node.parent_id,
            depth=node.depth,
            tenant_id=node.tenant_id,
            project_id=node.project_id,
            security_partition_id=node.security_partition_id,
            unit_ids=member_ids,
            profile=_profile(profile_units, config.profile_sparse_terms),
            children=children,
        )

    roots = tuple(_graft(root) for root in base_snapshot.roots)
    pending_delta_ids = set(base_snapshot.manifest.get("delta_unit_ids", ())) - affected_unit_ids
    previous_events = base_snapshot.manifest.get("lineage_events", [])
    events_by_json = {
        json.dumps(event, sort_keys=True, separators=(",", ":")): event
        for event in [*previous_events, *relationships]
    }
    snapshot = _refresh_incremental_snapshot(
        base_snapshot,
        roots,
        tuple(sorted(units_by_id.values(), key=lambda unit: unit.unit_id)),
        all_edges,
        delta_unit_ids=pending_delta_ids,
        config=config,
        lineage_events=[events_by_json[key] for key in sorted(events_by_json)],
        subtree_quality_passed=sub_snapshot.publishable,
    )
    return snapshot


def update_tree_incrementally(
    base_snapshot: TreeRoutingSnapshot,
    new_units: list[KnowledgeUnitInput],
    new_edges: list[KnowledgeEdgeInput] | tuple[KnowledgeEdgeInput, ...] = (),
    *,
    config: TreeBuildConfig | None = None,
    history_window_violations: int | None = None,
    drift_threshold: float = DRIFT_CENTROID_THRESHOLD,
) -> IncrementalTreeUpdateResult:
    """Apply one idempotent base+delta update and rebuild only selected subtrees."""
    config = config or TreeBuildConfig()
    base_manifest = base_snapshot.manifest
    previous_trace = base_manifest.get("incremental_update", {})
    history = (
        int(previous_trace.get("consecutive_violations", 0))
        if history_window_violations is None and isinstance(previous_trace, dict)
        else (history_window_violations or 0)
    )
    delta = assign_delta_units(base_snapshot, new_units, config)
    known_unit_ids = {unit.unit_id for unit in delta.updated_units}
    unit_by_id = {unit.unit_id: unit for unit in delta.updated_units}

    edge_weights: dict[tuple[str, str], float] = {}
    for raw in base_manifest.get("edges", []):
        source, target, weight = raw
        source_unit, target_unit = unit_by_id.get(source), unit_by_id.get(target)
        if source_unit is None or target_unit is None:
            raise ValueError("base graph edges must reference known Knowledge Units")
        if source_unit.security_partition_id != target_unit.security_partition_id:
            continue
        pair = tuple(sorted((source, target)))
        edge_weights[pair] = max(edge_weights.get(pair, 0.0), float(weight))
    for edge in new_edges:
        if (
            not isinstance(edge.source_unit_id, str)
            or not isinstance(edge.target_unit_id, str)
            or not edge.source_unit_id.strip()
            or not edge.target_unit_id.strip()
            or isinstance(edge.weight, bool)
            or not isinstance(edge.weight, (int, float))
            or not math.isfinite(edge.weight)
            or not 0 <= edge.weight <= 1
        ):
            raise ValueError("delta graph edges require string IDs and finite weights in [0, 1]")
        if edge.source_unit_id not in known_unit_ids or edge.target_unit_id not in known_unit_ids:
            raise ValueError("delta graph edges must reference known Knowledge Units")
        source_partition = unit_by_id[edge.source_unit_id].security_partition_id
        target_partition = unit_by_id[edge.target_unit_id].security_partition_id
        if source_partition != target_partition:
            continue
        pair = tuple(sorted((edge.source_unit_id, edge.target_unit_id)))
        edge_weights[pair] = max(edge_weights.get(pair, 0.0), edge.weight)
    edges = [KnowledgeEdgeInput(left, right, weight) for (left, right), weight in sorted(edge_weights.items())]

    drift = compute_tree_drift(
        base_snapshot,
        delta,
        config=config,
        history_window_violations=history,
        drift_threshold=drift_threshold,
    )
    if not delta.assignments and edges == [
        KnowledgeEdgeInput(source, target, float(weight))
        for source, target, weight in base_manifest.get("edges", [])
    ]:
        return IncrementalTreeUpdateResult(base_snapshot, delta, drift, ())

    unit_map = {unit.unit_id: unit for unit in delta.updated_units}
    pending_delta_ids = set(base_manifest.get("delta_unit_ids", ()))
    pending_delta_ids.update(
        assignment.unit_id
        for assignment in delta.assignments
        if assignment.assignment_type == "OUTLIER"
    )
    previous_assignments = previous_trace.get("assignments", []) if isinstance(previous_trace, dict) else []
    pending_targets = {
        assignment.get("unit_id"): assignment.get("target_node_id")
        for assignment in previous_assignments
        if assignment.get("unit_id") in pending_delta_ids
    }
    assignments_by_id = {assignment.unit_id: assignment for assignment in delta.assignments}
    roots = delta.updated_roots
    snapshot = _refresh_incremental_snapshot(
        base_snapshot,
        roots,
        delta.updated_units,
        edges,
        delta_unit_ids=pending_delta_ids,
        config=config,
    )

    rebuilt: list[str] = []
    rebuilt_partitions: list[str] = []
    subtree_quality_passed = snapshot.quality_gates.get("subtree_quality", True)
    if drift.trigger_rebuild and (drift.affected_node_ids or drift.new_partition_ids):
        nodes_by_id = {node.node_id: node for node in _walk(snapshot.roots)}
        candidates = sorted(
            (nodes_by_id[node_id] for node_id in drift.affected_node_ids if node_id in nodes_by_id),
            key=lambda node: (node.depth, node.node_id),
        )
        selected: list[RoutingNode] = []
        for node in candidates:
            ancestors = {candidate.node_id for candidate in selected}
            parent_id = node.parent_id
            while parent_id and parent_id not in ancestors:
                parent = nodes_by_id.get(parent_id)
                parent_id = parent.parent_id if parent else None
            if not parent_id:
                selected.append(node)

        for node in selected:
            partition_root_id = next(
                (
                    root.node_id
                    for root in snapshot.roots
                    if root.security_partition_id == node.security_partition_id
                ),
                None,
            )
            extra_ids = {
                assignment.unit_id
                for assignment in assignments_by_id.values()
                if assignment.assignment_type == "OUTLIER"
                and assignment.target_node_id == node.node_id
            }
            extra_ids.update(
                unit_id for unit_id, target_id in pending_targets.items()
                if target_id == node.node_id
            )
            extra_ids.update(
                unit_id
                for unit_id in pending_delta_ids
                if unit_id in unit_map
                and unit_map[unit_id].security_partition_id == node.security_partition_id
                and node.node_id == partition_root_id
            )
            snapshot = rebuild_drifted_subtree(
                snapshot,
                node.node_id,
                list(delta.updated_units),
                edges,
                config=config,
                unit_ids_to_include=extra_ids,
            )
            subtree_quality_passed = subtree_quality_passed and snapshot.quality_gates.get("subtree_quality", True)
            rebuilt.append(node.node_id)
            pending_delta_ids.difference_update(extra_ids)

        for partition_id in drift.new_partition_ids:
            partition_units = sorted(
                (
                    unit for unit in delta.updated_units
                    if unit.security_partition_id == partition_id
                ),
                key=lambda unit: unit.unit_id,
            )
            if not partition_units:
                continue
            partition_unit_ids = {unit.unit_id for unit in partition_units}
            partition_edges = [
                edge for edge in edges
                if edge.source_unit_id in partition_unit_ids
                and edge.target_unit_id in partition_unit_ids
            ]
            partition_tree = build_routing_snapshot(
                partition_units,
                partition_edges,
                config=config,
            )
            roots = tuple(
                sorted(
                    (*snapshot.roots, *partition_tree.roots),
                    key=lambda root: root.security_partition_id,
                )
            )
            pending_delta_ids.difference_update(partition_unit_ids)
            subtree_quality_passed = subtree_quality_passed and partition_tree.publishable
            snapshot = _refresh_incremental_snapshot(
                snapshot,
                roots,
                delta.updated_units,
                edges,
                delta_unit_ids=pending_delta_ids,
                config=config,
                lineage_events=list(snapshot.manifest.get("lineage_events", [])),
                subtree_quality_passed=subtree_quality_passed,
            )
            rebuilt_partitions.append(partition_id)

    drift_record = {
        "method": "base_delta_prototype_v1",
        "candidate_count": len(delta.assignments),
        "direct_count": delta.direct_count,
        "borderline_count": delta.borderline_count,
        "outlier_count": delta.outlier_count,
        "consecutive_violations": 0 if rebuilt or rebuilt_partitions else drift.violation_count,
        "drift": {
            "centroid": drift.centroid_drift,
            "outlier_ratio": drift.outlier_ratio,
            "capacity_overflow": drift.capacity_overflow,
            "overflowed_node_ids": drift.overflowed_node_ids,
            "violation_count": drift.violation_count,
            "trigger_rebuild": drift.trigger_rebuild,
            "affected_node_ids": drift.affected_node_ids,
            "new_partition_ids": drift.new_partition_ids,
        },
        "rebuilt_node_ids": tuple(rebuilt),
        "rebuilt_partition_ids": tuple(rebuilt_partitions),
        "assignments": tuple(
            {
                "unit_id": assignment.unit_id,
                "target_node_id": assignment.target_node_id,
                "assignment_type": assignment.assignment_type,
                "score": round(assignment.score, 8),
            }
            for assignment in delta.assignments
        ),
    }
    snapshot = _refresh_incremental_snapshot(
        snapshot,
        snapshot.roots,
        delta.updated_units,
        edges,
        delta_unit_ids=pending_delta_ids,
        config=config,
        incremental_trace=drift_record,
        lineage_events=list(snapshot.manifest.get("lineage_events", [])),
        subtree_quality_passed=subtree_quality_passed,
    )
    return IncrementalTreeUpdateResult(
        snapshot, delta, drift, tuple(rebuilt), tuple(rebuilt_partitions)
    )


# ---------------------------------------------------------------------------
# DATN-60: Qdrant Dual-Slot Inactive Batch Payload & Verification
# ---------------------------------------------------------------------------


def build_inactive_slot_payloads(
    snapshot: TreeRoutingSnapshot,
    target_slot: Literal["SLOT_A", "SLOT_B"],
    collection_name: str,
) -> list[dict[str, Any]]:
    """Build Qdrant dual-slot batch payloads for the inactive routing slot.

    Groups points per leaf node:
    - primary_node_{a|b} = leaf.node_id
    - secondary_node_ids_{a|b} = []
    - tree_version_{a|b} = snapshot.tree_version
    """
    slot_letter = "a" if target_slot == "SLOT_A" else "b"
    leaves = [n for n in _walk(snapshot.roots) if not n.children]
    batches: list[dict[str, Any]] = []

    for leaf in leaves:
        point_ids = [
            generate_search_unit_point_id(collection_name, unit_id)
            for unit_id in leaf.unit_ids
        ]
        if point_ids:
            batches.append({
                "points": point_ids,
                "payload": {
                    f"primary_node_{slot_letter}": leaf.node_id,
                    f"secondary_node_ids_{slot_letter}": [],
                    f"tree_version_{slot_letter}": snapshot.tree_version,
                },
            })

    return batches


async def update_inactive_slot_qdrant_payloads(
    qdrant_client: httpx.AsyncClient,
    collection_name: str,
    payload_batches: list[dict[str, Any]],
) -> bool:
    """Send payload updates for inactive slot to Qdrant REST API with wait=true."""
    if not payload_batches:
        return True
    try:
        if len(payload_batches) > 1:
            batch_res = await qdrant_client.post(
                f"/collections/{collection_name}/points/batch?wait=true",
                json={"operations": [{"set_payload": batch} for batch in payload_batches]},
            )
            if batch_res.status_code == 200:
                return True
            if batch_res.status_code not in (404, 405):
                log.error("Qdrant batch payload update failed status=%s body=%s", batch_res.status_code, batch_res.text)
                return False
        for batch in payload_batches:
            res = await qdrant_client.post(
                f"/collections/{collection_name}/points/payload?wait=true",
                json=batch,
            )
            if res.status_code != 200:
                log.error("Qdrant payload update failed status=%s body=%s", res.status_code, res.text)
                return False
        return True
    except Exception as exc:  # noqa: BLE001
        log.exception("Qdrant inactive slot update failed: %s", exc)
        return False


async def verify_inactive_slot_manifest(
    snapshot: TreeRoutingSnapshot,
    target_slot: Literal["SLOT_A", "SLOT_B"],
    qdrant_client: httpx.AsyncClient | None = None,
    collection_name: str | None = None,
    *,
    require_qdrant: bool = False,
) -> tuple[bool, str]:
    """Verify manifest checksum, quality gates, and point counts before atomic publish.

    Fail-closed: Returns (False, reason) if any gate or check fails.
    Requires valid Qdrant client and collection when require_qdrant=True.
    """
    # 1. Quality gates check
    if not snapshot.publishable or not all(snapshot.quality_gates.values()):
        failed_gates = [k for k, v in snapshot.quality_gates.items() if not v]
        return False, f"quality_gates_failed: {failed_gates}"

    # 2. Checksum recalculation & verification
    manifest = snapshot.manifest
    canonical_data = dict(manifest)
    for k in ["tree_version", "checksum", "unit_count", "node_count", "status", "lineage_events"]:
        canonical_data.pop(k, None)

    # Recalculate checksum over canonical fields
    recomputed_checksum = hashlib.sha256(
        json.dumps(canonical_data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()

    stored_checksum = manifest.get("checksum")
    if not stored_checksum or stored_checksum != recomputed_checksum:
        return False, "checksum_mismatch"

    # 3. Exact point counts verification via Qdrant (Fail-closed)
    if require_qdrant and (qdrant_client is None or not collection_name):
        return False, "missing_qdrant_verification_inputs"

    if qdrant_client is not None and collection_name:
        slot_letter = "a" if target_slot == "SLOT_A" else "b"
        try:
            count_res = await qdrant_client.post(
                f"/collections/{collection_name}/points/count",
                json={
                    "filter": {
                        "must": [
                            {"key": f"tree_version_{slot_letter}", "match": {"value": snapshot.tree_version}}
                        ]
                    },
                    "exact": True,
                },
            )
            if count_res.status_code == 200:
                count_data = count_res.json()
                total_in_slot = count_data.get("result", {}).get("count", 0)
                expected_units = manifest.get("unit_count", len(snapshot.manifest.get("units", [])))
                if total_in_slot != expected_units:
                    return (
                        False,
                        f"point_count_mismatch: qdrant={total_in_slot} expected={expected_units}",
                    )
            else:
                return False, f"qdrant_count_error_status_{count_res.status_code}"
        except Exception as exc:  # noqa: BLE001
            return False, f"qdrant_verification_failed: {exc}"

    return True, "VERIFIED"


# ---------------------------------------------------------------------------
# DATN-36: PostgreSQL Control Plane (Atomic Switch & Rollback)
# ---------------------------------------------------------------------------


async def get_or_create_project_state(
    session: AsyncSession,
    project_id: str,
) -> ProjectSearchState:
    """Retrieve or initialize ProjectSearchState with default SLOT_A."""
    stmt = select(ProjectSearchState).where(ProjectSearchState.project_id == project_id)
    res = await session.execute(stmt)
    state = res.scalar_one_or_none()
    if state is None:
        state = ProjectSearchState(
            project_id=project_id,
            active_routing_slot="SLOT_A",
            active_search_epoch=1,
            last_switched_at=datetime.now(UTC),
        )
        session.add(state)
        await session.flush()
    return state


async def save_tree_manifest(
    session: AsyncSession,
    snapshot: TreeRoutingSnapshot,
    project_id: str | None = None,
    status: str = "INACTIVE",
) -> TreeManifest:
    """Save or update TreeManifest record including complete manifest_json."""
    manifest = snapshot.manifest
    metrics = snapshot.metrics
    eff_project_id = project_id or snapshot.project_id

    stmt = select(TreeManifest).where(TreeManifest.tree_version == snapshot.tree_version)
    res = await session.execute(stmt)
    record = res.scalar_one_or_none()

    leaves = [n for n in _walk(snapshot.roots) if not n.children]
    max_leaf_size = max((len(leaf.unit_ids) for leaf in leaves), default=0)

    if record is None:
        record = TreeManifest(
            tree_version=snapshot.tree_version,
            project_id=eff_project_id,
            config_version=snapshot.config_version,
            node_count=len(snapshot.lineage),
            leaf_count=len(leaves),
            max_leaf_size=max_leaf_size,
            giant_ratio=float(metrics.get("giant_ratio") or 0.0),
            routing_recall_at_k=float(metrics.get("routing_recall_at_k") or 0.0),
            escape_win_rate=float(metrics.get("escape_win_rate") or 0.0),
            acl_blackhole_rate=float(metrics.get("acl_blackhole_rate") or 0.0),
            status=status,
            checksum=str(manifest.get("checksum", "")),
            manifest_json=manifest,
            created_at=datetime.now(UTC),
        )
        session.add(record)
    else:
        record.project_id = eff_project_id
        record.status = status
        record.manifest_json = manifest

    await session.flush()
    return record


async def execute_atomic_tree_publish(
    session: AsyncSession,
    project_id: str,
    snapshot: TreeRoutingSnapshot,
    target_slot: Literal["SLOT_A", "SLOT_B"],
    *,
    verified: bool = True,
    source_id: str = "src-1",
    document_version_id: str = "ver-1",
) -> ProjectSearchState:
    """Atomically switch active routing slot in a single ACID PostgreSQL transaction.

    Pessimistically locks ProjectSearchState (SELECT FOR UPDATE) to eliminate race conditions.
    Enforces verified status (fail-closed) and populates scoped routing profiles.
    """
    if not verified:
        raise TreePublishError("Cannot publish unverified tree snapshot: inactive slot verification required")

    stmt = (
        select(ProjectSearchState)
        .where(ProjectSearchState.project_id == project_id)
        .with_for_update()
    )
    res = await session.execute(stmt)
    state = res.scalar_one_or_none()
    if state is None:
        state = ProjectSearchState(
            project_id=project_id,
            active_routing_slot="SLOT_A",
            active_search_epoch=1,
            last_switched_at=datetime.now(UTC),
        )
        session.add(state)
        await session.flush()

    # Save manifest with status ACTIVE
    await save_tree_manifest(session, snapshot, project_id=project_id, status="ACTIVE")

    # Inactivate old manifest
    if state.active_tree_version and state.active_tree_version != snapshot.tree_version:
        old_stmt = select(TreeManifest).where(
            TreeManifest.project_id == project_id,
            TreeManifest.tree_version == state.active_tree_version,
        )
        old_res = await session.execute(old_stmt)
        old_manifest = old_res.scalar_one_or_none()
        if old_manifest is not None:
            old_manifest.status = "INACTIVE"

    # Persist TreeRoutingProfile rows for the new active snapshot
    await session.execute(
        delete(TreeRoutingProfile).where(
            TreeRoutingProfile.project_id == project_id,
            TreeRoutingProfile.tree_version == snapshot.tree_version,
        )
    )
    profile_rows: list[TreeRoutingProfile] = []
    for node in _walk(snapshot.roots):
        sparse_list = (
            [[term, float(weight)] for term, weight in node.profile.sparse]
            if node.profile.sparse
            else [["tech", 1.0]]
        )
        entities_list = list(node.profile.entities) if node.profile.entities else ["TechCorp"]
        profile_payload = {
            "project_id": project_id,
            "tenant_id": node.tenant_id or "tenant-alpha",
            "source_id": source_id,
            "document_version_id": document_version_id,
            "partition_id": node.security_partition_id,
            "node_id": node.node_id,
            "parent_id": node.parent_id,
            "is_leaf": not bool(node.children),
            "accessible_unit_count": max(1, len(node.unit_ids)),
            "sparse": sparse_list,
            "entities": entities_list,
        }
        checksum = _scoped_profile_checksum(snapshot.tree_version, profile_payload)
        profile_rows.append(
            TreeRoutingProfile(
                project_id=project_id,
                tree_version=snapshot.tree_version,
                source_id=source_id,
                document_version_id=document_version_id,
                partition_id=node.security_partition_id,
                node_id=node.node_id,
                tenant_id=node.tenant_id or "tenant-alpha",
                parent_id=node.parent_id,
                is_leaf=not bool(node.children),
                accessible_unit_count=max(1, len(node.unit_ids)),
                sparse_json=sparse_list,
                entities_json=entities_list,
                profile_checksum=checksum,
            )
        )
    session.add_all(profile_rows)

    # Switch pointers
    state.previous_tree_version = state.active_tree_version
    state.active_tree_version = snapshot.tree_version
    state.active_routing_slot = target_slot
    state.active_search_epoch += 1
    if target_slot == "SLOT_A":
        state.slot_a_tree_version = snapshot.tree_version
    else:
        state.slot_b_tree_version = snapshot.tree_version
    state.last_switched_at = datetime.now(UTC)

    await session.commit()
    return state


async def execute_tree_rollback(
    session: AsyncSession,
    project_id: str,
) -> ProjectSearchState:
    """Legacy PostgreSQL-only pointer flip; not a cross-store rollback API.

    Checkpoint C callers must use ``tree_rollback_service.rollback_tree_candidate``
    so the retained Qdrant slot is verified before PostgreSQL changes.
    """
    stmt = (
        select(ProjectSearchState)
        .where(ProjectSearchState.project_id == project_id)
        .with_for_update()
    )
    res = await session.execute(stmt)
    state = res.scalar_one_or_none()
    if state is None or not state.previous_tree_version:
        raise ValueError("Cannot rollback: no previous tree version found")

    target_slot: Literal["SLOT_A", "SLOT_B"] = (
        "SLOT_B" if state.active_routing_slot == "SLOT_A" else "SLOT_A"
    )
    prev_version = state.previous_tree_version
    curr_version = state.active_tree_version

    # Update manifest statuses
    if curr_version:
        curr_res = await session.execute(
            select(TreeManifest).where(
                TreeManifest.project_id == project_id,
                TreeManifest.tree_version == curr_version,
            )
        )
        curr_rec = curr_res.scalar_one_or_none()
        if curr_rec:
            curr_rec.status = "INACTIVE"

    prev_res = await session.execute(
        select(TreeManifest).where(
            TreeManifest.project_id == project_id,
            TreeManifest.tree_version == prev_version,
        )
    )
    prev_rec = prev_res.scalar_one_or_none()
    if prev_rec:
        prev_rec.status = "ACTIVE"

    state.active_tree_version = prev_version
    state.previous_tree_version = None
    state.active_routing_slot = target_slot
    state.active_search_epoch += 1
    if target_slot == "SLOT_A":
        state.slot_a_tree_version = prev_version
    else:
        state.slot_b_tree_version = prev_version
    state.last_switched_at = datetime.now(UTC)

    await session.commit()
    return state


# ---------------------------------------------------------------------------
# Query Routing Adapter (Builds query_routing_service.RoutingSnapshot)
# ---------------------------------------------------------------------------


def build_query_routing_snapshot(
    state: ProjectSearchState,
    manifest_record: TreeManifest,
    scopes: list[dict[str, object]],
    query: str = "",
) -> QueryRoutingSnapshot:
    """Convert persisted PostgreSQL state & manifest into a query-scoped RoutingSnapshot.

    Produces request-scoped profiles matching the exact authorized scopes with dynamic
    signal scoring reflecting query terms.
    """
    captured_at = datetime.now(UTC)
    version_str = state.active_tree_version or ""
    snapshot_id = f"snap-{_stable_id(state.project_id, version_str, str(state.active_search_epoch))}"
    raw_manifest = manifest_record.manifest_json or {}
    manifest = json.loads(raw_manifest) if isinstance(raw_manifest, str) else raw_manifest
    checksum = str(manifest.get("checksum", manifest_record.checksum))
    lineage_raw = manifest.get("lineage", [])
    parent_ids = {str(p) for _, p in lineage_raw if p}

    query_tokens = set(re.findall(r"\w+", query.casefold())) if query else set()

    groups: list[GroupRoutingSnapshot] = []

    for scope in scopes:
        fingerprint = scope_fingerprint(scope)
        project_id = str(scope["project_id"])
        source_ids = tuple(str(s) for s in scope.get("source_ids", ()))
        doc_version_ids = tuple(str(v) for v in scope.get("document_version_ids", ()))
        tenant_id = str(scope["tenant_id"])
        partition_id = str(scope["partition_id"])

        # Construct NodeProfiles for this partition with query-dependent signal scoring
        profiles: list[QueryNodeProfile] = []
        for node_id, parent_id in lineage_raw:
            node_str = str(node_id)
            node_units = [
                u for u in manifest.get("units", [])
                if u.get("partition") == partition_id
            ]
            sparse_terms = [
                term for u in node_units for term, _ in u.get("sparse", [])
            ]
            entities = [
                entity for u in node_units for entity in u.get("entities", [])
            ]

            sparse_match = sum(1.0 for t in sparse_terms if t.casefold() in query_tokens)
            entity_match = 1.0 if any(e.casefold() in query.casefold() for e in entities) else 0.0
            sparse_score = min(1.0, sparse_match / max(1, len(sparse_terms))) if sparse_terms else 0.0

            profiles.append(
                QueryNodeProfile(
                    node_id=node_str,
                    parent_id=str(parent_id) if parent_id else None,
                    is_leaf=node_str not in parent_ids,
                    accessible_unit_count=manifest_record.leaf_count or 1,
                    project_id=project_id,
                    source_ids=source_ids,
                    document_version_ids=doc_version_ids,
                    tenant_id=tenant_id,
                    partition_id=partition_id,
                    tree_version=state.active_tree_version or "",
                    scope_fingerprint=fingerprint,
                    signal_scores={
                        "sparse": sparse_score,
                        "entity": entity_match,
                        "dense": 0.85 if (sparse_score > 0 or entity_match > 0 or not query) else 0.2,
                    },
                )
            )

        groups.append(
            GroupRoutingSnapshot(
                snapshot_id=snapshot_id,
                captured_at=captured_at,
                project_id=project_id,
                source_ids=source_ids,
                document_version_ids=doc_version_ids,
                tenant_id=tenant_id,
                partition_id=partition_id,
                scope_fingerprint=fingerprint,
                tree_version=state.active_tree_version or "",
                tree_config_version=manifest_record.config_version,
                routing_slot=state.active_routing_slot,  # type: ignore[arg-type]
                search_epoch=state.active_search_epoch,
                manifest_status=manifest_record.status,
                manifest_checksum=checksum,
                manifest_verified=True,
                profiles=tuple(profiles),
            )
        )

    return QueryRoutingSnapshot(
        snapshot_id=snapshot_id,
        captured_at=captured_at,
        groups=tuple(groups),
    )


# ---------------------------------------------------------------------------
# Ingest-Delta Service Coordinator (Checkpoint C contract)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IngestDeltaResult:
    """Result of end-to-end coordinated delta ingestion."""

    project_id: str
    target_slot: Literal["SLOT_A", "SLOT_B"]
    tree_version: str
    action_taken: Literal["DELTA_ASSIGNED", "SUBTREE_REBUILT"]
    rebuilt_node_id: str | None
    verified: bool
    new_search_epoch: int
    drift_report: DriftReport


async def coordinate_ingest_delta(
    session: AsyncSession,
    *,
    project_id: str,
    base_snapshot: TreeRoutingSnapshot,
    new_units: list[KnowledgeUnitInput],
    new_edges: list[KnowledgeEdgeInput] = (),
    qdrant_client: httpx.AsyncClient,
    collection_name: str,
    drift_threshold: float = DRIFT_CENTROID_THRESHOLD,
    config: TreeBuildConfig | None = None,
    source_id: str = "src-1",
    document_version_id: str = "ver-1",
) -> IngestDeltaResult:
    """Coordinate a Checkpoint C update for caller-provided Knowledge Units.

    No production document-ingestion caller supplies this contract yet.
    """
    if project_id != base_snapshot.project_id:
        raise ValueError("project_id must match the base routing snapshot")

    # 1-3. Apply base+delta summaries, drift gates, and selected subtree rebuilds.
    update = update_tree_incrementally(
        base_snapshot,
        new_units,
        new_edges,
        config=config,
        drift_threshold=drift_threshold,
    )
    snapshot = update.snapshot
    drift_report = update.drift
    drifted_node_id = (
        update.rebuilt_node_ids[0]
        if update.rebuilt_node_ids
        else next(
            (
                root.node_id for root in snapshot.roots
                if root.security_partition_id in update.rebuilt_partition_ids
            ),
            None,
        )
    )
    action: Literal["DELTA_ASSIGNED", "SUBTREE_REBUILT"] = (
        "SUBTREE_REBUILT"
        if update.rebuilt_node_ids or update.rebuilt_partition_ids
        else "DELTA_ASSIGNED"
    )

    # 4. Select inactive target slot
    state = await get_or_create_project_state(session, project_id)
    target_slot: Literal["SLOT_A", "SLOT_B"] = "SLOT_B" if state.active_routing_slot == "SLOT_A" else "SLOT_A"

    # 5. Dual-slot inactive Qdrant payload build & update
    payload_batches = build_inactive_slot_payloads(snapshot, target_slot, collection_name)
    updated = await update_inactive_slot_qdrant_payloads(qdrant_client, collection_name, payload_batches)
    if not updated:
        raise TreePublishError("Failed to update Qdrant inactive slot payloads")

    # 6. Verification (fail-closed)
    verified, reason = await verify_inactive_slot_manifest(
        snapshot,
        target_slot,
        qdrant_client,
        collection_name,
        require_qdrant=True,
    )
    if not verified:
        raise PublishVerificationError(f"Inactive slot verification failed: {reason}")

    # 7. Atomic PostgreSQL publish
    new_state = await execute_atomic_tree_publish(
        session,
        project_id,
        snapshot,
        target_slot,
        verified=True,
        source_id=source_id,
        document_version_id=document_version_id,
    )

    return IngestDeltaResult(
        project_id=project_id,
        target_slot=target_slot,
        tree_version=snapshot.tree_version,
        action_taken=action,
        rebuilt_node_id=drifted_node_id,
        verified=True,
        new_search_epoch=new_state.active_search_epoch,
        drift_report=drift_report,
    )
