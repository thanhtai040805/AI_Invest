"""Pydantic schemas for SAG v2 Research Chat Orchestrator & Knowledge Graph API.

Aligned with PROJECT.md Contracts 3 & 4 (`ResearchChatMetadata` and HTTP/SSE routes).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1, max_length=32000)


class ResearchChatStreamRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    ticker: str = Field(min_length=1, max_length=32)
    compare_tickers: list[str] = Field(default_factory=list, max_length=16)
    as_of_date: date | None = None
    enable_kg: bool = True
    history: list[ChatHistoryMessage] = Field(default_factory=list, max_length=40)


class CitationItem(BaseModel):
    citation_id: str
    doc_id: str
    document_version_id: str
    sub_doc_slot: str
    page: int = Field(ge=1)
    published_date: str
    quote: str
    quote_hash: str
    verified: bool


class HydratedTableItem(BaseModel):
    table_id: str
    title: str
    sub_doc_slot: str
    page_from: int = Field(ge=0)
    page_to: int = Field(ge=0)
    currency: str = "VND"
    unit_raw: str = ""
    unit_multiplier: float = 1.0
    verification_status: str
    sanity_error_ratio: float | None = None
    headers: list[Any] = Field(default_factory=list)
    rows: list[Any] = Field(default_factory=list)
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
    leiden_nodes_visited: int = 0
    hipporag_hops: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


class ResearchChatMetadata(BaseModel):
    mode: Literal["RESEARCH_CHAT_V2"] = "RESEARCH_CHAT_V2"
    primary_ticker: str
    expanded_tickers: list[str] = Field(default_factory=list)
    as_of_date: str
    enable_kg: bool = True
    citations: list[CitationItem] = Field(default_factory=list)
    hydrated_tables: list[HydratedTableItem] = Field(default_factory=list)
    calculator_traces: list[CalculatorTraceItem] = Field(default_factory=list)
    graph_hops: list[GraphHopItem] = Field(default_factory=list)
    discrepancy_alerts: list[DiscrepancyAlertItem] = Field(default_factory=list)
    declared_blindspots: list[DeclaredBlindspotItem] = Field(default_factory=list)
    telemetry: ResearchTelemetry = Field(default_factory=ResearchTelemetry)


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
    refresh_kg: bool = False
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
