"""Deterministic, ACL-partitioned routing tree builder and B2 fixture contract.

The input types intentionally describe Phase 5 Knowledge Units, rather than
Search Units. This keeps the tree lane independent from ingestion and lets the
planner lane integrate against a stable snapshot/profile shape.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from types import MappingProxyType

import orjson

_PARTITION_ALGORITHM = "constrained-hierarchical-leiden-cpm-v1"


@dataclass(frozen=True, slots=True)
class KnowledgeUnitInput:
    unit_id: str
    tenant_id: str
    project_id: str
    security_partition_id: str
    dense: tuple[float, ...] = ()
    sparse: tuple[tuple[str, float], ...] = ()
    entities: tuple[str, ...] = ()
    valid_from: datetime | None = None
    valid_to: datetime | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeEdgeInput:
    """One calibrated Phase 5 edge; duplicate unit pairs use their max weight."""

    source_unit_id: str
    target_unit_id: str
    weight: float


@dataclass(frozen=True, slots=True)
class RoutingBenchmarkCase:
    query_id: str
    target_unit_id: str
    routed_unit_ids: tuple[str, ...]
    k: int = 10


@dataclass(frozen=True, slots=True)
class TreeBuildConfig:
    version: str = "routing-tree-v1"
    min_cluster_size: int = 2
    target_cluster_size: int = 8
    max_cluster_size: int = 32
    max_children: int = 8
    max_depth: int = 5
    min_cohesion: float = 0.0
    max_edge_cut: float = 1.0
    max_giant_ratio: float = 1.0
    min_routing_recall_at_k: float = 0.0
    profile_sparse_terms: int = 64
    min_partition_units: int = 2
    leiden_resolution: float = 0.05

    def __post_init__(self) -> None:
        integer_limits = (
            self.min_cluster_size,
            self.target_cluster_size,
            self.max_cluster_size,
            self.max_children,
            self.max_depth,
            self.profile_sparse_terms,
            self.min_partition_units,
        )
        quality_limits = (
            self.min_cohesion,
            self.max_edge_cut,
            self.max_giant_ratio,
            self.min_routing_recall_at_k,
        )
        if (
            any(not isinstance(value, int) or isinstance(value, bool) for value in integer_limits)
            or any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in quality_limits)
            or self.min_cluster_size < 1
            or self.target_cluster_size < self.min_cluster_size
            or self.max_cluster_size < self.target_cluster_size
            or self.max_children < 2
            or self.max_depth < 0
            or self.profile_sparse_terms < 0
            or self.min_partition_units < 1
            or not 0 <= self.min_cohesion <= 1
            or not 0 <= self.max_edge_cut <= 1
            or not 0 <= self.max_giant_ratio <= 1
            or not 0 <= self.min_routing_recall_at_k <= 1
            or not isinstance(self.leiden_resolution, (int, float))
            or not math.isfinite(self.leiden_resolution)
            or isinstance(self.leiden_resolution, bool)
            or self.leiden_resolution <= 0
            or any(not math.isfinite(value) for value in quality_limits)
            or not isinstance(self.version, str)
            or not self.version.strip()
        ):
            raise ValueError("invalid routing tree build constraints")


@dataclass(frozen=True, slots=True)
class NodeProfile:
    dense_medoid: tuple[float, ...] | None
    dense_medoid_candidate_count: int
    dense_medoid_is_exact: bool
    sparse: tuple[tuple[str, float], ...]
    entities: tuple[str, ...]
    temporal_from: datetime | None
    temporal_to: datetime | None
    accessible_unit_count: int


@dataclass(frozen=True, slots=True)
class RoutingNode:
    node_id: str
    parent_id: str | None
    depth: int
    tenant_id: str
    project_id: str
    security_partition_id: str
    unit_ids: tuple[str, ...]
    profile: NodeProfile
    children: tuple[RoutingNode, ...] = ()


@dataclass(frozen=True, slots=True)
class RoutingSnapshot:
    contract_version: str
    tree_version: str
    tenant_id: str
    project_id: str
    config_version: str
    roots: tuple[RoutingNode, ...]
    metrics: Mapping[str, float | int | None]
    quality_gates: Mapping[str, bool]
    publishable: bool
    manifest_json: str
    lineage: tuple[tuple[str, str | None], ...]
    partition_algorithm: str

    @property
    def manifest(self) -> dict[str, object]:
        """Return a copy so consumers cannot mutate the checksummed snapshot."""
        return orjson.loads(self.manifest_json)


def _stable_id(*parts: str) -> str:
    value = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:32]


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    left_scale = max(map(abs, left))
    right_scale = max(map(abs, right))
    if not left_scale or not right_scale:
        return 0.0
    scaled_left = tuple(value / left_scale for value in left)
    scaled_right = tuple(value / right_scale for value in right)
    dot = math.sumprod(scaled_left, scaled_right)
    norm = math.sqrt(
        math.sumprod(scaled_left, scaled_left)
        * math.sumprod(scaled_right, scaled_right)
    )
    return max(0.0, dot / norm) if norm else 0.0


def _profile(
    units: list[KnowledgeUnitInput],
    sparse_limit: int,
    *,
    prepared_dense: dict[str, tuple[tuple[float, ...], float]] | None = None,
) -> NodeProfile:
    vectors = [(unit.unit_id, unit.dense) for unit in units if unit.dense]
    medoid = None
    if vectors:
        if prepared_dense is not None:
            prepared = {uid: prepared_dense[uid] for uid, _ in vectors}
        else:
            prepared = {}
            for uid, vector in vectors:
                scale = max(map(abs, vector))
                normalized = tuple(value / scale for value in vector) if scale else vector
                prepared[uid] = (normalized, math.sumprod(normalized, normalized))

        def cosine(left, right):
            a, norm_a = prepared[left]
            b, norm_b = prepared[right]
            if len(a) != len(b) or not norm_a or not norm_b:
                return 0.0
            return max(0.0, math.sumprod(a, b) / math.sqrt(norm_a * norm_b))

        candidates = sorted(vectors, key=lambda item: hashlib.sha256(item[0].encode()).digest())[:64]
        medoid = min(
            candidates,
            key=lambda candidate: (
                -math.fsum(cosine(candidate[0], uid) for uid, _ in vectors),
                candidate[1],
            ),
        )[1]
    sparse_values: dict[str, list[float]] = defaultdict(list)
    for unit in units:
        for term, weight in unit.sparse:
            sparse_values[term].append(weight)
    sparse = {term: math.fsum(sorted(weights)) for term, weights in sparse_values.items()}
    times_from = [unit.valid_from for unit in units if unit.valid_from is not None]
    times_to = [unit.valid_to for unit in units if unit.valid_to is not None]
    return NodeProfile(
        dense_medoid=medoid,
        dense_medoid_candidate_count=len(candidates) if vectors else 0,
        dense_medoid_is_exact=len(vectors) <= 64,
        sparse=tuple(sorted(sparse.items(), key=lambda item: (-item[1], item[0]))[:sparse_limit]),
        entities=tuple(sorted({entity for unit in units for entity in unit.entities})),
        temporal_from=min(times_from) if times_from else None,
        temporal_to=max(times_to) if times_to else None,
        accessible_unit_count=len(units),
    )


def _partition(
    unit_ids: tuple[str, ...],
    edge_weights: dict[tuple[str, str], float],
    config: TreeBuildConfig,
) -> list[tuple[str, ...]]:
    """Constrained Leiden partition with capacity and balanced fallback."""
    if len(unit_ids) <= config.target_cluster_size:
        return [unit_ids]
    try:
        import igraph as ig
        import leidenalg
    except ImportError as exc:
        raise RuntimeError("Phase 6 requires the igraph and leidenalg dependencies") from exc

    indices = {unit_id: index for index, unit_id in enumerate(unit_ids)}
    graph_pairs = sorted(edge_weights.items())
    graph_edges = [(indices[left], indices[right]) for (left, right), _ in graph_pairs]
    weights = [weight for _, weight in graph_pairs]
    graph = ig.Graph(n=len(unit_ids), edges=graph_edges, directed=False)
    seed_bytes = bytes.fromhex(_stable_id(*unit_ids))[:4]
    partition = leidenalg.find_partition(
        graph,
        leidenalg.CPMVertexPartition,
        weights=weights,
        n_iterations=-1,
        max_comm_size=config.max_cluster_size,
        seed=int.from_bytes(seed_bytes, "big") & 0x7FFF_FFFF,
        resolution_parameter=config.leiden_resolution,
    )
    groups = [{unit_ids[index] for index in community} for community in partition]
    # Guard giant/unconnected components with deterministic balanced chunks.
    balanced: list[tuple[str, ...]] = []
    for group in sorted(groups, key=lambda items: min(items)):
        members = sorted(group)
        if len(members) > config.max_cluster_size:
            count = math.ceil(len(members) / config.max_cluster_size)
            balanced.extend(tuple(part) for part in _balanced_chunks(members, count))
        else:
            balanced.append(tuple(members))

    if len(balanced) > config.max_children:
        count = min(config.max_children, math.ceil(len(unit_ids) / config.target_cluster_size))
        balanced = [tuple(part) for part in _balanced_chunks(sorted(unit_ids), count)]
    elif len(balanced) <= 1 and len(unit_ids) > config.target_cluster_size:
        count = min(config.max_children, math.ceil(len(unit_ids) / config.target_cluster_size))
        balanced = [tuple(part) for part in _balanced_chunks(sorted(unit_ids), count)]

    # Avoid repeated singleton merges when Leiden yields many isolates. The
    # fallback stays linear and produces groups near target size.
    small_group_count = sum(len(group) < config.min_cluster_size for group in balanced)
    if small_group_count > max(1, math.ceil(len(unit_ids) / config.target_cluster_size)):
        count = min(config.max_children, math.ceil(len(unit_ids) / config.target_cluster_size))
        balanced = [tuple(part) for part in _balanced_chunks(sorted(unit_ids), count)]

    # Repair undersized groups using actual graph connectivity.
    while len(balanced) > 1:
        small_index = next((i for i, group in enumerate(balanced) if len(group) < config.min_cluster_size), None)
        if small_index is None:
            break
        small = balanced[small_index]
        group_for_unit = {
            unit_id: index for index, group in enumerate(balanced) for unit_id in group
        }
        weights_by_group: dict[int, list[float]] = defaultdict(list)
        for (source_id, target_id), weight in edge_weights.items():
            source_group = group_for_unit[source_id]
            target_group = group_for_unit[target_id]
            if source_group == small_index and target_group != small_index:
                weights_by_group[target_group].append(weight)
            elif target_group == small_index and source_group != small_index:
                weights_by_group[source_group].append(weight)
        options = [
            (math.fsum(weights_by_group[index]), index)
            for index, group in enumerate(balanced)
            if index != small_index and len(group) + len(small) <= config.max_cluster_size
        ]
        if not options:
            count = math.ceil(len(unit_ids) / config.max_cluster_size)
            return [tuple(part) for part in _balanced_chunks(sorted(unit_ids), count)]
        _, target_index = max(options, key=lambda pair: (pair[0], -pair[1]))
        balanced[target_index] = tuple(sorted(balanced[target_index] + small))
        balanced.pop(small_index)
    return sorted(balanced, key=lambda group: group[0])


def _balanced_chunks(values: list[str], count: int) -> list[list[str]]:
    count = max(1, count)
    return [values[i * len(values) // count : (i + 1) * len(values) // count] for i in range(count)]


def _edge_metrics(
    groups: list[tuple[str, ...]], edge_weights: dict[tuple[str, str], float]
) -> tuple[float, float]:
    total = math.fsum(sorted(edge_weights.values()))
    if total <= 0:
        return 0.0, 0.0
    membership = {unit_id: index for index, group in enumerate(groups) for unit_id in group}
    internal = math.fsum(
        sorted(weight for (a, b), weight in edge_weights.items() if membership.get(a) == membership.get(b))
    )
    return internal / total, 1.0 - internal / total


def build_routing_snapshot(
    units: Iterable[KnowledgeUnitInput],
    edges: Iterable[KnowledgeEdgeInput] = (),
    *,
    config: TreeBuildConfig = TreeBuildConfig(),
    benchmark: Iterable[RoutingBenchmarkCase] = (),
    extra_manifest: Mapping[str, object] | None = None,
    precomputed: RoutingSnapshot | None = None,
) -> RoutingSnapshot:
    rows = list(units)
    if not rows:
        raise ValueError("routing tree requires at least one Knowledge Unit")
    if any(
        not isinstance(unit.unit_id, str)
        or not isinstance(unit.tenant_id, str)
        or not isinstance(unit.project_id, str)
        or not isinstance(unit.security_partition_id, str)
        for unit in rows
    ):
        raise ValueError("Knowledge Unit IDs and scope fields must be strings")
    rows.sort(key=lambda unit: unit.unit_id)
    identity = {(unit.tenant_id, unit.project_id) for unit in rows}
    if len(identity) != 1 or any(
        not unit.unit_id.strip()
        or not unit.tenant_id.strip()
        or not unit.project_id.strip()
        or not unit.security_partition_id.strip()
        for unit in rows
    ):
        raise ValueError("build input must belong to one tenant/project and have a security partition")
    unit_map = {unit.unit_id: unit for unit in rows}
    if len(unit_map) != len(rows):
        raise ValueError("Knowledge Unit IDs must be unique")
    if any(
        not isinstance(unit.dense, tuple)
        or not isinstance(unit.sparse, tuple)
        or not isinstance(unit.entities, tuple)
        for unit in rows
    ):
        raise ValueError("Knowledge Unit feature collections must be tuples")
    dense_dimensions = {len(unit.dense) for unit in rows if unit.dense}
    if len(dense_dimensions) > 1:
        raise ValueError("dense vectors must have a consistent dimension")
    sparse_weights_by_term: dict[str, list[float]] = defaultdict(list)
    for unit in rows:
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            for value in unit.dense
        ):
            raise ValueError("dense vectors must contain finite values")
        if any(
            not isinstance(term, str)
            or not term.strip()
            or isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(weight)
            or weight < 0
            for term, weight in unit.sparse
        ):
            raise ValueError("sparse terms require a non-empty term and finite non-negative weight")
        for term, weight in unit.sparse:
            sparse_weights_by_term[term].append(weight)
        if any(not isinstance(entity, str) or not entity.strip() for entity in unit.entities):
            raise ValueError("entity labels must be non-empty")
        for timestamp in (unit.valid_from, unit.valid_to):
            if timestamp is not None and not isinstance(timestamp, datetime):
                raise ValueError("temporal profile timestamps must be datetimes")
            if timestamp is not None and (timestamp.tzinfo is None or timestamp.utcoffset() is None):
                raise ValueError("temporal profile timestamps must include a timezone")
        if unit.valid_from and unit.valid_to and unit.valid_from > unit.valid_to:
            raise ValueError("Knowledge Unit validity range is inverted")
    try:
        sparse_totals = (math.fsum(sorted(weights)) for weights in sparse_weights_by_term.values())
        if any(not math.isfinite(total) for total in sparse_totals):
            raise ValueError("sparse term aggregate must be finite")
    except OverflowError as exc:
        raise ValueError("sparse term aggregate must be finite") from exc

    edge_weights_by_pair: dict[tuple[str, str], list[float]] = defaultdict(list)
    for edge in edges:
        if not isinstance(edge.source_unit_id, str) or not isinstance(edge.target_unit_id, str):
            raise ValueError("graph edge endpoints must be Knowledge Unit ID strings")
        source, target = unit_map.get(edge.source_unit_id), unit_map.get(edge.target_unit_id)
        if source is None or target is None:
            raise ValueError("graph edge references a missing Knowledge Unit")
        if (
            isinstance(edge.weight, bool)
            or not isinstance(edge.weight, (int, float))
            or not math.isfinite(edge.weight)
            or not 0 <= edge.weight <= 1
        ):
            raise ValueError("graph edge weights must be finite values in [0, 1]")
        if source.security_partition_id != target.security_partition_id:
            continue
        if edge.source_unit_id != edge.target_unit_id and edge.weight > 0:
            edge_weights_by_pair[tuple(sorted((edge.source_unit_id, edge.target_unit_id)))].append(edge.weight)
    edge_map = {pair: max(weights) for pair, weights in edge_weights_by_pair.items()}
    edge_adjacency: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for (source_id, target_id), weight in edge_map.items():
        edge_adjacency[source_id].append((target_id, weight))
        edge_adjacency[target_id].append((source_id, weight))
    for neighbors in edge_adjacency.values():
        neighbors.sort()

    tenant_id, project_id = next(iter(identity))
    partition_units: dict[str, list[str]] = defaultdict(list)
    for unit in rows:
        partition_units[unit.security_partition_id].append(unit.unit_id)
    if precomputed is not None:
        roots = list(precomputed.roots)
        lineage = list(precomputed.lineage)
        edge_cut = float(precomputed.metrics.get("edge_cut") or 0.0)
        cohesion = float(precomputed.metrics.get("cohesion") or 0.0)
        entropy = float(precomputed.metrics.get("child_size_entropy") or 0.0)
        max_depth = int(precomputed.metrics.get("depth") or 0)
        largest_group_ratio = float(precomputed.metrics.get("giant_ratio") or 0.0)
    else:
        prepared_dense_by_uid: dict[str, tuple[tuple[float, ...], float]] = {}
        for unit in rows:
            if unit.dense:
                scale = max(map(abs, unit.dense))
                normalized = tuple(value / scale for value in unit.dense) if scale else unit.dense
                prepared_dense_by_uid[unit.unit_id] = (normalized, math.sumprod(normalized, normalized))

        roots = []
        lineage = []
        all_child_sizes: list[int] = []
        cohesion_values: list[float] = []
        edge_cut_values: list[float] = []
        max_depth = 0
        largest_group_ratio = 0.0

        def make_node(partition: str, member_ids: tuple[str, ...], parent_id: str | None, depth: int) -> RoutingNode:
            nonlocal max_depth, largest_group_ratio
            node_id = _stable_id(tenant_id, project_id, partition, *member_ids)
            lineage.append((node_id, parent_id))
            max_depth = max(max_depth, depth)
            children: tuple[RoutingNode, ...] = ()
            if len(member_ids) > config.target_cluster_size and depth < config.max_depth:
                member_set = set(member_ids)
                scoped_edges = {
                    (source_id, target_id): weight
                    for source_id in member_ids
                    for target_id, weight in edge_adjacency.get(source_id, ())
                    if source_id < target_id and target_id in member_set
                }
                clusters = _partition(member_ids, scoped_edges, config)
                if len(clusters) > config.max_children:
                    raise RuntimeError("partitioner exceeded max_children after balanced fallback")
                if len(clusters) > 1:
                    internal_ratio, cut_ratio = _edge_metrics(clusters, scoped_edges)
                    cohesion_values.append(internal_ratio)
                    edge_cut_values.append(cut_ratio)
                    all_child_sizes.extend(len(group) for group in clusters)
                    largest_group_ratio = max(largest_group_ratio, max(map(len, clusters)) / len(member_ids))
                    children = tuple(
                        make_node(partition, group, node_id, depth + 1)
                        for group in clusters
                    )
            return RoutingNode(
                node_id=node_id,
                parent_id=parent_id,
                depth=depth,
                tenant_id=tenant_id,
                project_id=project_id,
                security_partition_id=partition,
                unit_ids=member_ids,
                profile=_profile(
                    [unit_map[item] for item in member_ids],
                    config.profile_sparse_terms,
                    prepared_dense=prepared_dense_by_uid,
                ),
                children=children,
            )

        for partition, member_ids in sorted(partition_units.items()):
            root = make_node(partition, tuple(sorted(member_ids)), None, 0)
            roots.append(root)

        edge_cut = math.fsum(edge_cut_values) / len(edge_cut_values) if edge_cut_values else 0.0
        cohesion = math.fsum(cohesion_values) / len(cohesion_values) if cohesion_values else 0.0
        if all_child_sizes:
            total_size = sum(all_child_sizes)
            probs = [size / total_size for size in all_child_sizes]
            entropy = -sum(prob * math.log(prob) for prob in probs)
        else:
            entropy = 0.0
    cases = list(benchmark)
    if any(
        not isinstance(case.query_id, str)
        or not isinstance(case.target_unit_id, str)
        or not isinstance(case.routed_unit_ids, tuple)
        or any(not isinstance(unit_id, str) for unit_id in case.routed_unit_ids)
        or not isinstance(case.k, int)
        or isinstance(case.k, bool)
        for case in cases
    ):
        raise ValueError("routing benchmark fields have invalid types")
    if len({case.query_id for case in cases}) != len(cases):
        raise ValueError("routing benchmark query IDs must be unique")
    if len({case.k for case in cases}) > 1:
        raise ValueError("routing benchmark cases must use one common k")
    for case in cases:
        if (
            not case.query_id.strip()
            or case.target_unit_id not in unit_map
            or case.k < 1
        ):
            raise ValueError("routing benchmark requires a query ID, in-scope target and positive k")
    recall = (
        sum(case.target_unit_id in case.routed_unit_ids[: case.k] for case in cases) / len(cases)
        if cases
        else None
    )
    acl_leakage_rate = (
        sum(
            any(
                routed_id not in unit_map
                or unit_map[routed_id].security_partition_id
                != unit_map[case.target_unit_id].security_partition_id
                for routed_id in case.routed_unit_ids
            )
            for case in cases
        )
        / len(cases)
        if cases
        else None
    )
    acl_blackhole_rate = (
        sum(
            not any(
                routed_id in unit_map
                and unit_map[routed_id].security_partition_id
                == unit_map[case.target_unit_id].security_partition_id
                for routed_id in case.routed_unit_ids[: case.k]
            )
            for case in cases
        )
        / len(cases)
        if cases
        else None
    )
    metrics: dict[str, float | int | None] = {
        "cohesion": cohesion,
        "giant_ratio": largest_group_ratio,
        "child_size_entropy": entropy,
        "edge_cut": edge_cut,
        "depth": max_depth,
        "routing_recall_at_k": recall,
        "routing_recall_k": cases[0].k if cases else None,
        "acl_routing_leakage_rate": acl_leakage_rate,
        "acl_blackhole_rate": acl_blackhole_rate,
    }
    gates = {
        "minimum_units": len(rows) >= config.min_partition_units,
        "cohesion": cohesion >= config.min_cohesion,
        "edge_cut": edge_cut <= config.max_edge_cut,
        "giant_ratio": largest_group_ratio <= config.max_giant_ratio,
        "routing_recall": (
            recall >= config.min_routing_recall_at_k
            if recall is not None
            else config.min_routing_recall_at_k == 0
        ),
        "acl_partition_isolation": acl_leakage_rate == 0.0 if acl_leakage_rate is not None else True,
        "acl_blackhole": acl_blackhole_rate == 0.0 if acl_blackhole_rate is not None else True,
        "tree_integrity": _tree_integrity(roots, set(unit_map)),
        "cluster_constraints": all(
            len(node.children) <= config.max_children
            and (node.children or len(node.unit_ids) <= config.max_cluster_size)
            and (node.depth == 0 or len(node.unit_ids) >= config.min_cluster_size)
            and node.depth <= config.max_depth
            for node in _walk(roots)
        ),
        "partition_profiles": all(
            node.profile.accessible_unit_count == len(node.unit_ids)
            and len({unit_map[unit_id].security_partition_id for unit_id in node.unit_ids}) == 1
            for node in _walk(roots)
        ),
    }
    quality_passed = all(gates.values())
    try:
        ig_ver = importlib.metadata.version("igraph")
    except importlib.metadata.PackageNotFoundError:
        ig_ver = "0.11.8"
    try:
        leiden_ver = importlib.metadata.version("leidenalg")
    except importlib.metadata.PackageNotFoundError:
        leiden_ver = "0.10.2"
    algorithm_versions = {
        "igraph": ig_ver,
        "leidenalg": leiden_ver,
    }
    canonical = {
        "contract_version": "routing-snapshot.v1",
        "tenant_id": tenant_id,
        "project_id": project_id,
        "config_version": config.version,
        "config": asdict(config),
        "partition_algorithm": _PARTITION_ALGORITHM,
        "profile_algorithm": "bounded-cosine-medoid-v1:candidate_cap=64",
        "algorithm_versions": algorithm_versions,
        "unit_ids": [unit.unit_id for unit in rows],
        "partitions": {key: sorted(value) for key, value in sorted(partition_units.items())},
        "units": [
            {
                "unit_id": unit.unit_id,
                "partition": unit.security_partition_id,
                "dense": unit.dense,
                "sparse": sorted(unit.sparse),
                "entities": sorted(unit.entities),
                "valid_from": unit.valid_from.isoformat() if unit.valid_from else None,
                "valid_to": unit.valid_to.isoformat() if unit.valid_to else None,
            }
            for unit in rows
        ],
        "edges": sorted((a, b, weight) for (a, b), weight in edge_map.items()),
        "lineage": sorted(lineage),
        "quality_metrics": metrics,
        "quality_gates": gates,
        "benchmark": [
            {
                "query_id": case.query_id,
                "target_unit_id": case.target_unit_id,
                "routed_unit_ids": case.routed_unit_ids,
                "k": case.k,
            }
            for case in cases
        ],
        **(dict(extra_manifest) if extra_manifest else {}),
    }
    checksum = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    tree_version = f"tree-{checksum[:24]}"
    manifest: dict[str, object] = {
        **canonical,
        "tree_version": tree_version,
        "checksum": checksum,
        "unit_count": len(rows),
        "node_count": len(lineage),
        "partition_algorithm": _PARTITION_ALGORITHM,
        "algorithm_versions": algorithm_versions,
        "status": "QUALITY_PASSED" if quality_passed else "REJECTED",
    }
    manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return RoutingSnapshot(
        contract_version="routing-snapshot.v1",
        tree_version=tree_version,
        tenant_id=tenant_id,
        project_id=project_id,
        config_version=config.version,
        roots=tuple(roots),
        metrics=MappingProxyType(metrics),
        quality_gates=MappingProxyType(gates),
        publishable=quality_passed,
        manifest_json=manifest_json,
        lineage=tuple(sorted(lineage)),
        partition_algorithm=_PARTITION_ALGORITHM,
    )


def _walk(roots: Iterable[RoutingNode]) -> Iterable[RoutingNode]:
    for root in roots:
        yield root
        yield from _walk(root.children)


def _tree_integrity(roots: list[RoutingNode], expected_unit_ids: set[str]) -> bool:
    seen_node_ids: set[str] = set()

    def valid(node: RoutingNode) -> bool:
        if node.node_id in seen_node_ids or len(node.unit_ids) != len(set(node.unit_ids)):
            return False
        seen_node_ids.add(node.node_id)
        if not node.children:
            return True
        child_ids = [unit_id for child in node.children for unit_id in child.unit_ids]
        if (
            len(child_ids) != len(set(child_ids))
            or set(child_ids) != set(node.unit_ids)
            or any(
                child.parent_id != node.node_id
                or child.depth != node.depth + 1
                or child.tenant_id != node.tenant_id
                or child.project_id != node.project_id
                or child.security_partition_id != node.security_partition_id
                for child in node.children
            )
        ):
            return False
        return all(valid(child) for child in node.children)

    root_unit_ids = [unit_id for root in roots for unit_id in root.unit_ids]
    return (
        len({root.node_id for root in roots}) == len(roots)
        and len(root_unit_ids) == len(expected_unit_ids)
        and set(root_unit_ids) == expected_unit_ids
        and len({root.security_partition_id for root in roots}) == len(roots)
        and all(root.parent_id is None and root.depth == 0 and valid(root) for root in roots)
    )


def fixture_snapshot() -> RoutingSnapshot:
    """Small deterministic fixture shared with the parallel B2 planner lane."""
    units = [
        KnowledgeUnitInput(
            unit_id=f"ku-{i:02d}",
            tenant_id="tenant-fixture",
            project_id="project-fixture",
            security_partition_id="partition-public" if i < 8 else "partition-private",
            dense=(1.0, float(i < 8)),
            sparse=(("alpha" if i < 8 else "beta", 1.0),),
            entities=("Alpha" if i < 8 else "Beta",),
        )
        for i in range(16)
    ]
    edges = [KnowledgeEdgeInput(f"ku-{i:02d}", f"ku-{i + 1:02d}", 0.9) for i in range(15)]
    cases = [RoutingBenchmarkCase("fixture-query", "ku-00", ("ku-00", "ku-01"))]
    return build_routing_snapshot(
        units,
        edges,
        config=TreeBuildConfig(target_cluster_size=2, max_cluster_size=4, max_children=4),
        benchmark=cases,
    )
