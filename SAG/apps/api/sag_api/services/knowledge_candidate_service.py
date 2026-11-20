"""Phase 5 -> existing routing builder -> verified inactive PostgreSQL candidate."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.db.models import (
    Document,
    DocumentVersion,
    KnowledgeGraphBuild,
    KnowledgeTreeNode,
    KnowledgeUnit,
    TreeManifest,
)
from sag_api.services.knowledge_service import (
    KnowledgeConfig,
    batch_version_inputs,
    checksum,
    knowledge_lock,
    persist_graph,
    terms,
    unit_checksums,
    verify_graph,
    version_input,
)
from sag_api.services.query_routing_service import (
    GroupRoutingSnapshot,
    NodeProfile,
    RoutingPolicy,
    route_snapshot,
    scope_fingerprint,
)
from sag_api.services.routing_tree_service import (
    KnowledgeEdgeInput,
    KnowledgeUnitInput,
    RoutingBenchmarkCase,
    RoutingSnapshot,
    TreeBuildConfig,
    _profile,
    _walk,
)
from sag_api.services.tree_publish_service import _checksum_manifest, _routing_profile_payload
from sag_api.services.tree_query_snapshot_service import _query_signal_scores


class CorpusQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    target_unit_id: str = Field(min_length=1)
    partition_id: str = Field(min_length=1)
    authorized_source_ids: tuple[str, ...] = Field(min_length=1)
    k: int = Field(default=10, ge=1, le=100)


def routing_unit(unit: KnowledgeUnit) -> KnowledgeUnitInput:
    features = unit.features_json
    return KnowledgeUnitInput(
        unit.id,
        unit.tenant_id,
        unit.project_id,
        unit.security_partition_id,
        tuple(features.get("dense", [])),
        tuple((t, float(w)) for t, w in features["sparse"]),
        tuple(features["entities"]),
        unit.valid_from,
        unit.valid_to,
    )


def benchmark_routes(
    snapshot, units: list[KnowledgeUnit], queries: list[CorpusQuery], policy: RoutingPolicy
) -> tuple[list[RoutingBenchmarkCase], list[dict]]:
    if not queries:
        return [], []
    unit_map = {u.id: u for u in units}
    manifest_meta = snapshot.manifest
    sparse_limit = manifest_meta["config"]["profile_sparse_terms"]
    config_version = manifest_meta["config_version"]
    manifest_checksum = manifest_meta["checksum"]
    all_nodes = tuple(_walk(snapshot.roots))
    scoped_node_cache: dict[tuple[str, str], tuple[int, dict[str, list]] | None] = {}
    cases, evidence = [], []
    for query in sorted(queries, key=lambda q: q.query_id):
        target = unit_map.get(query.target_unit_id)
        if (
            not target
            or target.security_partition_id != query.partition_id
            or target.source_id not in query.authorized_source_ids
        ):
            raise ValueError("Corpus gold target is outside the query ACL scope")
        accessible = {
            u.id: u
            for u in units
            if u.security_partition_id == query.partition_id and u.source_id in query.authorized_source_ids
        }
        scope = {
            "tenant_id": snapshot.tenant_id,
            "project_id": snapshot.project_id,
            "partition_id": query.partition_id,
            "source_ids": sorted({u.source_id for u in accessible.values()}),
            "document_version_ids": sorted({u.document_version_id for u in accessible.values()}),
        }
        fingerprint = scope_fingerprint(scope)
        profiles = []
        for node in all_nodes:
            cache_key = (fingerprint, node.node_id)
            if cache_key not in scoped_node_cache:
                member_units = [accessible[uid] for uid in node.unit_ids if uid in accessible]
                if not member_units:
                    scoped_node_cache[cache_key] = None
                elif len(member_units) == len(node.unit_ids):
                    scoped_node_cache[cache_key] = (
                        len(member_units),
                        {
                            "sparse": [list(item) for item in node.profile.sparse],
                            "entities": list(node.profile.entities),
                        },
                    )
                else:
                    # Recompute from authorized members BEFORE beam scoring (also tests Source revocation).
                    members = [
                        KnowledgeUnitInput(
                            u.id,
                            u.tenant_id,
                            u.project_id,
                            u.security_partition_id,
                            (),
                            tuple((t, float(w)) for t, w in u.features_json["sparse"]),
                            tuple(u.features_json["entities"]),
                            u.valid_from,
                            u.valid_to,
                        )
                        for u in member_units
                    ]
                    profile = _profile(members, sparse_limit)
                    scoped_node_cache[cache_key] = (
                        len(members),
                        {
                            "sparse": [list(item) for item in profile.sparse],
                            "entities": list(profile.entities),
                        },
                    )
            cached_node = scoped_node_cache[cache_key]
            if cached_node is None:
                continue
            member_count, profile_dict = cached_node
            scores = _query_signal_scores(query.query, profile_dict)
            profiles.append(
                NodeProfile(
                    node_id=node.node_id,
                    parent_id=node.parent_id,
                    is_leaf=not node.children,
                    accessible_unit_count=member_count,
                    tree_version=snapshot.tree_version,
                    scope_fingerprint=fingerprint,
                    signal_scores=scores,
                    **scope,
                )
            )
        group = GroupRoutingSnapshot(
            snapshot_id="offline-benchmark",
            captured_at=datetime(2026, 1, 1, tzinfo=UTC),
            tree_version=snapshot.tree_version,
            tree_config_version=config_version,
            routing_slot="SLOT_A",
            search_epoch=1,
            manifest_status="ACTIVE",
            manifest_checksum=manifest_checksum,
            manifest_verified=True,
            profiles=tuple(profiles),
            scope_fingerprint=fingerprint,
            **scope,
        )
        # Simulates consumer routing only; no DB active manifest/pointer/slot is changed.
        decision = route_snapshot(group, policy=policy)
        membership = {
            uid
            for node in _walk(snapshot.roots)
            if node.node_id in decision.membership_node_ids
            for uid in node.unit_ids
            if uid in accessible
        }
        query_terms = set(terms(query.query))
        ranked = sorted(
            membership,
            key=lambda uid: (
                -sum(weight for term, weight in accessible[uid].features_json["sparse"] if term in query_terms),
                uid,
            ),
        )
        cases.append(RoutingBenchmarkCase(query.query_id, query.target_unit_id, tuple(ranked), query.k))
        evidence.append(
            {
                **query.model_dump(mode="json"),
                "routed_unit_ids": ranked[: query.k],
                "candidate_count": len(ranked),
                "authorized_count": len(accessible),
                "selected_node_ids": list(decision.selected_nodes),
                "reason_code": decision.reason_code,
                "fallback_reason": decision.fallback_reason,
            }
        )
    return cases, evidence


async def current_units(
    session: AsyncSession,
    tenant_id: str,
    project_id: str,
    *,
    allow_empty: bool = False,
) -> list[KnowledgeUnit]:
    now = datetime.now(UTC)
    units = (
        await session.scalars(
            select(KnowledgeUnit)
            .join(DocumentVersion)
            .join(Document)
            .where(
                KnowledgeUnit.tenant_id == tenant_id,
                KnowledgeUnit.project_id == project_id,
                KnowledgeUnit.is_current.is_(True),
                Document.is_active.is_(True),
                DocumentVersion.knowledge_status == "DATA_READY",
                DocumentVersion.search_status.in_(("READY", "SEARCH_READY")),
                DocumentVersion.valid_from <= now,
                DocumentVersion.valid_to > now,
                DocumentVersion.search_ready_at.is_not(None),
            )
            .order_by(KnowledgeUnit.id).execution_options(populate_existing=True)
        )
    ).all()
    versions = await batch_version_inputs(session, [unit.document_version_id for unit in units]) if units else {}
    checked = await unit_checksums(session, units)
    for unit in units:
        if unit.document_version_id not in versions:
            version = await session.get(DocumentVersion, unit.document_version_id)
            config = KnowledgeConfig.model_validate((version.metadata_json or {}).get("knowledge_config", {}))
            _, blocks, provenance, digest = await version_input(session, version.id, config)
            versions[version.id] = ({b.id: b for b in blocks}, provenance, digest)
        blocks, provenance, digest = versions[unit.document_version_id]
        ids = unit.provenance_json.get("evidence_block_ids", [])
        if (
            len(ids) != 1
            or ids[0] not in blocks
            or blocks[ids[0]].content_hash != unit.content_hash
            or blocks[ids[0]].normalized_text != unit.text
            or unit.input_checksum != checksum({"version_input": digest, "block_id": ids[0]})
            or any(unit.provenance_json.get(key) != value for key, value in provenance.items())
            or unit.tenant_id != provenance["tenant_id"]
            or unit.project_id != provenance["project_id"]
            or unit.source_id != provenance["source_id"]
            or unit.security_partition_id != provenance["security_partition_id"]
            or unit.checksum != checked[unit.id]
        ):
            raise ValueError("Knowledge candidate input/evidence/ACL checksum mismatch")
    if not units and not allow_empty:
        raise ValueError("No current Knowledge Units for tenant/project")
    return list(units)


@asynccontextmanager
async def _project_build_lock(session, tenant_id, project_id):
    if session.get_bind().dialect.name != "postgresql":
        yield
        return
    from sag_api.core.errors import ServiceUnavailableError
    from sag_api.services.tree_publish_service import TreePublishInProgress, _project_publish_lock

    try:
        async with _project_publish_lock("build:" + tenant_id + ":" + project_id):
            yield
    except TreePublishInProgress as error:
        raise ServiceUnavailableError("Another project builder owns this scope") from error


async def build_project_routing_snapshot(session, *, tenant_id, project_id, **options):
    async with _project_build_lock(session, tenant_id, project_id):
        return await _build_project_routing_snapshot(
            session, tenant_id=tenant_id, project_id=project_id, **options,
        )


async def _build_project_routing_snapshot(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    queries: list[CorpusQuery],
    config: TreeBuildConfig,
    graph_config: KnowledgeConfig = KnowledgeConfig(),
    routing_policy: RoutingPolicy = RoutingPolicy(),
    allow_empty: bool = False,
) -> tuple[RoutingSnapshot, list[KnowledgeUnit], KnowledgeGraphBuild, list[dict]] | None:
    units = await current_units(session, tenant_id, project_id, allow_empty=allow_empty)
    if not units:
        return None
    captured = [(u.id, u.checksum) for u in units]
    graph = await persist_graph(session, units, graph_config, preverified=True)
    inputs = [routing_unit(unit) for unit in units]
    edges = [
        KnowledgeEdgeInput(edge["source"], edge["target"], edge["weight"])
        for edge in graph.manifest_json["edges"]
    ]
    from sag_api.services.cpu_worker import run_cpu

    payload = {"units": inputs, "edges": edges, "config": config,
               "knowledge_metadata": {"knowledge_graph_build_id": graph.id, "knowledge_inputs": captured}}
    topology = await run_cpu("routing", payload)
    cases, benchmark = benchmark_routes(topology, units, queries, routing_policy)
    snapshot = (await run_cpu("routing", {**payload, "benchmark": cases, "precomputed": topology})
                if cases else topology)
    now = datetime.now(UTC)
    latest = (
        await session.execute(
            select(KnowledgeUnit.id, KnowledgeUnit.checksum)
            .join(DocumentVersion)
            .join(Document)
            .where(
                KnowledgeUnit.tenant_id == tenant_id,
                KnowledgeUnit.project_id == project_id,
                KnowledgeUnit.is_current.is_(True),
                Document.is_active.is_(True),
                DocumentVersion.knowledge_status == "DATA_READY",
                DocumentVersion.search_status.in_(("READY", "SEARCH_READY")),
                DocumentVersion.valid_from <= now,
                DocumentVersion.valid_to > now,
                DocumentVersion.search_ready_at.is_not(None),
            )
            .order_by(KnowledgeUnit.id)
        )
    ).all()
    if captured != [(row[0], row[1]) for row in latest]:
        from sag_api.core.errors import ServiceUnavailableError

        raise ServiceUnavailableError("Knowledge inputs changed during project rebuild")
    return snapshot, units, graph, benchmark


async def build_knowledge_candidate(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    queries: list[CorpusQuery],
    config: TreeBuildConfig,
    graph_config: KnowledgeConfig = KnowledgeConfig(),
    routing_policy: RoutingPolicy = RoutingPolicy(),
) -> TreeManifest:
    await knowledge_lock(session, "build:" + checksum([tenant_id, project_id]))
    built = await build_project_routing_snapshot(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        queries=queries,
        config=config,
        graph_config=graph_config,
        routing_policy=routing_policy,
    )
    if built is None:
        raise ValueError("No current Knowledge Units for tenant/project")
    snapshot, units, graph, benchmark = built
    if snapshot.manifest["checksum"] != _checksum_manifest(snapshot.manifest):
        raise ValueError("Routing builder manifest checksum mismatch")
    profiles, _ = _routing_profile_payload(snapshot)
    nodes_by_id = {n.node_id: n for n in _walk(snapshot.roots)}
    for profile in profiles:
        node = nodes_by_id[profile["node_id"]]
        profile.update(
            depth=node.depth,
            dense_medoid_candidate_count=node.profile.dense_medoid_candidate_count,
            dense_medoid_is_exact=node.profile.dense_medoid_is_exact,
        )
    producer = {
        "graph_build_id": graph.id,
        "graph_checksum": graph.checksum,
        "knowledge_inputs": [[u.id, u.input_checksum, u.checksum, u.provenance_json] for u in units],
        "routing_policy": routing_policy.model_dump(mode="json"),
        "benchmark_evidence": benchmark,
        "benchmarked": bool(queries),
        "profile_checksum": checksum(profiles),
        "input_checksum": checksum(snapshot.manifest["units"]),
        "config_checksum": checksum(snapshot.manifest["config"]),
        "edge_checksum": checksum(snapshot.manifest["edges"]),
        "lineage_checksum": checksum(snapshot.manifest["lineage"]),
    }
    payload = {
        "contract": "knowledge-candidate.v1",
        "snapshot": snapshot.manifest,
        "producer": producer,
        "profiles": profiles,
    }
    digest = checksum(payload)
    tree_version = "candidate-" + digest[:24]
    status = "INACTIVE" if snapshot.publishable else "REJECTED"
    existing = await session.get(TreeManifest, tree_version)
    if existing:
        await verify_knowledge_candidate(session, tree_version)
        if existing.checksum != digest or existing.status != status:
            raise ValueError("Persisted candidate differs from rebuilt candidate")
        return existing
    leaves = [node for node in _walk(snapshot.roots) if not node.children]
    manifest = TreeManifest(
        tree_version=tree_version,
        project_id=project_id,
        config_version=config.version,
        node_count=len(profiles),
        leaf_count=len(leaves),
        max_leaf_size=max((len(n.unit_ids) for n in leaves), default=0),
        giant_ratio=snapshot.metrics["giant_ratio"],
        routing_recall_at_k=snapshot.metrics["routing_recall_at_k"] or 0,
        escape_win_rate=0,
        acl_blackhole_rate=snapshot.metrics["acl_blackhole_rate"] or 0,
        status=status,
        checksum=digest,
        manifest_json=payload,
    )
    session.add(manifest)
    await session.flush()
    for profile in profiles:
        session.add(
            KnowledgeTreeNode(
                tree_version=tree_version,
                node_id=profile["node_id"],
                parent_id=profile["parent_id"],
                security_partition_id=profile["partition_id"],
                payload_json=profile,
                checksum=checksum(profile),
            )
        )
    await session.flush()
    await verify_knowledge_candidate(session, tree_version)
    return manifest


async def verify_knowledge_candidate(session: AsyncSession, tree_version: str) -> TreeManifest:
    manifest = await session.get(TreeManifest, tree_version, populate_existing=True)
    if not manifest or manifest.status not in {"INACTIVE", "REJECTED"}:
        raise ValueError("Expected an inactive knowledge candidate")
    payload = manifest.manifest_json
    if (
        payload.get("contract") != "knowledge-candidate.v1"
        or checksum(payload) != manifest.checksum
        or (manifest.tree_version != "candidate-" + manifest.checksum[:24])
    ):
        raise ValueError("Persisted candidate manifest checksum mismatch")
    snapshot, producer = payload["snapshot"], payload["producer"]
    graph = await verify_graph(session, producer["graph_build_id"])
    if (
        graph.checksum != producer["graph_checksum"]
        or graph.tenant_id != snapshot["tenant_id"]
        or graph.project_id != manifest.project_id
        or graph.manifest_json["units"] != [[u[0], u[2]] for u in producer["knowledge_inputs"]]
        or snapshot["edges"] != [[e["source"], e["target"], e["weight"]] for e in graph.manifest_json["edges"]]
    ):
        raise ValueError("Persisted candidate graph/input lineage mismatch")
    if snapshot["checksum"] != _checksum_manifest(snapshot) or snapshot["project_id"] != manifest.project_id:
        raise ValueError("Persisted routing snapshot checksum mismatch")
    for field in ("input", "config", "edge", "lineage"):
        key = {"input": "units", "edge": "edges"}.get(field, field)
        if checksum(snapshot[key]) != producer[field + "_checksum"]:
            raise ValueError("Persisted candidate component checksum mismatch")
    gates = snapshot["quality_gates"]
    expected_status = "INACTIVE" if gates and all(value is True for value in gates.values()) else "REJECTED"
    if manifest.status != expected_status or snapshot["status"] != (
        "QUALITY_PASSED" if expected_status == "INACTIVE" else "REJECTED"
    ):
        raise ValueError("Failed quality/ACL candidate cannot be accepted")
    nodes = (
        await session.scalars(
            select(KnowledgeTreeNode)
            .where(KnowledgeTreeNode.tree_version == tree_version)
            .order_by(KnowledgeTreeNode.node_id)
        )
    ).all()
    actual = [n.payload_json for n in nodes]
    leaves = [n for n in nodes if n.payload_json["is_leaf"]]
    metrics = snapshot["quality_metrics"]
    if (
        manifest.config_version != snapshot["config_version"]
        or manifest.leaf_count != len(leaves)
        or manifest.max_leaf_size != max((len(n.payload_json["unit_ids"]) for n in leaves), default=0)
        or manifest.giant_ratio != metrics["giant_ratio"]
        or manifest.routing_recall_at_k != (metrics["routing_recall_at_k"] or 0)
        or manifest.acl_blackhole_rate != (metrics["acl_blackhole_rate"] or 0)
        or manifest.escape_win_rate != 0
    ):
        raise ValueError("Persisted candidate metrics/config columns mismatch")
    if (
        actual != payload["profiles"]
        or checksum(actual) != producer["profile_checksum"]
        or len(nodes) != manifest.node_count
        or len(nodes) != snapshot["node_count"]
        or any(
            checksum(n.payload_json) != n.checksum
            or n.node_id != n.payload_json["node_id"]
            or n.parent_id != n.payload_json["parent_id"]
            or n.security_partition_id != n.payload_json["partition_id"]
            for n in nodes
        )
        or sorted([n.node_id, n.parent_id] for n in nodes) != snapshot["lineage"]
    ):
        raise ValueError("Persisted candidate nodes/profiles/lineage mismatch")
    inventory = {u["unit_id"]: u for u in snapshot["units"]}
    for node in nodes:
        p = node.payload_json
        if (
            p["tenant_id"] != snapshot["tenant_id"]
            or p["project_id"] != manifest.project_id
            or p["accessible_unit_count"] != len(p["unit_ids"])
            or any(uid not in inventory or inventory[uid]["partition"] != p["partition_id"] for uid in p["unit_ids"])
        ):
            raise ValueError("Persisted candidate profile violates ACL partition isolation")
    return manifest
