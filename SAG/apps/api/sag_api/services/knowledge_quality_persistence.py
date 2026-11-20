"""Persistence helpers for quality snapshots and query demand."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.db.models import KnowledgeGap, KnowledgeQualityAssessment, KnowledgeQueryDemandDaily
from sag_api.services.knowledge_quality_service import (
    KnowledgeQualityConfig,
    KnowledgeQualityResult,
    QualityTopicInput,
)
from sag_api.services.knowledge_service import checksum

_DEMAND_KEY = (
    "tenant_id",
    "project_id",
    "security_partition_id",
    "tree_version",
    "node_id",
    "day",
)
def _stable_id(namespace: str, value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"sag:{namespace}:{value}"))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC)


async def record_query_demand(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    security_partition_id: str,
    tree_version: str,
    node_ids: tuple[str, ...],
    occurred_at: datetime,
) -> int:
    """Increment daily node buckets; no query text or user message is accepted."""
    scope = (tenant_id, project_id, security_partition_id, tree_version)
    if any(not value.strip() for value in scope):
        raise ValueError("query demand scope must be complete")
    unique_nodes = tuple(sorted(set(node_ids)))
    if not unique_nodes or any(not node_id.strip() for node_id in unique_nodes):
        return 0
    day = _utc(occurred_at).date()
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        insert = postgresql_insert
    elif dialect == "sqlite":
        insert = sqlite_insert
    else:
        raise ValueError(f"query demand upsert is unsupported for dialect {dialect}")

    table = KnowledgeQueryDemandDaily.__table__
    for node_id in unique_nodes:
        values = {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "security_partition_id": security_partition_id,
            "tree_version": tree_version,
            "node_id": node_id,
            "day": day,
            "request_count": 1,
        }
        statement = insert(table).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[table.c[column] for column in _DEMAND_KEY],
            set_={
                "request_count": table.c.request_count + 1,
                "updated_at": func.current_timestamp(),
            },
        )
        await session.execute(statement)
    return len(unique_nodes)


async def query_demand_totals(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    security_partition_id: str,
    tree_version: str,
    node_id: str,
    as_of: datetime,
    window_days: int,
) -> tuple[int | None, int | None]:
    """Return current and prior equal-length demand windows for one node."""
    return (
        await query_demand_totals_by_node(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            security_partition_id=security_partition_id,
            tree_version=tree_version,
            node_ids=(node_id,),
            as_of=as_of,
            window_days=window_days,
        )
    )[node_id]


async def query_demand_totals_by_node(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    security_partition_id: str,
    tree_version: str,
    node_ids: tuple[str, ...],
    as_of: datetime,
    window_days: int,
) -> dict[str, tuple[int | None, int | None]]:
    """Aggregate both equal-length windows for all nodes in one scoped query."""
    if window_days < 1:
        raise ValueError("demand window must contain at least one day")
    unique_node_ids = tuple(sorted(set(node_ids)))
    if not unique_node_ids:
        return {}
    end_day = _utc(as_of).date()
    start_day = end_day - timedelta(days=window_days - 1)
    previous_end = start_day - timedelta(days=1)
    previous_start = previous_end - timedelta(days=window_days - 1)
    scope_filters = (
        KnowledgeQueryDemandDaily.tenant_id == tenant_id,
        KnowledgeQueryDemandDaily.project_id == project_id,
        KnowledgeQueryDemandDaily.security_partition_id == security_partition_id,
        KnowledgeQueryDemandDaily.tree_version == tree_version,
    )
    rows = (
        await session.execute(
            select(
                KnowledgeQueryDemandDaily.node_id,
                func.sum(
                    case(
                        (KnowledgeQueryDemandDaily.day >= start_day, KnowledgeQueryDemandDaily.request_count),
                        else_=0,
                    )
                ).label("current_total"),
                func.sum(
                    case(
                        (KnowledgeQueryDemandDaily.day <= previous_end, KnowledgeQueryDemandDaily.request_count),
                        else_=0,
                    )
                ).label("previous_total"),
                func.sum(
                    case((KnowledgeQueryDemandDaily.day >= start_day, 1), else_=0)
                ).label("current_bucket_count"),
                func.sum(
                    case((KnowledgeQueryDemandDaily.day <= previous_end, 1), else_=0)
                ).label("previous_bucket_count"),
            )
            .where(
                *scope_filters,
                KnowledgeQueryDemandDaily.day >= previous_start,
                KnowledgeQueryDemandDaily.day <= end_day,
            )
            .group_by(KnowledgeQueryDemandDaily.node_id)
        )
    ).all()
    if not rows:
        return {node_id: (None, None) for node_id in unique_node_ids}
    totals = {
        row.node_id: (
            int(row.current_total or 0) if row.current_bucket_count else None,
            int(row.previous_total or 0) if row.previous_bucket_count else None,
        )
        for row in rows
    }
    return {node_id: totals.get(node_id, (None, None)) for node_id in unique_node_ids}


def _validate_input_snapshot(snapshot: dict[str, Any]) -> None:
    allowed_query_aggregates = {"querydemand", "querydemandcurrent", "querydemandprevious"}
    for key, value in snapshot.items():
        if not isinstance(key, str):
            raise ValueError("assessment snapshot keys must be strings")
        normalized_key = "".join(character for character in key.casefold() if character.isalnum())
        if normalized_key.startswith("query") and normalized_key not in allowed_query_aggregates:
            raise ValueError("raw query/message content cannot be persisted in a quality snapshot")
        if normalized_key in {"rawmessage", "messagetext", "messagecontent"}:
            raise ValueError("raw query/message content cannot be persisted in a quality snapshot")
        if normalized_key in allowed_query_aggregates:
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise ValueError("query demand snapshot fields must contain numeric aggregates only")
            continue
        if isinstance(value, dict):
            _validate_input_snapshot(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _validate_input_snapshot(item)


async def persist_scoring_results(
    session: AsyncSession,
    results: tuple[KnowledgeQualityResult, ...],
    *,
    config: KnowledgeQualityConfig,
    input_snapshot: dict[str, dict[str, Any]],
) -> KnowledgeQualityAssessment:
    """Persist an immutable assessment and one evidence-linked gap row per tree node."""
    if not results:
        raise ValueError("at least one node score is required")
    ordered = tuple(sorted(results, key=lambda result: result.node_id))
    first = ordered[0]
    common_scope = (
        first.tenant_id,
        first.project_id,
        first.security_partition_id,
        first.tree_version,
        first.as_of,
        first.metric_definition_version,
        first.scoring_formula_version,
        first.config_checksum,
    )
    for result in ordered:
        scope = (
            result.tenant_id,
            result.project_id,
            result.security_partition_id,
            result.tree_version,
            result.as_of,
            result.metric_definition_version,
            result.scoring_formula_version,
            result.config_checksum,
        )
        if scope != common_scope:
            raise ValueError("all node scores must share assessment scope and versions")
    node_ids = [result.node_id for result in ordered]
    if len(node_ids) != len(set(node_ids)) or set(input_snapshot) != set(node_ids):
        raise ValueError("input snapshot must contain exactly one entry per scored node")
    for snapshot in input_snapshot.values():
        _validate_input_snapshot(snapshot)

    allowed_snapshot_keys = {
        "scoring_input",
        "tenant_id",
        "project_id",
        "security_partition_id",
        "tree_version",
        "tree_manifest_checksum",
        "node_profile_checksum",
        "unit_ids",
        "evidence_ids",
        "evidence_refs",
        "contradiction_relation_ids",
        "query_demand_current",
        "query_demand_previous",
        "demand_window_days",
        "as_of",
    }
    for result in ordered:
        snapshot = input_snapshot[result.node_id]
        if set(snapshot) - allowed_snapshot_keys:
            raise ValueError("assessment snapshot contains unsupported fields")
        raw_scoring_input = snapshot.get("scoring_input")
        if not isinstance(raw_scoring_input, dict):
            raise ValueError("assessment snapshot must retain the complete scoring input")
        scoring_input = QualityTopicInput.model_validate(raw_scoring_input)
        if checksum(scoring_input.model_dump(mode="json")) != result.input_checksum:
            raise ValueError("scoring input snapshot does not match the scored input checksum")
        if (
            scoring_input.node_id != result.node_id
            or scoring_input.tenant_id != result.tenant_id
            or scoring_input.project_id != result.project_id
            or scoring_input.security_partition_id != result.security_partition_id
            or scoring_input.tree_version != result.tree_version
            or scoring_input.as_of.astimezone(UTC) != _utc(result.as_of)
        ):
            raise ValueError("scoring input snapshot does not match scored scope")

        expected_evidence_refs = sorted(
            (
                {
                    "evidence_id": evidence.evidence_id,
                    "unit_id": unit.unit_id,
                    "source_id": unit.source_id,
                    "quote": evidence.quote,
                    "locator": evidence.locator,
                }
                for unit in scoring_input.units
                for evidence in unit.evidence
                if evidence.anchored
            ),
            key=lambda ref: ref["evidence_id"],
        )
        expected_evidence_ids = sorted(
            evidence.evidence_id
            for unit in scoring_input.units
            for evidence in unit.evidence
        )
        if (
            snapshot.get("tenant_id") != scoring_input.tenant_id
            or snapshot.get("project_id") != scoring_input.project_id
            or snapshot.get("security_partition_id") != scoring_input.security_partition_id
            or snapshot.get("tree_version") != scoring_input.tree_version
            or snapshot.get("tree_manifest_checksum") != scoring_input.tree_manifest_checksum
            or snapshot.get("node_profile_checksum") != scoring_input.node_profile_checksum
            or snapshot.get("unit_ids") != sorted(unit.unit_id for unit in scoring_input.units)
            or snapshot.get("evidence_ids") != expected_evidence_ids
            or snapshot.get("evidence_refs") != expected_evidence_refs
            or snapshot.get("contradiction_relation_ids")
            != sorted(relation.relation_id for relation in scoring_input.contradictions)
            or snapshot.get("query_demand_current") != scoring_input.query_demand_current
            or snapshot.get("query_demand_previous") != scoring_input.query_demand_previous
            or snapshot.get("demand_window_days") != scoring_input.demand_window_days
            or snapshot.get("as_of") != scoring_input.as_of.astimezone(UTC).isoformat()
            or result.supporting_evidence_ids
            != tuple(sorted(ref["evidence_id"] for ref in expected_evidence_refs))
        ):
            raise ValueError("assessment snapshot lineage does not match scored input")

    config_json = config.model_dump(mode="json")
    config_checksum = checksum(config_json)
    if config_checksum != first.config_checksum:
        raise ValueError("scored config checksum does not match persisted config")
    snapshot_json = {node_id: input_snapshot[node_id] for node_id in sorted(input_snapshot)}
    input_checksum = checksum(
        {
            "node_input_checksums": {result.node_id: result.input_checksum for result in ordered},
            "input_snapshot": snapshot_json,
        }
    )
    results_json = {result.node_id: result.model_dump(mode="json") for result in ordered}
    reasons_json = {result.node_id: list(result.reason_codes) for result in ordered}
    assessment_checksum = checksum(
        {
            "scope": {
                "tenant_id": first.tenant_id,
                "project_id": first.project_id,
                "security_partition_id": first.security_partition_id,
                "tree_version": first.tree_version,
                "as_of": _utc(first.as_of).isoformat(),
            },
            "metric_definition_version": first.metric_definition_version,
            "scoring_formula_version": first.scoring_formula_version,
            "config_checksum": config_checksum,
            "input_checksum": input_checksum,
            "results": results_json,
        }
    )
    assessment_id = _stable_id("quality-assessment", assessment_checksum)
    assessment = await session.get(KnowledgeQualityAssessment, assessment_id)
    if assessment is not None:
        if assessment.assessment_checksum != assessment_checksum:
            raise ValueError("persisted assessment identity checksum mismatch")
        return assessment

    assessment = KnowledgeQualityAssessment(
        id=assessment_id,
        tenant_id=first.tenant_id,
        project_id=first.project_id,
        security_partition_id=first.security_partition_id,
        tree_version=first.tree_version,
        as_of=_utc(first.as_of),
        metric_definition_version=first.metric_definition_version,
        scoring_formula_version=first.scoring_formula_version,
        config_version=config.version,
        config_checksum=config_checksum,
        input_checksum=input_checksum,
        assessment_checksum=assessment_checksum,
        config_json=config_json,
        input_snapshot_json=snapshot_json,
        metrics_json={
            node_id: results_json[node_id]["metrics"]
            for node_id in sorted(results_json)
        },
        reason_codes_json=reasons_json,
    )
    session.add(assessment)
    await session.flush()

    for result in ordered:
        snapshot = snapshot_json[result.node_id]
        evidence_by_id = {
            str(item.get("evidence_id")): item
            for item in snapshot.get("evidence_refs", [])
            if isinstance(item, dict) and item.get("evidence_id")
        }
        evidence_refs = []
        for evidence_id in result.supporting_evidence_ids:
            ref = evidence_by_id.get(evidence_id, {"evidence_id": evidence_id})
            evidence_refs.append(
                {
                    key: ref[key]
                    for key in ("evidence_id", "unit_id", "source_id", "quote", "locator")
                    if key in ref
                }
            )
        gap_checksum = checksum(
            {
                "assessment_checksum": assessment_checksum,
                "node_id": result.node_id,
                "result_checksum": result.result_checksum,
                "evidence_refs": evidence_refs,
            }
        )
        session.add(
            KnowledgeGap(
                id=_stable_id("knowledge-gap", f"{assessment_id}:{result.node_id}"),
                assessment_id=assessment_id,
                node_id=result.node_id,
                priority=result.priority,
                severity=result.severity,
                importance=result.importance,
                eligible=result.eligible,
                metrics_json=result.metrics.model_dump(mode="json"),
                reason_codes_json=list(result.reason_codes),
                evidence_refs_json=evidence_refs,
                checksum=gap_checksum,
            )
        )
    await session.flush()
    return assessment
