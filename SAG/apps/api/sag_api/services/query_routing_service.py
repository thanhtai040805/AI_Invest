"""Request-scoped, ACL-safe beam routing over immutable tree profiles.

The tree builder owns profile production. This module only accepts profiles
already reduced to one authorized Source/version/partition scope and produces
node filters that are added alongside canonical SearchUnit ACL filters.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from sag_api.core.config import settings
from sag_api.services.route_cache_service import (
    ROUTE_DECISION_MODEL_VERSION,
    build_route_decision_cache_key,
    effective_acl_epoch,
    principal_acl_fingerprint,
    route_decision_cache,
)

log = logging.getLogger(__name__)


class _RoutingSource(Protocol):
    id: str


class _RoutingGroup(Protocol):
    project_id: str
    source: _RoutingSource
    versions: tuple[str, ...]
    tenant_id: str
    partition_id: str
    acl_mapping_version: int
    search_index_versions: tuple[tuple[str, str, str, str], ...]


def scope_fingerprint(scope: dict[str, object]) -> str:
    """Stable opaque identity for the exact evidence scope used to build profiles."""
    normalized = {
        "project_id": str(scope.get("project_id") or ""),
        "source_ids": sorted({str(value) for value in scope.get("source_ids", [])}),
        "document_version_ids": sorted({str(value) for value in scope.get("document_version_ids", [])}),
        "tenant_id": str(scope.get("tenant_id") or ""),
        "partition_id": str(scope.get("partition_id") or ""),
    }
    if not all(normalized[key] for key in ("project_id", "tenant_id", "partition_id")):
        raise ValueError("Incomplete routing scope")
    if not normalized["source_ids"] or not normalized["document_version_ids"]:
        raise ValueError("Empty routing scope")
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RoutingPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    beam_width: int = Field(default=3, ge=1, le=8)
    broad_entropy_threshold: float = Field(default=0.72, ge=0.0, le=1.0)
    broad_margin_threshold: float = Field(default=0.10, ge=0.0, le=1.0)
    decisive_margin: float = Field(default=0.18, ge=0.0, le=1.0)
    membership_limit: int = Field(default=256, ge=1, le=256)


class NodeProfile(BaseModel):
    """A query-scoped profile; no content or aggregates outside its scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str = Field(min_length=1, max_length=128)
    parent_id: str | None = Field(default=None, max_length=128)
    is_leaf: bool
    accessible_unit_count: int = Field(ge=0)
    project_id: str
    source_ids: tuple[str, ...]
    document_version_ids: tuple[str, ...]
    tenant_id: str
    partition_id: str
    tree_version: str
    scope_fingerprint: str
    signal_scores: dict[str, float] = Field(default_factory=dict)
    temporal_from_ts: int | None = None
    temporal_to_ts: int | None = None


class GroupRoutingSnapshot(BaseModel):
    """Immutable tree/search scope captured for one project/source/partition group."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str = Field(min_length=1, max_length=128)
    captured_at: datetime
    project_id: str
    source_ids: tuple[str, ...]
    document_version_ids: tuple[str, ...]
    tenant_id: str
    partition_id: str
    scope_fingerprint: str
    tree_version: str
    tree_config_version: str = Field(min_length=1, max_length=32)
    routing_slot: Literal["SLOT_A", "SLOT_B"]
    search_epoch: int = Field(ge=1)
    manifest_status: str
    manifest_checksum: str
    manifest_verified: bool
    profiles: tuple[NodeProfile, ...] = Field(max_length=settings.search_tree_profile_limit)


class RoutingSnapshot(BaseModel):
    """One request snapshot spanning every authorized Project scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str = Field(min_length=1, max_length=128)
    captured_at: datetime
    groups: tuple[GroupRoutingSnapshot, ...] = Field(max_length=256)


def _validate_snapshot_profile_budget(raw: object) -> None:
    """Reject oversized provider payloads before parsing/scoring all profiles."""
    limit = settings.search_tree_profile_limit
    if isinstance(raw, RoutingSnapshot):
        profile_count = sum(len(group.profiles) for group in raw.groups)
    elif isinstance(raw, Mapping):
        groups = raw.get("groups")
        if not isinstance(groups, (list, tuple)):
            return
        if len(groups) > 256:
            raise ValueError("request routing snapshot group limit exceeded")
        profile_count = 0
        for group in groups:
            if not isinstance(group, Mapping):
                continue
            profiles = group.get("profiles", ())
            if isinstance(profiles, (list, tuple)):
                profile_count += len(profiles)
            if profile_count > limit:
                raise ValueError("request routing snapshot profile limit exceeded")
    else:
        return
    if profile_count > limit:
        raise ValueError("request routing snapshot profile limit exceeded")


class RouteDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str | None = None
    request_snapshot_id: str | None = None
    acl_scope_fingerprint: str | None = None
    project_id: str
    tree_version: str | None = None
    routing_slot: str | None = None
    search_epoch: int | None = None
    selected_nodes: tuple[str, ...] = ()
    membership_node_ids: tuple[str, ...] = ()
    entropy: float | None = None
    margin: float | None = None
    broad_route: bool = False
    fallback_reason: str | None = None
    reason_code: str

    @property
    def routed(self) -> bool:
        return bool(self.membership_node_ids)


def routing_scope_for_group(group: _RoutingGroup) -> dict[str, object]:
    return {
        "project_id": str(group.project_id),
        "source_ids": (str(group.source.id),),
        "document_version_ids": tuple(sorted(group.versions)),
        "tenant_id": str(group.tenant_id),
        "partition_id": str(group.partition_id),
        "acl_mapping_version": getattr(group, "acl_mapping_version", 0),
        "search_index_versions": tuple(
            {
                "document_version_id": document_version_id,
                "ingestion_run_id": ingestion_run_id,
                "index_stage_run_id": index_stage_run_id,
                "manifest_checksum": manifest_checksum,
            }
            for (
                document_version_id,
                ingestion_run_id,
                index_stage_run_id,
                manifest_checksum,
            ) in getattr(group, "search_index_versions", ())
        ),
    }


async def capture_routing_decisions(
    engine_manager: object,
    groups: list[_RoutingGroup],
    *,
    query: str,
    planner_trace: dict[str, object],
    principal: object | None = None,
    timeout_seconds: float | None = None,
    query_vector_tasks_by_scope: Mapping[str, Any] | None = None,
    as_of_ts: int | None = None,
) -> dict[tuple[str, str, str, str], RouteDecision]:
    """Capture all per-Project profiles in one provider call for request consistency."""
    import inspect

    scopes = [routing_scope_for_group(group) for group in groups]
    expected = {scope_fingerprint(scope): scope for scope in scopes}
    acl_fingerprint = principal_acl_fingerprint(principal)
    acl_epoch = effective_acl_epoch(scopes)
    fallback_code = "TREE_PROVIDER_UNAVAILABLE"
    fallback_reason = "routing_snapshot_provider_unavailable"
    provider = getattr(engine_manager, "get_routing_snapshot", None)
    if callable(provider):
        request_snapshot_id: str | None = None
        provider_kwargs: dict[str, Any] = {
            "query": query,
            "scopes": scopes,
            "planner": planner_trace,
        }
        if query_vector_tasks_by_scope is not None or timeout_seconds is not None or as_of_ts is not None:
            try:
                sig = inspect.signature(provider)
                has_varkw = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                )
                if query_vector_tasks_by_scope is not None and (
                    "query_vector_tasks_by_scope" in sig.parameters or has_varkw
                ):
                    provider_kwargs["query_vector_tasks_by_scope"] = query_vector_tasks_by_scope
                if timeout_seconds is not None and (
                    "timeout_seconds" in sig.parameters or has_varkw
                ):
                    provider_kwargs["timeout_seconds"] = timeout_seconds
                if as_of_ts is not None and ("as_of_ts" in sig.parameters or has_varkw):
                    provider_kwargs["as_of_ts"] = as_of_ts
            except (TypeError, ValueError):
                pass
        try:
            if timeout_seconds is None:
                raw = await provider(**provider_kwargs)
            else:
                async with asyncio.timeout(max(0.01, float(timeout_seconds))):
                    raw = await provider(**provider_kwargs)
            if raw is not None:
                if isinstance(raw, Mapping):
                    candidate_id = raw.get("snapshot_id")
                    request_snapshot_id = candidate_id if isinstance(candidate_id, str) else None
                else:
                    candidate_id = getattr(raw, "snapshot_id", None)
                    request_snapshot_id = candidate_id if isinstance(candidate_id, str) else None
                _validate_snapshot_profile_budget(raw)
                snapshot = RoutingSnapshot.model_validate(raw)
                request_snapshot_id = snapshot.snapshot_id
                if not snapshot.groups or snapshot.captured_at is None:
                    raise ValueError("empty request routing snapshot")
                by_scope = {profile.scope_fingerprint: profile for profile in snapshot.groups}
                if len(by_scope) != len(snapshot.groups) or set(by_scope) != set(expected):
                    raise ValueError("request routing snapshot scope set mismatch")
                decisions: dict[tuple[str, str, str, str], RouteDecision] = {}
                for fingerprint, scope in expected.items():
                    profile = by_scope[fingerprint]
                    if profile.captured_at != snapshot.captured_at:
                        raise ValueError("request routing snapshot is not immutable")
                    group_scope = _scope_dict(profile)
                    if scope_fingerprint(group_scope) != fingerprint:
                        raise ValueError("group routing snapshot scope mismatch")
                    source_id = str(scope["source_ids"][0])
                    key = (
                        str(scope["project_id"]),
                        source_id,
                        str(scope["tenant_id"]),
                        str(scope["partition_id"]),
                    )
                    recency_intent = bool(planner_trace.get("recency_intent"))
                    if not recency_intent and isinstance(planner_trace.get("query_analysis"), Mapping):
                        qa_feats = planner_trace["query_analysis"].get("features")  # type: ignore[index]
                        if isinstance(qa_feats, Mapping):
                            recency_intent = bool(qa_feats.get("recency_intent"))
                    cache_key = None
                    if settings.search_route_decision_cache_enabled and acl_fingerprint and acl_epoch:
                        cache_filters: dict[str, object] = {"authorized_scopes": scopes}
                        if as_of_ts is not None:
                            cache_filters["as_of_ts"] = int(as_of_ts)
                        cache_key = build_route_decision_cache_key(
                            profile,
                            query=query,
                            planner_trace=planner_trace,
                            acl_fingerprint=acl_fingerprint,
                            acl_epoch=acl_epoch,
                            model_version=ROUTE_DECISION_MODEL_VERSION,
                            filters=cache_filters,
                        )
                    cached_decision = (
                        route_decision_cache.get(cache_key, enabled=settings.search_route_decision_cache_enabled)
                        if cache_key
                        else None
                    )
                    if isinstance(cached_decision, RouteDecision):
                        decision = cached_decision.model_copy(update={"snapshot_id": profile.snapshot_id})
                    else:
                        decision = route_snapshot(
                            profile,
                            as_of_ts=as_of_ts,
                            recency_intent=recency_intent,
                        )
                        if cache_key and decision.routed and decision.fallback_reason is None:
                            route_decision_cache.put(
                                cache_key,
                                decision,
                                enabled=settings.search_route_decision_cache_enabled,
                            )
                    decisions[key] = decision.model_copy(
                        update={
                            "request_snapshot_id": snapshot.snapshot_id,
                            "acl_scope_fingerprint": fingerprint,
                        }
                    )
                return decisions
            fallback_code = "TREE_SNAPSHOT_UNAVAILABLE"
            fallback_reason = "routing_snapshot_unavailable"
        except Exception as error:  # noqa: BLE001 - stale/malformed snapshots use global escape
            log.warning("request tree snapshot unavailable error_type=%s", type(error).__name__)
            release = getattr(engine_manager, "release_routing_snapshot", None)
            if request_snapshot_id and callable(release):
                try:
                    await release(request_snapshot_id)
                except Exception as release_error:  # noqa: BLE001 - cleanup must not hide safe fallback
                    log.warning(
                        "could not release invalid request tree snapshot error_type=%s",
                        type(release_error).__name__,
                    )
            fallback_code = "TREE_PROVIDER_ERROR"
            fallback_reason = "routing_snapshot_invalid_or_unavailable"

    result = {}
    for scope in scopes:
        key = (
            str(scope["project_id"]),
            str(scope["source_ids"][0]),
            str(scope["tenant_id"]),
            str(scope["partition_id"]),
        )
        result[key] = RouteDecision(
            project_id=key[0],
            fallback_reason=fallback_reason,
            reason_code=fallback_code,
        )
    return result


def _scope_dict(snapshot: GroupRoutingSnapshot) -> dict[str, object]:
    return {
        "project_id": snapshot.project_id,
        "source_ids": snapshot.source_ids,
        "document_version_ids": snapshot.document_version_ids,
        "tenant_id": snapshot.tenant_id,
        "partition_id": snapshot.partition_id,
    }


def _profile_score(profile: NodeProfile) -> float:
    scores = profile.signal_scores
    if not scores:
        return 0.0
    if any(
        key not in {"dense", "sparse", "entity", "temporal", "prior"}
        or not math.isfinite(value)
        or value < 0.0
        or value > 1.0
        for key, value in scores.items()
    ):
        raise ValueError("Invalid normalized routing signal")
    # Missing signals are excluded; the present normalized signals share mass.
    return sum(scores.values()) / len(scores)


def _profile_matches_snapshot(profile: NodeProfile, snapshot: GroupRoutingSnapshot) -> bool:
    return (
        profile.project_id == snapshot.project_id
        and tuple(sorted(set(profile.source_ids))) == tuple(sorted(set(snapshot.source_ids)))
        and tuple(sorted(set(profile.document_version_ids))) == tuple(sorted(set(snapshot.document_version_ids)))
        and profile.tenant_id == snapshot.tenant_id
        and profile.partition_id == snapshot.partition_id
        and profile.tree_version == snapshot.tree_version
        and profile.scope_fingerprint == snapshot.scope_fingerprint
    )


def _invalid_decision(snapshot: GroupRoutingSnapshot, reason: str) -> RouteDecision:
    return RouteDecision(
        snapshot_id=snapshot.snapshot_id,
        project_id=snapshot.project_id,
        tree_version=snapshot.tree_version,
        routing_slot=snapshot.routing_slot,
        search_epoch=snapshot.search_epoch,
        fallback_reason=reason,
        reason_code="ROUTING_SNAPSHOT_INVALID",
    )


def route_snapshot(
    snapshot: GroupRoutingSnapshot,
    *,
    policy: RoutingPolicy | None = None,
    as_of_ts: int | None = None,
    recency_intent: bool = False,
) -> RouteDecision:
    """Route only across profiles matching the exact authorized scope."""
    policy = policy or RoutingPolicy()
    try:
        invalid_snapshot = (
            not snapshot.manifest_verified
            or snapshot.manifest_status != "ACTIVE"
            or snapshot.routing_slot not in {"SLOT_A", "SLOT_B"}
            or len(snapshot.manifest_checksum) != 64
            or any(character not in "0123456789abcdef" for character in snapshot.manifest_checksum)
            or not snapshot.tree_version
            or scope_fingerprint(_scope_dict(snapshot)) != snapshot.scope_fingerprint
        )
    except (TypeError, ValueError):
        invalid_snapshot = True
    if invalid_snapshot:
        return _invalid_decision(snapshot, "tree_manifest_or_scope_invalid")

    accessible: dict[str, NodeProfile] = {}
    try:
        for profile in snapshot.profiles:
            if profile.node_id in accessible:
                return _invalid_decision(snapshot, "duplicate_node_id")
            if not _profile_matches_snapshot(profile, snapshot):
                continue
            if (
                as_of_ts is not None
                and profile.temporal_from_ts is not None
                and profile.temporal_from_ts > int(as_of_ts)
            ):
                continue
            _profile_score(profile)
            if profile.accessible_unit_count > 0:
                accessible[profile.node_id] = profile
    except (TypeError, ValueError):
        return _invalid_decision(snapshot, "invalid_profile_signal")

    if not accessible:
        return RouteDecision(
            snapshot_id=snapshot.snapshot_id,
            project_id=snapshot.project_id,
            tree_version=snapshot.tree_version,
            routing_slot=snapshot.routing_slot,
            search_epoch=snapshot.search_epoch,
            fallback_reason="no_profile_in_effective_acl_scope",
            reason_code="TREE_NO_ACCESSIBLE_PROFILE",
        )

    children: dict[str | None, list[NodeProfile]] = defaultdict(list)
    for profile in accessible.values():
        parent = profile.parent_id if profile.parent_id in accessible else None
        children[parent].append(profile)
    for values in children.values():
        values.sort(key=lambda profile: profile.node_id)

    leaf_ids_by_node: dict[str, tuple[str, ...]] = {}

    def descendant_leaves(node_id: str, visiting: set[str] | None = None) -> tuple[str, ...]:
        cached = leaf_ids_by_node.get(node_id)
        if cached is not None:
            return cached
        visiting = visiting or set()
        if node_id in visiting or len(visiting) >= 32:
            raise ValueError("Routing profile tree contains a cycle")
        profile = accessible[node_id]
        if profile.is_leaf:
            if children.get(node_id):
                raise ValueError("Leaf routing profile has children")
            leaf_ids_by_node[node_id] = (node_id,)
            return (node_id,)
        next_visiting = {*visiting, node_id}
        leaves = tuple(
            leaf_id
            for child in children.get(node_id, [])
            for leaf_id in descendant_leaves(child.node_id, next_visiting)
        )
        leaf_ids_by_node[node_id] = leaves
        return leaves

    def entropy_and_margin(profiles: list[NodeProfile]) -> tuple[float, float]:
        scores = [_profile_score(profile) for profile in profiles]
        margin = scores[0] - scores[1] if len(scores) > 1 else 1.0
        total = sum(scores)
        if total <= 0.0 or len(scores) <= 1:
            entropy = 1.0 if total <= 0.0 and len(scores) > 1 else 0.0
        else:
            probabilities = [score / total for score in scores if score > 0.0]
            entropy = -sum(value * math.log(value) for value in probabilities) / math.log(len(scores))
        return entropy, margin

    frontier = children.get(None, [])
    selected: list[NodeProfile] = []
    last_entropy: float | None = None
    last_margin: float | None = None
    broad_route = False
    reason_code = "ROUTE_DESCENDED"
    try:
        for _depth in range(32):
            if not frontier:
                break
            ranked = sorted(
                frontier,
                key=lambda profile: (
                    -_profile_score(profile),
                    -(
                        profile.temporal_from_ts
                        if profile.temporal_from_ts is not None
                        else (profile.temporal_to_ts or 0)
                    )
                    if recency_intent
                    else 0,
                    profile.node_id,
                ),
            )
            last_entropy, last_margin = entropy_and_margin(ranked)
            uncertain = len(ranked) > 1 and (
                (last_entropy >= policy.broad_entropy_threshold and last_margin < policy.broad_margin_threshold)
                or last_margin < policy.decisive_margin
            )
            if uncertain:
                selected = ranked[: policy.beam_width]
                broad_route = True
                reason_code = "ROUTE_UNCERTAIN_BROADENED"
                break

            current = ranked[0]
            if current.is_leaf or not children.get(current.node_id):
                selected = [current]
                break
            frontier = children[current.node_id]
        else:
            return _invalid_decision(snapshot, "routing_depth_limit")
        if not selected:
            return RouteDecision(
                snapshot_id=snapshot.snapshot_id,
                project_id=snapshot.project_id,
                tree_version=snapshot.tree_version,
                routing_slot=snapshot.routing_slot,
                search_epoch=snapshot.search_epoch,
                fallback_reason="tree_has_no_accessible_leaf",
                reason_code="TREE_NO_ACCESSIBLE_LEAF",
            )
        selected_node_ids = tuple(sorted(profile.node_id for profile in selected))
        membership_ids = tuple(sorted({leaf for profile in selected for leaf in descendant_leaves(profile.node_id)}))
    except ValueError:
        return _invalid_decision(snapshot, "invalid_profile_topology")

    if not membership_ids:
        return _invalid_decision(snapshot, "selected_branch_has_no_valid_membership")
    if len(membership_ids) > policy.membership_limit:
        return RouteDecision(
            snapshot_id=snapshot.snapshot_id,
            project_id=snapshot.project_id,
            tree_version=snapshot.tree_version,
            routing_slot=snapshot.routing_slot,
            search_epoch=snapshot.search_epoch,
            fallback_reason="branch_membership_limit_exceeded",
            reason_code="TREE_MEMBERSHIP_LIMIT",
        )
    return RouteDecision(
        snapshot_id=snapshot.snapshot_id,
        project_id=snapshot.project_id,
        tree_version=snapshot.tree_version,
        routing_slot=snapshot.routing_slot,
        search_epoch=snapshot.search_epoch,
        selected_nodes=selected_node_ids,
        membership_node_ids=membership_ids,
        entropy=last_entropy,
        margin=last_margin,
        broad_route=broad_route,
        reason_code=reason_code,
    )
