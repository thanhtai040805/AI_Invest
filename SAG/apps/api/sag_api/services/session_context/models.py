"""Typed data models for the SAG Evidence-Centric Context Engine (SAG-ECCE)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EpistemicStatus = Literal[
    "FACT",
    "PROPOSED",
    "CONSIDERING",
    "CONFIRMED",
    "SUPERSEDED",
    "NEGATED",
    "OPEN_ISSUE",
]

AblationMode = Literal[
    "V0_legacy",
    "V1_hybrid",
    "V2_graph_ppr",
    "V3_dual_hierarchy",
    "V4_closure_temporal",
    "V5_ecce_full",
]


@dataclass(frozen=True)
class EntityMention:
    """Canonicalized entity or technical anchor extracted with 0 LLM tokens."""

    key: str
    surface: str
    soft_aliases: tuple[str, ...] = ()
    confidence: float = 0.9


@dataclass(frozen=True)
class IndexedSentence:
    """Sentence unit used for Zero-Mem query-conditioned entity-sentence gating."""

    sentence_id: str
    turn_id: str
    ordinal: int
    text: str
    entity_keys: tuple[str, ...]
    dense_vector: tuple[float, ...]


@dataclass
class IndexedTurn:
    """Lossless turn representation enriched with multi-view token-free features."""

    turn_id: str
    thread_id: str
    ordinal: int
    role: str
    content: str
    created_at_ts: float
    created_at_iso: str
    token_count: int
    content_hash: str
    episode_id: str
    window_id: str
    dense_vector: tuple[float, ...]
    sparse_bm25: dict[int, float]
    content_terms: frozenset[str]
    entities: tuple[EntityMention, ...]
    sentences: tuple[IndexedSentence, ...]
    term_freqs: dict[str, int] = field(default_factory=dict)
    doc_word_len: int = 1
    epistemic_status: EpistemicStatus = "FACT"
    topic_slot_key: str | None = None
    superseded_by_turn_id: str | None = None
    supersedes_turn_ids: tuple[str, ...] = ()
    superseded_entity_keys: tuple[str, ...] = ()
    is_anaphoric_or_short: bool = False
    cited_source_ids: tuple[str, ...] = ()


@dataclass
class SlidingWindowNode:
    """3-turn overlapping local trigram window inside an Episode."""

    window_id: str
    episode_id: str
    ordinal: int
    turn_ids: tuple[str, ...]
    dense_vector: tuple[float, ...]
    content_terms: frozenset[str]


@dataclass
class EpisodeSegment:
    """Semantic-lexical drift episode grouping contiguous coherent turns."""

    episode_id: str
    thread_id: str
    ordinal: int
    turn_ids: list[str] = field(default_factory=list)
    window_ids: list[str] = field(default_factory=list)
    centroid: list[float] = field(default_factory=list)
    content_terms: set[str] = field(default_factory=set)
    start_ts: float = 0.0
    end_ts: float = 0.0


@dataclass
class DecisionLedgerEntry:
    """Bi-temporal working & decision state entry ([valid_from, valid_to))."""

    entry_id: str
    topic_slot_key: str
    turn_id: str
    turn_ordinal: int
    epistemic_status: EpistemicStatus
    summary_text: str
    entity_keys: tuple[str, ...]
    companion_turn_ids: tuple[str, ...] = ()
    valid_from_ts: float = 0.0
    valid_to_ts: float | None = None
    superseded_by_turn_id: str | None = None
    supersedes_turn_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SelectedEvidenceGroup:
    """Closed evidence bundle admitted by the Submodular Budget Optimizer."""

    citation_tag: str
    primary_turn_id: str
    included_turn_ids: tuple[str, ...]
    closure_reason: str
    epistemic_status: EpistemicStatus
    utility_score: float
    redundancy_penalty: float
    coverage_gain: float
    marginal_gain_per_token: float
    token_cost: int


@dataclass(frozen=True)
class RejectedCandidateTrace:
    """Explainable audit entry for a candidate excluded from LLM context."""

    turn_id: str
    turn_ordinal: int
    raw_score: float
    epistemic_status: EpistemicStatus
    reason_code: str
    detail: str


@dataclass
class ContextAuditTrace:
    """Self-auditing retrieval & budget trace explaining every context decision."""

    mode: str
    ablation: AblationMode
    memory_scope: str
    temporal_mode: str
    router_model: str
    router_fallback_used: bool
    total_indexed_turns: int
    episode_count: int
    active_decisions_count: int
    budget_tokens: int
    used_evidence_tokens: int
    used_recent_tokens: int
    total_output_history_tokens: int
    llm_memory_calls: int
    partial_coverage: bool = False
    route_decision: dict[str, Any] = field(default_factory=dict)
    view_weights: dict[str, float] = field(default_factory=dict)
    selected_groups: list[SelectedEvidenceGroup] = field(default_factory=list)
    recent_turn_ids: tuple[str, ...] = ()
    rejected_candidates: list[RejectedCandidateTrace] = field(default_factory=list)
    latency_ms: float = 0.0
    persistence_status: str = "memory_only"
    persisted_units_written: int = 0
    qdrant_points_upserted: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "ablation": self.ablation,
            "memory_scope": self.memory_scope,
            "temporal_mode": self.temporal_mode,
            "router_model": self.router_model,
            "router_fallback_used": self.router_fallback_used,
            "total_indexed_turns": self.total_indexed_turns,
            "episode_count": self.episode_count,
            "active_decisions_count": self.active_decisions_count,
            "budget_tokens": self.budget_tokens,
            "used_evidence_tokens": self.used_evidence_tokens,
            "used_recent_tokens": self.used_recent_tokens,
            "total_output_history_tokens": self.total_output_history_tokens,
            "llm_memory_calls": self.llm_memory_calls,
            "partial_coverage": self.partial_coverage,
            "view_weights": dict(self.view_weights),
            "selected_groups": [
                {
                    "citation_tag": g.citation_tag,
                    "primary_turn_id": g.primary_turn_id,
                    "included_turn_ids": list(g.included_turn_ids),
                    "closure_reason": g.closure_reason,
                    "epistemic_status": g.epistemic_status,
                    "utility_score": round(g.utility_score, 4),
                    "redundancy_penalty": round(g.redundancy_penalty, 4),
                    "coverage_gain": round(g.coverage_gain, 4),
                    "marginal_gain_per_token": round(g.marginal_gain_per_token, 4),
                    "token_cost": g.token_cost,
                }
                for g in self.selected_groups
            ],
            "recent_turn_ids": list(self.recent_turn_ids),
            "rejected_candidates": [
                {
                    "turn_id": r.turn_id,
                    "turn_ordinal": r.turn_ordinal,
                    "raw_score": round(r.raw_score, 4),
                    "epistemic_status": r.epistemic_status,
                    "reason_code": r.reason_code,
                    "detail": r.detail,
                }
                for r in self.rejected_candidates
            ],
            "latency_ms": round(self.latency_ms, 2),
            "persistence_status": self.persistence_status,
            "persisted_units_written": self.persisted_units_written,
            "qdrant_points_upserted": self.qdrant_points_upserted,
        }


@dataclass
class SessionContextPack:
    """Output of the Session Context Engine ready for AskPlan construction."""

    history_messages: list[dict[str, str]]
    selected_turn_ids: list[str]
    session_citations: list[dict[str, Any]]
    audit_trace: ContextAuditTrace
