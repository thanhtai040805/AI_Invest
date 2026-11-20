"""Deterministic, versioned knowledge-quality metrics and gap scoring."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sag_api.services.knowledge_service import checksum


class EvidenceSignal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str = Field(min_length=1)
    quote: str
    locator: str
    confidence: float = Field(ge=0.0, le=1.0)

    @property
    def anchored(self) -> bool:
        return bool(self.quote.strip() and self.locator.strip())


class UnitSignal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    unit_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    published_at: datetime | None = None
    observed_at: datetime | None = None
    ingested_at: datetime | None = None
    evidence: tuple[EvidenceSignal, ...] = ()

    @property
    def freshness_timestamp(self) -> datetime | None:
        for timestamp in (self.published_at, self.observed_at, self.ingested_at):
            if timestamp is not None:
                return timestamp.astimezone(UTC) if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)
        return None


class ContradictionSignal(BaseModel):
    """A persisted CONTRADICTS candidate with explicit evidence linkage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    relation_id: str = Field(min_length=1)
    source_unit_id: str = Field(min_length=1)
    target_unit_id: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)


class QualityTopicInput(BaseModel):
    """Immutable input snapshot for one tenant/project/partition/tree node."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    security_partition_id: str = Field(min_length=1)
    tree_version: str = Field(min_length=1)
    tree_manifest_checksum: str = "fixture-manifest"
    node_profile_checksum: str = "fixture-profile"
    node_id: str = Field(min_length=1)
    as_of: datetime
    demand_window_days: int = Field(gt=0)
    query_demand_current: int | None = Field(default=None, ge=0)
    query_demand_previous: int | None = Field(default=None, ge=0)
    units: tuple[UnitSignal, ...] = Field(min_length=1)
    contradictions: tuple[ContradictionSignal, ...] = ()

    @model_validator(mode="after")
    def validate_snapshot(self) -> QualityTopicInput:
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must include a timezone")
        unit_ids = [unit.unit_id for unit in self.units]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("topic unit IDs must be unique")
        evidence_ids = [evidence.evidence_id for unit in self.units for evidence in unit.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("topic evidence IDs must be unique")
        unit_id_set = set(unit_ids)
        evidence_id_set = set(evidence_ids)
        relation_ids: set[str] = set()
        for relation in self.contradictions:
            if relation.relation_id in relation_ids:
                raise ValueError("contradiction relation IDs must be unique")
            relation_ids.add(relation.relation_id)
            if (
                relation.source_unit_id not in unit_id_set
                or relation.target_unit_id not in unit_id_set
                or relation.source_unit_id == relation.target_unit_id
                or not set(relation.evidence_ids).issubset(evidence_id_set)
            ):
                raise ValueError("contradiction evidence is outside topic scope")
        return self


class KnowledgeQualityConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = "knowledge-quality-v2"
    metric_definition_version: str = "knowledge-quality-metrics-v2"
    scoring_formula_version: str = "knowledge-gap-priority-v1"
    coverage_weight: float = Field(default=0.30, ge=0.0)
    source_diversity_weight: float = Field(default=0.15, ge=0.0)
    freshness_weight: float = Field(default=0.15, ge=0.0)
    contradiction_weight: float = Field(default=0.25, ge=0.0)
    uncertainty_weight: float = Field(default=0.15, ge=0.0)
    demand_weight: float = Field(default=0.80, ge=0.0)
    growth_weight: float = Field(default=0.20, ge=0.0)
    freshness_half_life_days: float = Field(default=90.0, gt=0.0)
    query_demand_daily_cap: float = Field(default=50.0, gt=0.0)
    demand_growth_cap: float = Field(default=2.0, gt=0.0)
    minimum_priority: float = Field(default=0.20, ge=0.0, le=1.0)
    low_source_diversity_threshold: float = Field(default=0.50, ge=0.0, le=1.0)
    stale_freshness_threshold: float = Field(default=0.50, ge=0.0, le=1.0)
    high_uncertainty_threshold: float = Field(default=0.50, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_weights(self) -> KnowledgeQualityConfig:
        severity_weights = (
            self.coverage_weight
            + self.source_diversity_weight
            + self.freshness_weight
            + self.contradiction_weight
            + self.uncertainty_weight
        )
        importance_weights = self.demand_weight + self.growth_weight
        if not math.isclose(severity_weights, 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("severity weights must sum to 1")
        if not math.isclose(importance_weights, 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("demand and growth weights must sum to 1")
        return self


class KnowledgeQualityMetrics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    coverage: float = Field(ge=0.0, le=1.0)
    source_diversity: float = Field(ge=0.0, le=1.0)
    freshness: float | None = Field(default=None, ge=0.0, le=1.0)
    contradiction_candidate_rate: float = Field(ge=0.0, le=1.0)
    uncertainty_proxy: float = Field(ge=0.0, le=1.0)
    query_demand: float | None = Field(default=None, ge=0.0, le=1.0)
    demand_growth: float | None = Field(default=None, ge=0.0, le=1.0)


class KnowledgeQualityResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    project_id: str
    security_partition_id: str
    tree_version: str
    node_id: str
    as_of: datetime
    metric_definition_version: str
    scoring_formula_version: str
    config_version: str
    config_checksum: str
    input_checksum: str
    result_checksum: str
    metrics: KnowledgeQualityMetrics
    severity: float | None
    importance: float | None
    priority: float | None
    eligible: bool
    supporting_evidence_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]


def _normalized_entropy(counts: list[int]) -> float:
    nonzero = [count for count in counts if count > 0]
    if len(nonzero) <= 1:
        return 0.0
    total = sum(nonzero)
    entropy = -sum((count / total) * math.log(count / total) for count in nonzero)
    return min(1.0, max(0.0, entropy / math.log(len(nonzero))))


def _freshness(
    units: tuple[UnitSignal, ...],
    *,
    as_of: datetime,
    half_life_days: float,
) -> tuple[float | None, bool]:
    as_of_utc = as_of.astimezone(UTC)
    values: list[float] = []
    had_future_timestamp = False
    missing_timestamp = False
    for unit in units:
        timestamp = unit.freshness_timestamp
        if timestamp is None:
            missing_timestamp = True
            continue
        age_days = (as_of_utc - timestamp).total_seconds() / 86400
        if age_days < 0:
            had_future_timestamp = True
            age_days = 0.0
        values.append(math.exp(-math.log(2) * age_days / half_life_days))
    if not values or missing_timestamp:
        return None, had_future_timestamp
    return sum(values) / len(values), had_future_timestamp


def _demand_metrics(
    topic: QualityTopicInput,
    config: KnowledgeQualityConfig,
) -> tuple[float | None, float | None]:
    if topic.query_demand_current is None or topic.query_demand_previous is None:
        return None, None
    current_per_day = topic.query_demand_current / topic.demand_window_days
    previous_per_day = topic.query_demand_previous / topic.demand_window_days
    demand = math.log1p(current_per_day) / math.log1p(config.query_demand_daily_cap)
    demand = min(1.0, max(0.0, demand))
    growth_ratio = max(0.0, (current_per_day - previous_per_day) / max(previous_per_day, 1.0))
    growth = min(1.0, growth_ratio / config.demand_growth_cap)
    return demand, growth


def score_topic(
    topic: QualityTopicInput,
    config: KnowledgeQualityConfig = KnowledgeQualityConfig(),
) -> KnowledgeQualityResult:
    """Compute versioned, deterministic metrics for one node without external calls."""
    units = tuple(sorted(topic.units, key=lambda unit: unit.unit_id))
    anchored_evidence = tuple(
        sorted(
            (
                evidence
                for unit in units
                for evidence in unit.evidence
                if evidence.anchored
            ),
            key=lambda evidence: evidence.evidence_id,
        )
    )
    supporting_evidence_ids = tuple(evidence.evidence_id for evidence in anchored_evidence)
    anchored_unit_ids = {
        unit.unit_id
        for unit in units
        if any(evidence.anchored for evidence in unit.evidence)
    }
    coverage = len(anchored_unit_ids) / len(units)
    source_counts: dict[str, int] = {}
    for unit in units:
        if unit.unit_id in anchored_unit_ids:
            source_counts[unit.source_id] = source_counts.get(unit.source_id, 0) + 1
    source_diversity = _normalized_entropy(list(source_counts.values()))
    freshness, had_future_timestamp = _freshness(
        units,
        as_of=topic.as_of,
        half_life_days=config.freshness_half_life_days,
    )
    possible_pairs = len(units) * (len(units) - 1) / 2
    relation_pairs = {
        tuple(sorted((relation.source_unit_id, relation.target_unit_id)))
        for relation in topic.contradictions
    }
    contradiction_rate = (
        min(1.0, len(relation_pairs) / possible_pairs)
        if possible_pairs
        else 0.0
    )
    uncertainty_proxy = (
        sum(1.0 - evidence.confidence for evidence in anchored_evidence) / len(anchored_evidence)
        if anchored_evidence
        else 1.0
    )
    query_demand, demand_growth = _demand_metrics(topic, config)
    freshness_deficit = 1.0 - freshness if freshness is not None else 1.0
    severity = None
    if freshness is not None:
        severity = (
            config.coverage_weight * (1.0 - coverage)
            + config.source_diversity_weight * (1.0 - source_diversity)
            + config.freshness_weight * freshness_deficit
            + config.contradiction_weight * contradiction_rate
            + config.uncertainty_weight * uncertainty_proxy
        )
        severity = min(1.0, max(0.0, severity))
    importance = (
        config.demand_weight * query_demand + config.growth_weight * demand_growth
        if query_demand is not None and demand_growth is not None
        else None
    )
    priority = (
        round(severity * importance, 12)
        if severity is not None and importance is not None
        else None
    )

    reason_codes: list[str] = []
    if coverage < 1.0:
        reason_codes.append("ANCHORED_EVIDENCE_COVERAGE_GAP")
    if source_diversity < config.low_source_diversity_threshold:
        reason_codes.append("LOW_SOURCE_DIVERSITY")
    if freshness is None:
        reason_codes.append("FRESHNESS_TIMESTAMP_UNAVAILABLE")
    elif freshness < config.stale_freshness_threshold:
        reason_codes.append("STALE_EVIDENCE")
    if relation_pairs:
        reason_codes.append("CONTRADICTION_CANDIDATE")
    if uncertainty_proxy >= config.high_uncertainty_threshold:
        reason_codes.append("UNCALIBRATED_UNCERTAINTY_PROXY_HIGH")
    if query_demand is None:
        reason_codes.append("QUERY_DEMAND_UNAVAILABLE")
    elif query_demand > 0:
        reason_codes.append("QUERY_DEMAND_PRESENT")
    if demand_growth is not None and demand_growth > 0:
        reason_codes.append("QUERY_DEMAND_GROWING")
    if had_future_timestamp:
        reason_codes.append("FUTURE_EVIDENCE_TIMESTAMP_CLAMPED")
    if not supporting_evidence_ids:
        reason_codes.append("NO_SUPPORTING_EVIDENCE")
    eligible = (
        priority is not None
        and priority >= config.minimum_priority
        and bool(supporting_evidence_ids)
    )
    if priority is not None and priority < config.minimum_priority:
        reason_codes.append("PRIORITY_BELOW_THRESHOLD")

    input_checksum = checksum(topic.model_dump(mode="json"))
    config_checksum = checksum(config.model_dump(mode="json"))
    payload = {
        "scope": {
            "tenant_id": topic.tenant_id,
            "project_id": topic.project_id,
            "security_partition_id": topic.security_partition_id,
            "tree_version": topic.tree_version,
            "node_id": topic.node_id,
            "as_of": topic.as_of.astimezone(UTC).isoformat(),
        },
        "metric_definition_version": config.metric_definition_version,
        "scoring_formula_version": config.scoring_formula_version,
        "config_checksum": config_checksum,
        "input_checksum": input_checksum,
        "metrics": {
            "coverage": round(coverage, 12),
            "source_diversity": round(source_diversity, 12),
            "freshness": round(freshness, 12) if freshness is not None else None,
            "contradiction_candidate_rate": round(contradiction_rate, 12),
            "uncertainty_proxy": round(uncertainty_proxy, 12),
            "query_demand": round(query_demand, 12) if query_demand is not None else None,
            "demand_growth": round(demand_growth, 12) if demand_growth is not None else None,
        },
        "severity": round(severity, 12) if severity is not None else None,
        "importance": round(importance, 12) if importance is not None else None,
        "priority": priority,
        "eligible": eligible,
        "supporting_evidence_ids": supporting_evidence_ids,
        "reason_codes": reason_codes,
    }
    return KnowledgeQualityResult(
        tenant_id=topic.tenant_id,
        project_id=topic.project_id,
        security_partition_id=topic.security_partition_id,
        tree_version=topic.tree_version,
        node_id=topic.node_id,
        as_of=topic.as_of.astimezone(UTC),
        metric_definition_version=config.metric_definition_version,
        scoring_formula_version=config.scoring_formula_version,
        config_version=config.version,
        config_checksum=config_checksum,
        input_checksum=input_checksum,
        result_checksum=checksum(payload),
        metrics=KnowledgeQualityMetrics(
            coverage=round(coverage, 12),
            source_diversity=round(source_diversity, 12),
            freshness=round(freshness, 12) if freshness is not None else None,
            contradiction_candidate_rate=round(contradiction_rate, 12),
            uncertainty_proxy=round(uncertainty_proxy, 12),
            query_demand=round(query_demand, 12) if query_demand is not None else None,
            demand_growth=round(demand_growth, 12) if demand_growth is not None else None,
        ),
        severity=round(severity, 12) if severity is not None else None,
        importance=round(importance, 12) if importance is not None else None,
        priority=priority,
        eligible=eligible,
        supporting_evidence_ids=supporting_evidence_ids,
        reason_codes=tuple(reason_codes),
    )
