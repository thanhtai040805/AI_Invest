"""Read-only, ACL-scoped presentation of published Knowledge Routing snapshots."""

from __future__ import annotations

import asyncio
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
import math
import re
from typing import Any

from sqlalchemy import and_, case, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.errors import ConflictError, NotFoundError, ServiceUnavailableError, ValidationError
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.db.models import (
    CanonicalBlock,
    Document,
    DocumentVersion,
    KnowledgeEvidence,
    KnowledgeUnit,
    KnowledgeUnitEdge,
    ProjectSearchState,
    Source,
    SourceProjectMapping,
    TreeManifest,
)
from sag_api.sag.engine_manager import _decode_universe_cursor, _encode_universe_cursor
from sag_api.schemas.universe import (
    UniverseKnowledgeDetailOut,
    UniverseKnowledgeEdgeOut,
    UniverseKnowledgeEvidenceOut,
    UniverseKnowledgeMatchOut,
    UniverseKnowledgePageOut,
    UniverseKnowledgeReferenceIn,
    UniverseKnowledgeSynapseOut,
    UniverseKnowledgeUnitOut,
    UniverseTreeOut,
)
from sag_api.services.knowledge_service import _cosine, checksum, terms, unit_checksum
from sag_api.services.search_unit_retrieval_service import _load_current_ready_versions
from sag_api.services.tree_publish_service import PublishVerificationError, _checksum_manifest


def _norm_ent(raw_e: str) -> str:
    return re.sub(r"[\s_-]+", " ", str(raw_e).casefold()).strip()


@dataclass
class KnowledgeUniverseSnapshot:
    tree: UniverseTreeOut
    tenant_id: str
    scope_key: str
    scopes: set[tuple[str, str, str]]
    members: dict[str, set[str] | frozenset[str]]
    assignments: list[dict[str, Any]]
    edges: list[tuple[str, str, float]]
    edges_by_unit: dict[str, tuple[tuple[int, str, str, float], ...]] | None = None
    cached_unit_ids: frozenset[str] | None = None
    _runtime_cache: dict[str, Any] | None = None

    @property
    def unit_ids(self) -> set[str]:
        if self.cached_unit_ids is not None:
            return set(self.cached_unit_ids)
        return set().union(*self.members.values())

    @property
    def allowed_unit_set(self) -> set[str] | frozenset[str]:
        if self.cached_unit_ids is not None:
            return self.cached_unit_ids
        return set().union(*self.members.values())


_VERIFIED_TREE_MANIFEST_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}
_VERIFIED_TREE_MANIFEST_CACHE_MAX = 16
_VERIFIED_TREE_MANIFEST_LOCKS: dict[tuple[str, str, str], asyncio.Lock] = {}

_SNAPSHOT_MEMBERSHIP_CACHE: dict[
    tuple[Any, ...],
    tuple[
        dict[str, frozenset[str]],
        tuple[dict[str, Any], ...],
        tuple[tuple[str, str, float], ...],
        dict[str, tuple[tuple[int, str, str, float], ...]],
        frozenset[str],
        dict[str, Any],
    ],
] = {}
_SNAPSHOT_MEMBERSHIP_CACHE_MAX = 32


def clear_universe_knowledge_caches(project_id: str | None = None) -> None:
    """Evict cached snapshot memberships and page projections (all or for a specific project)."""
    if project_id is None:
        _SNAPSHOT_MEMBERSHIP_CACHE.clear()
        return
    stale_keys = [
        key for key in _SNAPSHOT_MEMBERSHIP_CACHE if isinstance(key, tuple) and len(key) > 0 and key[0] == project_id
    ]
    for key in stale_keys:
        _SNAPSHOT_MEMBERSHIP_CACHE.pop(key, None)


def _build_edges_by_unit(
    edges: list[tuple[str, str, float]] | tuple[tuple[str, str, float], ...],
) -> dict[str, tuple[tuple[int, str, str, float], ...]]:
    by_unit: dict[str, list[tuple[int, str, str, float]]] = defaultdict(list)
    for idx, (src, tgt, weight) in enumerate(edges):
        entry = (idx, src, tgt, weight)
        by_unit[src].append(entry)
        if tgt != src:
            by_unit[tgt].append(entry)
    return {uid: tuple(items) for uid, items in by_unit.items()}


def _slim_verified_manifest(raw: dict[str, Any]) -> dict[str, Any]:
    """Drop bulky embedding payloads after SHA-256 verification while preserving all scope/routing fields."""
    raw_routing = raw.get("routing_profiles")
    slim_routing = (
        [
            {k: v for k, v in item.items() if k != "centroid"} if isinstance(item, dict) else item
            for item in raw_routing
        ]
        if isinstance(raw_routing, list)
        else raw_routing
    )
    raw_source_profiles = raw.get("source_routing_profiles")
    slim_source_profiles = (
        [
            {
                "project_id": item.get("project_id"),
                "source_id": item.get("source_id"),
                "document_version_id": item.get("document_version_id"),
                "partition_id": item.get("partition_id"),
                "tenant_id": item.get("tenant_id"),
                "node_id": item.get("node_id"),
            }
            if isinstance(item, dict)
            else item
            for item in raw_source_profiles
        ]
        if isinstance(raw_source_profiles, list)
        else raw_source_profiles
    )
    return {
        "tenant_id": raw.get("tenant_id"),
        "project_id": raw.get("project_id"),
        "routing_profiles": slim_routing,
        "source_routing_profiles": slim_source_profiles,
        "search_unit_assignments": raw.get("search_unit_assignments"),
        "edges": raw.get("edges", []),
    }


async def get_verified_tree_manifest_projection(
    session: AsyncSession,
    project_id: str,
    tree_version: str,
    expected_checksum: str,
) -> dict[str, Any]:
    """Load and cryptographically verify an immutable TreeManifest once per (project_id, tree_version, checksum)."""
    if (
        not isinstance(expected_checksum, str)
        or len(expected_checksum) != 64
        or any(ch not in "0123456789abcdef" for ch in expected_checksum)
        or tree_version != f"tree-{expected_checksum[:24]}"
    ):
        raise ServiceUnavailableError("Published knowledge snapshot failed integrity validation")

    cache_key = (project_id, tree_version, expected_checksum)
    cached = _VERIFIED_TREE_MANIFEST_CACHE.get(cache_key)
    if cached is not None:
        return cached

    lock = _VERIFIED_TREE_MANIFEST_LOCKS.get(cache_key)
    if lock is None:
        lock = asyncio.Lock()
        _VERIFIED_TREE_MANIFEST_LOCKS[cache_key] = lock

    try:
        async with lock:
            cached = _VERIFIED_TREE_MANIFEST_CACHE.get(cache_key)
            if cached is not None:
                return cached
            raw = await session.scalar(
                select(TreeManifest.manifest_json).where(
                    TreeManifest.project_id == project_id,
                    TreeManifest.tree_version == tree_version,
                    TreeManifest.checksum == expected_checksum,
                    TreeManifest.status == "ACTIVE",
                )
            )
            if not isinstance(raw, dict):
                raise ServiceUnavailableError("Published knowledge snapshot failed integrity validation")
            try:
                verified_digest = await asyncio.to_thread(_checksum_manifest, raw)
            except PublishVerificationError as error:
                raise ServiceUnavailableError("Published knowledge snapshot is not canonical JSON") from error
            if raw.get("project_id") != project_id or verified_digest != expected_checksum:
                raise ServiceUnavailableError("Published knowledge snapshot failed integrity validation")
            slim = _slim_verified_manifest(raw)
            if len(_VERIFIED_TREE_MANIFEST_CACHE) >= _VERIFIED_TREE_MANIFEST_CACHE_MAX:
                _VERIFIED_TREE_MANIFEST_CACHE.pop(next(iter(_VERIFIED_TREE_MANIFEST_CACHE)))
            _VERIFIED_TREE_MANIFEST_CACHE[cache_key] = slim
            return slim
    finally:
        if not lock.locked():
            _VERIFIED_TREE_MANIFEST_LOCKS.pop(cache_key, None)


async def warm_active_tree_manifest_cache(session_factory: Any) -> None:
    """Pre-verify active project tree manifests in the background so cold Universe loads do not block."""
    try:
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        ProjectSearchState.project_id,
                        TreeManifest.tree_version,
                        TreeManifest.checksum,
                    )
                    .join(TreeManifest, ProjectSearchState.active_tree_version == TreeManifest.tree_version)
                    .where(
                        TreeManifest.project_id == ProjectSearchState.project_id,
                        TreeManifest.status == "ACTIVE",
                    )
                    .limit(_VERIFIED_TREE_MANIFEST_CACHE_MAX)
                )
            ).all()
            for project_id, tree_version, digest in rows:
                try:
                    await get_verified_tree_manifest_projection(session, project_id, tree_version, digest)
                except ServiceUnavailableError:
                    continue
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - best-effort startup warm-up
        return


def _snapshot_memberships(
    raw: dict[str, Any],
    tree: UniverseTreeOut,
    tenant_id: str,
    scopes: set[tuple[str, str, str]],
) -> tuple[dict[str, set[str]], list[dict[str, Any]], list[tuple[str, str, float]]]:
    profiles = raw.get("routing_profiles")
    assignments = raw.get("search_unit_assignments")
    if not isinstance(profiles, list) or not isinstance(assignments, list):
        raise ServiceUnavailableError("The active tree has no published knowledge membership")
    allowed_nodes = {node.id for node in tree.nodes}
    allowed_assignments = []
    allowed_units: set[str] = set()
    for item in assignments:
        if not isinstance(item, dict):
            raise ServiceUnavailableError("Invalid published knowledge assignment")
        scope = (item.get("source_id"), item.get("document_version_id"), item.get("partition_id"))
        if scope not in scopes:
            continue
        ids = item.get("knowledge_unit_ids")
        if (
            not isinstance(ids, list)
            or not all(isinstance(value, str) and value for value in ids)
            or not isinstance(item.get("search_unit_id"), str)
        ):
            raise ServiceUnavailableError("Invalid published knowledge assignment")
        allowed_assignments.append(item)
        allowed_units.update(ids)
    members = {}
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ServiceUnavailableError("Invalid published knowledge profile")
        node_id = profile.get("node_id")
        if node_id not in allowed_nodes:
            continue
        ids = profile.get("unit_ids")
        if (
            profile.get("tenant_id") != tenant_id
            or profile.get("project_id") != tree.project_id
            or profile.get("partition_id") not in {scope[2] for scope in scopes}
            or not isinstance(ids, list)
            or not all(isinstance(value, str) and value for value in ids)
            or node_id in members
        ):
            raise ServiceUnavailableError("Published knowledge membership failed scope validation")
        members[node_id] = set(ids) & allowed_units
    if set(members) != allowed_nodes:
        raise ServiceUnavailableError("Published knowledge membership is incomplete")
    visible = set().union(*members.values())
    edges = []
    for edge in raw.get("edges", []):
        if (
            not isinstance(edge, (list, tuple))
            or len(edge) != 3
            or not isinstance(edge[0], str)
            or not isinstance(edge[1], str)
            or isinstance(edge[2], bool)
            or not isinstance(edge[2], (float, int))
            or not math.isfinite(edge[2])
            or not 0 <= edge[2] <= 1
        ):
            raise ServiceUnavailableError("Invalid published knowledge edge")
        if edge[0] in visible and edge[1] in visible:
            edges.append((edge[0], edge[1], float(edge[2])))
    return members, allowed_assignments, edges


async def load_knowledge_universe_snapshot(
    session: AsyncSession,
    principal: VerifiedPrincipal,
    tree: UniverseTreeOut,
) -> KnowledgeUniverseSnapshot:
    if tree.project_id not in principal.allowed_project_ids or not principal.tenant_id:
        raise NotFoundError("Knowledge project is unavailable")

    session_info = getattr(session, "info", None)
    shared_scope = session_info.get("_universe_req_scope") if isinstance(session_info, dict) else None
    verified_checksums = (
        shared_scope.get("verified_checksums")
        if isinstance(shared_scope, dict) and isinstance(shared_scope.get("verified_checksums"), dict)
        else None
    )
    digest = verified_checksums.get(tree.project_id) if verified_checksums is not None else None
    if digest is None:
        digest = await session.scalar(
            select(TreeManifest.checksum)
            .join(ProjectSearchState, ProjectSearchState.active_tree_version == TreeManifest.tree_version)
            .where(
                ProjectSearchState.project_id == tree.project_id,
                ProjectSearchState.active_search_epoch == tree.search_epoch,
                TreeManifest.project_id == tree.project_id,
                TreeManifest.tree_version == tree.tree_version,
                TreeManifest.status == "ACTIVE",
            )
            .with_for_update(read=True, of=ProjectSearchState)
        )
    if digest is None:
        raise ConflictError("The knowledge snapshot changed; refresh the universe")
    raw = await get_verified_tree_manifest_projection(
        session,
        tree.project_id,
        tree.tree_version,
        digest,
    )
    if raw.get("tenant_id") != principal.tenant_id or raw.get("project_id") != tree.project_id:
        raise ServiceUnavailableError("Published knowledge snapshot failed integrity validation")

    if isinstance(shared_scope, dict) and shared_scope.get("ready_versions") is not None:
        versions = {
            key: value
            for key, value in shared_scope["ready_versions"].items()
            if value.project_id == tree.project_id
        }
    else:
        sources = list(
            (
                await session.scalars(
                    select(Source)
                    .where(
                        Source.id.in_(select(SourceProjectMapping.source_id).where(
                            SourceProjectMapping.organization_id == principal.organization_id,
                            SourceProjectMapping.project_id == tree.project_id,
                            SourceProjectMapping.state == "CONFIRMED",
                        )),
                    )
                    .order_by(Source.id)
                    .limit(settings.universe_manifest_source_limit + 1)
                )
            ).all()
        )
        if len(sources) > settings.universe_manifest_source_limit:
            raise ServiceUnavailableError("Universe exceeds the configured authorized source limit")
        versions = await _load_current_ready_versions(session, sources, principal)
        versions = {key: value for key, value in versions.items() if value.project_id == tree.project_id}

    scopes = {(value.source_id, key, value.partition_id) for key, value in versions.items()}
    scope_key = checksum(
        {
            "subject": principal.subject,
            "organization": principal.organization_id,
            "tenant": principal.tenant_id,
            "tree": tree.tree_version,
            "epoch": tree.search_epoch,
            "versions": sorted(
                (key, value.source_id, value.partition_id, value.acl_mapping_version,
                 value.ingestion_run_id, value.index_stage_run_id, value.search_manifest_checksum)
                for key, value in versions.items()
            ),
        }
    )
    membership_key = (
        tree.project_id,
        tree.tree_version,
        digest,
        principal.tenant_id,
        frozenset(scopes),
        tuple(node.id for node in tree.nodes),
    )
    cached_membership = _SNAPSHOT_MEMBERSHIP_CACHE.get(membership_key)
    if cached_membership is None:
        raw_members, assignments, edges = _snapshot_memberships(raw, tree, principal.tenant_id, scopes)
        edges_by_unit = _build_edges_by_unit(edges)
        frozen_members = {node_id: frozenset(unit_set) for node_id, unit_set in raw_members.items()}
        frozen_unit_ids = frozenset().union(*frozen_members.values()) if frozen_members else frozenset()
        runtime_cache: dict[str, Any] = {
            "sorted_members": {node_id: tuple(sorted(unit_set)) for node_id, unit_set in frozen_members.items()},
            "doc_by_unit": {},
            "cross_doc_counts": {},
            "presented_units": {},
            "edge_signals": {},
            "page_cache": {},
        }
        if len(_SNAPSHOT_MEMBERSHIP_CACHE) >= _SNAPSHOT_MEMBERSHIP_CACHE_MAX:
            _SNAPSHOT_MEMBERSHIP_CACHE.pop(next(iter(_SNAPSHOT_MEMBERSHIP_CACHE)))
        _SNAPSHOT_MEMBERSHIP_CACHE[membership_key] = (
            frozen_members,
            tuple(assignments),
            tuple(edges),
            edges_by_unit,
            frozen_unit_ids,
            runtime_cache,
        )
        members: dict[str, set[str] | frozenset[str]] = dict(frozen_members)
    else:
        (
            frozen_members,
            tuple_assignments,
            tuple_edges,
            edges_by_unit,
            frozen_unit_ids,
            runtime_cache,
        ) = cached_membership
        members = dict(frozen_members)
        assignments = list(tuple_assignments)
        edges = list(tuple_edges)
    return KnowledgeUniverseSnapshot(
        tree,
        principal.tenant_id,
        scope_key,
        scopes,
        members,
        assignments,
        edges,
        edges_by_unit=edges_by_unit,
        cached_unit_ids=frozen_unit_ids,
        _runtime_cache=runtime_cache,
    )


def _unit_query(snapshot: KnowledgeUniverseSnapshot, ids: set[str] | frozenset[str] | list[str] | tuple[str, ...] | None):
    now = datetime.now(UTC)
    return (
        select(KnowledgeUnit, Document, DocumentVersion, Source)
        .join(DocumentVersion, DocumentVersion.id == KnowledgeUnit.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(Source, Source.id == Document.source_id)
        .where(
            KnowledgeUnit.id.in_(ids) if ids is not None else True,
            KnowledgeUnit.tenant_id == snapshot.tenant_id,
            KnowledgeUnit.project_id == snapshot.tree.project_id,
            KnowledgeUnit.is_current.is_(True),
            KnowledgeUnit.valid_from <= now,
            KnowledgeUnit.valid_to > now,
            tuple_(KnowledgeUnit.source_id, KnowledgeUnit.document_version_id,
                   KnowledgeUnit.security_partition_id).in_(sorted(snapshot.scopes)),
            Document.tenant_id == KnowledgeUnit.tenant_id,
            Document.project_id == KnowledgeUnit.project_id,
            Document.source_id == KnowledgeUnit.source_id,
            Document.is_active.is_(True),
            DocumentVersion.knowledge_status == "DATA_READY",
            DocumentVersion.search_status.in_(("READY", "SEARCH_READY")),
            DocumentVersion.valid_from <= now,
            DocumentVersion.valid_to > now,
        )
    )


def _unit_query_by_ids_fast(
    snapshot: KnowledgeUniverseSnapshot,
    ids: list[str] | tuple[str, ...] | set[str],
):
    """Primary-key indexed lookup for a small candidate slice without thousands of scope tuple bind params."""
    now = datetime.now(UTC)
    return (
        select(KnowledgeUnit, Document, DocumentVersion, Source)
        .join(DocumentVersion, DocumentVersion.id == KnowledgeUnit.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(Source, Source.id == Document.source_id)
        .where(
            KnowledgeUnit.id.in_(ids),
            KnowledgeUnit.tenant_id == snapshot.tenant_id,
            KnowledgeUnit.project_id == snapshot.tree.project_id,
            KnowledgeUnit.is_current.is_(True),
            KnowledgeUnit.valid_from <= now,
            KnowledgeUnit.valid_to > now,
            Document.tenant_id == KnowledgeUnit.tenant_id,
            Document.project_id == KnowledgeUnit.project_id,
            Document.source_id == KnowledgeUnit.source_id,
            Document.is_active.is_(True),
            DocumentVersion.knowledge_status == "DATA_READY",
            DocumentVersion.search_status.in_(("READY", "SEARCH_READY")),
            DocumentVersion.valid_from <= now,
            DocumentVersion.valid_to > now,
        )
        .order_by(KnowledgeUnit.id)
    )


async def load_source_universe_snapshots(
    session: AsyncSession, principal: VerifiedPrincipal, project_id: str | None = None,
) -> list[KnowledgeUniverseSnapshot]:
    """Current source groups for display only; never a published routing tree."""
    if project_id and project_id not in principal.allowed_project_ids:
        raise NotFoundError("Knowledge project is unavailable")
    if not principal.tenant_id:
        return []
    projects = [project_id] if project_id else sorted(principal.allowed_project_ids)
    sources = list((await session.scalars(
        select(Source).where(Source.id.in_(select(SourceProjectMapping.source_id).where(
            SourceProjectMapping.organization_id == principal.organization_id,
            SourceProjectMapping.project_id.in_(projects), SourceProjectMapping.state == "CONFIRMED",
        ))).order_by(Source.id).limit(settings.universe_manifest_source_limit + 1)
    )).all())
    if len(sources) > settings.universe_manifest_source_limit:
        raise ServiceUnavailableError("Universe exceeds the configured authorized source limit")
    versions = await _load_current_ready_versions(session, sources, principal)
    snapshots = []
    for project in sorted({value.project_id for value in versions.values()} & set(projects)):
        current = {key: value for key, value in versions.items() if value.project_id == project}
        scope_key = checksum({
            "subject": principal.subject, "organization": principal.organization_id,
            "tenant": principal.tenant_id, "project": project, "projection": "source",
            "versions": sorted((key, value.source_id, value.partition_id, value.acl_mapping_version,
                                value.ingestion_run_id, value.index_stage_run_id, value.search_manifest_checksum)
                               for key, value in current.items()),
        })
        tree = UniverseTreeOut(projection="source", project_id=project, tree_version=f"source-{scope_key[:24]}",
                                   search_epoch=None, node_count=0, unit_count=0, source_count=0)
        snapshot = KnowledgeUniverseSnapshot(
            tree, principal.tenant_id, scope_key,
            {(value.source_id, key, value.partition_id) for key, value in current.items()}, {}, [], [],
        )
        inventory = _unit_query(snapshot, None).subquery()
        counts = (await session.execute(
            select(
                inventory.c.source_id,
                func.count(),
                func.count(func.distinct(inventory.c.document_version_id)),
            )
            .group_by(inventory.c.source_id)
            .order_by(inventory.c.source_id)
        )).all()
        names = {source.id: source.name for source in sources}
        nodes = [dict(id=f"source:{source}", label=names[source], depth=0, is_leaf=True,
                      unit_count=count, source_count=1, document_count=doc_count)
                 for source, count, doc_count in counts]
        # Revalidate the presentation model; these groups carry no routing membership or edges.
        tree = UniverseTreeOut.model_validate({**tree.model_dump(), "nodes": nodes,
                                              "node_count": len(counts), "source_count": len(counts),
                                              "unit_count": sum(count for _, count, _ in counts)})
        snapshot.tree = tree
        snapshot.members = {node.id: set() for node in tree.nodes}
        if tree.nodes:
            snapshots.append(snapshot)
    return snapshots


def _extract_shared_entities_and_signals(
    unit_a: KnowledgeUnit,
    doc_a: Document,
    unit_b: KnowledgeUnit,
    doc_b: Document,
    persisted_signals: dict[str, Any] | None = None,
) -> tuple[bool, list[str], dict[str, float | None]]:
    cross_document = doc_a.id != doc_b.id
    features_a = unit_a.features_json if isinstance(unit_a.features_json, dict) else {}
    features_b = unit_b.features_json if isinstance(unit_b.features_json, dict) else {}
    raw_ents_a = [str(e).strip() for e in (features_a.get("entities") or []) if isinstance(e, str) and str(e).strip()]
    raw_ents_b = [str(e).strip() for e in (features_b.get("entities") or []) if isinstance(e, str) and str(e).strip()]
    norm_to_display_a: dict[str, str] = {}
    for ent in raw_ents_a:
        norm = _norm_ent(ent)
        if norm and norm not in norm_to_display_a:
            norm_to_display_a[norm] = ent
    norm_b = {_norm_ent(ent) for ent in raw_ents_b if _norm_ent(ent)}
    shared_norms = sorted(set(norm_to_display_a) & norm_b)
    shared_entities = [norm_to_display_a[norm] for norm in shared_norms]

    if isinstance(persisted_signals, dict) and isinstance(persisted_signals.get("signals"), dict):
        raw_sig = persisted_signals["signals"]
        signals: dict[str, float | None] = {
            key: (float(val) if isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val) else None)
            for key, val in raw_sig.items()
        }
        return cross_document, shared_entities, signals

    dense_a = features_a.get("dense") if isinstance(features_a.get("dense"), list) else []
    dense_b = features_b.get("dense") if isinstance(features_b.get("dense"), list) else []
    semantic = _cosine(dense_a, dense_b)
    union_norms = set(norm_to_display_a) | norm_b
    entity_score = (len(shared_norms) / len(union_norms)) if union_norms else None
    tokens_a = terms(unit_a.text)
    tokens_b = terms(unit_b.text)
    common_tokens = set(tokens_a) & set(tokens_b)
    all_tokens = set(tokens_a) | set(tokens_b)
    lexical = (len(common_tokens) / len(all_tokens)) if all_tokens else 0.0
    prov_a = unit_a.provenance_json if isinstance(unit_a.provenance_json, dict) else {}
    prov_b = unit_b.provenance_json if isinstance(unit_b.provenance_json, dict) else {}
    same_doc = not cross_document
    structure = (
        0.85
        if same_doc and prov_a.get("section_path") == prov_b.get("section_path")
        else 0.65
        if same_doc and abs(unit_a.ordinal - unit_b.ordinal) <= 2
        else 0.45
        if same_doc
        else 0.25
        if unit_a.source_id == unit_b.source_id
        else 0.0
    )
    citations_a = set(features_a.get("citations") or [])
    citations_b = set(features_b.get("citations") or [])
    citation = (0.7 if (citations_a & citations_b) else 0.0) if (citations_a or citations_b) else None
    return cross_document, shared_entities, {
        "semantic": round(semantic, 4) if semantic is not None else None,
        "entity": round(entity_score, 4) if entity_score is not None else None,
        "lexical": round(lexical, 4),
        "structure": round(structure, 4),
        "citation": citation,
    }


async def _load_persisted_edge_signals(
    session: AsyncSession,
    pairs: list[tuple[str, str]],
    runtime_cache: dict[str, Any] | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    if not pairs:
        return {}
    canonical_pairs = sorted({(min(a, b), max(a, b)) for a, b in pairs})
    edge_cache = runtime_cache.get("edge_signals") if isinstance(runtime_cache, dict) else None
    missing_pairs = (
        [pair for pair in canonical_pairs if pair not in edge_cache]
        if isinstance(edge_cache, dict)
        else canonical_pairs
    )
    if missing_pairs:
        rows = (
            await session.execute(
                select(
                    KnowledgeUnitEdge.source_unit_id,
                    KnowledgeUnitEdge.target_unit_id,
                    KnowledgeUnitEdge.signals_json,
                ).where(
                    tuple_(KnowledgeUnitEdge.source_unit_id, KnowledgeUnitEdge.target_unit_id).in_(missing_pairs)
                )
            )
        ).all()
        found_map: dict[tuple[str, str], dict[str, Any] | None] = {pair: None for pair in missing_pairs}
        for src, tgt, sig_json in rows:
            if isinstance(sig_json, dict):
                found_map[(min(src, tgt), max(src, tgt))] = sig_json
        if isinstance(edge_cache, dict):
            edge_cache.update(found_map)
        else:
            by_pair: dict[tuple[str, str], dict[str, Any]] = {}
            for (u_a, u_b), val in found_map.items():
                if val is not None:
                    by_pair[(u_a, u_b)] = val
                    by_pair[(u_b, u_a)] = val
            return by_pair

    by_pair = {}
    if isinstance(edge_cache, dict):
        for u_a, u_b in canonical_pairs:
            val = edge_cache.get((u_a, u_b))
            if isinstance(val, dict):
                by_pair[(u_a, u_b)] = val
                by_pair[(u_b, u_a)] = val
    return by_pair


async def _present_units(
    session: AsyncSession,
    rows,
    snapshot: KnowledgeUniverseSnapshot | None = None,
) -> list[UniverseKnowledgeUnitOut]:
    if not rows:
        return []
    runtime_cache = snapshot._runtime_cache if snapshot is not None else None
    presented_cache = runtime_cache.get("presented_units") if isinstance(runtime_cache, dict) else None
    doc_cache = runtime_cache.get("doc_by_unit") if isinstance(runtime_cache, dict) else None

    if isinstance(presented_cache, dict) and all(r[0].id in presented_cache for r in rows):
        return [presented_cache[r[0].id] for r in rows]

    unseen_rows = (
        [r for r in rows if r[0].id not in presented_cache]
        if isinstance(presented_cache, dict)
        else list(rows)
    )

    block_ids = set()
    unit_ids = []
    doc_by_unit: dict[str, str] = dict(doc_cache) if isinstance(doc_cache, dict) else {}
    for unit, document, _version, _source in unseen_rows:
        ids = unit.provenance_json.get("evidence_block_ids")
        if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str):
            raise ServiceUnavailableError("Knowledge unit has no canonical evidence locator")
        block_ids.add(ids[0])
        unit_ids.append(unit.id)
        doc_by_unit[unit.id] = document.id
        if isinstance(doc_cache, dict):
            doc_cache[unit.id] = document.id

    blocks = {
        block.id: block
        for block in (await session.scalars(select(CanonicalBlock).where(CanonicalBlock.id.in_(block_ids)))).all()
    }
    evidence_stats = {
        uid: (int(cnt or 0), bool(has_e2))
        for uid, cnt, has_e2 in (
            await session.execute(
                select(
                    KnowledgeEvidence.unit_id,
                    func.count(KnowledgeEvidence.id),
                    func.max(case((KnowledgeEvidence.tier == "E2", 1), else_=0)),
                )
                .where(KnowledgeEvidence.unit_id.in_(unit_ids))
                .group_by(KnowledgeEvidence.unit_id)
            )
        ).all()
    }
    cross_doc_counts: dict[str, int] = {uid: 0 for uid in unit_ids}
    if snapshot is not None and snapshot.edges:
        unit_id_set = set(unit_ids)
        neighbor_ids_by_unit: dict[str, set[str]] = {uid: set() for uid in unit_ids}
        missing_neighbor_ids: set[str] = set()
        if snapshot.edges_by_unit is not None:
            for uid in unit_ids:
                for _idx, src, tgt, _w in snapshot.edges_by_unit.get(uid, ()):
                    other = tgt if src == uid else src
                    neighbor_ids_by_unit[uid].add(other)
                    if other not in doc_by_unit:
                        missing_neighbor_ids.add(other)
        else:
            for src, tgt, _w in snapshot.edges:
                if src in unit_id_set:
                    neighbor_ids_by_unit[src].add(tgt)
                    if tgt not in doc_by_unit:
                        missing_neighbor_ids.add(tgt)
                if tgt in unit_id_set:
                    neighbor_ids_by_unit[tgt].add(src)
                    if src not in doc_by_unit:
                        missing_neighbor_ids.add(src)
        if missing_neighbor_ids:
            neighbor_doc_rows = (
                await session.execute(
                    select(KnowledgeUnit.id, DocumentVersion.document_id)
                    .join(DocumentVersion, DocumentVersion.id == KnowledgeUnit.document_version_id)
                    .where(KnowledgeUnit.id.in_(missing_neighbor_ids))
                )
            ).all()
            for n_uid, n_doc_id in neighbor_doc_rows:
                doc_by_unit[n_uid] = n_doc_id
                if isinstance(doc_cache, dict):
                    doc_cache[n_uid] = n_doc_id
        for uid, n_ids in neighbor_ids_by_unit.items():
            own_doc = doc_by_unit.get(uid)
            cross_doc_counts[uid] = sum(
                1 for n_id in n_ids if doc_by_unit.get(n_id) and doc_by_unit.get(n_id) != own_doc
            )

    newly_presented: dict[str, UniverseKnowledgeUnitOut] = {}
    for unit, document, version, source in unseen_rows:
        provenance = unit.provenance_json
        block_id = provenance["evidence_block_ids"][0]
        block = blocks.get(block_id)
        if (
            block is None
            or block.document_version_id != version.id
            or block.content_hash != unit.content_hash
            or block.normalized_text != unit.text
            or any(provenance.get(key) != value for key, value in {
                "tenant_id": unit.tenant_id,
                "project_id": unit.project_id,
                "source_id": source.id,
                "document_id": document.id,
                "document_version_id": version.id,
                "security_partition_id": unit.security_partition_id,
            }.items())
        ):
            raise ServiceUnavailableError("Knowledge evidence failed canonical scope validation")
        heading = block.section_path or document.filename
        text = " ".join(unit.text.split())
        features = unit.features_json if isinstance(unit.features_json, dict) else {}
        ev_count, has_e2 = evidence_stats.get(unit.id, (0, False))
        e2_reason = features.get("e2_reason") if isinstance(features.get("e2_reason"), str) else None
        is_e2 = bool(has_e2 or features.get("e2_enriched") or e2_reason)
        entities = [
            str(ent).strip()
            for ent in (features.get("entities") or [])
            if isinstance(ent, str) and str(ent).strip()
        ]
        importance = float(features.get("importance") or 0.0)
        local_confidence = float(features.get("local_confidence") or 0.0)
        unit_out = UniverseKnowledgeUnitOut(
            id=unit.id,
            label=text[:110] or heading,
            preview=text[:360],
            source_id=source.id,
            source_name=source.name,
            document_id=document.id,
            document_version_id=version.id,
            document_name=document.filename,
            version_no=version.version_no,
            block_id=block.id,
            heading=heading,
            page_from=block.page_from,
            page_to=block.page_to,
            anchor=(block.source_anchor or provenance.get("source_anchor") or f"block-{block.ordinal}"),
            neural_tier="E2" if is_e2 else "E0_E1",
            importance=round(importance, 4) if math.isfinite(importance) else 0.0,
            local_confidence=round(local_confidence, 4) if math.isfinite(local_confidence) else 0.0,
            e2_reason=e2_reason,
            entities=entities[:12],
            evidence_count=ev_count,
            cross_doc_synapse_count=cross_doc_counts.get(unit.id, 0),
            checksum=unit.checksum or "",
        )
        newly_presented[unit.id] = unit_out
        if isinstance(presented_cache, dict):
            presented_cache[unit.id] = unit_out

    if isinstance(presented_cache, dict):
        return [presented_cache[r[0].id] for r in rows]
    return [newly_presented[r[0].id] for r in rows]


def _cursor_after(cursor: str | None, context: dict[str, Any]) -> str:
    if cursor is None:
        return ""
    try:
        value = _decode_universe_cursor(cursor, settings.secret_key)
    except ValueError as error:
        raise ValidationError("Invalid knowledge universe cursor") from error
    if any(value.get(key) != expected for key, expected in context.items()):
        raise ConflictError("Knowledge cursor scope changed; refresh the universe")
    after = value.get("after")
    if not isinstance(after, str) or len(after) > 128:
        raise ValidationError("Invalid knowledge universe cursor position")
    return after


async def knowledge_universe_page(
    session: AsyncSession,
    snapshot: KnowledgeUniverseSnapshot,
    *,
    node_id: str,
    limit: int,
    cursor: str | None = None,
    related_to: str | None = None,
) -> UniverseKnowledgePageOut:
    if node_id not in snapshot.members:
        raise NotFoundError("Knowledge cluster is unavailable")
    source_mode = snapshot.tree.projection == "source"
    ids = None if source_mode else snapshot.members[node_id]
    anchor = None
    if related_to:
        if not source_mode and related_to not in snapshot.allowed_unit_set:
            raise NotFoundError("Knowledge unit is unavailable")
        anchor = (await session.execute(_unit_query(snapshot, {related_to}))).one_or_none()
        if anchor is None:
            raise NotFoundError("Knowledge unit is unavailable")
        if snapshot.edges_by_unit is not None:
            ids = {
                target if source == related_to else source
                for _idx, source, target, _weight in snapshot.edges_by_unit.get(related_to, ())
            }
        else:
            ids = {
                target if source == related_to else source
                for source, target, _weight in snapshot.edges
                if related_to in {source, target}
            }
    context = {
        "v": 2, "kind": "knowledge", "scope": snapshot.scope_key,
        "node": node_id, "related": related_to,
    }
    after = _cursor_after(cursor, context)

    rows = None
    total = 0
    runtime_cache = snapshot._runtime_cache
    page_cache = runtime_cache.get("page_cache") if isinstance(runtime_cache, dict) else None
    page_cache_key = (snapshot.scope_key, node_id, limit, after, related_to)
    cached_page = page_cache.get(page_cache_key) if isinstance(page_cache, dict) else None
    if not source_mode and not related_to and isinstance(runtime_cache, dict) and ids is not None:
        sorted_map = runtime_cache.get("sorted_members")
        sorted_ids = sorted_map.get(node_id) if isinstance(sorted_map, dict) else None
        if sorted_ids is None:
            sorted_ids = tuple(sorted(ids))
        start_idx = bisect_right(sorted_ids, after) if after else 0
        candidate_slice = sorted_ids[start_idx : start_idx + limit + 1]
        if not candidate_slice:
            rows = []
            total = len(sorted_ids)
        else:
            fast_rows = (await session.execute(_unit_query_by_ids_fast(snapshot, candidate_slice))).all()
            scoped_fast_rows = [
                r
                for r in fast_rows
                if (r[0].source_id, r[0].document_version_id, r[0].security_partition_id) in snapshot.scopes
            ]
            if len(scoped_fast_rows) == len(candidate_slice):
                rows = scoped_fast_rows
                total = len(sorted_ids)
                if (
                    cached_page is not None
                    and cached_page.total == total
                    and [u.id for u in cached_page.units] == [r[0].id for r in rows[:limit]]
                ):
                    return cached_page
            elif isinstance(page_cache, dict):
                page_cache.clear()
                cached_page = None

    if rows is None:
        query = _unit_query(snapshot, ids)
        if source_mode:
            query = query.where(KnowledgeUnit.source_id == node_id.removeprefix("source:"))
        total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = (
            await session.execute(query.where(KnowledgeUnit.id > after).order_by(KnowledgeUnit.id).limit(limit + 1))
        ).all()
        if (
            cached_page is not None
            and cached_page.total == total
            and [u.id for u in cached_page.units] == [r[0].id for r in rows[:limit]]
        ):
            return cached_page

    has_more = len(rows) > limit
    rows = rows[:limit]
    units = await _present_units(session, rows, snapshot)
    visible = {unit.id for unit in units}
    if related_to:
        visible.add(related_to)
    if snapshot.edges_by_unit is not None:
        matched_by_idx: dict[int, tuple[str, str, float]] = {}
        for uid in visible:
            for idx, source, target, weight in snapshot.edges_by_unit.get(uid, ()):
                if idx not in matched_by_idx and source in visible and target in visible:
                    matched_by_idx[idx] = (source, target, weight)
        visible_triples = [
            matched_by_idx[idx] for idx in sorted(matched_by_idx)
        ][:settings.universe_edge_budget_desktop]
    else:
        visible_triples = [
            (source, target, weight)
            for source, target, weight in snapshot.edges
            if source in visible and target in visible
        ][:settings.universe_edge_budget_desktop]
    row_by_id = {r[0].id: r for r in rows}
    if anchor is not None:
        row_by_id[anchor[0].id] = anchor
    persisted_signals = await _load_persisted_edge_signals(
        session,
        [(source, target) for source, target, _weight in visible_triples],
        runtime_cache=runtime_cache,
    )
    edges = []
    for source, target, weight in visible_triples:
        src_row = row_by_id.get(source)
        tgt_row = row_by_id.get(target)
        if src_row is not None and tgt_row is not None:
            cross_doc, shared_ents, sigs = _extract_shared_entities_and_signals(
                src_row[0],
                src_row[1],
                tgt_row[0],
                tgt_row[1],
                persisted_signals.get((source, target)),
            )
            edges.append(
                UniverseKnowledgeEdgeOut(
                    source=source,
                    target=target,
                    weight=weight,
                    cross_document=cross_doc,
                    source_document_id=src_row[1].id,
                    target_document_id=tgt_row[1].id,
                    source_document_name=src_row[1].filename,
                    target_document_name=tgt_row[1].filename,
                    shared_entities=shared_ents,
                    signals=sigs,
                )
            )
        else:
            edges.append(UniverseKnowledgeEdgeOut(source=source, target=target, weight=weight))
    page_out = UniverseKnowledgePageOut(
        project_id=snapshot.tree.project_id,
        tree_version=snapshot.tree.tree_version,
        node_id=node_id,
        scope_key=snapshot.scope_key,
        units=units,
        edges=edges,
        total=total,
        next_cursor=_encode_universe_cursor({**context, "after": units[-1].id}, settings.secret_key)
        if has_more and units else None,
    )
    if isinstance(page_cache, dict):
        page_cache[page_cache_key] = page_out
    return page_out


async def knowledge_universe_detail(
    session: AsyncSession,
    snapshot: KnowledgeUniverseSnapshot,
    unit_id: str,
) -> UniverseKnowledgeDetailOut:
    allowed_set = snapshot.allowed_unit_set
    if snapshot.tree.projection != "source" and unit_id not in allowed_set:
        raise NotFoundError("Knowledge unit is unavailable")
    ids = {unit_id}
    if snapshot._runtime_cache is not None and snapshot.tree.projection != "source":
        fast_rows = (await session.execute(_unit_query_by_ids_fast(snapshot, [unit_id]))).all()
        row = next(
            (
                r
                for r in fast_rows
                if (r[0].source_id, r[0].document_version_id, r[0].security_partition_id) in snapshot.scopes
            ),
            None,
        )
    else:
        row = (await session.execute(_unit_query(snapshot, ids))).one_or_none()
    if row is None:
        raise NotFoundError("Knowledge unit is unavailable")
    unit = row[0]
    if unit.checksum != await unit_checksum(session, unit):
        raise ServiceUnavailableError("Knowledge unit evidence checksum mismatch")
    presented = (await _present_units(session, [row], snapshot))[0]
    facts = (await session.scalars(
        select(KnowledgeEvidence).where(KnowledgeEvidence.unit_id == unit.id).order_by(KnowledgeEvidence.id)
    )).all()
    evidence = []
    for fact in facts:
        payload = fact.payload_json
        start, end, quote = payload.get("start"), payload.get("end"), payload.get("quote")
        if (
            type(start) is not int or type(end) is not int
            or not 0 <= start <= end <= len(unit.text)
            or not isinstance(quote, str) or unit.text[start:end] != quote
            or payload.get("evidence_block_ids") != [presented.block_id]
            or not math.isfinite(fact.confidence) or not 0 <= fact.confidence <= 1
        ):
            raise ServiceUnavailableError("Knowledge evidence quote is not anchored to its canonical block")
        evidence.append(UniverseKnowledgeEvidenceOut(
            id=fact.id, tier=fact.tier, kind=fact.kind, quote=quote, confidence=fact.confidence,
            candidate=payload.get("candidate") is not False, start=start, end=end,
            label=payload.get("label") if isinstance(payload.get("label"), str) else None,
            subject=payload.get("subject") if isinstance(payload.get("subject"), str) else None,
            predicate=payload.get("predicate") if isinstance(payload.get("predicate"), str) else None,
            object=payload.get("object") if isinstance(payload.get("object"), str) else None,
            extractor_version=fact.extractor_version or "",
        ))

    synapses: list[UniverseKnowledgeSynapseOut] = []
    if snapshot.edges and snapshot.tree.projection != "source":
        allowed_unit_ids = snapshot.allowed_unit_set
        neighbor_weights: dict[str, float] = {}
        if snapshot.edges_by_unit is not None:
            for _idx, src, tgt, weight in snapshot.edges_by_unit.get(unit.id, ()):
                if src == unit.id and tgt in allowed_unit_ids:
                    neighbor_weights[tgt] = max(neighbor_weights.get(tgt, 0.0), float(weight))
                elif tgt == unit.id and src in allowed_unit_ids:
                    neighbor_weights[src] = max(neighbor_weights.get(src, 0.0), float(weight))
        else:
            for src, tgt, weight in snapshot.edges:
                if src == unit.id and tgt in allowed_unit_ids:
                    neighbor_weights[tgt] = max(neighbor_weights.get(tgt, 0.0), float(weight))
                elif tgt == unit.id and src in allowed_unit_ids:
                    neighbor_weights[src] = max(neighbor_weights.get(src, 0.0), float(weight))
        if neighbor_weights:
            top_neighbor_ids = [
                nid
                for nid, _w in sorted(neighbor_weights.items(), key=lambda item: (-item[1], item[0]))[:24]
            ]
            if snapshot._runtime_cache is not None:
                fast_n_rows = (await session.execute(_unit_query_by_ids_fast(snapshot, top_neighbor_ids))).all()
                neighbor_rows = [
                    r
                    for r in fast_n_rows
                    if (r[0].source_id, r[0].document_version_id, r[0].security_partition_id) in snapshot.scopes
                ]
            else:
                neighbor_rows = (await session.execute(_unit_query(snapshot, set(top_neighbor_ids)))).all()
            if neighbor_rows:
                neighbor_presented = {
                    item.id: item
                    for item in await _present_units(session, neighbor_rows, snapshot)
                }
                persisted_signals = await _load_persisted_edge_signals(
                    session,
                    [(unit.id, r[0].id) for r in neighbor_rows],
                    runtime_cache=snapshot._runtime_cache,
                )
                leaf_node_ids = [n.id for n in snapshot.tree.nodes if n.is_leaf]
                all_node_ids = leaf_node_ids + [n.id for n in snapshot.tree.nodes if not n.is_leaf]
                for n_row in neighbor_rows:
                    n_unit, n_doc, _n_ver, _n_src = n_row
                    n_pres = neighbor_presented.get(n_unit.id)
                    if n_pres is None:
                        continue
                    cross_doc, shared_ents, sigs = _extract_shared_entities_and_signals(
                        unit,
                        row[1],
                        n_unit,
                        n_doc,
                        persisted_signals.get((unit.id, n_unit.id)),
                    )
                    target_node_id = next(
                        (nid for nid in all_node_ids if n_unit.id in snapshot.members.get(nid, set())),
                        None,
                    )
                    synapses.append(
                        UniverseKnowledgeSynapseOut(
                            target_unit_id=n_unit.id,
                            target_node_id=target_node_id,
                            target_label=n_pres.label,
                            target_preview=n_pres.preview,
                            target_heading=n_pres.heading,
                            target_document_id=n_pres.document_id,
                            target_document_name=n_pres.document_name,
                            target_source_id=n_pres.source_id,
                            target_source_name=n_pres.source_name,
                            target_page_from=n_pres.page_from,
                            target_neural_tier=n_pres.neural_tier,
                            weight=round(neighbor_weights[n_unit.id], 4),
                            cross_document=cross_doc,
                            shared_entities=shared_ents,
                            signals=sigs,
                        )
                    )
                synapses.sort(key=lambda s: (not s.cross_document, -s.weight, s.target_unit_id))
                synapses = synapses[:16]

    return UniverseKnowledgeDetailOut(
        **presented.model_dump(), project_id=snapshot.tree.project_id,
        tree_version=snapshot.tree.tree_version, scope_key=snapshot.scope_key,
        content=unit.text, evidence=evidence, synapses=synapses,
    )


async def resolve_knowledge_universe_references(
    session: AsyncSession,
    snapshot: KnowledgeUniverseSnapshot,
    references: list[UniverseKnowledgeReferenceIn],
) -> list[UniverseKnowledgeMatchOut]:
    if snapshot.tree.projection == "source":
        predicates = []
        for ref in references:
            conditions = [KnowledgeUnit.source_id == ref.source_id]
            if ref.document_version_id:
                conditions.append(KnowledgeUnit.document_version_id == ref.document_version_id)
            if ref.block_id:
                conditions.append(KnowledgeUnit.provenance_json["evidence_block_ids"][0].as_string() == ref.block_id)
            elif ref.search_unit_id:
                continue  # SearchUnit-only references require a verified published assignment.
            predicates.append(and_(*conditions))
        if not predicates:
            return []
        rows = (await session.execute(_unit_query(snapshot, None).where(or_(*predicates))
                                      .order_by(KnowledgeUnit.id).limit(500))).all()
        by_source: dict[str, list[str]] = {}
        for row in rows:
            by_source.setdefault(row[0].source_id, []).append(row[0].id)
        return [UniverseKnowledgeMatchOut(
            project_id=snapshot.tree.project_id, tree_version=snapshot.tree.tree_version,
            node_id=f"source:{source}", unit_ids=ids,
        ) for source, ids in sorted(by_source.items())]
    candidate_ids: set[str] = set()
    for assignment in snapshot.assignments:
        if any(
            ref.source_id == assignment["source_id"]
            and (ref.search_unit_id == assignment["search_unit_id"] or (not ref.search_unit_id and not ref.block_id))
            and (not ref.document_version_id or ref.document_version_id == assignment["document_version_id"])
            for ref in references
        ):
            candidate_ids.update(assignment["knowledge_unit_ids"])
    block_refs = [ref for ref in references if ref.block_id]
    block_ids = {ref.block_id for ref in block_refs}
    if block_ids:
        query = _unit_query(snapshot, snapshot.allowed_unit_set).where(
            KnowledgeUnit.provenance_json["evidence_block_ids"][0].as_string().in_(block_ids)
        )
        for unit, _doc, _version, _source in (await session.execute(query)).all():
            if any(
                ref.source_id == unit.source_id
                and ref.block_id == unit.provenance_json["evidence_block_ids"][0]
                and (not ref.document_version_id or ref.document_version_id == unit.document_version_id)
                for ref in block_refs
            ):
                candidate_ids.add(unit.id)
    rows = (
        await session.execute(
            _unit_query(snapshot, candidate_ids & snapshot.allowed_unit_set).order_by(KnowledgeUnit.id).limit(500)
        )
    ).all()
    matched_ids = {row[0].id for row in rows}
    leaves = {node.id for node in snapshot.tree.nodes if node.is_leaf}
    return [
        UniverseKnowledgeMatchOut(
            project_id=snapshot.tree.project_id, tree_version=snapshot.tree.tree_version,
            node_id=node_id, unit_ids=sorted(ids & matched_ids),
        )
        for node_id, ids in snapshot.members.items()
        if node_id in leaves and ids & matched_ids
    ]
