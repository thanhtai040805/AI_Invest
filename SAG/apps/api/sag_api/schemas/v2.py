from __future__ import annotations

from datetime import date
from typing import Any

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
    metadata: dict[str, Any] = Field(default_factory=dict)


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


class PillarAssessmentOutV2(BaseModel):
    verdict: str
    score: float | None
    confidence: float
    evidence: list[dict[str, Any]]
    counter_evidence: list[dict[str, Any]]


class MoatAssessmentOutV2(BaseModel):
    ticker: str
    assessment_status: str
    moat_score: float | None
    multiplier: float | None
    coverage_ratio: float
    active_roles: list[str]
    missing_roles: list[str]
    pillars: dict[str, PillarAssessmentOutV2]
    reasons: list[str]
    policy_version: str = "moat-policy-v2"


class GILAssessmentOutV2(BaseModel):
    ticker: str
    analysis_status: str
    gil_flag: str
    risk_level: str
    rpt_ratio: float | None
    total_rpt_exposure_vnd: float
    equity_vnd: float | None
    cycles_detected: int
    cycle_paths: list[list[str]]
    reasons: list[str]
    nodes_count: int
    edges_count: int
    policy_version: str = "gil-policy-v2"


class ReviewQueueOut(BaseModel):
    id: str
    ticker: str | None
    document_id: str | None
    item_type: str
    status: str
    reason: str
    payload: dict[str, Any]
