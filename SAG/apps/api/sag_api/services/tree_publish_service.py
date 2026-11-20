"""Verified blue-green tree publishing with an atomic PostgreSQL switch."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from collections import Counter, defaultdict
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import quote

import httpx
import orjson
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.db import SessionLocal, engine, publish_lock_engine
from sag_api.db.models.document import Document
from sag_api.db.models.routing_rag import (
    DocumentVersion,
    ProjectSearchState,
    SearchUnit,
    TreeManifest,
    TreeRoutingProfile,
    TreeSnapshotLease,
)
from sag_api.services.routing_tree_service import RoutingSnapshot as CandidateSnapshot
from sag_api.services.routing_tree_service import _walk
from sag_api.services.search_index_service import generate_search_unit_point_id

log = logging.getLogger(__name__)

_CHECKSUM_EXCLUDED_FIELDS = frozenset(
    {"tree_version", "checksum", "unit_count", "node_count", "status"}
)
_MAX_QDRANT_POINT_BATCH = 256
_LOCAL_PUBLISH_LOCKS: dict[str, asyncio.Lock] = {}


class TreePublishError(RuntimeError):
    """A fail-closed Checkpoint C publish error."""


class PublishVerificationError(TreePublishError):
    """The staged manifest or Qdrant inactive slot did not verify."""


class TreeSlotInUse(TreePublishError):
    """An earlier request still holds a lease for the inactive slot."""
    retryable = True


class TreePublishInProgress(TreePublishError):
    """Another publisher currently owns this project's publish lock."""
    retryable = True


class StaleTreePublish(TreePublishError):
    """The project pointer changed after this candidate chose its target slot."""
    retryable = True


@dataclass(frozen=True, slots=True)
class SearchUnitAssignment:
    """Map one canonical SearchUnit to the KnowledgeUnits it contains."""

    knowledge_unit_ids: tuple[str, ...]
    search_unit_id: str
    source_id: str
    document_version_id: str
    partition_id: str


@dataclass(frozen=True, slots=True)
class PreparedTreeCandidate:
    project_id: str
    tenant_id: str
    source_tree_version: str
    tree_version: str
    checksum: str
    manifest_json: str
    expected_point_count: int
    partition_counts: tuple[tuple[str, int], ...]
    search_unit_assignments: tuple[SearchUnitAssignment, ...]
    leaf_memberships: tuple[tuple[str, tuple[str, ...]], ...]
    node_count: int
    leaf_count: int
    max_leaf_size: int

    @property
    def manifest(self) -> dict[str, Any]:
        return orjson.loads(self.manifest_json)

    @property
    def source_routing_profiles(self) -> list[dict[str, Any]]:
        profiles = self.manifest.get("source_routing_profiles")
        return profiles if isinstance(profiles, list) else []

    @property
    def expected_partition_counts(self) -> dict[str, int]:
        return dict(self.partition_counts)


@dataclass(frozen=True, slots=True)
class TreePublishResult:
    project_id: str
    tree_version: str
    routing_slot: Literal["SLOT_A", "SLOT_B"]
    search_epoch: int
    checksum: str
    point_count: int


def _checksum_manifest(manifest: dict[str, Any]) -> str:
    canonical = {
        key: value for key, value in manifest.items() if key not in _CHECKSUM_EXCLUDED_FIELDS
    }
    try:
        encoded = json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise PublishVerificationError("Tree manifest is not canonical JSON") from error
    return hashlib.sha256(encoded).hexdigest()


def _scoped_profile_checksum(tree_version: str, profile: dict[str, Any]) -> str:
    try:
        encoded = json.dumps(
            {"tree_version": tree_version, **profile},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise PublishVerificationError("Source-scoped routing profile is not canonical JSON") from error
    return hashlib.sha256(encoded).hexdigest()


def _routing_profile_payload(
    snapshot: CandidateSnapshot,
) -> tuple[list[dict[str, Any]], tuple[tuple[str, tuple[str, ...]], ...]]:
    profiles: list[dict[str, Any]] = []
    leaves: list[tuple[str, tuple[str, ...]]] = []
    for node in _walk(snapshot.roots):
        profile = node.profile
        unit_ids = tuple(sorted(set(node.unit_ids)))
        if len(unit_ids) != len(node.unit_ids):
            raise PublishVerificationError("Tree node contains duplicate SearchUnit membership")
        profiles.append(
            {
                "node_id": node.node_id,
                "parent_id": node.parent_id,
                "is_leaf": not node.children,
                "tenant_id": node.tenant_id,
                "project_id": node.project_id,
                "partition_id": node.security_partition_id,
                "unit_ids": list(unit_ids),
                "accessible_unit_count": profile.accessible_unit_count,
                "dense_medoid": list(profile.dense_medoid) if profile.dense_medoid is not None else None,
                "sparse": [[term, weight] for term, weight in profile.sparse],
                "entities": list(profile.entities),
                "temporal_from": profile.temporal_from.isoformat() if profile.temporal_from else None,
                "temporal_to": profile.temporal_to.isoformat() if profile.temporal_to else None,
            }
        )
        if not node.children:
            leaves.append((node.node_id, unit_ids))
    profiles.sort(key=lambda item: item["node_id"])
    leaves.sort(key=lambda item: item[0])
    return profiles, tuple(leaves)


def _source_routing_profile_payloads(
    *,
    project_id: str,
    tenant_id: str,
    profiles: list[dict[str, Any]],
    units: list[dict[str, Any]],
    source_version_partition_by_knowledge: dict[str, tuple[str, str, str]],
    sparse_limit: int,
) -> list[dict[str, Any]]:
    """Precompute compact per-Source/partition features for bounded query reads."""
    unit_by_id = {unit["unit_id"]: unit for unit in units}
    scoped_profiles: list[dict[str, Any]] = []
    for profile in profiles:
        units_by_scope: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for unit_id in profile["unit_ids"]:
            scope = source_version_partition_by_knowledge.get(unit_id)
            unit = unit_by_id.get(unit_id)
            if scope is None or unit is None:
                raise PublishVerificationError("Routing profile is missing its canonical Source scope")
            units_by_scope[scope].append(unit)
        for (source_id, document_version_id, partition_id), scoped_units in sorted(units_by_scope.items()):
            sparse: dict[str, list[float]] = defaultdict(list)
            entities: set[str] = set()
            for unit in scoped_units:
                raw_sparse = unit.get("sparse")
                raw_entities = unit.get("entities")
                if not isinstance(raw_sparse, list) or not isinstance(raw_entities, list):
                    raise PublishVerificationError("KnowledgeUnit query features are malformed")
                for item in raw_sparse:
                    if (
                        not isinstance(item, list)
                        or len(item) != 2
                        or not isinstance(item[0], str)
                        or isinstance(item[1], bool)
                        or not isinstance(item[1], (int, float))
                        or not math.isfinite(float(item[1]))
                        or float(item[1]) < 0.0
                    ):
                        raise PublishVerificationError("KnowledgeUnit sparse profile is invalid")
                    sparse[item[0]].append(float(item[1]))
                if any(not isinstance(entity, str) for entity in raw_entities):
                    raise PublishVerificationError("KnowledgeUnit entity profile is invalid")
                entities.update(raw_entities)
            weighted = sorted(
                ((term, math.fsum(weights)) for term, weights in sparse.items()),
                key=lambda item: (-item[1], item[0]),
            )[:sparse_limit]
            scoped_profiles.append(
                {
                    "project_id": project_id,
                    "tenant_id": tenant_id,
                    "source_id": source_id,
                    "document_version_id": document_version_id,
                    "partition_id": partition_id,
                    "node_id": profile["node_id"],
                    "parent_id": profile["parent_id"],
                    "is_leaf": profile["is_leaf"],
                    "accessible_unit_count": len(scoped_units),
                    "sparse": [[term, weight] for term, weight in weighted],
                    "entities": sorted(entities),
                }
            )
    scoped_profiles.sort(
        key=lambda item: (
            item["source_id"],
            item["document_version_id"],
            item["partition_id"],
            item["node_id"],
        )
    )
    if not scoped_profiles:
        raise PublishVerificationError("Tree candidate has no Source-scoped routing profiles")
    return scoped_profiles


def prepare_tree_candidate(
    snapshot: CandidateSnapshot,
    search_unit_assignments: Iterable[SearchUnitAssignment],
) -> PreparedTreeCandidate:
    """Freeze the tree and its explicit KnowledgeUnit-to-SearchUnit bridge."""
    if not snapshot.publishable or not snapshot.quality_gates or not all(
        value is True for value in snapshot.quality_gates.values()
    ):
        raise PublishVerificationError("Tree candidate did not pass every quality gate")

    source_manifest = snapshot.manifest
    if (
        source_manifest.get("tree_version") != snapshot.tree_version
        or source_manifest.get("checksum") != _checksum_manifest(source_manifest)
    ):
        raise PublishVerificationError("Tree candidate checksum does not match its manifest")

    raw_units = source_manifest.get("units")
    raw_unit_ids = source_manifest.get("unit_ids")
    if not isinstance(raw_units, list) or not isinstance(raw_unit_ids, list) or not raw_unit_ids:
        raise PublishVerificationError("Tree candidate has no canonical unit inventory")
    unit_ids = [str(value) for value in raw_unit_ids]
    if len(unit_ids) != len(set(unit_ids)) or len(unit_ids) != len(raw_units):
        raise PublishVerificationError("Tree candidate unit inventory is duplicated or incomplete")
    if source_manifest.get("unit_count") != len(unit_ids):
        raise PublishVerificationError("Tree candidate unit count does not match its inventory")

    partition_by_unit: dict[str, str] = {}
    knowledge_partition_counts: Counter[str] = Counter()
    for unit in raw_units:
        if not isinstance(unit, dict):
            raise PublishVerificationError("Tree candidate unit inventory is malformed")
        unit_id = unit.get("unit_id")
        partition_id = unit.get("partition")
        if (
            not isinstance(unit_id, str)
            or not unit_id.strip()
            or not isinstance(partition_id, str)
            or not partition_id.strip()
        ):
            raise PublishVerificationError("Tree candidate unit has an invalid ACL partition")
        if unit_id in partition_by_unit:
            raise PublishVerificationError("Tree candidate unit inventory contains duplicates")
        partition_by_unit[unit_id] = partition_id
        knowledge_partition_counts[partition_id] += 1

    raw_assignments = tuple(search_unit_assignments)
    if any(not isinstance(item, SearchUnitAssignment) for item in raw_assignments):
        raise PublishVerificationError("SearchUnit assignment contract is invalid")
    if any(
        not isinstance(item.knowledge_unit_ids, tuple)
        or not item.knowledge_unit_ids
        or any(not isinstance(value, str) for value in item.knowledge_unit_ids)
        or len(set(item.knowledge_unit_ids)) != len(item.knowledge_unit_ids)
        or not all(
            isinstance(value, str)
            for value in (item.search_unit_id, item.source_id, item.document_version_id, item.partition_id)
        )
        for item in raw_assignments
    ):
        raise PublishVerificationError("SearchUnit assignment contains a non-string identity")
    assignments = tuple(sorted(raw_assignments, key=lambda item: item.search_unit_id))
    if not assignments or len({item.search_unit_id for item in assignments}) != len(assignments):
        raise PublishVerificationError("SearchUnit assignments are empty or duplicated")
    mapped_knowledge_ids: set[str] = set()
    scope_by_knowledge: dict[str, tuple[str, str, str]] = {}
    partition_counts: Counter[str] = Counter()
    for item in assignments:
        if (
            any(
                not value.strip()
                for value in (
                    item.search_unit_id,
                    item.source_id,
                    item.document_version_id,
                    item.partition_id,
                )
            )
        ):
            raise PublishVerificationError("SearchUnit assignment crosses the tree or ACL partition")
        identity = (item.source_id, item.document_version_id, item.partition_id)
        for knowledge_unit_id in item.knowledge_unit_ids:
            if not knowledge_unit_id.strip() or partition_by_unit.get(knowledge_unit_id) != item.partition_id:
                raise PublishVerificationError("SearchUnit assignment crosses the tree or ACL partition")
            previous_identity = scope_by_knowledge.setdefault(knowledge_unit_id, identity)
            if previous_identity != identity:
                raise PublishVerificationError("KnowledgeUnit feature spans multiple Source/version scopes")
            mapped_knowledge_ids.add(knowledge_unit_id)
        partition_counts[item.partition_id] += 1
    if mapped_knowledge_ids != set(unit_ids):
        raise PublishVerificationError("Every KnowledgeUnit must map to at least one SearchUnit")

    profiles, leaves = _routing_profile_payload(snapshot)
    config = source_manifest.get("config")
    sparse_limit = config.get("profile_sparse_terms") if isinstance(config, dict) else None
    if isinstance(sparse_limit, bool) or not isinstance(sparse_limit, int) or sparse_limit < 0:
        raise PublishVerificationError("Tree candidate profile sparse limit is invalid")
    source_version_partition_by_knowledge = dict(scope_by_knowledge)
    source_profiles = _source_routing_profile_payloads(
        project_id=snapshot.project_id,
        tenant_id=snapshot.tenant_id,
        profiles=profiles,
        units=raw_units,
        source_version_partition_by_knowledge=source_version_partition_by_knowledge,
        sparse_limit=sparse_limit,
    )
    node_ids = {profile["node_id"] for profile in profiles}
    if not profiles or len(node_ids) != len(profiles):
        raise PublishVerificationError("Tree candidate node inventory is empty or duplicated")
    for profile in profiles:
        if (
            profile["tenant_id"] != snapshot.tenant_id
            or profile["project_id"] != snapshot.project_id
            or not profile["partition_id"]
            or profile["accessible_unit_count"] != len(profile["unit_ids"])
            or (profile["parent_id"] is not None and profile["parent_id"] not in node_ids)
            or any(partition_by_unit.get(unit_id) != profile["partition_id"] for unit_id in profile["unit_ids"])
        ):
            raise PublishVerificationError("Tree candidate profile crosses its project or ACL partition")

    leaf_unit_ids = [unit_id for _node_id, members in leaves for unit_id in members]
    if len(leaf_unit_ids) != len(set(leaf_unit_ids)) or set(leaf_unit_ids) != set(unit_ids):
        raise PublishVerificationError("Tree leaves do not cover each SearchUnit exactly once")

    manifest = dict(source_manifest)
    manifest["source_tree_version"] = snapshot.tree_version
    manifest["publish_protocol"] = "routing-blue-green.v1"
    manifest["routing_profiles"] = profiles
    manifest["source_routing_profiles"] = source_profiles
    manifest["security_partition_unit_counts"] = dict(sorted(knowledge_partition_counts.items()))
    manifest["security_partition_search_unit_counts"] = dict(sorted(partition_counts.items()))
    manifest["search_unit_assignments"] = [asdict(item) for item in assignments]
    manifest["expected_point_count"] = len(assignments)
    checksum = _checksum_manifest(manifest)
    tree_version = f"tree-{checksum[:24]}"
    manifest["tree_version"] = tree_version
    manifest["checksum"] = checksum
    manifest["status"] = "QUALITY_PASSED"
    manifest_json = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    leaves_sizes = [len(members) for _node_id, members in leaves]
    return PreparedTreeCandidate(
        project_id=snapshot.project_id,
        tenant_id=snapshot.tenant_id,
        source_tree_version=snapshot.tree_version,
        tree_version=tree_version,
        checksum=checksum,
        manifest_json=manifest_json,
        expected_point_count=len(assignments),
        partition_counts=tuple(sorted(partition_counts.items())),
        search_unit_assignments=assignments,
        leaf_memberships=leaves,
        node_count=len(profiles),
        leaf_count=len(leaves),
        max_leaf_size=max(leaves_sizes, default=0),
    )


def build_inactive_slot_payloads(
    candidate: PreparedTreeCandidate,
    target_slot: Literal["SLOT_A", "SLOT_B"],
) -> list[dict[str, Any]]:
    if target_slot not in {"SLOT_A", "SLOT_B"}:
        raise ValueError("Invalid routing slot")
    slot = "a" if target_slot == "SLOT_A" else "b"
    collection_name = f"search_units_{candidate.project_id}"
    leaf_by_knowledge = {
        knowledge_unit_id: node_id
        for node_id, members in candidate.leaf_memberships
        for knowledge_unit_id in members
    }
    search_units_by_route: dict[tuple[str, tuple[str, ...]], list[str]] = defaultdict(list)
    for item in candidate.search_unit_assignments:
        node_ids = tuple(
            sorted(
                {
                    leaf_by_knowledge[unit_id]
                    for unit_id in item.knowledge_unit_ids
                    if unit_id in leaf_by_knowledge
                }
            )
        )
        if not node_ids:
            raise PublishVerificationError("SearchUnit assignment has no leaf membership")
        search_units_by_route[(node_ids[0], node_ids[1:])].append(item.search_unit_id)
    batches: list[dict[str, Any]] = []
    for (node_id, secondary_node_ids), unit_ids in sorted(search_units_by_route.items()):
        point_ids = [generate_search_unit_point_id(collection_name, unit_id) for unit_id in sorted(unit_ids)]
        for start in range(0, len(point_ids), _MAX_QDRANT_POINT_BATCH):
            points = point_ids[start : start + _MAX_QDRANT_POINT_BATCH]
            if points:
                batches.append(
                    {
                        "points": points,
                        "payload": {
                            f"primary_node_{slot}": node_id,
                            f"secondary_node_ids_{slot}": list(secondary_node_ids),
                            f"tree_version_{slot}": candidate.tree_version,
                        },
                    }
                )
    return batches


def _validate_qdrant_completed(data: Any) -> None:
    result = data.get("result") if isinstance(data, dict) else None
    if (
        not isinstance(data, dict)
        or data.get("status") != "ok"
        or not isinstance(result, dict)
        or result.get("status") != "completed"
    ):
        raise PublishVerificationError("Qdrant did not confirm completed payload writes")


def _validate_qdrant_batch_completed(data: Any, expected_count: int) -> None:
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(data, dict) or data.get("status") != "ok":
        raise PublishVerificationError("Qdrant did not confirm completed payload writes")
    if isinstance(result, list):
        if len(result) != expected_count or any(
            not isinstance(item, dict) or item.get("status") != "completed" for item in result
        ):
            raise PublishVerificationError("Qdrant did not confirm completed payload writes")
        return
    if isinstance(result, dict) and result.get("status") == "completed":
        return
    raise PublishVerificationError("Qdrant did not confirm completed payload writes")


async def update_inactive_slot_qdrant_payloads(
    qdrant_client: httpx.AsyncClient,
    collection_name: str,
    payload_batches: list[dict[str, Any]],
) -> None:
    if not payload_batches:
        raise PublishVerificationError("Tree candidate produced no Qdrant payload writes")
    quoted_collection = quote(collection_name, safe="")
    single_path = f"/collections/{quoted_collection}/points/payload?wait=true"
    if len(payload_batches) > 1:
        batch_path = f"/collections/{quoted_collection}/points/batch?wait=true"
        fallback_to_single = False
        for start in range(0, len(payload_batches), _MAX_QDRANT_POINT_BATCH):
            chunk = payload_batches[start : start + _MAX_QDRANT_POINT_BATCH]
            try:
                response = await qdrant_client.post(
                    batch_path,
                    json={"operations": [{"set_payload": item} for item in chunk]},
                )
                if response.status_code in (404, 405):
                    fallback_to_single = True
                    break
                if response.status_code != 200:
                    raise PublishVerificationError("Qdrant payload write returned an error status")
                _validate_qdrant_batch_completed(response.json(), len(chunk))
            except PublishVerificationError:
                raise
            except (httpx.HTTPError, ValueError, TypeError) as error:
                raise PublishVerificationError("Qdrant payload write could not be acknowledged") from error
        if not fallback_to_single:
            return

    for batch in payload_batches:
        try:
            response = await qdrant_client.post(single_path, json=batch)
            if response.status_code != 200:
                raise PublishVerificationError("Qdrant payload write returned an error status")
            _validate_qdrant_completed(response.json())
        except PublishVerificationError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise PublishVerificationError("Qdrant payload write could not be acknowledged") from error


async def _clear_inactive_slot_qdrant_payloads(
    qdrant_client: httpx.AsyncClient,
    candidate: PreparedTreeCandidate,
    target_slot: Literal["SLOT_A", "SLOT_B"],
) -> None:
    """Remove prior tree ownership from the leased-free target slot before reuse."""
    slot = "a" if target_slot == "SLOT_A" else "b"
    collection_name = f"search_units_{candidate.project_id}"
    path = f"/collections/{quote(collection_name, safe='')}/points/payload/delete?wait=true"
    try:
        response = await qdrant_client.post(
            path,
            json={
                "keys": [
                    f"primary_node_{slot}",
                    f"secondary_node_ids_{slot}",
                    f"tree_version_{slot}",
                ],
                "filter": {
                    "must": [
                        {"key": "project_id", "match": {"value": candidate.project_id}},
                        {"key": "tenant_id", "match": {"value": candidate.tenant_id}},
                    ]
                },
            },
        )
        if response.status_code != 200:
            raise PublishVerificationError("Qdrant inactive-slot cleanup returned an error status")
        _validate_qdrant_completed(response.json())
    except PublishVerificationError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as error:
        raise PublishVerificationError("Qdrant inactive-slot cleanup was not acknowledged") from error


async def _count_qdrant_points(
    qdrant_client: httpx.AsyncClient,
    collection_name: str,
    filters: list[dict[str, Any]],
) -> int:
    path = f"/collections/{quote(collection_name, safe='')}/points/count"
    try:
        response = await qdrant_client.post(path, json={"filter": {"must": filters}, "exact": True})
        if response.status_code != 200:
            raise PublishVerificationError("Qdrant point count verification returned an error status")
        data = response.json()
    except PublishVerificationError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as error:
        raise PublishVerificationError("Qdrant point count verification failed") from error
    result = data.get("result") if isinstance(data, dict) else None
    count = result.get("count") if isinstance(result, dict) else None
    if (
        not isinstance(data, dict)
        or data.get("status") != "ok"
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
    ):
        raise PublishVerificationError("Qdrant returned an invalid exact point count")
    return count


async def _verify_database_search_units(candidate: PreparedTreeCandidate) -> None:
    """Match the candidate bridge to canonical PostgreSQL Source/version/ACL rows."""
    assignments = {item.search_unit_id: item for item in candidate.search_unit_assignments}
    ids = sorted(assignments)
    async with SessionLocal() as session:
        for start in range(0, len(ids), _MAX_QDRANT_POINT_BATCH):
            batch = ids[start : start + _MAX_QDRANT_POINT_BATCH]
            rows = (
                await session.execute(
                    select(
                        SearchUnit.id,
                        SearchUnit.document_version_id,
                        SearchUnit.security_partition_id,
                        Document.source_id,
                        Document.project_id,
                        Document.tenant_id,
                    )
                    .join(DocumentVersion, DocumentVersion.id == SearchUnit.document_version_id)
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(SearchUnit.id.in_(batch))
                )
            ).all()
            if len(rows) != len(batch):
                raise PublishVerificationError("PostgreSQL SearchUnit inventory is incomplete")
            for unit_id, version_id, partition_id, source_id, project_id, tenant_id in rows:
                item = assignments[unit_id]
                if (
                    version_id != item.document_version_id
                    or partition_id != item.partition_id
                    or source_id != item.source_id
                    or project_id != candidate.project_id
                    or tenant_id != candidate.tenant_id
                ):
                    raise PublishVerificationError("PostgreSQL SearchUnit ACL mapping differs from the candidate")


async def _verify_qdrant_point_payloads(
    candidate: PreparedTreeCandidate,
    target_slot: Literal["SLOT_A", "SLOT_B"],
    qdrant_client: httpx.AsyncClient,
) -> None:
    slot = "a" if target_slot == "SLOT_A" else "b"
    collection = f"search_units_{candidate.project_id}"
    leaf_by_knowledge = {
        unit_id: node_id
        for node_id, members in candidate.leaf_memberships
        for unit_id in members
    }
    expected = {}
    for item in candidate.search_unit_assignments:
        node_ids = tuple(sorted({leaf_by_knowledge[unit_id] for unit_id in item.knowledge_unit_ids}))
        expected[generate_search_unit_point_id(collection, item.search_unit_id)] = (
            item,
            node_ids[0],
            list(node_ids[1:]),
        )
    ids = sorted(expected)
    path = f"/collections/{quote(collection, safe='')}/points"
    for start in range(0, len(ids), _MAX_QDRANT_POINT_BATCH):
        batch = ids[start : start + _MAX_QDRANT_POINT_BATCH]
        try:
            response = await qdrant_client.post(
                path,
                json={
                    "ids": batch,
                    "with_payload": [
                        "search_unit_id",
                        "source_id",
                        "document_version_id",
                        "project_id",
                        "tenant_id",
                        "security_partition_id",
                        f"primary_node_{slot}",
                        f"secondary_node_ids_{slot}",
                        f"tree_version_{slot}",
                    ],
                    "with_vector": False,
                },
            )
            if response.status_code != 200:
                raise PublishVerificationError("Qdrant exact point lookup returned an error status")
            data = response.json()
        except PublishVerificationError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise PublishVerificationError("Qdrant exact point lookup failed") from error
        points = data.get("result") if isinstance(data, dict) else None
        if (
            not isinstance(data, dict)
            or data.get("status") != "ok"
            or not isinstance(points, list)
            or len(points) != len(batch)
        ):
            raise PublishVerificationError("Qdrant exact point inventory is incomplete")
        observed: set[str] = set()
        for point in points:
            if not isinstance(point, dict) or not isinstance(point.get("payload"), dict):
                raise PublishVerificationError("Qdrant exact point payload is malformed")
            point_id = str(point.get("id"))
            if point_id not in batch or point_id in observed:
                raise PublishVerificationError("Qdrant exact point IDs differ from the candidate")
            observed.add(point_id)
            item, leaf_node_id, secondary_node_ids = expected[point_id]
            payload = point["payload"]
            required = {
                "search_unit_id": item.search_unit_id,
                "source_id": item.source_id,
                "document_version_id": item.document_version_id,
                "project_id": candidate.project_id,
                "tenant_id": candidate.tenant_id,
                "security_partition_id": item.partition_id,
                f"primary_node_{slot}": leaf_node_id,
                f"secondary_node_ids_{slot}": secondary_node_ids,
                f"tree_version_{slot}": candidate.tree_version,
            }
            if any(payload.get(key) != value for key, value in required.items()):
                raise PublishVerificationError("Qdrant point ACL or tree payload differs from the candidate")


async def _verify_qdrant_slot(
    candidate: PreparedTreeCandidate,
    target_slot: Literal["SLOT_A", "SLOT_B"],
    qdrant_client: httpx.AsyncClient,
) -> None:
    slot = "a" if target_slot == "SLOT_A" else "b"
    common = [
        {"key": f"tree_version_{slot}", "match": {"value": candidate.tree_version}},
        {"key": "project_id", "match": {"value": candidate.project_id}},
        {"key": "tenant_id", "match": {"value": candidate.tenant_id}},
    ]
    collection = f"search_units_{candidate.project_id}"
    count_tasks = [
        _count_qdrant_points(qdrant_client, collection, common),
        *(
            _count_qdrant_points(
                qdrant_client,
                collection,
                [*common, {"key": "security_partition_id", "match": {"value": partition_id}}],
            )
            for partition_id, _expected in candidate.partition_counts
        ),
    ]
    counts_and_payloads = await asyncio.gather(
        *count_tasks,
        _verify_qdrant_point_payloads(candidate, target_slot, qdrant_client),
    )
    counts = counts_and_payloads[:-1]
    if counts[0] != candidate.expected_point_count:
        raise PublishVerificationError("Qdrant total point count does not match the candidate manifest")

    # Per-partition counts are the pre-publish ACL smoke check. The payload must
    # retain the tree version and exact security partition for every expected unit.
    for (_partition_id, expected), actual in zip(candidate.partition_counts, counts[1:], strict=True):
        if actual != expected:
            raise PublishVerificationError("Qdrant ACL partition count does not match the candidate manifest")


def _verify_persisted_manifest(
    record: TreeManifest | None,
    candidate: PreparedTreeCandidate,
    *,
    expected_status: str = "INACTIVE",
    candidate_manifest: dict[str, Any] | None = None,
) -> None:
    if (
        record is None
        or record.project_id != candidate.project_id
        or record.tree_version != candidate.tree_version
        or record.status != expected_status
        or record.checksum != candidate.checksum
    ):
        raise PublishVerificationError("PostgreSQL staged manifest identity or status is inconsistent")
    raw = record.manifest_json
    if isinstance(raw, str):
        try:
            manifest = orjson.loads(raw)
        except ValueError as error:
            raise PublishVerificationError("PostgreSQL staged manifest JSON is malformed") from error
    elif isinstance(raw, dict):
        manifest = raw
    else:
        raise PublishVerificationError("PostgreSQL staged manifest JSON is missing")
    expected_manifest = candidate_manifest if candidate_manifest is not None else candidate.manifest
    if (
        manifest != expected_manifest
        or manifest.get("checksum") != record.checksum
        or manifest.get("tree_version") != record.tree_version
        or _checksum_manifest(manifest) != record.checksum
        or manifest.get("expected_point_count") != candidate.expected_point_count
        or not isinstance(manifest.get("quality_gates"), dict)
        or not manifest["quality_gates"]
        or any(value is not True for value in manifest["quality_gates"].values())
    ):
        raise PublishVerificationError("PostgreSQL staged manifest failed checksum or quality verification")


async def _database_now(session: AsyncSession) -> datetime:
    value = await session.scalar(select(func.now()))
    if not isinstance(value, datetime):
        raise TreePublishError("Could not read database time for snapshot lease handling")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _tree_manifest_row(
    candidate: PreparedTreeCandidate,
    *,
    manifest: dict[str, Any] | None = None,
) -> TreeManifest:
    manifest = manifest if manifest is not None else candidate.manifest
    metrics = manifest.get("quality_metrics") or {}
    return TreeManifest(
        tree_version=candidate.tree_version,
        project_id=candidate.project_id,
        config_version=str(manifest.get("config_version") or "routing-snapshot.v1"),
        node_count=candidate.node_count,
        leaf_count=candidate.leaf_count,
        max_leaf_size=candidate.max_leaf_size,
        giant_ratio=float(metrics.get("giant_ratio") or 0.0),
        routing_recall_at_k=float(metrics.get("routing_recall_at_k") or 0.0),
        escape_win_rate=float(metrics.get("escape_win_rate") or 0.0),
        acl_blackhole_rate=float(metrics.get("acl_blackhole_rate") or 0.0),
        status="INACTIVE",
        checksum=candidate.checksum,
        manifest_json=manifest,
    )


def _tree_routing_profile_rows(
    candidate: PreparedTreeCandidate,
    *,
    source_profiles: list[dict[str, Any]] | None = None,
) -> list[TreeRoutingProfile]:
    profiles = source_profiles if source_profiles is not None else candidate.source_routing_profiles
    rows = []
    for profile in profiles:
        rows.append(
            TreeRoutingProfile(
                project_id=profile["project_id"],
                tree_version=candidate.tree_version,
                source_id=profile["source_id"],
                document_version_id=profile["document_version_id"],
                partition_id=profile["partition_id"],
                node_id=profile["node_id"],
                tenant_id=profile["tenant_id"],
                parent_id=profile["parent_id"],
                is_leaf=profile["is_leaf"],
                accessible_unit_count=profile["accessible_unit_count"],
                sparse_json=profile["sparse"],
                entities_json=profile["entities"],
                profile_checksum=_scoped_profile_checksum(candidate.tree_version, profile),
            )
        )
    return rows


async def _verify_stored_source_profiles(
    session: AsyncSession,
    candidate: PreparedTreeCandidate,
    *,
    expected_profiles: list[dict[str, Any]] | None = None,
) -> None:
    records = (
        await session.scalars(
            select(TreeRoutingProfile)
            .where(
                TreeRoutingProfile.project_id == candidate.project_id,
                TreeRoutingProfile.tree_version == candidate.tree_version,
            )
            .order_by(
                TreeRoutingProfile.source_id,
                TreeRoutingProfile.document_version_id,
                TreeRoutingProfile.partition_id,
                TreeRoutingProfile.node_id,
            )
        )
    ).all()
    observed: list[dict[str, Any]] = []
    for record in records:
        profile = {
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
        if record.profile_checksum != _scoped_profile_checksum(candidate.tree_version, profile):
            raise PublishVerificationError("Persisted Source-scoped routing profile checksum is inconsistent")
        observed.append(profile)
    target_profiles = expected_profiles if expected_profiles is not None else candidate.source_routing_profiles
    if observed != target_profiles:
        raise PublishVerificationError("Persisted Source-scoped routing profiles differ from the candidate")


def _active_slot(state: ProjectSearchState) -> Literal["SLOT_A", "SLOT_B"]:
    if state.active_routing_slot == "SLOT_A":
        if state.slot_a_tree_version != state.active_tree_version:
            raise TreePublishError("Active PostgreSQL slot and tree version disagree")
        return "SLOT_A"
    if state.active_routing_slot == "SLOT_B":
        if state.slot_b_tree_version != state.active_tree_version:
            raise TreePublishError("Active PostgreSQL slot and tree version disagree")
        return "SLOT_B"
    raise TreePublishError("PostgreSQL active routing slot is invalid")


def _inactive_slot(state: ProjectSearchState) -> Literal["SLOT_A", "SLOT_B"]:
    if state.active_tree_version is None:
        return "SLOT_A"
    return "SLOT_B" if _active_slot(state) == "SLOT_A" else "SLOT_A"


def _advisory_lock_key(project_id: str) -> int:
    return int.from_bytes(hashlib.sha256(project_id.encode("utf-8")).digest()[:8], "big", signed=True)


@asynccontextmanager
async def _project_publish_lock(project_id: str) -> AsyncIterator[None]:
    if engine.dialect.name != "postgresql":
        lock = _LOCAL_PUBLISH_LOCKS.setdefault(project_id, asyncio.Lock())
        async with lock:
            yield
        return

    key = _advisory_lock_key(project_id)
    # The session lock must span Qdrant I/O to serialize slot writers. A
    # dedicated bounded pool keeps these long-lived connections away from API
    # request sessions; losing the PostgreSQL session releases the lock.
    connection = publish_lock_engine.connect()
    try:
        await connection.start()
    except SQLAlchemyTimeoutError as error:
        await connection.close()
        raise TreePublishInProgress("The bounded PostgreSQL publish-lock pool is busy") from error

    try:
        async with connection.begin():
            acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        if not acquired:
            raise TreePublishInProgress("Another tree publisher owns this project")
        try:
            yield
        finally:
            try:
                async with connection.begin():
                    await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            except SQLAlchemyError:
                log.exception("Could not release PostgreSQL tree publish lock")
                await connection.invalidate()
    finally:
        await connection.close()


async def _stage_candidate(
    candidate: PreparedTreeCandidate,
    *,
    candidate_manifest: dict[str, Any] | None = None,
) -> tuple[Literal["SLOT_A", "SLOT_B"], str | None, str, int] | TreePublishResult:
    parsed_manifest = candidate_manifest if candidate_manifest is not None else candidate.manifest
    profiles = parsed_manifest.get("source_routing_profiles")
    source_profiles = profiles if isinstance(profiles, list) else []
    async with SessionLocal() as session:
        async with session.begin():
            state = await session.scalar(
                select(ProjectSearchState)
                .where(ProjectSearchState.project_id == candidate.project_id)
                .with_for_update()
            )
            if state is None:
                state = ProjectSearchState(
                    project_id=candidate.project_id,
                    active_routing_slot="SLOT_A",
                    active_search_epoch=1,
                    last_switched_at=datetime.now(UTC),
                )
                session.add(state)
                await session.flush()

            existing = await session.get(TreeManifest, candidate.tree_version)
            if state.active_tree_version == candidate.tree_version:
                _verify_persisted_manifest(
                    existing,
                    candidate,
                    expected_status="ACTIVE",
                    candidate_manifest=parsed_manifest,
                )
                active_slot = _active_slot(state)
                await _verify_stored_source_profiles(
                    session,
                    candidate,
                    expected_profiles=source_profiles,
                )
                return TreePublishResult(
                    project_id=candidate.project_id,
                    tree_version=candidate.tree_version,
                    routing_slot=active_slot,
                    search_epoch=state.active_search_epoch,
                    checksum=candidate.checksum,
                    point_count=candidate.expected_point_count,
                )

            target_slot = _inactive_slot(state)
            database_now = await _database_now(session)
            await session.execute(
                delete(TreeSnapshotLease).where(
                    TreeSnapshotLease.project_id == candidate.project_id,
                    TreeSnapshotLease.expires_at <= database_now,
                )
            )
            active_lease = await session.scalar(
                select(TreeSnapshotLease.request_snapshot_id)
                .where(
                    TreeSnapshotLease.project_id == candidate.project_id,
                    TreeSnapshotLease.routing_slot == target_slot,
                    TreeSnapshotLease.expires_at > database_now,
                )
                .limit(1)
            )
            if active_lease is not None:
                raise TreeSlotInUse("A request still holds a lease for the inactive routing slot")

            inactive_slot_version = (
                state.slot_a_tree_version if target_slot == "SLOT_A" else state.slot_b_tree_version
            )
            if state.previous_tree_version and inactive_slot_version != state.previous_tree_version:
                raise TreePublishError("Previous tree pointer and retained inactive slot disagree")
            # The opposite slot is about to be mutated. Stop advertising its old
            # contents before any Qdrant I/O can partially replace that payload.
            state.previous_tree_version = None
            if target_slot == "SLOT_A":
                state.slot_a_tree_version = None
            else:
                state.slot_b_tree_version = None

            if existing is not None:
                if existing.project_id != candidate.project_id or existing.status == "ACTIVE":
                    raise TreePublishError("Candidate tree version already belongs to another active manifest")
                staged = _tree_manifest_row(candidate, manifest=parsed_manifest)
                for field in (
                    "config_version",
                    "node_count",
                    "leaf_count",
                    "max_leaf_size",
                    "giant_ratio",
                    "routing_recall_at_k",
                    "escape_win_rate",
                    "acl_blackhole_rate",
                    "status",
                    "checksum",
                    "manifest_json",
                ):
                    setattr(existing, field, getattr(staged, field))
            else:
                session.add(_tree_manifest_row(candidate, manifest=parsed_manifest))
            await session.execute(
                delete(TreeRoutingProfile).where(
                    TreeRoutingProfile.project_id == candidate.project_id,
                    TreeRoutingProfile.tree_version == candidate.tree_version,
                )
            )
            session.add_all(_tree_routing_profile_rows(candidate, source_profiles=source_profiles))
            return (
                target_slot,
                state.active_tree_version,
                state.active_routing_slot,
                state.active_search_epoch,
            )


async def _verify_staged_candidate(
    candidate: PreparedTreeCandidate,
    *,
    candidate_manifest: dict[str, Any] | None = None,
) -> None:
    parsed_manifest = candidate_manifest if candidate_manifest is not None else candidate.manifest
    profiles = parsed_manifest.get("source_routing_profiles")
    source_profiles = profiles if isinstance(profiles, list) else []
    async with SessionLocal() as session:
        record = await session.get(TreeManifest, candidate.tree_version)
        _verify_persisted_manifest(record, candidate, candidate_manifest=parsed_manifest)
        await _verify_stored_source_profiles(session, candidate, expected_profiles=source_profiles)


async def _mark_rejected(candidate: PreparedTreeCandidate) -> None:
    try:
        async with SessionLocal() as session:
            async with session.begin():
                record = await session.scalar(
                    select(TreeManifest)
                    .where(TreeManifest.tree_version == candidate.tree_version)
                    .with_for_update()
                )
                if record is not None and record.project_id == candidate.project_id and record.status != "ACTIVE":
                    record.status = "REJECTED"
    except SQLAlchemyError:
        log.exception("Could not mark rejected tree manifest")


async def _verify_knowledge_inputs(session, candidate, *, candidate_manifest: dict[str, Any] | None = None):
    manifest = candidate_manifest if candidate_manifest is not None else orjson.loads(candidate.manifest_json)
    expected = manifest.get("knowledge_inputs")
    if expected is None:
        return  # Existing published snapshots retain their original verification contract.
    from sag_api.db.models import KnowledgeUnit
    from sag_api.services.knowledge_service import knowledge_lock

    await knowledge_lock(session, "project-input:" + candidate.tenant_id + ":" + candidate.project_id)
    now = datetime.now(UTC)
    rows = (await session.execute(select(KnowledgeUnit.id, KnowledgeUnit.checksum)
        .join(DocumentVersion, DocumentVersion.id == KnowledgeUnit.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(KnowledgeUnit.tenant_id == candidate.tenant_id,
               KnowledgeUnit.project_id == candidate.project_id, KnowledgeUnit.is_current.is_(True),
               Document.is_active.is_(True), DocumentVersion.knowledge_status == "DATA_READY",
               Document.tenant_id == candidate.tenant_id, Document.project_id == candidate.project_id,
               DocumentVersion.search_status.in_(("READY", "SEARCH_READY")),
               DocumentVersion.search_ready_at.is_not(None),
               DocumentVersion.valid_from <= now, DocumentVersion.valid_to > now)
        .order_by(KnowledgeUnit.id).with_for_update())).all()
    if [list(row) for row in rows] != expected:
        raise StaleTreePublish("Knowledge inputs changed before the atomic switch")


async def _atomic_switch(
    candidate: PreparedTreeCandidate,
    target_slot: Literal["SLOT_A", "SLOT_B"],
    *,
    previous_tree_version: str | None,
    previous_slot: str,
    previous_epoch: int,
    already_verified: bool = False,
    candidate_manifest: dict[str, Any] | None = None,
) -> TreePublishResult:
    from sqlalchemy.orm import load_only

    async with SessionLocal() as session:
        async with session.begin():
            await _verify_knowledge_inputs(session, candidate, candidate_manifest=candidate_manifest)
            state = await session.scalar(
                select(ProjectSearchState)
                .where(ProjectSearchState.project_id == candidate.project_id)
                .with_for_update()
            )
            if already_verified:
                record = await session.scalar(
                    select(TreeManifest)
                    .options(
                        load_only(
                            TreeManifest.tree_version,
                            TreeManifest.project_id,
                            TreeManifest.status,
                            TreeManifest.checksum,
                        )
                    )
                    .where(TreeManifest.tree_version == candidate.tree_version)
                    .with_for_update()
                )
                if (
                    record is None
                    or record.project_id != candidate.project_id
                    or record.tree_version != candidate.tree_version
                    or record.status != "INACTIVE"
                    or record.checksum != candidate.checksum
                ):
                    raise PublishVerificationError("PostgreSQL staged manifest identity or status is inconsistent")
            else:
                record = await session.scalar(
                    select(TreeManifest)
                    .where(TreeManifest.tree_version == candidate.tree_version)
                    .with_for_update()
                )
                _verify_persisted_manifest(record, candidate, candidate_manifest=candidate_manifest)
                await _verify_stored_source_profiles(session, candidate)
            if (
                state is None
                or state.active_tree_version != previous_tree_version
                or state.active_routing_slot != previous_slot
                or state.active_search_epoch != previous_epoch
                or _inactive_slot(state) != target_slot
            ):
                raise StaleTreePublish("Project pointer changed while the candidate was being verified")

            if previous_tree_version and previous_tree_version != candidate.tree_version:
                previous = await session.scalar(
                    select(TreeManifest)
                    .options(load_only(TreeManifest.tree_version, TreeManifest.status))
                    .where(TreeManifest.tree_version == previous_tree_version)
                    .with_for_update()
                )
                if previous is not None:
                    previous.status = "INACTIVE"
            record.status = "ACTIVE"
            state.previous_tree_version = previous_tree_version
            state.active_tree_version = candidate.tree_version
            state.active_routing_slot = target_slot
            state.active_search_epoch += 1
            if target_slot == "SLOT_A":
                state.slot_a_tree_version = candidate.tree_version
            else:
                state.slot_b_tree_version = candidate.tree_version
            state.last_switched_at = datetime.now(UTC)
            epoch = state.active_search_epoch
    return TreePublishResult(
        project_id=candidate.project_id,
        tree_version=candidate.tree_version,
        routing_slot=target_slot,
        search_epoch=epoch,
        checksum=candidate.checksum,
        point_count=candidate.expected_point_count,
    )


async def publish_tree_candidate(
    snapshot: CandidateSnapshot,
    *,
    search_unit_assignments: Iterable[SearchUnitAssignment],
    qdrant_client: httpx.AsyncClient,
) -> TreePublishResult:
    """Write and verify an inactive Qdrant slot before the atomic PostgreSQL switch."""
    from sag_api.services.cpu_worker import run_cpu

    candidate = await run_cpu("prepare_tree", {
        "snapshot": replace(snapshot, metrics=dict(snapshot.metrics), quality_gates=dict(snapshot.quality_gates)),
        "assignments": tuple(search_unit_assignments),
    })
    candidate_manifest = candidate.manifest
    async with _project_publish_lock(candidate.project_id):
        staged = await _stage_candidate(candidate, candidate_manifest=candidate_manifest)
        if isinstance(staged, TreePublishResult):
            await _verify_qdrant_slot(candidate, staged.routing_slot, qdrant_client)
            return staged
        target_slot, previous_tree_version, previous_slot, previous_epoch = staged
        batches = build_inactive_slot_payloads(candidate, target_slot)
        try:
            await asyncio.gather(
                _verify_database_search_units(candidate),
                _clear_inactive_slot_qdrant_payloads(qdrant_client, candidate, target_slot),
            )
            await update_inactive_slot_qdrant_payloads(
                qdrant_client,
                f"search_units_{candidate.project_id}",
                batches,
            )
            await asyncio.gather(
                _verify_staged_candidate(candidate, candidate_manifest=candidate_manifest),
                _verify_qdrant_slot(candidate, target_slot, qdrant_client),
            )
        except (PublishVerificationError, httpx.HTTPError, SQLAlchemyError) as error:
            await _mark_rejected(candidate)
            if isinstance(error, PublishVerificationError):
                raise
            raise PublishVerificationError("Inactive-slot or PostgreSQL verification failed") from error
        try:
            return await _atomic_switch(
                candidate,
                target_slot,
                previous_tree_version=previous_tree_version,
                previous_slot=previous_slot,
                previous_epoch=previous_epoch,
                already_verified=True,
                candidate_manifest=candidate_manifest,
            )
        except Exception:
            await _mark_rejected(candidate)
            raise
