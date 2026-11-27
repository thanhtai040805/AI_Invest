"""Pydantic schemas for SAG v2 Research Chat Orchestrator & Knowledge Graph API.

Aligned with PROJECT.md Contracts 3 & 4 (`ResearchChatMetadata` and HTTP/SSE routes).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1, max_length=32000)
    message_id: str | None = Field(default=None, max_length=128)
    turn_as_of_date: date | None = None
    primary_ticker: str | None = Field(default=None, max_length=32)
    expanded_tickers: list[str] = Field(default_factory=list, max_length=24)
    screened_tickers: list[str] = Field(default_factory=list, max_length=30)
    screening_summary: list[dict[str, Any]] = Field(default_factory=list, max_length=15)
    cited_tickers: list[str] = Field(default_factory=list, max_length=24)
    route_intent: str | None = Field(default=None, max_length=64)
    eval_eligible: bool | None = None
    user_concern: str | None = Field(default=None, max_length=500)
    answer_shape: str | None = Field(default=None, max_length=64)
    needs_clarification: bool | None = None
    clarifying_question: str | None = Field(default=None, max_length=800)
    session_rolling_summary: str | None = Field(default=None, max_length=16000)


class PortfolioPositionSnapshot(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    quantity: float | None = None
    avg_cost: float | None = None
    market_value: float | None = None
    weight_pct: float | None = None
    unrealized_pnl_pct: float | None = None
    account_label: str | None = Field(default=None, max_length=64)

    @field_validator("symbol", mode="before")
    @classmethod
    def _normalize_symbol(cls, v: Any) -> str:
        return str(v or "").strip().upper()


class UserPortfolioSnapshot(BaseModel):
    as_of_timestamp: str | None = None
    positions: list[PortfolioPositionSnapshot] = Field(default_factory=list, max_length=100)
    watchlists: list[str] = Field(default_factory=list, max_length=200)
    screener_presets: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class RecalledMemoryItem(BaseModel):
    badge_id: str
    memory_id: str
    memory_type: str
    pit_sensitivity: str = "MARKET_BOUND"
    content_text: str
    tickers: list[str] = Field(default_factory=list)
    source_authority: str = "USER_DECLARED"
    source_as_of_date: str | None = None
    staleness_status: str = "FRESH"
    staleness_reason: str | None = None
    pinned_by_user: bool = False
    score: float = 0.0
    origin_session_id: str | None = None


class LinkedSessionItem(BaseModel):
    badge_id: str
    episode_id: str
    session_id: str
    session_title: str | None = None
    episode_summary: str
    tickers: list[str] = Field(default_factory=list)
    macro_factors: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    relation_type: str = "SESSION_RELATED_THEME"
    link_score: float = 0.0
    source_as_of_date: str | None = None
    staleness_status: str = "FRESH"
    staleness_reason: str | None = None


class ResearchChatStreamRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=128)
    ticker: str | None = Field(default=None, max_length=32)
    compare_tickers: list[str] = Field(default_factory=list, max_length=16)
    mode: Literal["GENERAL", "MOAT"] = "GENERAL"
    as_of_date: date | None = None
    enable_kg: bool = True
    memory_mode: Literal["auto", "read_only", "incognito"] = "auto"
    user_portfolio_snapshot: UserPortfolioSnapshot | None = None
    history: list[ChatHistoryMessage] = Field(default_factory=list, max_length=120)

    @field_validator("ticker", mode="before")
    @classmethod
    def _normalize_optional_ticker(cls, v: Any) -> str | None:
        if v is None:
            return None
        s = str(v).strip().upper()
        return s or None


class CitationItem(BaseModel):
    citation_id: str
    ticker: str | None = None
    doc_id: str
    document_version_id: str
    sub_doc_slot: str
    page: int = Field(default=0, ge=0)
    published_date: str
    quote: str
    quote_hash: str
    verified: bool
    evidence_origin: Literal["PRIMARY", "CROSS_REF"] = "PRIMARY"
    rejection_reason: str | None = None


class HydratedTableItem(BaseModel):
    table_id: str
    project_id: str | None = None
    title: str
    sub_doc_slot: str
    fiscal_year: int | None = None
    fiscal_quarter: int | None = None
    page_from: int = Field(ge=0)
    page_to: int = Field(ge=0)
    currency: str = "VND"
    unit_raw: str = ""
    unit_multiplier: float = 1.0
    verification_status: str
    sanity_error_ratio: float | None = None
    headers: list[Any] = Field(default_factory=list)
    rows: list[Any] = Field(default_factory=list)
    column_roles: list[str] = Field(default_factory=list)
    amount_col_indices: list[int] = Field(default_factory=list)
    footnotes_text: str | None = None


class CalculatorTraceItem(BaseModel):
    calc_id: str
    formula: str
    description: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    result: float | None = None
    formatted: str


class GraphHopItem(BaseModel):
    layer: Literal["MACRO", "VALUE_CHAIN", "TRUE_PEER"]
    src_label: str
    relation_type: str
    dst_label: str
    beta: float | None = None
    p_value: float | None = None
    fdr_q_value: float | None = None
    tercile_stable: bool | None = None
    verification_tier: str
    effective_from: str


class DiscrepancyAlertItem(BaseModel):
    alert_id: str
    severity: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    metric: str
    narrative_claim: str
    audited_value: str
    delta_pct: float | None = None
    citations: list[str] = Field(default_factory=list)


class DeclaredBlindspotItem(BaseModel):
    code: str
    reason: str
    scope: str


class ResearchTelemetry(BaseModel):
    routing_slot: str | None = None
    route_intent: str | None = None
    route_domain_class: str | None = None
    route_budget_tier: str | None = None
    route_confidence: float | None = None
    playbook_id: str | None = None
    playbook_mode: str | None = None
    prefetched_tools: list[str] = Field(default_factory=list)
    exposed_tools: list[str] = Field(default_factory=list)
    eval_eligible: bool = True
    quarantined_future_turns: int = 0
    retrieval_mode: str = "SHARED_CHAMBER_HYBRID"
    reranker_provider: str | None = None
    reranker_model: str | None = None
    crag_grade: str | None = None
    agent_turns: int = 0
    tool_calls_count: int = 0
    primary_section_count: int = 0
    cross_ref_section_count: int = 0
    leiden_nodes_visited: int = 0
    hipporag_hops: int = 0
    rejected_citations_count: int = 0
    unsupported_numeric_claims_count: int = 0
    hallucinated_citation_ids_count: int = 0
    hallucinated_memory_ids_count: int = 0
    user_memory_mode: str = "off"
    memory_recall_need: str = "none"
    recalled_memory_count: int = 0
    llm_model: str | None = None
    embedding_model: str | None = None
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    thinking_duration_ms: int | None = None
    latency_ms: int = 0


class ResearchChatMetadata(BaseModel):
    mode: Literal["RESEARCH_CHAT_V2"] = "RESEARCH_CHAT_V2"
    chat_mode: Literal["GENERAL", "MOAT"] = "GENERAL"
    route_intent: str | None = None
    eval_eligible: bool = True
    primary_ticker: str = ""
    expanded_tickers: list[str] = Field(default_factory=list)
    resolved_query: str | None = None
    sub_queries: list[str] = Field(default_factory=list)
    as_of_date: str
    enable_kg: bool = True
    citations: list[CitationItem] = Field(default_factory=list)
    rejected_citations: list[CitationItem] = Field(default_factory=list)
    hydrated_tables: list[HydratedTableItem] = Field(default_factory=list)
    screening_results: list[dict[str, Any]] = Field(default_factory=list)
    tool_traces: list[dict[str, Any]] = Field(default_factory=list)
    activity_log: list[dict[str, Any]] = Field(default_factory=list)
    research_brief: dict[str, Any] | None = None
    verdict_cards: list[dict[str, Any]] = Field(default_factory=list)
    calculator_traces: list[CalculatorTraceItem] = Field(default_factory=list)
    graph_hops: list[GraphHopItem] = Field(default_factory=list)
    discrepancy_alerts: list[DiscrepancyAlertItem] = Field(default_factory=list)
    declared_blindspots: list[DeclaredBlindspotItem] = Field(default_factory=list)
    recalled_memories: list[RecalledMemoryItem] = Field(default_factory=list)
    linked_sessions: list[LinkedSessionItem] = Field(default_factory=list)
    portfolio_snapshot_used: bool = False
    session_rolling_summary: str | None = None
    telemetry: ResearchTelemetry = Field(default_factory=ResearchTelemetry)


class IssuerProfileItem(BaseModel):
    ticker: str
    company_name: str
    sector: str | None = None
    industry: str | None = None
    exchange: str = "HOSE"
    market_cap: float | None = None
    aliases: list[str] = Field(default_factory=list)
    latest_fiscal_year: int | None = None
    has_sag_docs: bool = True


class IngestedTickersResponse(BaseModel):
    organization_id: str
    tickers: list[str] = Field(default_factory=list)
    pilot_fallback: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    profiles: list[IssuerProfileItem] = Field(default_factory=list)
    catalog_source: Literal["CORE_DB_READONLY", "SAG_LOCAL_FALLBACK"] = "SAG_LOCAL_FALLBACK"


class KGSubgraphResponse(BaseModel):
    ticker: str
    as_of_date: str
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    macro_snapshots: list[dict[str, Any]] = Field(default_factory=list)
    true_peers: list[dict[str, Any]] = Field(default_factory=list)


class HydratedTableResponse(BaseModel):
    table_id: str
    project_id: str
    document_version_id: str
    sub_doc_slot: str
    fiscal_year: int | None = None
    fiscal_quarter: int = 0
    title: str
    section_path: str = ""
    page_from: int = 0
    page_to: int = 0
    currency: str = "VND"
    unit_raw: str = ""
    unit_multiplier: float = 1.0
    headers: list[Any] = Field(default_factory=list)
    rows: list[Any] = Field(default_factory=list)
    column_roles: list[str] = Field(default_factory=list)
    amount_col_indices: list[int] = Field(default_factory=list)
    footnotes_text: str | None = None
    sanity_error_ratio: float | None = None
    verification_status: str
    table_hash: str
    valid_from: str


class PilotIngestRequest(BaseModel):
    tickers: list[str] = Field(min_length=1, max_length=12)
    as_of_date: date | None = None
    source_paths: dict[str, str] = Field(default_factory=dict)


class PilotIngestItemResult(BaseModel):
    ticker: str
    status: Literal["READY", "MISSING_SOURCE", "FAILED"]
    branch: str | None = None
    pages_total: int = 0
    detail: str = ""


class PilotIngestResponse(BaseModel):
    as_of_date: str
    results: list[PilotIngestItemResult] = Field(default_factory=list)


class BatchIngestRequest(BaseModel):
    tier: Literal["pilot_8", "a5_36", "a4_81", "a3_buffer_139", "custom"] = "pilot_8"
    tickers: list[str] = Field(default_factory=list, max_length=256)
    as_of_date: date | None = None
    years_back: int = Field(default=3, ge=1, le=6)
    dry_run: bool = True
    enable_llm_toc: bool = False
    refresh_kg: bool = True
    source_paths: dict[str, str] = Field(default_factory=dict)


class BatchIngestTickerReportSchema(BaseModel):
    ticker: str
    status: Literal["INGESTED", "DRY_RUN_READY", "NO_DOCUMENTS", "FAILED"]
    selected_documents: int = 0
    rejected_documents: int = 0
    branches: list[str] = Field(default_factory=list)
    localization_methods: list[str] = Field(default_factory=list)
    sub_doc_slots: list[str] = Field(default_factory=list)
    missing_core_slots: list[str] = Field(default_factory=list)
    ocr_pages_used: int = 0
    uploaded_sub_docs: int = 0
    tables_extracted: int = 0
    tables_verified: int = 0
    tables_unverified: int = 0
    bctc_notes_coverage_pct: float = 100.0
    noise_reduction_pct: float = 0.0
    errors: list[str] = Field(default_factory=list)


class BatchIngestResponse(BaseModel):
    tier: str
    as_of_date: str
    dry_run: bool
    resolved_tickers: list[str] = Field(default_factory=list)
    total_sub_docs_uploaded: int = 0
    total_ocr_pages: int = 0
    reports: list[BatchIngestTickerReportSchema] = Field(default_factory=list)
    kg_refresh: dict[str, Any] | None = None


# ── SAG-Native User Memory & Linked Sessions REST Contracts ───────────────────


class MemoryIngestTurnRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    parent_session_id: str | None = Field(default=None, max_length=128)
    session_title: str | None = Field(default=None, max_length=256)
    chat_mode: Literal["GENERAL", "MOAT"] = "GENERAL"
    user_message_id: str | None = Field(default=None, max_length=128)
    assistant_message_id: str = Field(min_length=1, max_length=128)
    user_content: str = Field(min_length=1, max_length=16000)
    assistant_content: str = Field(default="", max_length=32000)
    turn_index: int = Field(default=1, ge=1)
    turn_as_of_date: date | None = None
    memory_mode: Literal["auto", "read_only", "incognito"] = "auto"
    tickers: list[str] = Field(default_factory=list, max_length=32)
    sectors: list[str] = Field(default_factory=list, max_length=24)
    macro_factors: list[str] = Field(default_factory=list, max_length=24)
    cited_doc_version_ids: list[str] = Field(default_factory=list, max_length=64)
    blindspot_codes: list[str] = Field(default_factory=list, max_length=32)
    stage7_hallucinated: bool = False
    stream_interrupted: bool = False


class MemoryIngestTurnResponse(BaseModel):
    accepted: bool
    job_id: str | None = None
    deduplicated: bool = False
    is_backtest_sandbox: bool = False
    assistant_clean_for_digest: bool = True
    reason: str | None = None


class CascadeDeleteSessionRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    reason: str | None = Field(default="USER_DELETED_SESSION", max_length=256)


class CascadeDeleteSessionResponse(BaseModel):
    session_id: str
    tombstoned: bool
    retracted_memories: int = 0
    closed_edges: int = 0
    job_id: str | None = None


class UserMemoryItemResponse(BaseModel):
    memory_id: str
    scope: str
    memory_type: str
    pit_sensitivity: str
    canonical_slot_key: str | None = None
    content_text: str
    structured_payload: dict[str, Any] = Field(default_factory=dict)
    tickers: list[str] = Field(default_factory=list)
    bound_doc_version_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.85
    source_authority: str = "USER_DECLARED"
    origin_episode_id: str | None = None
    origin_session_id: str | None = None
    source_as_of_date: str
    is_backtest_sandbox: bool = False
    valid_from: str
    valid_to: str
    tx_from: str
    tx_to: str
    supersedes_memory_id: str | None = None
    staleness_status: str = "FRESH"
    staleness_reason: str | None = None
    reinforcement_count: int = 1
    pinned_by_user: bool = False
    qdrant_sync_status: str = "PENDING_UPSERT"
    sync_version: int = 1
    created_at: str
    updated_at: str


class UserMemoryListResponse(BaseModel):
    total: int
    items: list[UserMemoryItemResponse] = Field(default_factory=list)


class UserMemoryPatchRequest(BaseModel):
    content_text: str | None = Field(default=None, min_length=1, max_length=2000)
    pinned_by_user: bool | None = None
    reason: str | None = Field(default=None, max_length=256)


class UserMemoryClearResponse(BaseModel):
    retracted_memories: int = 0
    deleted_episodes: int = 0
    closed_edges: int = 0


class RelatedSessionsResponse(BaseModel):
    session_id: str
    episode_id: str | None = None
    staleness_status: str = "FRESH"
    staleness_reason: str | None = None
    related_sessions: list[LinkedSessionItem] = Field(default_factory=list)
