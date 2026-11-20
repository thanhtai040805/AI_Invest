"""ACL-scoped collectors for persisted tree, evidence, conflict and demand signals."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from sag_api.db.models import (
    CanonicalBlock,
    Document,
    KnowledgeEvidence,
    KnowledgeGraphEdge,
    KnowledgeQualityAssessment,
    KnowledgeTreeNode,
    KnowledgeUnit,
    SearchUnit,
    SourceProjectMapping,
    TreeManifest,
)
from sag_api.services.knowledge_candidate_service import current_units
from sag_api.services.knowledge_quality_persistence import (
    persist_scoring_results,
    query_demand_totals_by_node,
)
from sag_api.services.knowledge_quality_service import (
    ContradictionSignal,
    EvidenceSignal,
    KnowledgeQualityConfig,
    KnowledgeQualityResult,
    QualityTopicInput,
    UnitSignal,
    score_topic,
)
from sag_api.services.knowledge_service import checksum

MAX_CONTRADICTION_CANDIDATES = 5000


async def confirmed_source_ids_for_quality_scope(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
) -> set[str]:
    """Return sources still mapped to this project and tenant before reading evidence."""
    rows = await session.scalars(
        select(SourceProjectMapping.source_id)
        .join(Document, Document.source_id == SourceProjectMapping.source_id)
        .where(
            SourceProjectMapping.project_id == project_id,
            SourceProjectMapping.state == "CONFIRMED",
            Document.project_id == project_id,
            Document.tenant_id == tenant_id,
            Document.is_active.is_(True),
        )
        .distinct()
    )
    return set(rows.all())


def _tree_manifest_checksum_is_valid(manifest: TreeManifest) -> bool:
    payload = manifest.manifest_json
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return False
    if not isinstance(payload, dict):
        return False

    if payload.get("contract") == "knowledge-candidate.v1":
        return (
            checksum(payload) == manifest.checksum
            and manifest.tree_version == f"candidate-{manifest.checksum[:24]}"
        )

    if (
        payload.get("tree_version") != manifest.tree_version
        or payload.get("checksum") != manifest.checksum
    ):
        return False
    canonical = dict(payload)
    for key in ("tree_version", "checksum", "unit_count", "node_count", "status", "lineage_events"):
        canonical.pop(key, None)
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    return digest == manifest.checksum


def _parse_timestamp(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


async def _contradiction_signals(
    session: AsyncSession,
    *,
    project_id: str,
    partition_id: str,
    units_by_id: dict[str, KnowledgeUnit],
    evidence_by_unit: dict[str, list[KnowledgeEvidence]],
    node_members: dict[str, set[str]],
) -> dict[str, tuple[ContradictionSignal, ...]]:
    block_to_unit: dict[tuple[str, str], str] = {}
    version_ids: set[str] = set()
    for unit in units_by_id.values():
        version_ids.add(unit.document_version_id)
        block_ids = unit.provenance_json.get("evidence_block_ids", [])
        if not isinstance(block_ids, list):
            raise ValueError("knowledge unit evidence block lineage is malformed")
        for block_id in block_ids:
            block_to_unit[(unit.document_version_id, str(block_id))] = unit.id

    blocks = (
        await session.scalars(
            select(CanonicalBlock)
            .where(CanonicalBlock.document_version_id.in_(version_ids))
            .order_by(CanonicalBlock.document_version_id, CanonicalBlock.ordinal)
        )
    ).all() if version_ids else []
    blocks_by_version: dict[str, list[CanonicalBlock]] = defaultdict(list)
    block_index: dict[tuple[str, str], int] = {}
    for block in blocks:
        block_index[(block.document_version_id, block.id)] = block.ordinal
        blocks_by_version[block.document_version_id].append(block)

    def endpoint_unit_ids(search_unit: SearchUnit) -> set[str]:
        version_blocks = blocks_by_version.get(search_unit.document_version_id, [])
        start = block_index.get((search_unit.document_version_id, search_unit.block_from_id))
        end = block_index.get((search_unit.document_version_id, search_unit.block_to_id))
        if start is None or end is None:
            return set()
        lower, upper = sorted((start, end))
        return {
            unit_id
            for block in version_blocks
            if lower <= block.ordinal <= upper
            if (unit_id := block_to_unit.get((search_unit.document_version_id, block.id))) is not None
        }

    source_alias = aliased(SearchUnit)
    target_alias = aliased(SearchUnit)
    rows = (
        await session.execute(
            select(KnowledgeGraphEdge, source_alias, target_alias)
            .join(source_alias, source_alias.id == KnowledgeGraphEdge.source_unit_id)
            .join(target_alias, target_alias.id == KnowledgeGraphEdge.target_unit_id)
            .where(
                KnowledgeGraphEdge.project_id == project_id,
                KnowledgeGraphEdge.edge_type == "CONTRADICTS",
                source_alias.security_partition_id == partition_id,
                target_alias.security_partition_id == partition_id,
            )
            .order_by(KnowledgeGraphEdge.id)
            .limit(MAX_CONTRADICTION_CANDIDATES + 1)
        )
    ).all()
    if len(rows) > MAX_CONTRADICTION_CANDIDATES:
        raise ValueError("contradiction candidate limit exceeded; assessment is incomplete")

    evidence_ids_by_unit = {}
    for unit_id in units_by_id:
        evidence_ids_by_unit[unit_id] = tuple(
            sorted(
                evidence.id
                for evidence in evidence_by_unit.get(unit_id, [])
                if isinstance(evidence.payload_json.get("quote"), str)
                and evidence.payload_json.get("quote", "").strip()
                and (
                    isinstance(evidence.payload_json.get("source_anchor"), str)
                    and evidence.payload_json.get("source_anchor", "").strip()
                    or isinstance(evidence.payload_json.get("locator"), str)
                    and evidence.payload_json.get("locator", "").strip()
                )
            )
        )
    signals_by_node: dict[str, list[ContradictionSignal]] = {node_id: [] for node_id in node_members}
    for edge, source_search_unit, target_search_unit in rows:
        metadata = edge.metadata_json or {}
        if metadata.get("is_candidate", True) is not True:
            continue
        source_ids = endpoint_unit_ids(source_search_unit)
        target_ids = endpoint_unit_ids(target_search_unit)
        for node_id, members in node_members.items():
            for source_unit_id in sorted(source_ids & members):
                for target_unit_id in sorted(target_ids & members):
                    if source_unit_id == target_unit_id:
                        continue
                    evidence_ids = tuple(
                        sorted(
                            set(evidence_ids_by_unit[source_unit_id])
                            | set(evidence_ids_by_unit[target_unit_id])
                        )
                    )
                    if not evidence_ids:
                        continue
                    signals_by_node[node_id].append(
                        ContradictionSignal(
                            relation_id=f"{edge.id}:{source_unit_id}:{target_unit_id}",
                            source_unit_id=source_unit_id,
                            target_unit_id=target_unit_id,
                            evidence_ids=evidence_ids,
                        )
                    )
    return {
        node_id: tuple(sorted(signals, key=lambda signal: signal.relation_id))
        for node_id, signals in signals_by_node.items()
    }


async def build_topic_inputs(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    security_partition_id: str,
    tree_version: str,
    as_of: datetime,
    demand_window_days: int,
) -> tuple[tuple[QualityTopicInput, ...], dict[str, dict]]:
    """Build validated node inputs from one tenant/project/partition tree snapshot."""
    if as_of.tzinfo is None or demand_window_days < 1:
        raise ValueError("as_of must be timezone-aware and demand window positive")
    manifest = await session.get(TreeManifest, tree_version)
    if (
        manifest is None
        or manifest.project_id != project_id
        or manifest.status not in {"ACTIVE", "INACTIVE"}
        or not manifest.checksum
    ):
        raise ValueError("tree manifest is missing, rejected, or outside project scope")
    if not _tree_manifest_checksum_is_valid(manifest):
        raise ValueError("tree manifest checksum mismatch")
    current = await current_units(session, tenant_id, project_id)
    confirmed_source_ids = await confirmed_source_ids_for_quality_scope(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
    )
    units_by_id = {
        unit.id: unit
        for unit in current
        if unit.security_partition_id == security_partition_id
        and unit.tenant_id == tenant_id
        and unit.project_id == project_id
        and unit.source_id in confirmed_source_ids
    }
    nodes = (
        await session.scalars(
            select(KnowledgeTreeNode)
            .where(
                KnowledgeTreeNode.tree_version == tree_version,
                KnowledgeTreeNode.security_partition_id == security_partition_id,
            )
            .order_by(KnowledgeTreeNode.node_id)
        )
    ).all()
    if not nodes:
        raise ValueError("tree has no nodes for requested security partition")

    node_members: dict[str, set[str]] = {}
    for node in nodes:
        profile = node.payload_json
        unit_ids = profile.get("unit_ids")
        if (
            checksum(profile) != node.checksum
            or profile.get("node_id") != node.node_id
            or profile.get("tenant_id") != tenant_id
            or profile.get("project_id") != project_id
            or profile.get("partition_id") != security_partition_id
            or not isinstance(unit_ids, list)
            or not all(isinstance(unit_id, str) for unit_id in unit_ids)
            or not unit_ids
            or len(unit_ids) != len(set(unit_ids))
            or any(unit_id not in units_by_id for unit_id in unit_ids)
        ):
            raise ValueError("tree node profile/checksum violates current unit or ACL scope")
        node_members[node.node_id] = set(unit_ids)

    all_unit_ids = sorted({unit_id for member_ids in node_members.values() for unit_id in member_ids})
    evidence_rows = (
        await session.scalars(
            select(KnowledgeEvidence)
            .where(KnowledgeEvidence.unit_id.in_(all_unit_ids))
            .order_by(KnowledgeEvidence.unit_id, KnowledgeEvidence.id)
        )
    ).all()
    evidence_by_unit: dict[str, list[KnowledgeEvidence]] = defaultdict(list)
    for evidence in evidence_rows:
        evidence_by_unit[evidence.unit_id].append(evidence)
    contradictions = await _contradiction_signals(
        session,
        project_id=project_id,
        partition_id=security_partition_id,
        units_by_id={unit_id: units_by_id[unit_id] for unit_id in all_unit_ids},
        evidence_by_unit=evidence_by_unit,
        node_members=node_members,
    )
    demand_totals = await query_demand_totals_by_node(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        security_partition_id=security_partition_id,
        tree_version=tree_version,
        node_ids=tuple(node_members),
        as_of=as_of,
        window_days=demand_window_days,
    )

    topics: list[QualityTopicInput] = []
    snapshots: dict[str, dict] = {}
    for node in nodes:
        member_ids = node_members[node.node_id]
        signals: list[UnitSignal] = []
        evidence_refs: list[dict] = []
        for unit_id in sorted(member_ids):
            unit = units_by_id[unit_id]
            provenance = unit.provenance_json
            evidence_signals: list[EvidenceSignal] = []
            for evidence in evidence_by_unit.get(unit_id, []):
                payload = evidence.payload_json
                quote = payload.get("quote")
                locator = payload.get("source_anchor") or payload.get("locator")
                if not isinstance(quote, str):
                    quote = ""
                if not isinstance(locator, str):
                    locator = ""
                evidence_signal = EvidenceSignal(
                    evidence_id=evidence.id,
                    quote=quote,
                    locator=locator,
                    confidence=evidence.confidence,
                )
                evidence_signals.append(evidence_signal)
                if evidence_signal.anchored:
                    evidence_refs.append(
                        {
                            "evidence_id": evidence.id,
                            "unit_id": unit.id,
                            "source_id": unit.source_id,
                            "quote": quote,
                            "locator": locator,
                        }
                    )
            signals.append(
                UnitSignal(
                    unit_id=unit.id,
                    source_id=unit.source_id,
                    published_at=_parse_timestamp(provenance.get("published_at")),
                    observed_at=_parse_timestamp(provenance.get("observed_at")),
                    ingested_at=_parse_timestamp(provenance.get("ingested_at")),
                    evidence=tuple(evidence_signals),
                )
            )

        demand_current, demand_previous = demand_totals[node.node_id]
        topic_input = QualityTopicInput(
            tenant_id=tenant_id,
            project_id=project_id,
            security_partition_id=security_partition_id,
            tree_version=tree_version,
            tree_manifest_checksum=manifest.checksum,
            node_profile_checksum=node.checksum,
            node_id=node.node_id,
            as_of=as_of,
            demand_window_days=demand_window_days,
            query_demand_current=demand_current,
            query_demand_previous=demand_previous,
            units=tuple(signals),
            contradictions=contradictions[node.node_id],
        )
        topics.append(topic_input)
        snapshots[node.node_id] = {
            "scoring_input": topic_input.model_dump(mode="json"),
            "tenant_id": tenant_id,
            "project_id": project_id,
            "security_partition_id": security_partition_id,
            "tree_version": tree_version,
            "tree_manifest_checksum": manifest.checksum,
            "node_profile_checksum": node.checksum,
            "unit_ids": sorted(member_ids),
            "evidence_ids": sorted(
                evidence.id
                for unit_id in member_ids
                for evidence in evidence_by_unit.get(unit_id, [])
            ),
            "evidence_refs": sorted(evidence_refs, key=lambda item: item["evidence_id"]),
            "contradiction_relation_ids": [signal.relation_id for signal in contradictions[node.node_id]],
            "query_demand_current": demand_current,
            "query_demand_previous": demand_previous,
            "demand_window_days": demand_window_days,
            "as_of": as_of.astimezone(UTC).isoformat(),
        }
    return tuple(topics), snapshots


async def assess_knowledge_tree(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    security_partition_id: str,
    tree_version: str,
    as_of: datetime,
    demand_window_days: int,
    config: KnowledgeQualityConfig = KnowledgeQualityConfig(),
) -> tuple[KnowledgeQualityAssessment, tuple[KnowledgeQualityResult, ...]]:
    topics, input_snapshot = await build_topic_inputs(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        security_partition_id=security_partition_id,
        tree_version=tree_version,
        as_of=as_of,
        demand_window_days=demand_window_days,
    )
    results = tuple(score_topic(topic, config) for topic in topics)
    assessment = await persist_scoring_results(
        session,
        results,
        config=config,
        input_snapshot=input_snapshot,
    )
    return assessment, results
