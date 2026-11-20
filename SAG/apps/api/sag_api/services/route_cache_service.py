"""Exact route-decision cache keys and bounded in-process storage.

This cache stores route decisions only. It never stores query text or evidence,
and it cannot be used unless the caller has current ACL and version boundaries.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Mapping
from threading import RLock
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sag_api.services.query_routing_service import GroupRoutingSnapshot, RoutingPolicy

ROUTE_CACHE_KEY_VERSION = "route-cache-key-v1"
ROUTE_DECISION_MODEL_VERSION = "profile-query-signal-v2"


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def principal_acl_fingerprint(principal: object | None) -> str | None:
    """Fingerprint verified principal claims without retaining claim values."""
    if principal is None:
        return None
    subject = getattr(principal, "subject", None)
    organization_id = getattr(principal, "organization_id", None)
    tenant_id = getattr(principal, "tenant_id", None)
    project_ids = getattr(principal, "allowed_project_ids", None)
    partition_ids = getattr(principal, "allowed_partition_ids", None)
    if not all(isinstance(value, str) and value for value in (subject, organization_id, tenant_id)):
        return None
    if not isinstance(project_ids, (set, frozenset, tuple, list)):
        return None
    if not isinstance(partition_ids, (set, frozenset, tuple, list)):
        return None
    identity = {
        "subject": subject,
        "organization_id": organization_id,
        "tenant_id": tenant_id,
        "allowed_project_ids": sorted({str(value) for value in project_ids}),
        "allowed_partition_ids": sorted({str(value) for value in partition_ids}),
        "is_service": bool(getattr(principal, "is_service", False)),
        "roles": sorted({str(value) for value in getattr(principal, "roles", ())}),
    }
    return _canonical_digest(identity)


def effective_acl_epoch(scopes: list[Mapping[str, object]]) -> str | None:
    """Hash current effective scopes and mapping generations for this request.

    Source mapping generations advance on revoke/regrant. This content-derived
    digest is not a substitute for a broader authorization-provider epoch.
    """
    normalized_scopes: list[dict[str, object]] = []
    for scope in scopes:
        normalized = {
            "project_id": str(scope.get("project_id") or ""),
            "source_ids": sorted({str(value) for value in scope.get("source_ids", [])}),
            "document_version_ids": sorted({str(value) for value in scope.get("document_version_ids", [])}),
            "tenant_id": str(scope.get("tenant_id") or ""),
            "partition_id": str(scope.get("partition_id") or ""),
            "acl_mapping_version": scope.get("acl_mapping_version"),
        }
        if not all(normalized[field] for field in ("project_id", "tenant_id", "partition_id")):
            return None
        if not normalized["source_ids"] or not normalized["document_version_ids"]:
            return None
        mapping_version = normalized["acl_mapping_version"]
        if type(mapping_version) is not int or mapping_version < 1:
            return None
        normalized_scopes.append(normalized)
    if not normalized_scopes:
        return None
    normalized_scopes.sort(key=lambda value: _canonical_digest(value))
    return _canonical_digest(normalized_scopes)


def _current_search_index_versions(filters: Mapping[str, object]) -> list[dict[str, str]] | None:
    """Validate the per-document verified INDEX_SEARCH attempt identity."""
    scopes = filters.get("authorized_scopes")
    if not isinstance(scopes, (list, tuple)) or not scopes:
        return None

    versions: list[dict[str, str]] = []
    for scope in scopes:
        if not isinstance(scope, Mapping):
            return None
        project_id = scope.get("project_id")
        source_ids = scope.get("source_ids")
        tenant_id = scope.get("tenant_id")
        partition_id = scope.get("partition_id")
        document_version_ids = scope.get("document_version_ids")
        search_versions = scope.get("search_index_versions")
        if (
            not isinstance(project_id, str)
            or not project_id
            or not isinstance(tenant_id, str)
            or not tenant_id
            or not isinstance(partition_id, str)
            or not partition_id
            or not isinstance(source_ids, (list, tuple))
            or len(source_ids) != 1
            or not isinstance(source_ids[0], str)
            or not source_ids[0]
            or not isinstance(document_version_ids, (list, tuple))
            or not document_version_ids
            or not isinstance(search_versions, (list, tuple))
            or not search_versions
        ):
            return None
        source_id = source_ids[0]
        expected_document_versions = {str(value) for value in document_version_ids}
        observed_document_versions: set[str] = set()
        for item in search_versions:
            if not isinstance(item, Mapping):
                return None
            document_version_id = item.get("document_version_id")
            ingestion_run_id = item.get("ingestion_run_id")
            index_stage_run_id = item.get("index_stage_run_id")
            manifest_checksum = item.get("manifest_checksum")
            if (
                not all(
                    isinstance(value, str) and value
                    for value in (document_version_id, ingestion_run_id, index_stage_run_id)
                )
                or document_version_id not in expected_document_versions
                or document_version_id in observed_document_versions
                or not isinstance(manifest_checksum, str)
                or len(manifest_checksum) != 64
                or any(character not in "0123456789abcdef" for character in manifest_checksum)
            ):
                return None
            observed_document_versions.add(document_version_id)
            versions.append(
                {
                    "project_id": project_id,
                    "source_id": source_id,
                    "tenant_id": tenant_id,
                    "partition_id": partition_id,
                    "document_version_id": document_version_id,
                    "ingestion_run_id": ingestion_run_id,
                    "index_stage_run_id": index_stage_run_id,
                    "manifest_checksum": manifest_checksum,
                }
            )
        if observed_document_versions != expected_document_versions:
            return None
    versions.sort(
        key=lambda item: (
            item["project_id"],
            item["source_id"],
            item["tenant_id"],
            item["partition_id"],
            item["document_version_id"],
        )
    )
    return versions


def build_route_decision_cache_key(
    snapshot: GroupRoutingSnapshot,
    *,
    query: str,
    planner_trace: Mapping[str, object],
    acl_fingerprint: str,
    acl_epoch: str,
    model_version: str = ROUTE_DECISION_MODEL_VERSION,
    filters: Mapping[str, object],
    policy: RoutingPolicy | None = None,
) -> str | None:
    """Build an exact-query key or return ``None`` when a boundary is missing."""
    if (
        not acl_fingerprint
        or not acl_epoch
        or not model_version
        or not query
        or not snapshot.project_id
        or not snapshot.tenant_id
        or not snapshot.partition_id
        or not snapshot.source_ids
        or not snapshot.document_version_ids
        or snapshot.search_epoch < 1
        or not snapshot.tree_version
        or not snapshot.tree_config_version
        or not snapshot.manifest_verified
        or snapshot.manifest_status != "ACTIVE"
        or len(snapshot.manifest_checksum) != 64
        or any(character not in "0123456789abcdef" for character in snapshot.manifest_checksum)
        or snapshot.routing_slot not in {"SLOT_A", "SLOT_B"}
        or not isinstance(planner_trace.get("planner_version"), str)
        or not planner_trace.get("planner_version")
    ):
        return None
    if policy is None:
        from sag_api.services.query_routing_service import RoutingPolicy

        routing_policy = RoutingPolicy()
    else:
        routing_policy = policy
    search_index_versions = _current_search_index_versions(filters)
    if search_index_versions is None:
        return None
    try:
        payload: dict[str, Any] = {
            "key_version": ROUTE_CACHE_KEY_VERSION,
            "tenant_id": snapshot.tenant_id,
            "project_id": snapshot.project_id,
            "source_ids": sorted(set(snapshot.source_ids)),
            "document_version_ids": sorted(set(snapshot.document_version_ids)),
            "partition_id": snapshot.partition_id,
            "acl_fingerprint": acl_fingerprint,
            "acl_epoch": acl_epoch,
            "search_epoch": snapshot.search_epoch,
            "tree_version": snapshot.tree_version,
            "tree_config_version": snapshot.tree_config_version,
            "routing_slot": snapshot.routing_slot,
            "manifest_checksum": snapshot.manifest_checksum,
            "search_index_versions": search_index_versions,
            "model_version": model_version,
            "planner_trace": dict(planner_trace),
            "routing_config": {
                "policy_version": "routing-policy-v1",
                "policy": routing_policy.model_dump(mode="json"),
            },
            "filters": dict(filters),
            "query_digest": _canonical_digest(query),
            # The route scorer consumes these exact query-scoped values. Including
            # them protects against changed profile inputs within an epoch.
            "profiles": [profile.model_dump(mode="json") for profile in snapshot.profiles],
        }
        return _canonical_digest(payload)
    except (TypeError, ValueError):
        return None


class RouteDecisionCache:
    """Small bounded exact-key cache; disabled unless explicitly enabled."""

    def __init__(self, *, enabled: bool = False, max_entries: int = 512) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.enabled = enabled
        self.max_entries = max_entries
        self._entries: OrderedDict[str, object] = OrderedDict()
        self._lock = RLock()

    def get(self, key: str, *, enabled: bool | None = None) -> object | None:
        if not (self.enabled if enabled is None else enabled):
            return None
        with self._lock:
            value = self._entries.get(key)
            if value is not None:
                self._entries.move_to_end(key)
            return value

    def put(self, key: str, value: object, *, enabled: bool | None = None) -> None:
        if not (self.enabled if enabled is None else enabled):
            return
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


route_decision_cache = RouteDecisionCache()
