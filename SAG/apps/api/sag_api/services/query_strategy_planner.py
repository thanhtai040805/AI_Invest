"""Deterministic bridge from query features to retrieval strategy modes and 7-axis RetrievalPlan."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

from sag_api.services.query_analysis import QueryFeatures, extract_query_features
from sag_api.services.retrieval_plan import (
    DecompositionMode,
    DecompositionSpec,
    FieldPredicate,
    RetrievalPlan,
    build_retrieval_plan,
)


class RetrievalMode(StrEnum):
    EXACT = "EXACT"
    LOCAL_FACTUAL = "LOCAL_FACTUAL"
    ENTITY_RELATIONAL = "ENTITY_RELATIONAL"
    TEMPORAL = "TEMPORAL"
    GLOBAL_TOPIC = "GLOBAL_TOPIC"
    MULTI_HOP = "MULTI_HOP"


PLANNER_VERSION = "qsp-v2"


@dataclass(frozen=True, slots=True)
class QueryStrategyPlan:
    planner_version: str
    primary_strategy: RetrievalMode
    modifiers: tuple[RetrievalMode, ...]
    requested_strategy: RetrievalMode | None
    reason_codes: tuple[str, ...]
    multi_hop_eligible: bool
    retrieval_plan: RetrievalPlan = field(
        default_factory=lambda: build_retrieval_plan("")
    )

    def as_trace(self) -> dict[str, object]:
        return {
            "planner_version": self.planner_version,
            "requested_strategy": self.requested_strategy.value if self.requested_strategy else "AUTO",
            "effective_strategy": self.primary_strategy.value,
            "modifiers": [mode.value for mode in self.modifiers],
            "reason_codes": list(self.reason_codes),
            "multi_hop_eligible": self.multi_hop_eligible,
            "retrieval_plan": self.retrieval_plan.as_trace(),
        }


def _normalize_mode(value: RetrievalMode | str | None) -> RetrievalMode | None:
    if value is None:
        return None
    if isinstance(value, RetrievalMode):
        return value
    try:
        return RetrievalMode(str(value).upper())
    except ValueError as error:
        raise ValueError("Unsupported retrieval mode") from error


def plan_query(
    query: str,
    *,
    requested_mode: RetrievalMode | str | None = None,
    features: QueryFeatures | None = None,
    requested_top_k: int | None = None,
    answer_shape: str | None = None,
    constraints: tuple[FieldPredicate, ...] = (),
) -> QueryStrategyPlan:
    """Select a stable primary mode and compile the 7-axis RetrievalPlan."""
    effective_features = features or extract_query_features(query)
    requested = _normalize_mode(requested_mode)
    reasons: list[str] = []
    modifiers: list[RetrievalMode] = []
    if requested is not None:
        primary = requested
        reasons.append("EXPLICIT_MODE_OVERRIDE")
        if requested == RetrievalMode.MULTI_HOP:
            reasons.append("MULTI_HOP_EXPLICITLY_REQUESTED")
    elif effective_features.exact_terms or effective_features.identifier_terms or effective_features.path_terms:
        primary = RetrievalMode.EXACT
        if effective_features.exact_terms:
            reasons.append("EXACT_PHRASE_PRESENT")
        if effective_features.identifier_terms:
            reasons.append("EXACT_IDENTIFIER_PRESENT")
        if effective_features.path_terms:
            reasons.append("EXACT_PATH_PRESENT")
    elif effective_features.relation_cues:
        primary = RetrievalMode.ENTITY_RELATIONAL
        reasons.append("RELATION_CUE_PRESENT")
    elif effective_features.global_cues:
        primary = RetrievalMode.GLOBAL_TOPIC
        reasons.append("GLOBAL_TOPIC_CUE_PRESENT")
    elif effective_features.temporal_cues:
        primary = RetrievalMode.TEMPORAL
        reasons.append("TEMPORAL_CUE_PRESENT")
    else:
        primary = RetrievalMode.LOCAL_FACTUAL
        reasons.append("DEFAULT_LOCAL_FACTUAL")

    if effective_features.temporal_cues and primary != RetrievalMode.TEMPORAL:
        modifiers.append(RetrievalMode.TEMPORAL)
        reasons.append("TEMPORAL_MODIFIER_PRESENT")
    if effective_features.multi_hop:
        reasons.append("MULTI_HOP_CANDIDATE")

    multi_hop_eligible = requested == RetrievalMode.MULTI_HOP or effective_features.multi_hop
    exact_lookup = bool(
        effective_features.exact_terms
        or effective_features.identifier_terms
        or effective_features.path_terms
    )
    retrieval_plan = build_retrieval_plan(
        query,
        requested_top_k=requested_top_k,
        answer_shape=answer_shape,
        strategy_mode=primary.value,
        clauses=effective_features.clauses,
        multi_hop=multi_hop_eligible,
        exact_lookup=exact_lookup,
        constraints=constraints,
    )
    if retrieval_plan.target.value == "DOCUMENT":
        reasons.append(f"TARGET_DOCUMENT_{retrieval_plan.output.value}")
    if retrieval_plan.cardinality.count_source.value != "DEFAULT":
        reasons.append(f"CARDINALITY_{retrieval_plan.cardinality.count_source.value}")

    return QueryStrategyPlan(
        planner_version=PLANNER_VERSION,
        primary_strategy=primary,
        modifiers=tuple(modifiers),
        requested_strategy=requested,
        reason_codes=tuple(dict.fromkeys(reasons)),
        multi_hop_eligible=multi_hop_eligible,
        retrieval_plan=retrieval_plan,
    )


def promote_multi_hop(
    plan: QueryStrategyPlan,
    *,
    coverage_reason: str | None,
) -> QueryStrategyPlan:
    """Escalate only after a structural coverage check supplies a reason."""
    if not plan.multi_hop_eligible or not coverage_reason:
        return plan
    updated_retrieval_plan = plan.retrieval_plan
    if updated_retrieval_plan.decomposition.mode == DecompositionMode.NONE:
        updated_retrieval_plan = replace(
            updated_retrieval_plan,
            decomposition=DecompositionSpec(
                mode=DecompositionMode.SEQUENTIAL,
                subqueries=updated_retrieval_plan.decomposition.subqueries,
            ),
        )
    return QueryStrategyPlan(
        planner_version=plan.planner_version,
        primary_strategy=RetrievalMode.MULTI_HOP,
        modifiers=plan.modifiers,
        requested_strategy=plan.requested_strategy,
        reason_codes=tuple(dict.fromkeys((*plan.reason_codes, "MULTI_HOP_COVERAGE_ESCALATION", coverage_reason))),
        multi_hop_eligible=True,
        retrieval_plan=updated_retrieval_plan,
    )
