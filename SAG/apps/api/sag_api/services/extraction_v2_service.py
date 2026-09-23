from __future__ import annotations

import logging
import math
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.db.base import new_id
from sag_api.db.models import (
    Document,
    DocumentFacet,
    DocumentTreeNode,
    Entity,
    EntityAlias,
    EntityMention,
    EvidenceSpan,
    Fact,
    Issuer,
    Observation,
    Relation,
    ReviewQueueItem,
)
from sag_api.enums import EntityType, FactType, ProcessingStageStatus, RelationType, ValidationStatus
from sag_api.services.document_structure_service import sha256_text

logger = logging.getLogger("sag.extraction_v2")

TAXONOMY_VERSION = "financial-evidence-taxonomy-v2"


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str | None = None
    # Quote is hydrated server-side from the cited Markdown line.
    quote: str = Field(default="")
    line_start: int = Field(default=1, ge=1)
    line_end: int = Field(default=1, ge=1)
    # Accepted for compatibility with line-hint payloads.
    line_hint: int | None = Field(default=None, ge=1)
    _raw_llm_quote: str | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _legacy_line_hint(self) -> "EvidenceRef":
        if self.line_hint is not None and self.line_start == 1 and self.line_end == 1:
            self.line_start = self.line_hint
            self.line_end = self.line_hint
        return self


class NodeAnnotationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    relevance: Literal["NONE", "LOW", "MEDIUM", "HIGH"]
    summary: str | None = Field(default=None, max_length=1200)
    rationale: str | None = Field(default=None, max_length=1200)


class EntityMentionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str = Field(min_length=1, max_length=512)
    canonical_name: str = Field(min_length=1, max_length=512)
    entity_ref: str | None = Field(default=None, max_length=32)
    entity_type: EntityType
    raw_label: str | None = Field(default=None, max_length=256)
    industry_context: str | None = Field(default=None, max_length=1200)
    extraction_rationale: str | None = Field(default=None, max_length=1200)
    taxonomy_candidate: str | None = Field(default=None, max_length=256)
    evidence: EvidenceRef


class FactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_type: FactType
    label: str = Field(min_length=1, max_length=512)
    semantic_key: str | None = Field(default=None, max_length=256)
    value_text: str | None = Field(default=None, max_length=256)
    value_numeric: float | None = None
    unit: str | None = Field(default=None, max_length=64)
    currency: str | None = Field(default=None, max_length=16)
    period_start: date | None = None
    period_end: date | None = None
    as_of: date | None = None
    raw_label: str | None = Field(default=None, max_length=256)
    industry_context: str | None = Field(default=None, max_length=1200)
    extraction_rationale: str | None = Field(default=None, max_length=1200)
    taxonomy_candidate: str | None = Field(default=None, max_length=256)
    evidence: EvidenceRef

    @field_validator("semantic_key")
    @classmethod
    def _clean_semantic_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower().replace(" ", "_")
        return cleaned or None


class RelationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str | None = Field(default=None, min_length=1, max_length=512)
    object: str | None = Field(default=None, min_length=1, max_length=512)
    subject_ref: str | None = Field(default=None, max_length=32)
    object_ref: str | None = Field(default=None, max_length=32)
    relation_type: RelationType
    flow_kind: str | None = Field(default=None, max_length=64)
    amount_vnd: float | None = None
    ownership_pct: float | None = None
    period_start: date | None = None
    period_end: date | None = None
    as_of: date | None = None
    raw_label: str | None = Field(default=None, max_length=256)
    industry_context: str | None = Field(default=None, max_length=1200)
    extraction_rationale: str | None = Field(default=None, max_length=1200)
    taxonomy_candidate: str | None = Field(default=None, max_length=256)
    evidence: EvidenceRef


class GilDisclosureIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disclosure_type: Literal["risk", "no_risk", "scope", "data_quality"]
    label: str = Field(min_length=1, max_length=512)
    extraction_rationale: str | None = Field(default=None, max_length=1200)
    evidence: EvidenceRef


class DocumentFacetIn(BaseModel):
    """A discoverable content facet, independent of document role."""

    model_config = ConfigDict(extra="forbid")

    facet: str = Field(min_length=2, max_length=128)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str | None = Field(default=None, max_length=1200)
    evidence: EvidenceRef


class ObservationIn(BaseModel):
    """An open, evidence-backed interpretation."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=8, max_length=1600)
    subject: str | None = Field(default=None, max_length=512)
    predicate: str = Field(default="observation", min_length=2, max_length=256)
    object: str | None = Field(default=None, max_length=512)
    topic_tags: list[str] = Field(default_factory=list, max_length=12)
    attributes: dict[str, Any] = Field(default_factory=dict)
    period_start: date | None = None
    period_end: date | None = None
    as_of: date | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str | None = Field(default=None, max_length=1200)
    taxonomy_mapping: dict[str, Any] = Field(default_factory=dict)
    evidence: EvidenceRef

    @field_validator("predicate")
    @classmethod
    def _clean_predicate(cls, value: str) -> str:
        return " ".join(value.strip().split()).casefold()

    @field_validator("topic_tags")
    @classmethod
    def _clean_topic_tags(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(" ".join(value.strip().split()).casefold() for value in values if value.strip()))

    @field_validator("taxonomy_mapping", mode="before")
    @classmethod
    def _normalize_taxonomy_mapping(cls, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @field_validator("attributes", mode="before")
    @classmethod
    def _normalize_attributes(cls, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}


class TableRowSemanticIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_label: str = Field(min_length=1, max_length=512)
    role: str = Field(min_length=2, max_length=128)
    meaning: str = Field(min_length=8, max_length=500)
    relationship: str | None = Field(default=None, max_length=400)
    evidence: EvidenceRef


class TableSignalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=8, max_length=800)
    analytical_question: str = Field(min_length=8, max_length=400)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: EvidenceRef


class TableSemanticIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table_title: str = Field(default="section", min_length=2, max_length=512)
    table_role: str = Field(default="section_insight", min_length=2, max_length=128)
    analytical_purpose: str = Field(default="", max_length=800)
    section_id: int = Field(default=-1, ge=-1)
    insight: str = Field(min_length=8, max_length=800)
    row_semantics: list[TableRowSemanticIn] = Field(default_factory=list, max_length=12)
    analytical_signals: list[TableSignalIn] = Field(default_factory=list, max_length=3)
    evidence: EvidenceRef


class ExtractionManifestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taxonomy_version: str = TAXONOMY_VERSION
    node_annotations: list[NodeAnnotationIn] = Field(default_factory=list, max_length=1024)
    entity_mentions: list[EntityMentionIn] = Field(default_factory=list, max_length=2048)
    facts: list[FactIn] = Field(default_factory=list, max_length=2048)
    relations: list[RelationIn] = Field(default_factory=list, max_length=2048)
    gil_disclosures: list[GilDisclosureIn] = Field(default_factory=list, max_length=512)
    document_facets: list[DocumentFacetIn] = Field(default_factory=list, max_length=512)
    observations: list[ObservationIn] = Field(default_factory=list, max_length=1024)
    section_insights: list[TableSemanticIn] = Field(default_factory=list, max_length=512)
    moat_signals: list[dict[str, Any]] = Field(default_factory=list, exclude=True)


@dataclass(frozen=True)
class ExtractionResult:
    status: str
    fact_count: int = 0
    relation_count: int = 0
    observation_count: int = 0
    facet_count: int = 0
    evidence_count: int = 0
    token_usage: int = 0
    error: str | None = None
    metadata: dict[str, Any] | None = None


_FACT_TYPES = frozenset(item.value for item in FactType)
_ENTITY_TYPES = frozenset(item.value for item in EntityType)
_RELATION_TYPES = frozenset(item.value for item in RelationType)

_ENUM_COERCION_TARGETS = (
    ("facts", "fact_type", _FACT_TYPES),
    ("entity_mentions", "entity_type", _ENTITY_TYPES),
    ("relations", "relation_type", _RELATION_TYPES),
)

_MANIFEST_COLLECTION_KEYS = (
    "node_annotations",
    "entity_mentions",
    "facts",
    "relations",
    "gil_disclosures",
    "document_facets",
    "observations",
    "section_insights",
)

_TABLE_FACT_RULES: tuple[tuple[tuple[str, ...], FactType, str, str | None], ...] = (
    (("tong cong tai san",), FactType.OTHER, "total_assets", "asset_balance"),
    (("cac khoan phai thu ngan han",), FactType.RECEIVABLE_BALANCE, "total_receivables", None),
    (("tien va cac khoan tuong duong tien",), FactType.OTHER, "cash_and_equivalents", "cash_balance"),
    (("tong cong no phai tra",), FactType.PAYABLE_BALANCE, "total_payables", None),
    (("tien mat",), FactType.OTHER, "cash_on_hand", "cash_balance"),
    (("tien gui ngan hang",), FactType.OTHER, "bank_deposit_demand", "cash_balance"),
    (("cac khoan tuong duong tien",), FactType.OTHER, "cash_equivalents", "cash_balance"),
    (("phai thu",), FactType.RECEIVABLE_BALANCE, "receivable_balance", None),
    (("phai tra",), FactType.PAYABLE_BALANCE, "payable_balance", None),
    (("co tuc",), FactType.DIVIDEND, "dividend", None),
    (("von gop",), FactType.EQUITY, "contributed_capital", None),
    (("von chu",), FactType.EQUITY, "equity", None),
    (("loi nhuan",), FactType.PROFIT, "profit", None),
    (("doanh thu hoat dong tai chinh",), FactType.REVENUE, "financial_income", None),
    (("doanh thu",), FactType.REVENUE, "revenue", None),
    (("gia von",), FactType.OTHER, "cost_of_goods_sold", "cost_of_goods_sold"),
    (("chi phi di vay",), FactType.OTHER, "borrowing_cost", "interest_expense"),
    (("chi phi",), FactType.OTHER, "expense", "expense"),
    (("dau tu", "cong ty con"), FactType.OWNERSHIP_BALANCE, "investment_in_subsidiaries", None),
    (("dau tu",), FactType.OTHER, "investment", "investment_balance"),
    (("co phieu",), FactType.OTHER, "share_count", "share_count"),
    (("khau hao",), FactType.OTHER, "depreciation", "depreciation"),
    (("nguyen gia",), FactType.OTHER, "historical_cost", "asset_cost"),
    (("gia tri con lai",), FactType.OTHER, "carrying_amount", "asset_carrying_amount"),
)


def coerce_unknown_enums(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Coerce invented enum values to 'other' in place, keeping the original."""
    coerced: list[dict[str, str]] = []
    for collection_key, field, allowed in _ENUM_COERCION_TARGETS:
        items = payload.get(collection_key)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            value = item.get(field)
            if not isinstance(value, str) or value in allowed:
                continue
            original = value
            item[field] = "other"
            if not item.get("raw_label"):
                item["raw_label"] = original[:256]
            if not item.get("taxonomy_candidate"):
                item["taxonomy_candidate"] = original[:256]
            coerced.append(
                {"path": f"{collection_key}.{index}.{field}", "original": original[:256]}
            )
    return coerced


def normalize_manifest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Ensure all required manifest collections are present."""
    normalized = dict(payload)
    if "section_insights" not in normalized and isinstance(normalized.get("table_semantics"), list):
        normalized["section_insights"] = normalized.pop("table_semantics")
    for key in _MANIFEST_COLLECTION_KEYS:
        if key not in normalized:
            normalized[key] = []
    return normalized


def _classify_table_fact(section: str, row_label: str) -> tuple[FactType, str, str | None]:
    folded = _fold_text(f"{section} {row_label}")
    if "luu chuyen tien" in folded or "chuyen tien" in folded or "cash flow" in folded:
        return FactType.OTHER, _semantic_key("table", row_label), "unmapped_table_line_item"
    for terms, fact_type, semantic_key, taxonomy_candidate in _TABLE_FACT_RULES:
        if all(term in folded for term in terms):
            return fact_type, semantic_key, taxonomy_candidate
    return FactType.OTHER, _semantic_key("table", row_label), "unmapped_table_line_item"


def _table_value_scale(markdown: str, line_no: int, header: str, row_label: str) -> float:
    """Normalize figures whose table unit is declared immediately above it."""
    lines = markdown.splitlines()
    table_start = line_no - 1
    while table_start >= 0 and lines[table_start].strip().startswith("|"):
        table_start -= 1
    context = " ".join(lines[max(0, table_start - 6):table_start + 1] + [header, row_label])
    folded = _fold_text(context)
    if "trieu" in folded and ("vnd" in folded or "dong" in folded):
        return 1_000_000.0
    if "nghin" in folded and ("vnd" in folded or "dong" in folded):
        return 1_000.0
    if "ty vnd" in folded or "ty dong" in folded:
        return 1_000_000_000.0
    return 1.0


def enrich_manifest_facets_from_observations(manifest: ExtractionManifestIn) -> dict[str, int]:
    """Promote observation topics to discoverable document facets."""
    existing = {_fold_text(item.facet).replace("-", "_") for item in manifest.document_facets}
    added = 0
    for observation in manifest.observations:
        for topic in observation.topic_tags:
            normalized = _fold_text(topic).replace("-", "_")[:128]
            if len(normalized) < 2 or normalized in existing:
                continue
            manifest.document_facets.append(
                DocumentFacetIn(
                    facet=normalized,
                    confidence=max(0.0, min(1.0, observation.confidence * 0.9)),
                    rationale="Derived from an evidence-backed observation topic.",
                    evidence=observation.evidence,
                )
            )
            existing.add(normalized)
            added += 1
    return {"facets_derived_from_observations": added}


def deduplicate_manifest_items(manifest: ExtractionManifestIn) -> dict[str, int]:
    """Collapse semantically identical records."""
    def _date_key(value: date | None) -> str | None:
        return value.isoformat() if value else None

    kept_facts: list[FactIn] = []
    fact_by_key: dict[tuple[Any, ...], int] = {}
    fact_duplicates = 0
    for item in manifest.facts:
        key = (
            item.semantic_key or _semantic_key(item.fact_type.value, item.label),
            item.value_text,
            item.value_numeric,
            _date_key(item.period_start),
            _date_key(item.period_end),
            _date_key(item.as_of),
            item.unit,
            item.currency,
        )
        previous_index = fact_by_key.get(key)
        if previous_index is None:
            fact_by_key[key] = len(kept_facts)
            kept_facts.append(item)
            continue
        fact_duplicates += 1
    manifest.facts = kept_facts

    kept_relations: list[RelationIn] = []
    seen_relations: set[tuple[Any, ...]] = set()
    relation_duplicates = 0
    for item in manifest.relations:
        key = (
            _fold_text(item.subject),
            item.relation_type.value if hasattr(item.relation_type, "value") else str(item.relation_type),
            _fold_text(item.object),
            _date_key(item.period_start),
            _date_key(item.period_end),
            _date_key(item.as_of),
            item.amount_vnd,
            item.ownership_pct,
        )
        if key in seen_relations:
            relation_duplicates += 1
            continue
        seen_relations.add(key)
        kept_relations.append(item)
    manifest.relations = kept_relations

    kept_observations: list[ObservationIn] = []
    seen_observations: set[tuple[Any, ...]] = set()
    observation_duplicates = 0
    for item in manifest.observations:
        key = (
            _fold_text(item.subject or ""),
            item.predicate,
            _fold_text(item.object or ""),
            _fold_text(item.statement),
            _date_key(item.period_start),
            _date_key(item.period_end),
            _date_key(item.as_of),
        )
        if key in seen_observations:
            observation_duplicates += 1
            continue
        seen_observations.add(key)
        kept_observations.append(item)
    manifest.observations = kept_observations
    return {
        "fact_duplicates_dropped": fact_duplicates,
        "relation_duplicates_dropped": relation_duplicates,
        "observation_duplicates_dropped": observation_duplicates,
    }


def fill_missing_node_annotations(nodes: list[DocumentTreeNode], manifest: ExtractionManifestIn) -> int:
    """Ensure complete node coverage in manifest."""
    known = {item.node_id for item in manifest.node_annotations}
    added = 0
    for node in nodes:
        if node.node_id not in known:
            manifest.node_annotations.append(NodeAnnotationIn(node_id=node.node_id, relevance="NONE"))
            added += 1
    return added


def _fold_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return stripped.replace("đ", "d")


def _semantic_key(prefix: str, label: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in label.strip())
    return f"{prefix}:{'_'.join(part for part in cleaned.split('_') if part)[:220]}"


def _normalize_accounting_scope(value: str | None) -> str | None:
    normalized = str(value or "").strip().upper()
    if normalized in {"CONSOLIDATED", "HOP_NHAT", "HỢP NHẤT"}:
        return "CONSOLIDATED"
    if normalized in {"SEPARATE", "STANDALONE", "RIENG", "RIÊNG"}:
        return "STANDALONE"
    return None


def _infer_accounting_scope(
    markdown: str,
    document: Document,
    explicit_scope: str | None = None,
) -> str:
    if normalized := _normalize_accounting_scope(explicit_scope):
        return normalized

    def normalize(value: str) -> str:
        normalized = unicodedata.normalize("NFD", value).casefold()
        return "".join(char for char in normalized if unicodedata.category(char) != "Mn")

    filename = normalize(str(document.filename or ""))
    sample = normalize(markdown[:12000])
    consolidated_markers = (
        "bao cao tai chinh hop nhat", "bctc hop nhat", "consolidated financial", "consolidated",
    )
    standalone_markers = (
        "bao cao tai chinh rieng", "bctc rieng", "standalone", "separate financial",
    )

    # The title is the strongest signal. Notes commonly mention the other
    # scope, so checking the whole OCR body first causes scope flips.
    if any(marker in filename for marker in consolidated_markers):
        return "CONSOLIDATED"
    if any(marker in filename for marker in standalone_markers):
        return "STANDALONE"

    consolidated_score = sum(sample.count(marker) for marker in consolidated_markers)
    standalone_score = sum(sample.count(marker) for marker in standalone_markers)
    if consolidated_score > standalone_score:
        return "CONSOLIDATED"
    if standalone_score > consolidated_score:
        return "STANDALONE"
    return "UNKNOWN"


def _iter_manifest_refs(manifest: ExtractionManifestIn):
    """Yield (path, evidence) for every evidence-carrying item in stable order."""
    collections = (
        ("entity_mentions", manifest.entity_mentions),
        ("facts", manifest.facts),
        ("relations", manifest.relations),
        ("moat_signals", manifest.moat_signals),
        ("gil_disclosures", manifest.gil_disclosures),
        ("document_facets", manifest.document_facets),
        ("observations", manifest.observations),
    )
    for name, items in collections:
        for index, item in enumerate(items):
            evidence = item.get("evidence") if isinstance(item, dict) else item.evidence
            yield f"{name}.{index}", evidence
    for section_index, section in enumerate(manifest.section_insights):
        yield f"section_insights.{section_index}", section.evidence


def resolve_llm_evidence_nodes(markdown: str, nodes: list[DocumentTreeNode], manifest: ExtractionManifestIn) -> None:
    """Resolve evidence by cited line."""
    manifest.node_annotations.clear()
    source_lines = markdown.splitlines()
    for path, evidence in _iter_manifest_refs(manifest):
        evidence._raw_llm_quote = evidence.quote.strip() or None
        if evidence.line_start > evidence.line_end:
            raise ValueError(f"{path} line_start must be <= line_end")
        if evidence.line_end > len(source_lines):
            raise ValueError(f"{path} line_end outside Markdown")
        evidence.quote = "\n".join(source_lines[evidence.line_start - 1:evidence.line_end]).strip()
        if not evidence.quote:
            raise ValueError(f"{path} empty source quote")
        candidates = sorted(
            (node for node in nodes if node.start_line <= evidence.line_start <= node.end_line),
            key=lambda node: (node.end_line - node.start_line, -int(node.level or 0), int(node.order_index or 0)),
        )
        for node in candidates:
            try:
                _locate_quote(markdown, node, evidence.quote, evidence.line_start, all_nodes=nodes)
            except ValueError:
                continue
            evidence.node_id = node.node_id
            break
        else:
            raise ValueError(f"{path} không thể resolve node từ line_start={evidence.line_start}")


async def extract_and_persist_manifest(
    session: AsyncSession,
    issuer: Issuer,
    document: Document,
    markdown: str,
    nodes: list[DocumentTreeNode],
    statement_markdown: str | None = None,
    accounting_scope: str | None = None,
    source_completeness: dict[str, Any] | None = None,
) -> ExtractionResult:
    started_at = time.perf_counter()
    accounting_scope = _infer_accounting_scope(markdown, document, accounting_scope)

    try:
        from sag_api.extraction.parsers.statement_table_parser import assess_financial_document_completeness

        source_completeness = source_completeness or assess_financial_document_completeness(
            statement_markdown or markdown, str(document.doc_role or "").upper()
        )
        # Missing statement sections mean there is no trustworthy extraction
        # substrate. Missing optional numeric denominators do not: persist the
        # relations/facts we can ground and let GIL mark only those ratios as
        # NOT_EVALUATED.
        if source_completeness["missing"]:
            missing = ", ".join(source_completeness["missing"])
            data_issues = ", ".join(source_completeness.get("data_issues", []))
            reasons = [
                part
                for part in (
                    f"missing financial sections: {missing}" if missing else "",
                    f"missing financial data: {data_issues}" if data_issues else "",
                )
                if part
            ]
            error = f"source_incomplete: {'; '.join(reasons)}"
            return ExtractionResult(
                status=ProcessingStageStatus.INCOMPLETE.value,
                token_usage=0,
                error=error,
                metadata={
                    "mode": "deterministic_compiler",
                    "llm_used": False,
                    "source_completeness": source_completeness,
                    "duration_ms": round((time.perf_counter() - started_at) * 1000),
                },
            )

        from sag_api.extraction.compiler.gil_graph_compiler import GilGraphCompiler

        compiler = GilGraphCompiler(issuer.ticker)
        manifest = compiler.compile_manifest(
            markdown,
            nodes,
            str(document.doc_role or "").upper(),
            statement_markdown=statement_markdown or markdown,
        )
        async with session.begin_nested():
            counts = await validate_and_persist_manifest(
                session,
                issuer,
                document,
                markdown,
                nodes,
                manifest,
                statement_markdown=statement_markdown or markdown,
                accounting_scope=accounting_scope,
            )
        metadata: dict[str, Any] = {
            "mode": "deterministic_compiler",
            "llm_used": False,
            "duration_ms": round((time.perf_counter() - started_at) * 1000),
            "accounting_scope": accounting_scope,
            "taxonomy_version": TAXONOMY_VERSION,
            "source_completeness": source_completeness,
            "observation_count": counts.get("observation_count", 0),
            "facet_count": counts.get("facet_count", 0),
            "invalid_fact_count": counts.get("invalid_fact_count", 0),
            "invalid_relation_count": counts.get("invalid_relation_count", 0),
        }
        return ExtractionResult(
            status=ProcessingStageStatus.COMPLETE.value,
            token_usage=0,
            metadata=metadata,
            fact_count=counts.get("fact_count", 0),
            relation_count=counts.get("relation_count", 0),
            observation_count=counts.get("observation_count", 0),
            facet_count=counts.get("facet_count", 0),
            evidence_count=counts.get("evidence_count", 0),
        )
    except Exception as exc:
        logger.error("Deterministic compiler failed: %s", exc, exc_info=True)
        return ExtractionResult(
            status=ProcessingStageStatus.INCOMPLETE.value,
            token_usage=0,
            error=str(exc)[:2000],
            metadata={"mode": "deterministic_compiler", "error": str(exc)[:2000]},
        )


def _validate_manifest_shape_and_references(
    markdown: str,
    nodes: list[DocumentTreeNode],
    manifest: ExtractionManifestIn,
) -> None:
    if manifest.taxonomy_version != TAXONOMY_VERSION:
        raise ValueError(f"taxonomy_version không hợp lệ: {manifest.taxonomy_version}")

    nodes_by_id = {node.node_id: node for node in nodes}
    annotation_ids = [annotation.node_id for annotation in manifest.node_annotations]
    if len(annotation_ids) != len(set(annotation_ids)):
        raise ValueError("node_annotations bị trùng node_id")
    missing = set(nodes_by_id) - set(annotation_ids)
    extra = set(annotation_ids) - set(nodes_by_id)
    if missing:
        raise ValueError(f"node_annotations thiếu node_id: {sorted(missing)[:10]}")
    if extra:
        raise ValueError(f"node_annotations có node_id không tồn tại: {sorted(extra)[:10]}")

    refs: list[EvidenceRef] = []
    refs.extend(mention.evidence for mention in manifest.entity_mentions)
    refs.extend(fact.evidence for fact in manifest.facts)
    refs.extend(relation.evidence for relation in manifest.relations)
    refs.extend(disclosure.evidence for disclosure in manifest.gil_disclosures)
    refs.extend(facet.evidence for facet in manifest.document_facets)
    refs.extend(observation.evidence for observation in manifest.observations)
    for ref in refs:
        node = nodes_by_id.get(ref.node_id)
        if node is None:
            raise ValueError(f"evidence node_id không tồn tại: {ref.node_id}")
        _locate_quote(markdown, node, ref.quote, ref.line_hint or ref.line_start, all_nodes=nodes)
    for index, fact in enumerate(manifest.facts):
        if not _fact_value_grounded(fact):
            raise ValueError(f"facts.{index} value is not grounded in its evidence quote: {fact.label[:120]}")
    for index, relation in enumerate(manifest.relations):
        if relation.amount_vnd is not None and not _value_grounded_in_quote(relation.amount_vnd, relation.evidence.quote):
            raise ValueError(f"relations.{index}.amount_vnd is not grounded in its evidence quote")
        if relation.ownership_pct is not None and not _value_grounded_in_quote(relation.ownership_pct, relation.evidence.quote):
            raise ValueError(f"relations.{index}.ownership_pct is not grounded in its evidence quote")


async def validate_and_persist_manifest(
    session: AsyncSession,
    issuer: Issuer,
    document: Document,
    markdown: str,
    nodes: list[DocumentTreeNode],
    manifest: ExtractionManifestIn,
    statement_markdown: str | None = None,
    accounting_scope: str | None = None,
) -> dict[str, int]:
    if manifest.taxonomy_version != TAXONOMY_VERSION:
        raise ValueError(f"taxonomy_version không hợp lệ: {manifest.taxonomy_version}")

    nodes_by_id = {node.node_id: node for node in nodes}
    annotation_ids = [annotation.node_id for annotation in manifest.node_annotations]
    if len(annotation_ids) != len(set(annotation_ids)):
        raise ValueError("node_annotations bị trùng node_id")
    missing = set(nodes_by_id) - set(annotation_ids)
    extra = set(annotation_ids) - set(nodes_by_id)
    if missing:
        raise ValueError(f"node_annotations thiếu node_id: {sorted(missing)[:10]}")
    if extra:
        raise ValueError(f"node_annotations có node_id không tồn tại: {sorted(extra)[:10]}")

    invalid_fact_count = sum(not _fact_value_grounded(fact) for fact in manifest.facts)
    invalid_relation_count = sum(
        (relation.amount_vnd is not None and not _value_grounded_in_quote(relation.amount_vnd, relation.evidence.quote))
        or (relation.ownership_pct is not None and not _value_grounded_in_quote(relation.ownership_pct, relation.evidence.quote))
        for relation in manifest.relations
    )
    # Optional denominators and unrelated statement rows must not make the
    # whole document unusable. Drop only ungrounded numeric outputs and keep
    # the rest of the evidence graph; the counts remain visible in coverage.
    manifest.facts = [fact for fact in manifest.facts if _fact_value_grounded(fact)]
    manifest.relations = [
        relation
        for relation in manifest.relations
        if not (
            (relation.amount_vnd is not None and not _value_grounded_in_quote(relation.amount_vnd, relation.evidence.quote))
            or (relation.ownership_pct is not None and not _value_grounded_in_quote(relation.ownership_pct, relation.evidence.quote))
        )
    ]

    await _delete_previous_extraction(session, document.id)
    evidence_cache: dict[tuple[str, str], EvidenceSpan] = {}
    entity_cache: dict[str, Entity] = {}

    for annotation in manifest.node_annotations:
        node = nodes_by_id[annotation.node_id]
        node.summary = annotation.summary
        node.relevance_json = {
            "relevance": annotation.relevance,
            "rationale": annotation.rationale,
            "taxonomy_version": TAXONOMY_VERSION,
        }

    for mention in manifest.entity_mentions:
        span = await _evidence_span(session, issuer, document, markdown, nodes_by_id, mention.evidence, evidence_cache)
        entity = await _entity(session, issuer, mention.canonical_name, mention.raw_text, mention.entity_type.value, entity_cache)
        session.add(
            EntityMention(
                document_id=document.id,
                entity_id=entity.id,
                node_id=mention.evidence.node_id,
                raw_text=mention.raw_text,
                evidence_span_id=span.id,
                metadata_json={
                    "raw_label": mention.raw_label,
                    "industry_context": mention.industry_context,
                    "extraction_rationale": mention.extraction_rationale,
                    "taxonomy_candidate": mention.taxonomy_candidate,
                    "taxonomy_version": TAXONOMY_VERSION,
                },
            )
        )
        if mention.raw_text.strip().casefold() != entity.canonical_name.casefold():
            session.add(EntityAlias(entity_id=entity.id, alias=mention.raw_text.strip(), source="extraction"))
        if mention.entity_type == EntityType.OTHER and mention.taxonomy_candidate:
            await _review_item(session, issuer, document, "taxonomy_candidate", "Entity OTHER cần review", mention.model_dump(mode="json"))

    fact_count = 0
    for fact in manifest.facts:
        span = await _evidence_span(
            session,
            issuer,
            document,
            markdown,
            nodes_by_id,
            fact.evidence,
            evidence_cache,
            statement_markdown=statement_markdown,
        )
        session.add(
            Fact(
                issuer_id=issuer.id,
                document_id=document.id,
                node_id=fact.evidence.node_id,
                evidence_span_id=span.id,
                fact_type=fact.fact_type.value,
                semantic_key=fact.semantic_key or _semantic_key(fact.fact_type.value, fact.label),
                label=fact.label.strip(),
                value_text=fact.value_text,
                value_numeric=fact.value_numeric,
                unit=fact.unit,
                currency=fact.currency,
                period_start=fact.period_start or document.period_start,
                period_end=fact.period_end or document.period_end,
                as_of=fact.as_of or document.period_end,
                validation_status=ValidationStatus.VALIDATED.value,
                raw_label=fact.raw_label,
                taxonomy_candidate=fact.taxonomy_candidate,
                metadata_json={
                    "industry_context": fact.industry_context,
                    "extraction_rationale": fact.extraction_rationale,
                    "accounting_scope": accounting_scope or _infer_accounting_scope(markdown, document),
                    "taxonomy_version": TAXONOMY_VERSION,
                },
            )
        )
        if fact.fact_type == FactType.OTHER and fact.taxonomy_candidate:
            await _review_item(session, issuer, document, "taxonomy_candidate", "Fact OTHER cần review", fact.model_dump(mode="json"))
        fact_count += 1

    relation_count = 0
    for relation in manifest.relations:
        span = await _evidence_span(session, issuer, document, markdown, nodes_by_id, relation.evidence, evidence_cache)
        subject_entity = await _entity(session, issuer, relation.subject, relation.subject, EntityType.OTHER.value, entity_cache)
        object_entity = await _entity(session, issuer, relation.object, relation.object, EntityType.OTHER.value, entity_cache)
        status = ValidationStatus.VALIDATED.value
        if relation.relation_type == RelationType.OTHER or relation.taxonomy_candidate:
            status = ValidationStatus.NEEDS_REVIEW.value
            await _review_item(
                session,
                issuer,
                document,
                "relation_review",
                "Relation OTHER/taxonomy_candidate không tự động tham gia GIL",
                relation.model_dump(mode="json"),
            )
        session.add(
            Relation(
                issuer_id=issuer.id,
                document_id=document.id,
                node_id=relation.evidence.node_id,
                evidence_span_id=span.id,
                subject_entity_id=subject_entity.id,
                object_entity_id=object_entity.id,
                subject=relation.subject.strip(),
                object=relation.object.strip(),
                relation_type=relation.relation_type.value,
                amount_vnd=relation.amount_vnd,
                ownership_pct=relation.ownership_pct,
                period_start=relation.period_start or document.period_start,
                period_end=relation.period_end or document.period_end,
                as_of=relation.as_of or document.period_end,
                validation_status=status,
                raw_label=relation.raw_label,
                taxonomy_candidate=relation.taxonomy_candidate,
                metadata_json={
                    "industry_context": relation.industry_context,
                    "extraction_rationale": relation.extraction_rationale,
                    "flow_kind": relation.flow_kind,
                    "accounting_scope": accounting_scope or _infer_accounting_scope(markdown, document),
                    "taxonomy_version": TAXONOMY_VERSION,
                },
            )
        )
        relation_count += 1

    for disclosure in manifest.gil_disclosures:
        span = await _evidence_span(session, issuer, document, markdown, nodes_by_id, disclosure.evidence, evidence_cache)
        session.add(
            Fact(
                issuer_id=issuer.id,
                document_id=document.id,
                node_id=disclosure.evidence.node_id,
                evidence_span_id=span.id,
                fact_type=FactType.OTHER.value,
                semantic_key=_semantic_key("gil_disclosure", disclosure.disclosure_type),
                label=disclosure.label.strip(),
                value_text=disclosure.disclosure_type,
                period_start=document.period_start,
                period_end=document.period_end,
                as_of=document.period_end,
                validation_status=ValidationStatus.VALIDATED.value,
                raw_label="gil_disclosure",
                metadata_json={
                    "disclosure_type": disclosure.disclosure_type,
                    "extraction_rationale": disclosure.extraction_rationale,
                    "taxonomy_version": TAXONOMY_VERSION,
                },
            )
        )
        fact_count += 1

    facet_count = 0
    seen_facets: set[tuple[str, str, str]] = set()
    for facet in manifest.document_facets:
        span = await _evidence_span(session, issuer, document, markdown, nodes_by_id, facet.evidence, evidence_cache)
        normalized_facet = "_".join(
            part for part in _fold_text(facet.facet).replace("-", "_").split("_") if part
        )[:128]
        identity = (normalized_facet, span.node_id, span.quote_hash)
        if identity in seen_facets:
            continue
        seen_facets.add(identity)
        session.add(
            DocumentFacet(
                issuer_id=issuer.id,
                document_id=document.id,
                node_id=span.node_id,
                evidence_span_id=span.id,
                facet=normalized_facet,
                confidence=facet.confidence,
                source="deterministic",
                metadata_json={
                    "rationale": facet.rationale,
                    "taxonomy_version": TAXONOMY_VERSION,
                },
            )
        )
        facet_count += 1

    observation_count = 0
    seen_observations: set[tuple[str, str, str]] = set()
    for observation in manifest.observations:
        span = await _evidence_span(session, issuer, document, markdown, nodes_by_id, observation.evidence, evidence_cache)
        identity = (observation.statement.casefold(), span.node_id, span.quote_hash)
        if identity in seen_observations:
            continue
        seen_observations.add(identity)
        session.add(
            Observation(
                issuer_id=issuer.id,
                document_id=document.id,
                node_id=span.node_id,
                evidence_span_id=span.id,
                statement=observation.statement.strip(),
                subject=observation.subject.strip() if observation.subject else None,
                predicate=observation.predicate,
                object=observation.object.strip() if observation.object else None,
                topic_tags_json=observation.topic_tags,
                attributes_json=observation.attributes,
                period_start=observation.period_start or document.period_start,
                period_end=observation.period_end or document.period_end,
                as_of=observation.as_of or document.period_end,
                confidence=observation.confidence,
                normalization_json=observation.taxonomy_mapping,
                metadata_json={
                    "rationale": observation.rationale,
                    "taxonomy_version": TAXONOMY_VERSION,
                },
            )
        )
        observation_count += 1

    await session.flush()
    return {
        "fact_count": fact_count,
        "relation_count": relation_count,
        "observation_count": observation_count,
        "facet_count": facet_count,
        "evidence_count": len(evidence_cache),
        "invalid_fact_count": invalid_fact_count,
        "invalid_relation_count": invalid_relation_count,
    }


async def _delete_previous_extraction(session: AsyncSession, document_id: str) -> None:
    await session.execute(delete(Observation).where(Observation.document_id == document_id))
    await session.execute(delete(DocumentFacet).where(DocumentFacet.document_id == document_id))
    await session.execute(delete(Relation).where(Relation.document_id == document_id))
    await session.execute(delete(Fact).where(Fact.document_id == document_id))
    await session.execute(delete(EntityMention).where(EntityMention.document_id == document_id))
    await session.execute(delete(EvidenceSpan).where(EvidenceSpan.document_id == document_id))
    await session.execute(delete(ReviewQueueItem).where(ReviewQueueItem.document_id == document_id))


async def _entity(
    session: AsyncSession,
    issuer: Issuer,
    canonical_name: str,
    display_name: str,
    entity_type: str,
    cache: dict[str, Entity],
) -> Entity:
    canonical = " ".join(canonical_name.strip().split())
    key = canonical.casefold()
    if key in cache:
        return cache[key]
    entity = await session.scalar(
        select(Entity).where(Entity.issuer_id == issuer.id, Entity.canonical_name == canonical).limit(1)
    )
    if entity is None:
        entity = Entity(
            issuer_id=issuer.id,
            canonical_name=canonical,
            display_name=display_name.strip() or canonical,
            entity_type=entity_type,
            metadata_json={"taxonomy_version": TAXONOMY_VERSION},
        )
        session.add(entity)
        await session.flush()
    cache[key] = entity
    return entity


async def _evidence_span(
    session: AsyncSession,
    issuer: Issuer,
    document: Document,
    markdown: str,
    nodes_by_id: dict[str, DocumentTreeNode],
    evidence: EvidenceRef,
    cache: dict[tuple[str, str], EvidenceSpan],
    statement_markdown: str | None = None,
) -> EvidenceSpan:
    node = nodes_by_id.get(evidence.node_id)
    if node is None:
        raise ValueError(f"evidence node_id không tồn tại: {evidence.node_id}")
    location = _locate_quote(
        markdown,
        node,
        evidence.quote,
        evidence.line_hint or evidence.line_start,
        all_nodes=list(nodes_by_id.values()),
        statement_markdown=statement_markdown,
    )
    resolved_node_id = str(location.get("actual_node_id") or evidence.node_id)
    key = (resolved_node_id, evidence.quote)
    if key in cache:
        return cache[key]
    span = EvidenceSpan(
        issuer_id=issuer.id,
        document_id=document.id,
        node_id=resolved_node_id,
        start_line=location["start_line"],
        end_line=location["end_line"],
        start_char=location["start_char"],
        end_char=location["end_char"],
        quote_hash=sha256_text(str(location["resolved_quote"])),
        metadata_json={
            "quote": location["resolved_quote"],
            "raw_llm_quote": evidence._raw_llm_quote,
            "quote_resolution": location["resolution"],
            "cited_node_id": evidence.node_id,
            "taxonomy_version": TAXONOMY_VERSION,
        },
    )
    session.add(span)
    await session.flush()
    cache[key] = span
    return span


def _locate_quote(
    markdown: str,
    node: DocumentTreeNode,
    quote: str,
    line_hint: int | None = None,
    *,
    all_nodes: list[DocumentTreeNode] | None = None,
    statement_markdown: str | None = None,
) -> dict[str, int | str]:
    lines = markdown.splitlines()
    content = "\n".join(lines[int(node.start_line) - 1 : int(node.end_line)])
    hits: list[int] = []
    start = 0
    while True:
        idx = content.find(quote, start)
        if idx < 0:
            break
        hits.append(idx)
        start = idx + max(1, len(quote))
    if len(hits) != 1 and line_hint is not None:
        hinted = _hit_for_line_hint(content, hits, int(node.start_line), int(line_hint))
        if hinted is not None:
            hits = [hinted]
    resolution = "exact"
    resolved_quote = quote
    if len(hits) != 1:
        is_multiline = len([line for line in quote.splitlines() if line.strip()]) >= 2
        if not is_multiline:
            fallback = _fallback_line_match(lines, node, quote, line_hint)
            if fallback is not None:
                return fallback
        normalized = _normalized_fuzzy_location(content, node, quote)
        if normalized is not None:
            return normalized
        multiline = _multiline_fuzzy_location(lines, node, quote)
        if multiline is not None:
            return multiline
        if all_nodes:
            reattributed = _reattributed_location(markdown, lines, all_nodes, node, quote)
            if reattributed is not None:
                return reattributed
        if statement_markdown and quote in statement_markdown:
            s_lines = statement_markdown.splitlines()
            char_pos = statement_markdown.find(quote)
            l_hint = int(line_hint) if (line_hint and 1 <= int(line_hint) <= len(s_lines)) else 1
            return {
                "start_line": l_hint,
                "end_line": l_hint + quote.count("\n"),
                "start_char": char_pos,
                "end_char": char_pos + len(quote),
                "resolved_quote": quote,
                "resolution": "statement_table_exact",
                "actual_node_id": node.node_id,
            }
    if len(hits) != 1:
        raise ValueError(
            f"quote phải xuất hiện duy nhất trong node {node.node_id}; occurrences={len(hits)}; quote={quote[:120]!r}"
        )
    start_char = hits[0]
    end_char = start_char + len(quote)
    prefix = content[:start_char]
    quoted = content[start_char:end_char]
    start_line = int(node.start_line) + prefix.count("\n")
    end_line = start_line + quoted.count("\n")
    return {
        "start_line": start_line,
        "end_line": end_line,
        "start_char": start_char,
        "end_char": end_char,
        "resolved_quote": resolved_quote,
        "resolution": resolution,
    }


def _hit_for_line_hint(content: str, hits: list[int], node_start_line: int, line_hint: int) -> int | None:
    if not hits or line_hint < node_start_line:
        return None
    for hit in hits:
        absolute_line = node_start_line + content[:hit].count("\n")
        if absolute_line == line_hint:
            return hit
    return None


def _fold_with_map(content: str) -> tuple[str, list[int]]:
    folded_chars: list[str] = []
    index_map: list[int] = []
    for idx, ch in enumerate(content):
        for part in unicodedata.normalize("NFKD", ch.casefold()):
            if unicodedata.combining(part):
                continue
            folded_chars.append(" " if part.isspace() else part)
            index_map.append(idx)
    return "".join(folded_chars), index_map


def _normalized_fuzzy_location(
    content: str,
    node: DocumentTreeNode,
    quote: str,
) -> dict[str, int | str] | None:
    folded_content, index_map = _fold_with_map(content)
    folded_quote, _ = _fold_with_map(quote)
    if len(folded_quote) < 12:
        return None
    hits: list[int] = []
    start = 0
    while True:
        idx = folded_content.find(folded_quote, start)
        if idx < 0:
            break
        hits.append(idx)
        start = idx + max(1, len(folded_quote))
    if len(hits) != 1:
        return None
    start_char = index_map[hits[0]]
    end_char = index_map[hits[0] + len(folded_quote) - 1] + 1
    prefix = content[:start_char]
    quoted = content[start_char:end_char]
    start_line = int(node.start_line) + prefix.count("\n")
    end_line = start_line + quoted.count("\n")
    return {
        "start_line": start_line,
        "end_line": end_line,
        "start_char": start_char,
        "end_char": end_char,
        "resolved_quote": quoted,
        "resolution": "normalized_fuzzy",
    }


def _multiline_fuzzy_location(
    lines: list[str],
    node: DocumentTreeNode,
    quote: str,
) -> dict[str, int | str] | None:
    quote_lines = [line for line in quote.splitlines() if line.strip()]
    if len(quote_lines) < 2:
        return None
    start_line = int(node.start_line)
    end_line = int(node.end_line)
    node_lines = [(line_no, lines[line_no - 1]) for line_no in range(start_line, end_line + 1)]
    if len(node_lines) < len(quote_lines):
        return None
    runs: list[int] = []
    for offset in range(len(node_lines) - len(quote_lines) + 1):
        matched = True
        for depth, quote_line in enumerate(quote_lines):
            if not _quote_compatible_with_line(quote_line, node_lines[offset + depth][1]):
                matched = False
                break
        if matched:
            runs.append(offset)
    if len(runs) != 1:
        return None
    first_no = node_lines[runs[0]][0]
    last_no = node_lines[runs[0] + len(quote_lines) - 1][0]
    content_before = "\n".join(lines[start_line - 1 : first_no - 1])
    start_char = len(content_before) + (1 if content_before else 0)
    resolved = "\n".join(lines[first_no - 1 : last_no])
    return {
        "start_line": first_no,
        "end_line": last_no,
        "start_char": start_char,
        "end_char": start_char + len(resolved),
        "resolved_quote": resolved,
        "resolution": "multiline_fuzzy",
    }


def _find_all(haystack: str, needle: str) -> list[int]:
    if not needle:
        return []
    hits: list[int] = []
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx < 0:
            return hits
        hits.append(idx)
        start = idx + max(1, len(needle))


def _deepest_owner(nodes: list[DocumentTreeNode], line_no: int) -> DocumentTreeNode | None:
    candidates = [
        node
        for node in nodes
        if int(node.start_line) <= line_no <= int(node.end_line)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda node: (int(node.start_line), -int(node.end_line)))


def _reattributed_location(
    markdown: str,
    lines: list[str],
    all_nodes: list[DocumentTreeNode],
    cited_node: DocumentTreeNode,
    quote: str,
) -> dict[str, int | str] | None:
    hits = _find_all(markdown, quote)
    folded_hits: list[int] = []
    folded_map: list[int] = []
    if len(hits) != 1:
        folded_markdown, folded_map = _fold_with_map(markdown)
        folded_quote, _ = _fold_with_map(quote)
        if len(folded_quote) >= 12:
            folded_hits = _find_all(folded_markdown, folded_quote)
        if len(folded_hits) != 1:
            return None
        hit_line = markdown.count("\n", 0, folded_map[folded_hits[0]]) + 1
    else:
        hit_line = markdown.count("\n", 0, hits[0]) + 1
    owner = _deepest_owner(all_nodes, hit_line)
    if owner is None or owner.node_id == cited_node.node_id:
        return None
    location = _locate_quote(markdown, owner, quote, None)
    if location.get("resolution") not in {"exact", "normalized_fuzzy"}:
        return None
    location["actual_node_id"] = owner.node_id
    location["cited_node_id"] = cited_node.node_id
    location["resolution"] = f"reattributed_{location['resolution']}"
    return location


def _fallback_line_match(
    lines: list[str],
    node: DocumentTreeNode,
    quote: str,
    line_hint: int | None,
) -> dict[str, int | str] | None:
    start_line = int(node.start_line)
    end_line = int(node.end_line)
    node_lines = [(line_no, lines[line_no - 1]) for line_no in range(start_line, end_line + 1)]

    if line_hint is not None and start_line <= line_hint <= end_line:
        line_text = lines[line_hint - 1]
        if _quote_compatible_with_line(quote, line_text):
            return _line_location(lines, start_line, line_hint, line_text, "line_hint_fuzzy")

    compatible = [
        (line_no, line_text)
        for line_no, line_text in node_lines
        if line_text.strip() and _quote_compatible_with_line(quote, line_text)
    ]
    if len(compatible) == 1:
        line_no, line_text = compatible[0]
        return _line_location(lines, start_line, line_no, line_text, "unique_line_fuzzy")
    return None


def _line_location(
    lines: list[str],
    node_start_line: int,
    line_no: int,
    line_text: str,
    resolution: str,
) -> dict[str, int | str]:
    content_before = "\n".join(lines[node_start_line - 1 : line_no - 1])
    start_char = len(content_before) + (1 if content_before else 0)
    return {
        "start_line": line_no,
        "end_line": line_no,
        "start_char": start_char,
        "end_char": start_char + len(line_text),
        "resolved_quote": line_text,
        "resolution": resolution,
    }


def _quote_compatible_with_line(quote: str, line_text: str) -> bool:
    quote_numbers = _number_tokens(quote)
    quote_long_numbers = [token for token in quote_numbers if len(token) >= 6]
    if quote_long_numbers:
        line_numbers = set(_number_tokens(line_text))
        return all(token in line_numbers for token in set(quote_long_numbers))
    if quote_numbers:
        line_numbers = set(_number_tokens(line_text))
        matched_numbers = sum(1 for token in quote_numbers if token in line_numbers)
        if matched_numbers >= min(len(set(quote_numbers)), 2):
            return True

    quote_terms = _word_tokens(quote)
    if len(quote_terms) < 3:
        return False
    line_terms = set(_word_tokens(line_text))
    matched_terms = sum(1 for token in set(quote_terms) if token in line_terms)
    return matched_terms >= max(3, int(len(set(quote_terms)) * 0.65))


def _number_tokens(value: str) -> list[str]:
    return [re.sub(r"\D", "", token) for token in re.findall(r"\d[\d.,]*", value) if re.sub(r"\D", "", token)]


def _word_tokens(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    asciiish = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.findall(r"[a-z0-9]{2,}", asciiish)


def _fact_value_grounded(fact: FactIn) -> bool:
    if fact.value_numeric is None and not fact.value_text:
        return True
    quote = fact.evidence.quote
    if fact.value_numeric is not None and abs(float(fact.value_numeric)) < 1e-12:
        if str(fact.value_text or "").strip().casefold() in {"-", "–", "—", "nil", "null", "0", "0,0", "0.0"}:
            return True
    if fact.value_text and _value_grounded_in_quote(fact.value_text, quote):
        return True
    if fact.value_numeric is not None and _value_grounded_in_quote(fact.value_numeric, quote):
        return True
    return False


def _value_grounded_in_quote(value: object, quote: str) -> bool:
    value_numbers = _grounding_number_candidates(value)
    if not value_numbers:
        return True
    quote_numbers = set(_number_tokens(quote))
    for number in value_numbers:
        if number in quote_numbers:
            return True
        if len(number) >= 4 and any(
            candidate.startswith(number) or number.startswith(candidate)
            for candidate in quote_numbers
            if len(candidate) >= 4
        ):
            return True
    return False


def _grounding_number_candidates(value: object) -> list[str]:
    if isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [str(abs(value))]
    if isinstance(value, float):
        if not math.isfinite(value):
            return []
        absolute = abs(value)
        if absolute.is_integer():
            return [str(int(absolute))]
        normalized = f"{absolute:.12f}".rstrip("0").rstrip(".")
        compact = re.sub(r"\D", "", normalized)
        return [compact] if compact else []
    return _number_tokens(str(value))


async def _review_item(
    session: AsyncSession,
    issuer: Issuer,
    document: Document,
    item_type: str,
    reason: str,
    payload: dict[str, Any],
) -> None:
    session.add(
        ReviewQueueItem(
            issuer_id=issuer.id,
            document_id=document.id,
            item_type=item_type,
            status="OPEN",
            reason=reason,
            payload_json=payload,
        )
    )
