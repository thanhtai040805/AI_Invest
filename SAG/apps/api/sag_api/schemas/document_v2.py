from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class TreeNodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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
    summary: str | None = None
    relevance: dict[str, Any] = {}
    metadata: dict[str, Any] = {}


class DocumentTreeOut(BaseModel):
    document_id: str
    source_id: str
    ticker: str
    doc_role: str | None
    processing_version: int
    structure_status: str
    coverage: dict[str, Any]
    nodes: list[TreeNodeOut]


class NodeContentOut(BaseModel):
    document_id: str
    node_id: str
    heading: str
    heading_path: list[str]
    start_line: int
    end_line: int
    content_hash: str
    content: str


class PillarAssessmentOut(BaseModel):
    verdict: str
    score: float | None = None
    confidence: float
    evidence: list[dict[str, Any]]
    counter_evidence: list[dict[str, Any]]


class GILAssessmentOut(BaseModel):
    ticker: str
    analysis_status: str
    gil_flag: str | None
    risk_level: str
    rpt_ratio: float | None
    total_rpt_exposure_vnd: float | None
    equity_vnd: float | None
    cycles_detected: int
    cycle_paths: list[list[str]]
    reasons: list[str]
    nodes_count: int
    edges_count: int
    rpt_metrics: dict[str, Any] = {}
    rpt_breakdown: dict[str, Any] = {}
    risk_components: dict[str, Any] = {}
    circular_flow_proven: bool = False
    tunneling_signals: int = 0
    catastrophic_triggered: bool = False
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
