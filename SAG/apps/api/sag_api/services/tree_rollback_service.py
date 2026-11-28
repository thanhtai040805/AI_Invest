"""Fail-closed rollback to a retained blue-green routing tree."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from typing import Literal

import httpx
from sqlalchemy import delete, select

from sag_api.core.db import SessionLocal
from sag_api.db.models.routing_rag import ProjectSearchState, TreeManifest, TreeSnapshotLease
from sag_api.services.tree_publish_service import (
    PreparedTreeCandidate,
    PublishVerificationError,
    SearchUnitAssignment,
    StaleTreePublish,
    TreePublishError,
    TreePublishResult,
    _active_slot,
    _checksum_manifest,
    _database_now,
    _project_publish_lock,
    _verify_database_search_units,
    _verify_persisted_manifest,
    _verify_qdrant_slot,
    _verify_stored_source_profiles,
)


def _prepared_candidate_from_manifest(
    record: TreeManifest,
    *,
    expected_status: Literal["ACTIVE", "INACTIVE"] = "INACTIVE",
) -> PreparedTreeCandidate:
    """Rehydrate the exact retained candidate needed to verify its Qdrant slot."""
    raw_manifest = record.manifest_json
    if isinstance(raw_manifest, str):
        try:
            manifest = json.loads(raw_manifest)
        except ValueError as error:
            raise PublishVerificationError("Retained tree manifest JSON is malformed") from error
    elif isinstance(raw_manifest, dict):
        manifest = raw_manifest
    else:
        raise PublishVerificationError("Retained tree manifest JSON is missing")
    if (
        not isinstance(manifest, dict)
        or manifest.get("tree_version") != record.tree_version
        or manifest.get("checksum") != record.checksum
        or _checksum_manifest(manifest) != record.checksum
        or record.tree_version != f"tree-{record.checksum[:24]}"
    ):
        raise PublishVerificationError("Retained tree manifest checksum is inconsistent")

    quality_gates = manifest.get("quality_gates")
    raw_assignments = manifest.get("search_unit_assignments")
    raw_profiles = manifest.get("routing_profiles")
    raw_source_profiles = manifest.get("source_routing_profiles")
    raw_partition_counts = manifest.get("security_partition_search_unit_counts")
    project_id = manifest.get("project_id")
    tenant_id = manifest.get("tenant_id")
    source_tree_version = manifest.get("source_tree_version")
    expected_point_count = manifest.get("expected_point_count")
    if (
        record.status != expected_status
        or record.project_id != project_id
        or not isinstance(project_id, str)
        or not isinstance(tenant_id, str)
        or not isinstance(source_tree_version, str)
        or not isinstance(quality_gates, dict)
        or not quality_gates
        or any(value is not True for value in quality_gates.values())
        or not isinstance(raw_assignments, list)
        or not isinstance(raw_profiles, list)
        or not isinstance(raw_source_profiles, list)
        or any(not isinstance(profile, dict) for profile in raw_profiles)
        or not isinstance(raw_partition_counts, dict)
        or isinstance(expected_point_count, bool)
        or not isinstance(expected_point_count, int)
        or expected_point_count <= 0
        or len(raw_assignments) != expected_point_count
    ):
        raise PublishVerificationError("Retained tree manifest is incomplete or not rollback eligible")

    try:
        if any(
            not isinstance(profile.get("node_id"), str)
            or not profile["node_id"]
            or not isinstance(profile.get("is_leaf"), bool)
            or not isinstance(profile.get("unit_ids"), list)
            or any(not isinstance(unit_id, str) or not unit_id for unit_id in profile["unit_ids"])
            for profile in raw_profiles
        ):
            raise PublishVerificationError("Retained tree routing profile inventory is malformed")
        node_ids = [profile["node_id"] for profile in raw_profiles]
        if len(node_ids) != len(set(node_ids)):
            raise PublishVerificationError("Retained tree routing profile IDs are duplicated")
        assignments = tuple(
            sorted(
                (SearchUnitAssignment(**item) for item in raw_assignments if isinstance(item, dict)),
                key=lambda item: item.search_unit_id,
            )
        )
        leaves = tuple(
            sorted(
                (
                    (profile["node_id"], tuple(sorted(profile["unit_ids"])))
                    for profile in raw_profiles
                    if isinstance(profile, dict) and profile.get("is_leaf") is True
                ),
                key=lambda item: item[0],
            )
        )
        partition_counts = tuple(sorted((key, value) for key, value in raw_partition_counts.items()))
    except PublishVerificationError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise PublishVerificationError("Retained tree manifest inventory is malformed") from error

    if (
        len(assignments) != len(raw_assignments)
        or not assignments
        or len({item.search_unit_id for item in assignments}) != len(assignments)
        or any(not isinstance(node_id, str) or not node_id for node_id, _members in leaves)
        or any(
            not members or any(not isinstance(unit_id, str) or not unit_id for unit_id in members)
            for _node_id, members in leaves
        )
        or len({unit_id for _node_id, members in leaves for unit_id in members})
        != sum(len(members) for _node_id, members in leaves)
        or {item.knowledge_unit_id for item in assignments}
        != {unit_id for _node_id, members in leaves for unit_id in members}
        or any(
            not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for key, value in partition_counts
        )
        or dict(partition_counts) != Counter(item.partition_id for item in assignments)
        or len(raw_profiles) != record.node_count
        or len(leaves) != record.leaf_count
        or max((len(members) for _node_id, members in leaves), default=0) != record.max_leaf_size
    ):
        raise PublishVerificationError("Retained tree manifest inventory does not match its metadata")

    candidate = PreparedTreeCandidate(
        project_id=project_id,
        tenant_id=tenant_id,
        source_tree_version=source_tree_version,
        tree_version=record.tree_version,
        checksum=record.checksum,
        manifest_json=json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        expected_point_count=expected_point_count,
        partition_counts=partition_counts,
        search_unit_assignments=assignments,
        leaf_memberships=leaves,
        node_count=record.node_count,
        leaf_count=record.leaf_count,
        max_leaf_size=record.max_leaf_size,
    )
    _verify_persisted_manifest(record, candidate, expected_status=expected_status)
    return candidate


async def _atomic_rollback(
    candidate: PreparedTreeCandidate,
    target_slot: Literal["SLOT_A", "SLOT_B"],
    *,
    current_tree_version: str,
    current_slot: str,
    current_epoch: int,
) -> TreePublishResult:
    async with SessionLocal() as session:
        async with session.begin():
            state = await session.scalar(
                select(ProjectSearchState)
                .where(ProjectSearchState.project_id == candidate.project_id)
                .with_for_update()
            )
            target_record = await session.scalar(
                select(TreeManifest).where(TreeManifest.tree_version == candidate.tree_version).with_for_update()
            )
            active_record = await session.scalar(
                select(TreeManifest).where(TreeManifest.tree_version == current_tree_version).with_for_update()
            )
            _verify_persisted_manifest(target_record, candidate, expected_status="INACTIVE")
            # Qdrant was checked outside this transaction; recheck profiles before switching pointers.
            await _verify_stored_source_profiles(session, candidate)
            if (
                state is None
                or state.active_tree_version != current_tree_version
                or state.active_routing_slot != current_slot
                or state.active_search_epoch != current_epoch
                or state.previous_tree_version != candidate.tree_version
                or (state.slot_a_tree_version if target_slot == "SLOT_A" else state.slot_b_tree_version)
                != candidate.tree_version
                or _active_slot(state) != current_slot
                or active_record is None
                or active_record.project_id != candidate.project_id
                or active_record.status != "ACTIVE"
            ):
                raise StaleTreePublish("Project pointer changed while rollback was being verified")

            active_record.status = "INACTIVE"
            target_record.status = "ACTIVE"
            state.previous_tree_version = current_tree_version
            state.active_tree_version = candidate.tree_version
            state.active_routing_slot = target_slot
            state.active_search_epoch += 1
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


async def rollback_tree_candidate(
    project_id: str,
    *,
    target_tree_version: str,
    qdrant_client: httpx.AsyncClient,
) -> TreePublishResult:
    """Restore a specific retained tree while its Qdrant slot is available.

    A later publish ends this rollback window when it reserves and clears the
    inactive slot. A repeated request for the now-active target verifies the
    active state and returns without switching the pointer again.
    """
    async with _project_publish_lock(project_id):
        async with SessionLocal() as session:
            async with session.begin():
                state = await session.scalar(
                    select(ProjectSearchState).where(ProjectSearchState.project_id == project_id).with_for_update()
                )
                if state is None or not state.active_tree_version:
                    raise TreePublishError("No active tree is available for rollback")
                database_now = await _database_now(session)
                await session.execute(
                    delete(TreeSnapshotLease).where(
                        TreeSnapshotLease.project_id == project_id,
                        TreeSnapshotLease.expires_at <= database_now,
                    )
                )
                current_slot = _active_slot(state)
                current_tree_version = state.active_tree_version
                current_epoch = state.active_search_epoch

                if target_tree_version == current_tree_version:
                    record = await session.scalar(
                        select(TreeManifest)
                        .where(
                            TreeManifest.project_id == project_id,
                            TreeManifest.tree_version == target_tree_version,
                        )
                        .with_for_update()
                    )
                    if record is None:
                        raise PublishVerificationError("Active tree manifest is missing")
                    candidate = _prepared_candidate_from_manifest(record, expected_status="ACTIVE")
                    await _verify_stored_source_profiles(session, candidate)
                    target_slot = current_slot
                    already_active = True
                else:
                    if not state.previous_tree_version:
                        raise TreePublishError("No retained previous tree is available for rollback")
                    previous_version = state.previous_tree_version
                    if target_tree_version != previous_version:
                        raise TreePublishError("Requested rollback target is not the retained previous tree")
                    already_active = False

                    if previous_version == current_tree_version:
                        raise TreePublishError("Previous and active tree versions are identical")
                    target_slot: Literal["SLOT_A", "SLOT_B"] = (
                        "SLOT_B" if current_slot == "SLOT_A" else "SLOT_A"
                    )
                    slot_tree_version = (
                        state.slot_a_tree_version if target_slot == "SLOT_A" else state.slot_b_tree_version
                    )
                    if slot_tree_version != previous_version:
                        raise TreePublishError("Previous tree is no longer present in either Qdrant slot")
                    record = await session.scalar(
                        select(TreeManifest)
                        .where(
                            TreeManifest.project_id == project_id,
                            TreeManifest.tree_version == previous_version,
                        )
                        .with_for_update()
                    )
                    if record is None:
                        raise PublishVerificationError("Retained previous tree manifest is missing")
                    candidate = _prepared_candidate_from_manifest(record)
                    await _verify_stored_source_profiles(session, candidate)

        await _verify_database_search_units(candidate)
        await _verify_qdrant_slot(candidate, target_slot, qdrant_client)
        if already_active:
            return TreePublishResult(
                project_id=candidate.project_id,
                tree_version=candidate.tree_version,
                routing_slot=target_slot,
                search_epoch=current_epoch,
                checksum=candidate.checksum,
                point_count=candidate.expected_point_count,
            )
        return await _atomic_rollback(
            candidate,
            target_slot,
            current_tree_version=current_tree_version,
            current_slot=current_slot,
            current_epoch=current_epoch,
        )
