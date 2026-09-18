from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sag_api.enums import DocumentRole


class DocumentCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    markdown: str | None = None
    object_uri: str | None = Field(default=None, max_length=1024)
    content_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    canonical_markdown_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    doc_role: DocumentRole
    fiscal_year: int | None = Field(default=None, ge=1900, le=2200)
    fiscal_quarter: int | None = Field(default=None, ge=1, le=4)
    period_start: date | None = None
    period_end: date | None = None
    activate: bool = True
    processing_mode: Literal["FULL", "OCR_ONLY"] = "FULL"
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentObjectCreateIn(BaseModel):
    """Create a document from a PDF object already stored in R2."""

    title: str = Field(min_length=1, max_length=512)
    object_uri: str = Field(min_length=1, max_length=1024)
    doc_role: DocumentRole
    fiscal_year: int | None = Field(default=None, ge=1900, le=2200)
    fiscal_quarter: int | None = Field(default=None, ge=1, le=4)
    period_start: date | None = None
    period_end: date | None = None
    activate: bool = False
    processing_mode: Literal["FULL", "OCR_ONLY"] = "OCR_ONLY"
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentSourceCreateIn(BaseModel):
    """Queue OCR directly from a public source URL without storing the PDF."""

    title: str = Field(min_length=1, max_length=512)
    source_url: str = Field(min_length=8, max_length=4096)
    doc_role: DocumentRole
    fiscal_year: int | None = Field(default=None, ge=1900, le=2200)
    fiscal_quarter: int | None = Field(default=None, ge=1, le=4)
    period_start: date | None = None
    period_end: date | None = None
    activate: bool = False
    processing_mode: Literal["FULL", "OCR_ONLY"] = "OCR_ONLY"
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentObjectSubmitOut(BaseModel):
    job_id: str
    status: str
    document_id: str | None = None


class DocumentOutV2(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ticker: str
    title: str
    doc_role: str
    fiscal_year: int | None
    fiscal_quarter: int | None
    period_start: date | None
    period_end: date | None
    is_active: bool
    status: str
    content_sha256: str | None
    canonical_markdown_sha256: str | None = None
    object_uri: str | None = None
    processing_version: int
    structure_status: str
    extraction_status: str
    embedding_status: str
    fact_count: int
    coverage: dict[str, Any]
    deduplicated: bool = False


class TreeNodeOutV2(BaseModel):
    node_id: str
    parent_id: str | None
    level: int
    order: int
    heading: str
    heading_path: list[str]
    node_kind: str
    start_line: int
    end_line: int
    content_hash: str
    excluded_from_analysis: bool = False
    exclusion_reason: str | None = None
    summary: str | None = None
    relevance: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentTreeOutV2(BaseModel):
    document_id: str
    ticker: str
    doc_role: str
    processing_version: int
    structure_status: str
    coverage: dict[str, Any]
    nodes: list[TreeNodeOutV2]


class NodeContentOutV2(BaseModel):
    document_id: str
    node_id: str
    heading: str
    heading_path: list[str]
    start_line: int
    end_line: int
    content_hash: str
    content: str


class EvidenceSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=8, ge=1, le=50)
    include_historical: bool = False


class EvidenceSearchHit(BaseModel):
    document_id: str
    doc_role: str | None
    node_id: str
    heading_path: list[str]
    line_span: tuple[int, int]
    score: float
    content: str
    quote_hash: str


class EvidenceSearchOut(BaseModel):
    ticker: str
    hits: list[EvidenceSearchHit]


class ObservationOutV2(BaseModel):
    id: str
    document_id: str
    node_id: str
    statement: str
    subject: str | None = None
    predicate: str
    object: str | None = None
    topic_tags: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    period_start: date | None = None
    period_end: date | None = None
    as_of: date | None = None
    confidence: float
    normalization: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any]


class DocumentFacetOutV2(BaseModel):
    id: str
    document_id: str
    node_id: str
    facet: str
    confidence: float
    source: str
    evidence: dict[str, Any]


class ObservationSearchIn(BaseModel):
    facets: list[str] = Field(default_factory=list, max_length=12)
    topic_tags: list[str] = Field(default_factory=list, max_length=12)
    predicates: list[str] = Field(default_factory=list, max_length=12)
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    include_historical: bool = False
    top_k: int = Field(default=50, ge=1, le=200)


class PillarAssessmentOutV2(BaseModel):
    verdict: str
    score: float | None
    confidence: float
    evidence: list[dict[str, Any]]
    counter_evidence: list[dict[str, Any]]


class GILAssessmentOutV2(BaseModel):
    ticker: str
    analysis_status: str
    gil_flag: str
    risk_level: str
    gil_score: float | None = None
    rpt_ratio: float | None
    total_rpt_exposure_vnd: float | None
    equity_vnd: float | None
    cycles_detected: int
    cycle_paths: list[list[str]]
    reasons: list[str]
    nodes_count: int
    edges_count: int
    policy_version: str = "gil-policy-v3"
    policy_context: dict[str, Any] = {}
    company_context: dict[str, Any] = {}
    exposure: dict[str, Any] = {}
    relative_risk: dict[str, Any] = {}
    scores: dict[str, Any] = {}
    graph: dict[str, Any] = {}
    policy: dict[str, Any] = {}
    financial_denominators: dict[str, Any] = {}
    relationship_risk: dict[str, Any] = {}
    flow_risk: dict[str, Any] = {}
    ownership_risk: dict[str, Any] = {}
    structural_risk: dict[str, Any] = {}
    materiality: dict[str, Any] = {}
    rpt_metrics: dict[str, Any] = {}
    rpt_breakdown: dict[str, Any] = {}
    insider_evidence: dict[str, Any] = {}
    risk_components: dict[str, Any] = {}
    dimensions: dict[str, Any] = {}
    circular_flow_proven: bool = False
    tunneling_signals: int = 0
    catastrophic_triggered: bool = False
    hard_triggers: dict[str, Any] = {}
    decision: dict[str, Any] = {}
    data_quality: dict[str, Any] = {}
    temporal_risk: dict[str, Any] = {}
    # Compact provenance for the Agent: no quote duplication, only the
    # deduplicated graph edges that actually feed the GIL decision.
    evidence: list[dict[str, Any]] = []


class ReviewQueueOut(BaseModel):
    id: str
    ticker: str | None
    document_id: str | None
    item_type: str
    status: str
    reason: str
    payload: dict[str, Any]
