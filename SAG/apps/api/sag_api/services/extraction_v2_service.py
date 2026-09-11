from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.errors import ConfigurationError
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
    MoatSignal,
    Observation,
    Relation,
    ReviewQueueItem,
)
from sag_api.enums import EntityType, FactType, MoatPillar, ProcessingStageStatus, RelationType, ValidationStatus
from sag_api.generation.llm import LLMClient
from sag_api.services.document_structure_service import sha256_text

TAXONOMY_VERSION = "financial-evidence-taxonomy-v2"
EXTRACTION_PROMPT_VERSION = "financial-full-document-extraction-v4"


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str | None = None
    quote: str = Field(min_length=1, max_length=4000)
    line_hint: int | None = Field(default=None, ge=1)


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

    subject: str = Field(min_length=1, max_length=512)
    object: str = Field(min_length=1, max_length=512)
    relation_type: RelationType
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


class MoatSignalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pillar: MoatPillar
    direction: Literal["positive", "counter"]
    strength: Literal["weak", "medium", "strong"]
    durability: Literal["unknown", "short", "medium", "long"]
    materiality: Literal["unknown", "low", "medium", "high"]
    signal: str = Field(min_length=1, max_length=512)
    extraction_rationale: str | None = Field(default=None, max_length=1200)
    evidence: EvidenceRef


class GilDisclosureIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disclosure_type: Literal["risk", "no_risk", "scope", "data_quality"]
    label: str = Field(min_length=1, max_length=512)
    extraction_rationale: str | None = Field(default=None, max_length=1200)
    evidence: EvidenceRef


class DocumentFacetIn(BaseModel):
    """A discoverable content facet, deliberately independent of document role."""

    model_config = ConfigDict(extra="forbid")

    facet: str = Field(min_length=2, max_length=128)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str | None = Field(default=None, max_length=1200)
    evidence: EvidenceRef


class ObservationIn(BaseModel):
    """An open, evidence-backed interpretation that is not forced into taxonomy."""

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


class ExtractionManifestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taxonomy_version: str = TAXONOMY_VERSION
    node_annotations: list[NodeAnnotationIn]
    entity_mentions: list[EntityMentionIn] = Field(default_factory=list)
    facts: list[FactIn] = Field(default_factory=list)
    relations: list[RelationIn] = Field(default_factory=list)
    moat_signals: list[MoatSignalIn] = Field(default_factory=list)
    gil_disclosures: list[GilDisclosureIn] = Field(default_factory=list)
    document_facets: list[DocumentFacetIn] = Field(default_factory=list)
    observations: list[ObservationIn] = Field(default_factory=list)


@dataclass(frozen=True)
class ExtractionResult:
    status: str
    fact_count: int = 0
    relation_count: int = 0
    moat_signal_count: int = 0
    observation_count: int = 0
    facet_count: int = 0
    evidence_count: int = 0
    token_usage: int = 0
    error: str | None = None
    metadata: dict[str, Any] | None = None


def extraction_json_schema() -> dict[str, Any]:
    return ExtractionManifestIn.model_json_schema()


def build_extraction_prompt(
    issuer: Issuer,
    document: Document,
    markdown: str,
    nodes: list[DocumentTreeNode],
) -> list[dict[str, str]]:
    numbered_markdown = "\n".join(f"{index + 1}: {line}" for index, line in enumerate(markdown.splitlines()))
    system = (
        "You are SAG v2, a financial evidence extraction engine for Vietnamese public-company reports. "
        "Return only valid JSON matching the provided schema. Do not invent taxonomy enum values for normalized facts/relations. "
        "The allowed enum sets are closed: every fact_type/entity_type/relation_type you output "
        "must be exactly one of the allowed_enums values. Any other concept "
        "(for example cash_balance) must use 'other' plus raw_label and taxonomy_candidate. "
        "Use OTHER plus taxonomy_candidate for industry-specific normalized concepts. "
        "Use open observations for industry-specific drivers, changes, risks, policy disclosures, business context, and other meaning that does not fit a normalized metric. "
        "Observation predicates and topic_tags are open strings; do not force them into an enum. Do not score MOAT or GIL. "
        "Every extracted item must cite an exact quote and its exact Markdown line number. "
        "Quotes should be long enough to identify one source span."
    )
    user = {
        "task": "full_document_financial_evidence_extraction",
        "prompt_version": EXTRACTION_PROMPT_VERSION,
        "taxonomy_version": TAXONOMY_VERSION,
        "ticker": issuer.ticker,
        "document_id": document.id,
        "doc_role": document.doc_role,
        "period": {
            "fiscal_year": document.fiscal_year,
            "fiscal_quarter": document.fiscal_quarter,
            "period_start": str(document.period_start) if document.period_start else None,
            "period_end": str(document.period_end) if document.period_end else None,
        },
        "rules": [
            "Do not emit evidence.node_id. Return node_annotations as an empty array; SAG resolves internal nodes from evidence.line_hint and validates each quote.",
            "Create facts only when there is a concrete financial/economic disclosure.",
            "Create directed relations only when subject, object and direction are explicit.",
            "TRANSACTS_WITH is not a capital-flow edge.",
            "Use exact quotes copied from the node without line numbers. Include enough surrounding words/table labels so the quote appears exactly once in the referenced node.",
            "Copy quotes verbatim, preserving every Vietnamese diacritic exactly as printed. Never strip tones or normalize spelling.",
            "Always include evidence.line_hint with the exact Markdown line number from markdown_with_line_numbers. It is mandatory for every evidence item.",
            "For table numbers or short repeated values, evidence.line_hint is mandatory: use the Markdown line number from markdown_with_line_numbers.",
            "Never output enum values outside allowed_enums. Examples: cash_balance, inventory_balance, operating_cash_flow, production_volume, market_share, board_member, audit_opinion must use fact_type/entity_type/relation_type='other' plus raw_label and taxonomy_candidate.",
            "If you are unsure whether a label fits an enum, use 'other' and put the original concept in taxonomy_candidate.",
            "Create document_facets only for content demonstrably present in this document. A facet is an open content capability (for example asset_quality, production_capacity, related_party, strategy), not a document type.",
            "Create observations for material, source-grounded meaning that a future question may need, including business profile, changes, causal drivers, risks, policy changes, and sector-specific disclosures. Preserve the meaning in statement even if it cannot be normalized today.",
            "Every observation and document_facet must cite exact evidence. Each observation.statement must stay within the scope of its own quote: do not aggregate multiple table rows, infer totals/rankings, or use terms such as primary, highest, lowest, material, or mainly unless that exact claim is explicit in the quote.",
            "An observation is not a fact: do not invent numbers, causality, intent, or comparative conclusions beyond the cited wording.",
        ],
        "allowed_enums": {
            "entity_type": [item.value for item in EntityType],
            "fact_type": [item.value for item in FactType],
            "relation_type": [item.value for item in RelationType],
            "moat_pillar": [item.value for item in MoatPillar],
        },
        "json_schema": extraction_json_schema(),
        "markdown_with_line_numbers": numbered_markdown,
    }
    return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]


_FACT_TYPES = frozenset(item.value for item in FactType)
_ENTITY_TYPES = frozenset(item.value for item in EntityType)
_RELATION_TYPES = frozenset(item.value for item in RelationType)

# (collection_key, enum_field, allowed_values) walked by the coercion pass.
_ENUM_COERCION_TARGETS = (
    ("facts", "fact_type", _FACT_TYPES),
    ("entity_mentions", "entity_type", _ENTITY_TYPES),
    ("relations", "relation_type", _RELATION_TYPES),
)

_TABLE_SEPARATOR_RE = re.compile(r"^\s*:?-{2,}:?\s*$")
_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_QUARTER_RE = re.compile(r"qu[ýy]\s*([ivx]+|\d{1,2})\s*n[aă]m\s*(\d{4})", re.IGNORECASE)
_NUMERIC_CELL_RE = re.compile(r"^\(?-?\d[\d., ]*\)?%?$")

_ROMAN_QUARTERS = {"i": 1, "ii": 2, "iii": 3, "iv": 4}

_TABLE_FACT_RULES: tuple[tuple[tuple[str, ...], FactType, str, str | None], ...] = (
    (("tien mat",), FactType.OTHER, "cash_on_hand", "cash_balance"),
    (("tien gui ngan hang",), FactType.OTHER, "bank_deposit_demand", "cash_balance"),
    (("cac khoan tuong duong tien",), FactType.OTHER, "cash_equivalents", "cash_balance"),
    (("tien", "cong"), FactType.OTHER, "cash_and_equivalents", "cash_balance"),
    (("phai thu",), FactType.RECEIVABLE_BALANCE, "receivable_balance", None),
    (("phai tra",), FactType.PAYABLE_BALANCE, "payable_balance", None),
    (("co tuc",), FactType.DIVIDEND, "dividend", None),
    (("von gop",), FactType.EQUITY, "contributed_capital", None),
    (("von chu",), FactType.EQUITY, "equity", None),
    (("loi nhuan",), FactType.PROFIT, "profit", None),
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
    """Coerce invented enum values to 'other' in place, keeping the original.

    Strict single-request policy removed the validation retry, so a single
    invented value (e.g. fact_type='cash_balance') would fail the whole
    full-document manifest. This pass enforces the same contract the prompt
    already states: unknown concepts become 'other' with the original kept in
    raw_label/taxonomy_candidate (when those are empty), which also routes the
    item into the existing review-queue flow. Returns the coercion records.
    """
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


def enrich_manifest_with_table_facts(
    document: Document,
    markdown: str,
    nodes: list[DocumentTreeNode],
    manifest: ExtractionManifestIn,
) -> dict[str, Any]:
    """Add deterministic facts for every numeric markdown-table cell.

    LLM extraction is good at semantic hints but weak at exhaustive table
    coverage. This pass keeps the same single pipeline while making tabular
    facts complete and directly grounded to the source line.
    """

    existing = {_fact_identity(fact) for fact in manifest.facts}
    added = 0
    skipped = 0
    for line_no, cells, headers in _iter_markdown_table_body_rows(markdown):
        if _is_separator_row(cells) or len(cells) < 2:
            continue
        node = _deepest_owner(nodes, line_no)
        if node is None:
            skipped += 1
            continue
        row_label = _row_label(cells)
        if not row_label:
            continue
        if _looks_like_table_header(row_label, cells):
            continue
        section = node.heading_path
        quote = markdown.splitlines()[line_no - 1].strip()
        for index, cell in enumerate(cells[1:], start=1):
            parsed = _parse_table_value(cell)
            if parsed is None:
                continue
            value_numeric, value_text = parsed
            header = headers.get(index, "")
            if "ma so" in _fold_text(header) or _is_accounting_code_cell(cells, index, value_text):
                skipped += 1
                continue
            fact_type, semantic_key, taxonomy_candidate = _classify_table_fact(section, row_label)
            period_start, period_end, as_of = _period_from_column(header)
            if period_start is None and period_end is None and as_of is None:
                as_of = document.period_end
            unit = _unit_from_context(header, row_label, value_text)
            fact = FactIn(
                fact_type=fact_type,
                label=_table_fact_label(row_label, header),
                semantic_key=semantic_key,
                value_text=value_text,
                value_numeric=value_numeric,
                unit=unit,
                currency="VND" if unit == "VND" else None,
                period_start=period_start,
                period_end=period_end,
                as_of=as_of,
                raw_label=row_label[:256],
                extraction_rationale="Deterministic markdown table extraction.",
                taxonomy_candidate=taxonomy_candidate,
                evidence=EvidenceRef(node_id=node.node_id, quote=quote, line_hint=line_no),
            )
            identity = _fact_identity(fact)
            if identity in existing:
                skipped += 1
                continue
            existing.add(identity)
            manifest.facts.append(fact)
            added += 1
    return {"table_fact_added": added, "table_fact_skipped": skipped}


def sanitize_manifest_value_grounding(
    manifest: ExtractionManifestIn,
) -> dict[str, Any]:
    """Drop fact values not present in their quote and strip ungrounded relation fields."""

    kept_facts: list[FactIn] = []
    dropped_facts = 0
    stripped_relation_amounts = 0
    stripped_relation_pcts = 0
    for fact in manifest.facts:
        if _fact_value_grounded(fact):
            kept_facts.append(fact)
        else:
            dropped_facts += 1
    manifest.facts = kept_facts

    for relation in manifest.relations:
        if relation.amount_vnd is not None and not _value_grounded_in_quote(relation.amount_vnd, relation.evidence.quote):
            relation.amount_vnd = None
            stripped_relation_amounts += 1
        if relation.ownership_pct is not None and not _value_grounded_in_quote(relation.ownership_pct, relation.evidence.quote):
            relation.ownership_pct = None
            stripped_relation_pcts += 1
    return {
        "ungrounded_facts_dropped": dropped_facts,
        "ungrounded_relation_amounts_stripped": stripped_relation_amounts,
        "ungrounded_relation_pcts_stripped": stripped_relation_pcts,
    }


def enrich_manifest_facets_from_observations(manifest: ExtractionManifestIn) -> dict[str, int]:
    """Promote open observation topics to discoverable document facets.

    A model may correctly retain a business-profile observation while omitting
    the redundant document-level facet. The same grounded evidence can safely
    make that capability discoverable without another model call.
    """

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


def fill_missing_node_annotations(nodes: list[DocumentTreeNode], manifest: ExtractionManifestIn) -> int:
    """Make node coverage deterministic; LLM annotations are enrichment, not a gate."""
    known = {item.node_id for item in manifest.node_annotations}
    added = 0
    for node in nodes:
        if node.node_id not in known:
            manifest.node_annotations.append(NodeAnnotationIn(node_id=node.node_id, relevance="NONE"))
            added += 1
    return added


def _iter_markdown_table_rows(markdown: str):
    for line_no, line in enumerate(markdown.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        yield line_no, cells


def _iter_markdown_table_body_rows(markdown: str):
    rows = list(_iter_markdown_table_rows(markdown))
    block: list[tuple[int, list[str]]] = []
    previous_line = 0
    for line_no, cells in rows:
        if block and line_no != previous_line + 1:
            yield from _body_rows_for_table_block(block)
            block = []
        block.append((line_no, cells))
        previous_line = line_no
    if block:
        yield from _body_rows_for_table_block(block)


def _body_rows_for_table_block(block: list[tuple[int, list[str]]]):
    separator_index = next((index for index, (_line_no, cells) in enumerate(block) if _is_separator_row(cells)), None)
    if separator_index is None:
        return
    header_rows = [cells for _line_no, cells in block[:separator_index]]
    data_rows = block[separator_index + 1 :]
    max_columns = max((len(cells) for _line_no, cells in block), default=0)
    headers: dict[int, str] = {}
    for column in range(max_columns):
        parts: list[str] = []
        for cells in header_rows:
            if column >= len(cells):
                continue
            cell = cells[column].strip()
            if cell and _parse_table_value(cell) is None:
                parts.append(cell)
        headers[column] = " ".join(parts)
    for line_no, cells in data_rows:
        yield line_no, cells, headers


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(not cell or _TABLE_SEPARATOR_RE.match(cell) for cell in cells)


def _looks_like_table_header(row_label: str, cells: list[str]) -> bool:
    folded = _fold_text(" ".join(cells))
    if _DATE_RE.search(" ".join(cells)) or _QUARTER_RE.search(folded):
        return True
    if not any(_parse_table_value(cell) is not None for cell in cells[1:]):
        return True
    return False


def _row_label(cells: list[str]) -> str:
    for cell in cells:
        value = cell.strip()
        if value and _parse_table_value(value) is None and not _TABLE_SEPARATOR_RE.match(value):
            return value
    return ""


def _parse_table_value(cell: str) -> tuple[float, str] | None:
    value = cell.strip()
    if not value or value in {"-", "–", "—"}:
        return None
    if not _NUMERIC_CELL_RE.match(value):
        return None
    negative = value.startswith("(") and value.endswith(")")
    is_pct = value.endswith("%")
    digits = re.sub(r"[^\d]", "", value)
    if not digits:
        return None
    if is_pct:
        normalized = value.strip("%").strip("()").replace(".", "").replace(",", ".").replace(" ", "")
        try:
            numeric = float(normalized)
        except ValueError:
            return None
    else:
        numeric = float(digits)
    if negative:
        numeric = -numeric
    return numeric, value


def _is_accounting_code_cell(cells: list[str], index: int, value_text: str) -> bool:
    """Detect a `Mã số` column when a converted table continues on a new page.

    MinerU may split a table at a page boundary and repeat only the data rows,
    leaving the continuation block without its original `Mã số` header.
    """

    if index != 1 or not re.fullmatch(r"\d{3}", value_text.strip()):
        return False
    return any(
        len(re.sub(r"\D", "", cell)) >= 4
        for cell in cells[index + 1 :]
        if _parse_table_value(cell) is not None
    )


def _column_context(markdown: str, line_no: int, cell_index: int) -> str:
    lines = markdown.splitlines()
    contexts: list[str] = []
    for previous in range(line_no - 1, max(0, line_no - 6), -1):
        stripped = lines[previous - 1].strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            break
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if _is_separator_row(cells):
            continue
        if cell_index < len(cells) and cells[cell_index]:
            contexts.insert(0, cells[cell_index])
    return " ".join(contexts).strip()


def _period_from_column(header: str) -> tuple[date | None, date | None, date | None]:
    match = _DATE_RE.search(header)
    if match:
        day, month, year = (int(part) for part in match.groups())
        try:
            return None, None, date(year, month, day)
        except ValueError:
            return None, None, None
    folded = _fold_text(header)
    quarter = _QUARTER_RE.search(folded)
    if quarter:
        raw_q, raw_year = quarter.groups()
        q = _ROMAN_QUARTERS.get(raw_q.casefold(), None)
        if q is None:
            try:
                q = int(raw_q)
            except ValueError:
                q = None
        if q in {1, 2, 3, 4}:
            year = int(raw_year)
            start_month = (q - 1) * 3 + 1
            end_month = start_month + 2
            end_day = 30 if end_month in {6, 9} else 31
            return date(year, start_month, 1), date(year, end_month, end_day), None
    return None, None, None


def _classify_table_fact(section: str, row_label: str) -> tuple[FactType, str, str | None]:
    folded = _fold_text(f"{section} {row_label}")
    for terms, fact_type, semantic_key, taxonomy_candidate in _TABLE_FACT_RULES:
        if all(term in folded for term in terms):
            return fact_type, semantic_key, taxonomy_candidate
    return FactType.OTHER, _semantic_key("table", row_label), "unmapped_table_line_item"


def _table_fact_label(row_label: str, header: str) -> str:
    parts = [row_label.strip()]
    if header.strip():
        parts.append(header.strip())
    return " - ".join(parts)[:512]


def _unit_from_context(header: str, row_label: str, value_text: str) -> str | None:
    folded = _fold_text(f"{header} {row_label}")
    if value_text.strip().endswith("%"):
        return "%"
    if "co phieu" in folded:
        return "cổ phiếu"
    if "vnd" in folded or len(re.sub(r"\D", "", value_text)) >= 4:
        return "VND"
    return None


def _fact_identity(fact: FactIn) -> tuple[Any, ...]:
    return (
        fact.semantic_key or _semantic_key(fact.fact_type.value, fact.label),
        fact.value_text,
        fact.value_numeric,
        str(fact.period_start),
        str(fact.period_end),
        str(fact.as_of),
        fact.evidence.node_id,
        fact.evidence.line_hint,
    )


def _fact_value_grounded(fact: FactIn) -> bool:
    if fact.value_numeric is None and not fact.value_text:
        return True
    quote = fact.evidence.quote
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
        # A float can lose the original decimal separator/scale when persisted.
        # Accept only substantial shared prefixes to avoid matching small numbers.
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


def _fold_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return stripped.replace("đ", "d")


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
            yield f"{name}.{index}", item.evidence


def resolve_llm_evidence_nodes(markdown: str, nodes: list[DocumentTreeNode], manifest: ExtractionManifestIn) -> None:
    """Resolve LLM evidence by cited line, keeping opaque node IDs outside the prompt."""
    manifest.node_annotations.clear()
    for path, evidence in _iter_manifest_refs(manifest):
        if evidence.line_hint is None:
            raise ValueError(f"{path} thiếu evidence.line_hint")
        candidates = sorted(
            (node for node in nodes if node.start_line <= evidence.line_hint <= node.end_line),
            key=lambda node: (node.end_line - node.start_line, -int(node.level or 0), int(node.order_index or 0)),
        )
        for node in candidates:
            try:
                _locate_quote(markdown, node, evidence.quote, evidence.line_hint, all_nodes=nodes)
            except ValueError:
                continue
            evidence.node_id = node.node_id
            break
        else:
            raise ValueError(f"{path} không thể resolve node từ line_hint={evidence.line_hint}")


def _collect_grounding_failures(
    markdown: str,
    nodes: list[DocumentTreeNode],
    manifest: ExtractionManifestIn,
) -> list[dict[str, Any]]:
    """Ground every ref in isolation; return structured records for failures only.

    This is how the runner knows exactly which items are broken without
    re-running the whole document: each record carries the manifest path, the
    cited node, the bad quote and the exact validator error.
    """
    nodes_by_id = {node.node_id: node for node in nodes}
    failures: list[dict[str, Any]] = []
    for path, ref in _iter_manifest_refs(manifest):
        node = nodes_by_id.get(ref.node_id)
        if node is None:
            failures.append(
                {
                    "path": path,
                    "node_id": ref.node_id,
                    "quote": ref.quote,
                    "line_hint": ref.line_hint,
                    "error": f"evidence node_id không tồn tại: {ref.node_id}",
                }
            )
            continue
        try:
            _locate_quote(markdown, node, ref.quote, ref.line_hint, all_nodes=nodes)
        except ValueError as exc:
            failures.append(
                {
                    "path": path,
                    "node_id": ref.node_id,
                    "quote": ref.quote,
                    "line_hint": ref.line_hint,
                    "error": str(exc)[:1000],
                }
            )
    return failures


def _drop_ungrounded_manifest_items(manifest: ExtractionManifestIn, failures: list[dict[str, Any]]) -> list[str]:
    """Discard only ungrounded LLM items; deterministic table facts remain intact."""
    dropped: list[str] = []
    for prefix, items in (
        ("entity_mentions", manifest.entity_mentions),
        ("facts", manifest.facts),
        ("relations", manifest.relations),
        ("moat_signals", manifest.moat_signals),
        ("gil_disclosures", manifest.gil_disclosures),
        ("document_facets", manifest.document_facets),
        ("observations", manifest.observations),
    ):
        indices = sorted(
            (int(item["path"].split(".")[1]) for item in failures if item["path"].startswith(f"{prefix}.")),
            reverse=True,
        )
        for index in indices:
            if 0 <= index < len(items):
                items.pop(index)
                dropped.append(f"{prefix}.{index}")
    return dropped


class RepairedEvidenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    node_id: str
    quote: str = Field(min_length=1, max_length=4000)
    line_hint: int | None = Field(default=None, ge=1)


class RepairResponseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RepairedEvidenceIn]


def build_repair_prompt(
    markdown: str,
    nodes: list[DocumentTreeNode],
    failures: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Minimal repair prompt: only broken refs plus their cited node text."""
    lines = markdown.splitlines()
    nodes_by_id = {node.node_id: node for node in nodes}
    numbered = "\n".join(f"{i + 1}: {line}" for i, line in enumerate(lines))
    _ = numbered  # line numbers are embedded per node slice below
    items: list[dict[str, Any]] = []
    for failure in failures:
        node = nodes_by_id.get(str(failure["node_id"]))
        if node is not None:
            start, end = int(node.start_line), int(node.end_line)
            window = lines[start - 1 : min(end, start + 199)]
            node_text = "\n".join(f"{start + i}: {text}" for i, text in enumerate(window))
            if end - start + 1 > 200:
                node_text += f"\n[... {end - start + 1 - 200} dòng cuối node bị cắt ...]"
        else:
            start = end = 0
            node_text = "(node_id không tồn tại; hãy chọn node đúng từ node_manifest)"
        items.append(
            {
                "path": failure["path"],
                "cited_node_id": failure["node_id"],
                "cited_node_lines": [start, end],
                "cited_node_text_with_line_numbers": node_text,
                "bad_quote": failure["quote"],
                "bad_line_hint": failure["line_hint"],
                "validator_error": failure["error"],
            }
        )
    system = (
        "You fix broken evidence citations in a financial extraction manifest. "
        "Return only valid JSON matching the response schema. Never invent numbers: "
        "every quote must be copied verbatim from a single node listed in node_manifest, "
        "preserving Vietnamese diacritics. Always set line_hint to the exact Markdown "
        "line number. If the cited node is wrong, set node_id to the node that actually "
        "contains the quote."
    )
    user = {
        "task": "repair_evidence_citations",
        "prompt_version": EXTRACTION_PROMPT_VERSION,
        "response_schema": RepairResponseIn.model_json_schema(),
        "rules": [
            "Return exactly one item per failed path, same path string, same order.",
            "Each quote must appear in the returned node_id. Prefer a span containing a number or proper noun.",
            "Do not change anything except node_id/quote/line_hint of the listed paths.",
        ],
        "node_manifest": [
            {
                "node_id": node.node_id,
                "start_line": node.start_line,
                "end_line": node.end_line,
                "heading_path": node.heading_path,
            }
            for node in nodes
        ],
        "failures": items,
    }
    return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]


async def repair_grounding_failures(
    llm: Any,
    markdown: str,
    nodes: list[DocumentTreeNode],
    manifest: ExtractionManifestIn,
    failures: list[dict[str, Any]],
) -> tuple[list[str], int]:
    """One small repair call for exactly the broken refs; splice + re-validate.

    Mutates manifest evidence in place. Returns (repaired paths, token usage).
    Raises ValueError with the exact reason when the repair is unusable, so the
    caller reports it instead of hiding it. At most one repair round per file.
    """
    if len(failures) > 10:
        raise ValueError(
            f"quá nhiều evidence hỏng ({len(failures)}), không repair: {failures[0]['error'][:500]}"
        )
    messages = build_repair_prompt(markdown, nodes, failures)
    result = await llm.complete_extraction_json(messages)
    usage = _usage_total(result.usage)
    try:
        response = RepairResponseIn.model_validate_json(result.content)
    except (json.JSONDecodeError, PydanticValidationError) as exc:
        raise ValueError(f"repair response không hợp lệ: {exc}") from exc
    expected = [failure["path"] for failure in failures]
    actual = [item.path for item in response.items]
    if actual != expected:
        raise ValueError(f"repair response sai paths: expected {expected[:5]}, got {actual[:5]}")
    nodes_by_id = {node.node_id: node for node in nodes}
    ref_by_path = dict(_iter_manifest_refs(manifest))
    for item in response.items:
        if item.node_id not in nodes_by_id:
            raise ValueError(f"repair node_id không tồn tại: {item.path} -> {item.node_id}")
        ref = ref_by_path[item.path]
        ref.node_id = item.node_id
        ref.quote = item.quote
        ref.line_hint = item.line_hint
    # Full re-validation: the repaired manifest must ground completely.
    _validate_manifest_shape_and_references(markdown, nodes, manifest)
    return expected, usage


async def extract_and_persist_manifest(
    session: AsyncSession,
    issuer: Issuer,
    document: Document,
    markdown: str,
    nodes: list[DocumentTreeNode],
) -> ExtractionResult:
    llm = LLMClient(settings)
    if not llm.extraction_configured:
        return ExtractionResult(
            status=ProcessingStageStatus.INCOMPLETE.value,
            error="LLM extraction chưa được cấu hình",
            metadata={"mode": "llm_manifest", "prompt_version": EXTRACTION_PROMPT_VERSION, "configured": False},
        )

    messages = build_extraction_prompt(issuer, document, markdown, nodes)
    total_usage = 0
    # Strict single-request policy: exactly one full-document LLM call per file.
    # No validation retry and no chunked fallback. Fail fast with the exact reason.
    try:
        result = await llm.complete_extraction_json(messages)
        total_usage += _usage_total(result.usage)
        if result.finish_reason and result.finish_reason not in {"stop", "tool_calls"}:
            if result.finish_reason == "length":
                # Output bị cắt do vượt max_tokens — trả INCOMPLETE ngay với
                # reason exact. Không chunk, không retry.
                return ExtractionResult(
                    status=ProcessingStageStatus.INCOMPLETE.value,
                    token_usage=total_usage,
                    error=(
                        "finish_reason=length: manifest vượt max_tokens "
                        f"(doc_role={document.doc_role}, nodes={len(nodes)}, "
                        f"max_tokens={settings.llm_max_tokens}). "
                        "Gợi ý: tăng SAG_LLM_MAX_TOKENS trong giới hạn output của provider."
                    )[:2000],
                    metadata={
                        "mode": "llm_manifest",
                        "attempts": 1,
                        "finish_reason": "length",
                        "suggested_action": "increase_max_tokens",
                        "prompt_version": EXTRACTION_PROMPT_VERSION,
                        "taxonomy_version": TAXONOMY_VERSION,
                    },
                )
            raise ValueError(f"finish_reason={result.finish_reason}")
        try:
            payload = json.loads(result.content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM trả về JSON không parse được: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("LLM manifest phải là JSON object")
        coerced = coerce_unknown_enums(payload)
        manifest = ExtractionManifestIn.model_validate(payload)
        metadata: dict[str, Any] = {
            "mode": "llm_manifest",
            "attempts": 1,
            "finish_reason": result.finish_reason,
            "prompt_version": EXTRACTION_PROMPT_VERSION,
            "taxonomy_version": TAXONOMY_VERSION,
        }
        if coerced:
            metadata["coerced_enum_count"] = len(coerced)
            metadata["coerced_enums"] = coerced[:32]
        grounding_stats = sanitize_manifest_value_grounding(manifest)
        resolve_llm_evidence_nodes(markdown, nodes, manifest)
        table_stats = enrich_manifest_with_table_facts(document, markdown, nodes, manifest)
        facet_stats = enrich_manifest_facets_from_observations(manifest)
        missing_annotation_count = fill_missing_node_annotations(nodes, manifest)
        metadata.update(grounding_stats)
        metadata.update(table_stats)
        metadata.update(facet_stats)
        metadata["deterministic_node_annotations_added"] = missing_annotation_count
        try:
            _validate_manifest_shape_and_references(markdown, nodes, manifest)
        except ValueError:
            failures = _collect_grounding_failures(markdown, nodes, manifest)
            if not failures:
                raise
            dropped = _drop_ungrounded_manifest_items(manifest, failures)
            if dropped:
                metadata["dropped_ungrounded_paths"] = dropped
                _validate_manifest_shape_and_references(markdown, nodes, manifest)
                failures = []
            if failures:
                repaired, repair_usage = await repair_grounding_failures(
                    llm, markdown, nodes, manifest, failures
                )
                total_usage += repair_usage
                metadata["repair_paths"] = repaired
                metadata["repair_attempts"] = 1
        async with session.begin_nested():
            counts = await validate_and_persist_manifest(session, issuer, document, markdown, nodes, manifest)
        metadata["observation_count"] = counts["observation_count"]
        metadata["facet_count"] = counts["facet_count"]
        return ExtractionResult(
            status=ProcessingStageStatus.COMPLETE.value,
            token_usage=total_usage,
            metadata=metadata,
            **counts,
        )
    except (PydanticValidationError, ValueError) as exc:
        last_error = str(exc)

    return ExtractionResult(
        status=ProcessingStageStatus.INCOMPLETE.value,
        token_usage=total_usage,
        error=last_error[:2000],
        metadata={"mode": "llm_manifest", "attempts": 1, "error": last_error[:2000]},
    )


def _validate_manifest_shape_and_references(
    markdown: str,
    nodes: list[DocumentTreeNode],
    manifest: ExtractionManifestIn,
) -> None:
    """Pure validation before opening a DB savepoint.

    A rejected manifest should not roll back a SQLAlchemy nested transaction;
    otherwise async ORM objects may be expired and later attribute access can
    trigger `greenlet_spawn` errors.
    """

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
    refs.extend(signal.evidence for signal in manifest.moat_signals)
    refs.extend(disclosure.evidence for disclosure in manifest.gil_disclosures)
    refs.extend(facet.evidence for facet in manifest.document_facets)
    refs.extend(observation.evidence for observation in manifest.observations)
    for ref in refs:
        node = nodes_by_id.get(ref.node_id)
        if node is None:
            raise ValueError(f"evidence node_id không tồn tại: {ref.node_id}")
        _locate_quote(markdown, node, ref.quote, ref.line_hint, all_nodes=nodes)
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
        span = await _evidence_span(session, issuer, document, markdown, nodes_by_id, fact.evidence, evidence_cache)
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
                    "taxonomy_version": TAXONOMY_VERSION,
                },
            )
        )
        relation_count += 1

    moat_signal_count = 0
    for signal in manifest.moat_signals:
        span = await _evidence_span(session, issuer, document, markdown, nodes_by_id, signal.evidence, evidence_cache)
        session.add(
            MoatSignal(
                issuer_id=issuer.id,
                document_id=document.id,
                node_id=signal.evidence.node_id,
                evidence_span_id=span.id,
                pillar=signal.pillar.value,
                direction=signal.direction,
                strength=signal.strength,
                durability=signal.durability,
                materiality=signal.materiality,
                signal=signal.signal.strip(),
                validation_status=ValidationStatus.VALIDATED.value,
                metadata_json={
                    "extraction_rationale": signal.extraction_rationale,
                    "taxonomy_version": TAXONOMY_VERSION,
                },
            )
        )
        moat_signal_count += 1

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
                source="llm",
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
        "moat_signal_count": moat_signal_count,
        "observation_count": observation_count,
        "facet_count": facet_count,
        "evidence_count": len(evidence_cache),
    }


async def _delete_previous_extraction(session: AsyncSession, document_id: str) -> None:
    await session.execute(delete(Observation).where(Observation.document_id == document_id))
    await session.execute(delete(DocumentFacet).where(DocumentFacet.document_id == document_id))
    await session.execute(delete(MoatSignal).where(MoatSignal.document_id == document_id))
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
) -> EvidenceSpan:
    node = nodes_by_id.get(evidence.node_id)
    if node is None:
        raise ValueError(f"evidence node_id không tồn tại: {evidence.node_id}")
    location = _locate_quote(
        markdown, node, evidence.quote, evidence.line_hint, all_nodes=list(nodes_by_id.values())
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
            "llm_quote": evidence.quote,
            "quote_resolution": location["resolution"],
            "cited_node_id": evidence.node_id,
            "taxonomy_version": TAXONOMY_VERSION,
            "prompt_version": EXTRACTION_PROMPT_VERSION,
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
        # Multi-line quotes must ground as a whole span: single-line number
        # matching would ground them to one partial line and lose evidence.
        is_multiline = len([line for line in quote.splitlines() if line.strip()]) >= 2
        if not is_multiline:
            fallback = _fallback_line_match(lines, node, quote, line_hint)
            if fallback is not None:
                return fallback
        # Last resort before failing: diacritic-mangled and/or multi-line
        # quotes that no single line is compatible with. Requires the full
        # folded quote to occur exactly once in the node.
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
    """Accent/case-fold text keeping a folded-index → original-index map.

    LLM quotes often mangle Vietnamese diacritics ("cung cấp dịch vụ" →
    "cung cp dch v") while numbers and word shapes stay intact. Folding both
    sides lets such quotes ground to the exact original span.
    """
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
    """Ground a diacritic-mangled (possibly multi-line) quote to one span.

    Returns None unless the folded quote occurs exactly once in the folded
    node content, preserving the uniqueness guarantee of exact matching.
    """
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
    """Align a multi-line quote to consecutive node lines.

    Each non-empty quote line must be compatible with its node line (via the
    same number/word rule as single-line fallback, which tolerates dropped
    letters), the matched node lines must be consecutive and in order, and the
    alignment must be unique inside the node. Returns whole-line span.
    """
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
    """Most specific node containing the line (nested ranges overlap)."""
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
    """Ground a wrong-node citation to its unique true location document-wide.

    The model sometimes cites a node that does not contain the quote while the
    quote exists verbatim (or folded-equal) exactly once elsewhere. Accepting
    that unique true span preserves verified evidence instead of failing the
    whole manifest; the correction is recorded via actual_node_id and the
    reattributed_* resolution. Anything ambiguous still returns None.
    """
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


def _semantic_key(prefix: str, label: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in label.strip())
    return f"{prefix}:{'_'.join(part for part in cleaned.split('_') if part)[:220]}"


def _usage_total(usage: Any) -> int:
    if usage is None:
        return 0
    if isinstance(usage, dict):
        return int(usage.get("total_tokens") or usage.get("input_tokens", 0) + usage.get("output_tokens", 0) or 0)
    return int(getattr(usage, "total_tokens", 0) or 0)
