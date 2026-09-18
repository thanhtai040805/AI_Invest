from __future__ import annotations

import json
import math
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError as PydanticValidationError, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.errors import ConfigurationError
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
from sag_api.generation.llm import LLMClient
from sag_api.services.document_structure_service import sha256_text

TAXONOMY_VERSION = "financial-evidence-taxonomy-v2"
# v36/v8 use a deliberately small provider wire contract.  The persistence
# model remains richer, but the LLM must not spend tokens re-emitting data that
# deterministic parsers or PostgreSQL already own.
EXTRACTION_PROMPT_VERSION = "sag-gil-final-quarter-v38-vi"
ANNUAL_PROMPT_VERSION = "sag-gil-final-annual-v39-vi"
GOVERNANCE_PROMPT_VERSION = "sag-gil-final-governance-v10-vi"
_MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def prompt_version_for_document(document: Document) -> str:
    """Return the extraction profile version used for this document role."""
    role = str(document.doc_role or "").upper()
    if role == "GOVERNANCE_REPORT":
        return GOVERNANCE_PROMPT_VERSION
    if role == "ANNUAL_BACKBONE":
        return ANNUAL_PROMPT_VERSION
    return EXTRACTION_PROMPT_VERSION


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str | None = None
    # Quote is hydrated server-side from the cited Markdown line.
    quote: str = Field(default="")
    line_start: int = Field(default=1, ge=1)
    line_end: int = Field(default=1, ge=1)
    # Accepted only for old test/legacy payloads; new LLM wire schema removes it.
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
    # Governance wire responses may assign a short reference so relations do
    # not repeat long legal names. It is resolved and removed server-side.
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
    # Economic meaning is deliberately one compact semantic hint.  The
    # server still owns amounts, periods and risk scoring; GIL uses this only
    # to distinguish, for example, a loan balance from an operating trade.
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

    @field_validator("taxonomy_mapping", mode="before")
    @classmethod
    def _normalize_taxonomy_mapping(cls, value: Any) -> dict[str, Any]:
        # Providers sometimes serialize an unused optional object as null.
        # Treat it as the declared empty mapping instead of rejecting the
        # otherwise valid manifest.
        return value if isinstance(value, dict) else {}

    @field_validator("attributes", mode="before")
    @classmethod
    def _normalize_attributes(cls, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}


class TableRowSemanticIn(BaseModel):
    """The analytical role of one meaningful row inside a table."""

    model_config = ConfigDict(extra="forbid")

    row_label: str = Field(min_length=1, max_length=512)
    role: str = Field(min_length=2, max_length=128)
    meaning: str = Field(min_length=8, max_length=500)
    relationship: str | None = Field(default=None, max_length=400)
    evidence: EvidenceRef


class TableSignalIn(BaseModel):
    """A grounded analytical signal supported by several rows in one table."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=8, max_length=800)
    analytical_question: str = Field(min_length=8, max_length=400)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: EvidenceRef


class TableSemanticIn(BaseModel):
    """Compact section/table insight; facts remain separately queryable."""

    model_config = ConfigDict(extra="forbid")

    # These persistence fields are hydrated/retained for compatibility.  The
    # LLM wire contract only needs the compact insight below and its evidence.
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
    # Safety ceilings prevent provider loops; they are not ranking or Top-K
    # rules. Selection remains evidence/utility driven.
    node_annotations: list[NodeAnnotationIn] = Field(default_factory=list, max_length=128)
    entity_mentions: list[EntityMentionIn] = Field(default_factory=list, max_length=256)
    facts: list[FactIn] = Field(default_factory=list, max_length=256)
    relations: list[RelationIn] = Field(default_factory=list, max_length=256)
    gil_disclosures: list[GilDisclosureIn] = Field(default_factory=list, max_length=128)
    document_facets: list[DocumentFacetIn] = Field(default_factory=list, max_length=128)
    observations: list[ObservationIn] = Field(default_factory=list, max_length=256)
    section_insights: list[TableSemanticIn] = Field(default_factory=list, max_length=64)
    # Read-only compatibility for manifests produced before Competitive/MOAT
    # was removed. It is never present in the LLM wire contract or persisted.
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


def extraction_json_schema() -> dict[str, Any]:
    schema = ExtractionManifestIn.model_json_schema()
    schema.get("properties", {}).pop("moat_signals", None)
    if isinstance(schema.get("required"), list):
        schema["required"] = [item for item in schema["required"] if item != "moat_signals"]
    # quote is an internal/server-derived field, never part of the LLM
    # response contract. Keep it on the internal model for persistence.
    evidence_schema = schema.get("$defs", {}).get("EvidenceRef")
    if isinstance(evidence_schema, dict):
        evidence_schema.get("properties", {}).pop("quote", None)
        evidence_schema.get("properties", {}).pop("line_hint", None)
        required = evidence_schema.get("required")
        if isinstance(required, list):
            evidence_schema["required"] = [item for item in required if item != "quote"]
        else:
            evidence_schema["required"] = ["line_start", "line_end"]
    return schema


_LLM_WIRE_FIELDS = {
    "EntityMentionIn": {"canonical_name", "entity_type", "evidence", "entity_ref"},
    "FactIn": {"fact_type", "label", "value_text", "evidence"},
    "RelationIn": {"subject", "object", "subject_ref", "object_ref", "relation_type", "flow_kind", "amount_vnd", "ownership_pct", "evidence"},
    "GilDisclosureIn": {"disclosure_type", "label", "evidence"},
    "DocumentFacetIn": {"facet", "confidence", "evidence"},
    # The statement is the reusable semantic representation.  Subject /
    # predicate / object are optional graph projections and are derived later
    # only when a consumer actually needs them.
    "ObservationIn": {"statement", "topic_tags", "confidence", "evidence"},
    "TableRowSemanticIn": {"row_label", "role", "meaning", "relationship", "evidence"},
    "TableSignalIn": {"statement", "analytical_question", "confidence", "evidence"},
    "TableSemanticIn": {"section_id", "insight", "evidence"},
}

_LLM_WIRE_COLLECTIONS = {
    "node_annotations": {"node_id", "relevance", "summary", "rationale"},
    "entity_mentions": {"canonical_name", "entity_type", "evidence", "entity_ref"},
    "facts": {"fact_type", "label", "value_text", "evidence"},
    "relations": {"subject", "object", "subject_ref", "object_ref", "relation_type", "flow_kind", "amount_vnd", "ownership_pct", "evidence"},
    "gil_disclosures": {"disclosure_type", "label", "evidence"},
    "document_facets": {"facet", "confidence", "evidence"},
    "observations": {"statement", "topic_tags", "confidence", "evidence"},
    "section_insights": {"section_id", "insight", "evidence"},
}
_LLM_EVIDENCE_FIELDS = {"line_start", "line_end"}

# These are semantic outputs only.  Facts from Markdown tables, node
# annotations, document facets and their metadata are deterministic/server
# responsibilities.  Keeping them out of the provider schema is important:
# saying "return []" still costs schema/decision tokens and leaves room for the
# model to repeat the same evidence in multiple projections.
_LLM_WIRE_ROOT_COLLECTIONS = (
    "entity_mentions",
    "relations",
    "observations",
    "section_insights",
    "gil_disclosures",
)


def compact_llm_payload_for_validation(payload: dict[str, Any], *, governance: bool = False) -> tuple[dict[str, Any], int]:
    """Project provider output onto the SAG wire contract before validation.

    ``json_object`` providers are allowed to emit extra keys even when the
    prompt contains a schema.  Keep the untouched response in audit metadata,
    but never let provider-only metadata enter the normalized manifest.
    """
    # Only semantic collections cross the LLM boundary.  The old contract
    # allowed the model to return facts/node annotations/facets even though
    # those are already produced by code; those arrays were a major source of
    # duplicated Annual output.
    compact = {
        key: value
        for key, value in payload.items()
        if key in _LLM_WIRE_ROOT_COLLECTIONS or key == "taxonomy_version"
    }
    dropped = len(payload) - len(compact)
    wire_collections = {
        key: value for key, value in _LLM_WIRE_COLLECTIONS.items()
        if key in _LLM_WIRE_ROOT_COLLECTIONS
    }
    if not governance:
        wire_collections["entity_mentions"] = {"canonical_name", "entity_type", "evidence"}
        wire_collections["relations"] = {"subject", "object", "relation_type", "flow_kind", "amount_vnd", "ownership_pct", "evidence"}
    for collection, allowed in wire_collections.items():
        items = compact.get(collection)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if collection == "section_insights" and not item.get("insight"):
                # Accept older provider-shaped responses while migrating the
                # wire contract; the legacy purpose becomes the compact
                # insight and all other metadata is still discarded.
                item = {**item, "insight": item.get("analytical_purpose") or item.get("table_role") or ""}
            kept = {key: value for key, value in item.items() if key in allowed}
            dropped += len(item) - len(kept)
            items[index] = kept
            evidence = kept.get("evidence")
            if isinstance(evidence, dict):
                evidence_copy = {key: value for key, value in evidence.items() if key in _LLM_EVIDENCE_FIELDS}
                dropped += len(evidence) - len(evidence_copy)
                evidence.clear()
                evidence.update(evidence_copy)
            if collection == "section_insights":
                for row in kept.get("row_semantics", []):
                    if isinstance(row, dict):
                        row_allowed = _LLM_WIRE_FIELDS["TableRowSemanticIn"]
                        dropped += len(row) - len([key for key in row if key in row_allowed])
                        row_copy = {key: value for key, value in row.items() if key in row_allowed}
                        row.clear()
                        row.update(row_copy)
                for signal in kept.get("analytical_signals", []):
                    if isinstance(signal, dict):
                        signal_allowed = _LLM_WIRE_FIELDS["TableSignalIn"]
                        dropped += len(signal) - len([key for key in signal if key in signal_allowed])
                        signal_copy = {key: value for key, value in signal.items() if key in signal_allowed}
                        signal.clear()
                        signal.update(signal_copy)
    return compact, dropped


def llm_wire_json_schema(*, governance: bool = False) -> dict[str, Any]:
    """Return the compact LLM contract; persistence keeps the richer internal model."""
    schema = extraction_json_schema()
    wire_fields = dict(_LLM_WIRE_FIELDS)
    if not governance:
        wire_fields["EntityMentionIn"] = {"canonical_name", "entity_type", "evidence"}
        wire_fields["RelationIn"] = {"subject", "object", "relation_type", "flow_kind", "amount_vnd", "ownership_pct", "evidence"}
    else:
        # Annual and Governance use short refs so long legal names are emitted
        # once in entity_mentions and never repeated in every relation.
        wire_fields["RelationIn"] = {"subject_ref", "object_ref", "relation_type", "flow_kind", "amount_vnd", "ownership_pct", "evidence"}
    for name, allowed in wire_fields.items():
        definition = schema.get("$defs", {}).get(name)
        if not isinstance(definition, dict):
            continue
        properties = definition.get("properties", {})
        definition["properties"] = {key: value for key, value in properties.items() if key in allowed}
        if isinstance(definition.get("required"), list):
            definition["required"] = [key for key in definition["required"] if key in allowed]
    if governance:
        entity_definition = schema.get("$defs", {}).get("EntityMentionIn")
        if isinstance(entity_definition, dict):
            required = set(entity_definition.get("required", []))
            required.add("entity_ref")
            entity_definition["required"] = sorted(required)
        relation_definition = schema.get("$defs", {}).get("RelationIn")
        if isinstance(relation_definition, dict):
            required = set(relation_definition.get("required", []))
            required.update({"subject_ref", "object_ref"})
            relation_definition["required"] = sorted(required)

    # Narrow the root contract as well as nested definitions.  Pydantic's
    # internal manifest is intentionally richer for persistence, but exposing
    # that model to the provider invited it to fill every array and repeat the
    # same evidence as facts, annotations and facets.  The server adds those
    # deterministic collections after validation.
    root_properties = schema.get("properties", {})
    if isinstance(root_properties, dict):
        keep = {"taxonomy_version", *_LLM_WIRE_ROOT_COLLECTIONS}
        schema["properties"] = {
            key: value for key, value in root_properties.items() if key in keep
        }
        if isinstance(schema.get("required"), list):
            schema["required"] = [
                key for key in schema["required"] if key in keep
            ]
    return schema


def expand_compact_manifest_payload(payload: dict[str, Any], *, governance: bool = False) -> dict[str, Any]:
    """Hydrate omitted persistence-only fields after the compact LLM response."""
    expanded = dict(payload)
    defaults = {
        "EntityMentionIn": {"raw_text": None, "raw_label": None, "industry_context": None, "extraction_rationale": None, "taxonomy_candidate": None},
        "FactIn": {"semantic_key": None, "value_numeric": None, "unit": None, "currency": None, "period_start": None, "period_end": None, "as_of": None, "raw_label": None, "industry_context": None, "extraction_rationale": None, "taxonomy_candidate": None},
        "RelationIn": {"period_start": None, "period_end": None, "as_of": None, "raw_label": None, "industry_context": None, "extraction_rationale": None, "taxonomy_candidate": None},
        "GilDisclosureIn": {"extraction_rationale": None},
        "DocumentFacetIn": {"rationale": None},
        "ObservationIn": {"subject": None, "predicate": "observation", "object": None, "attributes": {}, "period_start": None, "period_end": None, "as_of": None, "rationale": None, "taxonomy_mapping": {}},
        "TableRowSemanticIn": {},
        "TableSignalIn": {},
        "TableSemanticIn": {"table_title": "section", "table_role": "section_insight", "analytical_purpose": ""},
    }
    collection_types = {
        "entity_mentions": "EntityMentionIn", "facts": "FactIn", "relations": "RelationIn",
        "gil_disclosures": "GilDisclosureIn",
        "document_facets": "DocumentFacetIn", "observations": "ObservationIn",
    }
    entity_refs: dict[str, str] = {}
    if governance:
        for item in expanded.get("entity_mentions", []):
            if isinstance(item, dict) and item.get("entity_ref") and item.get("canonical_name"):
                entity_refs[str(item["entity_ref"])] = str(item["canonical_name"])
        for item in expanded.get("relations", []):
            if not isinstance(item, dict):
                continue
            if not item.get("subject") and item.get("subject_ref") in entity_refs:
                item["subject"] = entity_refs[item["subject_ref"]]
            if not item.get("object") and item.get("object_ref") in entity_refs:
                item["object"] = entity_refs[item["object_ref"]]
            # References are a wire-only compression mechanism.
            item.pop("subject_ref", None)
            item.pop("object_ref", None)
        for item in expanded.get("entity_mentions", []):
            if isinstance(item, dict):
                item.pop("entity_ref", None)

    for collection, type_name in collection_types.items():
        for item in expanded.get(collection, []):
            if isinstance(item, dict):
                for key, value in defaults[type_name].items():
                    item.setdefault(key, value)
                if type_name == "EntityMentionIn" and not item.get("raw_text"):
                    item["raw_text"] = item.get("canonical_name")
    for table in expanded.get("section_insights", []):
        if not isinstance(table, dict):
            continue
        for key, value in defaults["TableSemanticIn"].items():
            table.setdefault(key, value)
        table.setdefault("analytical_purpose", table.get("insight", ""))
        for row in table.get("row_semantics", []):
            if isinstance(row, dict):
                row.setdefault("relationship", None)
        for signal in table.get("analytical_signals", []):
            if isinstance(signal, dict):
                signal.setdefault("confidence", 0.5)
    return expanded


def build_analytical_section_index(
    markdown: str,
    table_candidates: list[dict[str, Any]],
    movement_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Index table/text context by analytical section without duplicating text."""
    lines = markdown.splitlines()
    headings: list[tuple[int, int, str]] = []
    for line_no, line in enumerate(lines, start=1):
        match = _MARKDOWN_HEADING_RE.match(line)
        if match:
            headings.append((line_no, len(match.group(1)), match.group(2).strip()))
    sections: dict[str, dict[str, Any]] = {}
    for heading_line, level, heading_text in headings:
        key = f"{heading_line}:{level}"
        sections[key] = {
            "section_start": heading_line,
            "section_end": heading_line,
            "heading": heading_text,
            "table_ranges": [],
            "table_hints": [],
            "movement_lines": [],
        }
    for candidate in table_candidates:
        start, end = candidate["table_start"], candidate["table_end"]
        prior = [item for item in headings if item[0] <= start]
        heading = prior[-1] if prior else (1, 1, "Document context")
        key = f"{heading[0]}:{heading[1]}"
        section = sections.setdefault(key, {
            "section_start": heading[0],
            "section_end": end,
            "heading": heading[2],
            "table_ranges": [],
            "table_hints": [],
            "movement_lines": [],
        })
        section["section_end"] = max(section["section_end"], end)
        section["table_ranges"].append([start, end])
        section["table_hints"] = list(dict.fromkeys(
            [*(section.get("table_hints") or []), *(candidate.get("topic_hints") or [])]
        ))
    for movement in movement_candidates:
        line = movement["line"]
        matching = [section for section in sections.values() if any(a <= line <= b for a, b in section["table_ranges"])]
        if matching:
            matching[0]["movement_lines"].append(line)
    ordered_headings = sorted(headings, key=lambda item: item[0])
    for section in sections.values():
        next_headings = [item[0] for item in ordered_headings if item[0] > section["section_start"]]
        section["content_line_start"] = section["section_start"]
        section["content_line_end"] = (next_headings[0] - 1) if next_headings else len(lines)
        section["context"] = "\n".join(
            f"{line_no}: {lines[line_no - 1]}"
            for line_no in range(section["content_line_start"], section["content_line_end"] + 1)
        )
    result = sorted(sections.values(), key=lambda item: item["section_start"])
    for index, section in enumerate(result):
        section["section_id"] = index
    return result


def build_extraction_prompt(
    issuer: Issuer,
    document: Document,
    markdown: str,
    nodes: list[DocumentTreeNode],
) -> list[dict[str, str]]:
    numbered_markdown = "\n".join(f"{index + 1}: {line}" for index, line in enumerate(markdown.splitlines()))
    movement_candidates = detect_material_table_movements(markdown)
    table_candidates = detect_table_semantic_candidates(markdown)
    analytical_sections = build_analytical_section_index(markdown, table_candidates, movement_candidates)
    document_role = str(document.doc_role or "").upper()
    is_governance = document_role == "GOVERNANCE_REPORT"
    is_annual = document_role == "ANNUAL_BACKBONE"
    use_entity_refs = is_governance or is_annual
    prompt_version = prompt_version_for_document(document)
    governance_rules = [
        "GOVERNANCE PROFILE — Đây là báo cáo quản trị. Duyệt toàn bộ section và phụ lục, không dừng sau vài bảng. Ưu tiên dữ liệu phục vụ Governance Graph, ownership graph, related-party graph và event graph; không coi các nhóm dưới đây là bắt buộc nếu tài liệu không có.",
        "QUY TRÌNH SUY NGHĨ NỘI BỘ, KHÔNG IN RA: (1) lập inventory toàn bộ section/bảng/phụ lục; (2) phân loại mục đích và ánh xạ ý nghĩa các cột; (3) trích xuất entity, role, relation, ownership, transaction và resolution có evidence; (4) kiểm tra entity, hướng quan hệ, trùng lặp và suy diễn; (5) nén thành JSON cuối cùng. Chỉ trả JSON theo schema, không trả kế hoạch, table map, reasoning, rationale, quote hoặc bản sao dữ liệu trung gian.",
        "Khi có bảng/thông tin thành viên HĐQT, Ban kiểm soát, Ban điều hành hoặc kế toán trưởng: lấy TOÀN BỘ người xuất hiện, entity_type=person, và tạo relation manages từ người đến công ty khi chức vụ được nêu. Chức vụ, thời điểm bổ nhiệm/mãn nhiệm và tỷ lệ tham dự/biểu quyết phải được giữ trong observation có evidence; không bỏ người chỉ vì đã có một người đại diện.",
        "Khi có nghị quyết/quyết định: tạo observation RIÊNG cho từng nghị quyết có action thực chất (bổ nhiệm, tăng vốn, chuyển nhượng, cổ tức, giao dịch bên liên quan, kế hoạch kinh doanh hoặc thay đổi quản trị). Statement phải nêu số nghị quyết nếu có, ngày, action và entity liên quan; không gộp các nghị quyết khác nhau thành một observation và không chép toàn bộ văn bản.",
        "Khi có bảng họp/attendance: nếu tỷ lệ giữa các thành viên khác nhau, giữ dữ liệu theo từng người; nếu tất cả giống hệt nhau, có thể tạo một observation tổng hợp nhưng phải nêu số người và tỷ lệ. Không biến tỷ lệ tham dự thành relation quản trị.",
        "Khi có danh sách affiliated persons hoặc người nội bộ: giữ toàn bộ người nội bộ, lãnh đạo, cổ đông lớn và người liên quan trực tiếp của họ; giữ thêm người/tổ chức có sở hữu hoặc giao dịch. Có thể bỏ người thân thứ cấp chỉ có thông tin hành chính, không sở hữu, không giao dịch và không tạo giá trị truy hồi. Quan hệ gia đình phải thể hiện bằng affiliated_with/observation có evidence.",
        "PHÂN LOẠI NGHIÊM NGẶT: chỉ dùng entity_type=subsidiary hoặc relation controls/owns khi tài liệu nêu rõ công ty con, quyền kiểm soát, tỷ lệ sở hữu hoặc quyền biểu quyết. Cụm 'related organization', 'tổ chức có liên quan' hoặc việc xuất hiện trong danh sách affiliated persons chỉ cho phép related_party/affiliate và affiliated_with; tuyệt đối không tự nâng thành subsidiary hoặc controls.",
        "Khi có sở hữu hoặc giao dịch cổ phiếu: lấy người/tổ chức, số lượng, tỷ lệ, loại giao dịch và thời điểm nếu có. Dùng fact ownership_balance cho số lượng/tỷ lệ; chỉ tạo relation owns khi số cổ phiếu hoặc tỷ lệ > 0 hoặc tài liệu nói rõ quyền sở hữu. Không tạo owns cho dòng 0 cổ phiếu/0%; không suy đoán giao dịch khi bảng chỉ ghi số dư cuối kỳ.",
        "Không để SectionInsight thay thế entity, relation, fact hoặc observation Governance. SectionInsight chỉ dùng bổ sung khi section có ý nghĩa tổng hợp về quyền kiểm soát, thay đổi quản trị, tập trung sở hữu hoặc rủi ro giao dịch; dữ liệu từng người, từng nghị quyết và từng giao dịch vẫn phải được trích xuất riêng nếu có evidence.",
        "Chỉ trả các field compact mà schema cho phép: canonical_name/entity_type/evidence cho entity; subject/object/relation_type/amount_vnd/ownership_pct/evidence cho relation; fact_type/label/value_text/evidence cho fact; statement/topic_tags/confidence/evidence cho observation. Không trả quote, node_id, raw_label, rationale, địa chỉ, số giấy tờ hoặc metadata hành chính trong response.",
        "Evidence chỉ trả line_start/line_end 1-based, inclusive, nhỏ nhất nhưng đủ chứng minh item. Luôn trả đủ top-level key của schema với [] khi không có item; không trả {} và không suy đoán dữ liệu bị thiếu.",
    ] if is_governance else []
    role_rules = {
        "LATEST_QUARTER": [
            "ROLE CONTRACT — LATEST_QUARTER: Tập trung vào thay đổi và exposure mới nhất của kỳ báo cáo. Giữ entity/relation về sở hữu, bên liên quan, cho vay, phải thu/phải trả, bảo lãnh, đầu tư, cổ tức, giao dịch và các observation giải thích biến động hoặc phụ thuộc dòng tiền. Không chép lại bảng BS/IS/CF; số liệu nền do server parse. Nếu tài liệu là BCTC công ty mẹ, phải giữ rõ quan hệ với công ty con và không suy diễn giao dịch nội bộ thành gian lận.",
            "LATEST QUARTER GIL DECISION EVIDENCE: Mỗi relation/observation phải giúp Agent trả lời ít nhất một câu: tiền đi giữa ai; quan hệ kiểm soát là gì; exposure là flow hay balance; có phụ thuộc, tập trung, khoản bất thường hoặc dấu hiệu dòng tiền nhiều bước không. Không tạo cycle/circular_flow chỉ từ một bảng hoặc một giao dịch riêng lẻ.",
        ],
        "ANNUAL_BACKBONE": [
            "ANNUAL RELATION COMPRESSION: trước khi trả JSON phải lập một graph edge registry cho TOÀN BỘ tài liệu, không lập riêng theo từng section/bảng. Một cặp subject-object với cùng relation_type và flow_kind chỉ được xuất ĐÚNG MỘT relation trong response. Nếu cùng edge lặp ở danh sách công ty, bảng giao dịch và phần diễn giải thì chỉ giữ một record với evidence trực tiếp và đầy đủ nhất; không xuất lại theo từng nguồn.",
            "ANNUAL CONFLICT RULE: khác số tiền, kỳ hoặc dòng evidence không tự tạo relation mới. Nếu đó là cùng một edge kinh tế thì giữ một relation đại diện; chỉ tạo relation thứ hai khi relation_type hoặc flow_kind thực sự khác nhau và tài liệu chứng minh đó là một quan hệ kinh tế riêng. Không hy sinh edge registry để giữ các bản sao số liệu.",
            "ANNUAL TRANSACTS_WITH GATING: Chỉ tạo transacts_with khi subject và object là entity được nêu rõ và giao dịch có utility cho GIL (dòng vốn, công nợ, bảo lãnh, đầu tư, phụ thuộc hoặc exposure đáng kể). Dòng số liệu không có endpoint rõ, dòng thuần túy đã được facts deterministic bao phủ, hoặc bản sao cùng evidence thì không tạo relation.",
            "ROLE CONTRACT — ANNUAL_BACKBONE: Xây graph nền của issuer, không phải bản tóm tắt toàn bộ báo cáo. Duyệt toàn bộ tài liệu nhưng chỉ giữ entity/relation/observation có utility cho cấu trúc sở hữu, công ty con/liên kết, đầu tư, vay/cho vay, phải thu/phải trả, bảo lãnh, giao dịch bên liên quan, cổ tức và dependency. Giữ relation nếu nó thiết lập node/edge nền dù chưa có biến động; không lặp lại edge đã xuất hiện nhiều lần trong cùng tài liệu.",
            "ANNUAL GIL EVIDENCE: Phân biệt rõ ownership edge, capital-flow edge và accounting disclosure. Một khoản đầu tư vào công ty con không tự là circular flow; một khoản vay nội bộ không tự là tunneling. Chỉ ghi observation khi văn bản nêu cơ chế, bất thường, phụ thuộc hoặc rủi ro trực tiếp.",
        ],
        "GOVERNANCE_REPORT": [
            "ROLE CONTRACT — GOVERNANCE_REPORT: Chỉ trích xuất Governance Graph phục vụ GIL. Ưu tiên người kiểm soát, HĐQT/BKS/Ban điều hành, quan hệ chức vụ, người liên quan, quan hệ gia đình, sở hữu cổ phiếu, giao dịch nội bộ, nghị quyết có action, affiliated entities và quan hệ kiểm soát. Không trả tiểu sử, địa chỉ, thông tin hành chính hoặc danh sách lặp nếu không tạo node/edge/risk evidence.",
            "GOVERNANCE GIL DECISION EVIDENCE: Mỗi person/entity phải có vai trò hoặc quan hệ được tài liệu xác nhận. Chỉ tạo edge insider/affiliate/owns/manages khi hướng và chủ thể rõ. Attendance, chức vụ và nghị quyết là observation; không biến chúng thành relation tài chính. Nén entity bằng ref ngắn, không lặp tên pháp nhân trong từng relation.",
            "GOVERNANCE OUTPUT CONTROL: Không tạo item cho mọi dòng danh sách. Giữ toàn bộ node có vai trò, sở hữu hoặc giao dịch thực tế; bỏ node chỉ có thông tin hành chính và không tạo được edge/risk/retrieval utility. Một sự kiện chỉ xuất hiện một lần ở lớp phù hợp nhất.",
        ],
    }.get(document_role, [])
    system = (
        "PHẠM VI EXTRACT HIỆN TẠI CHỈ CÓ GIL. Financial Quality không phải nhiệm vụ của LLM; các chỉ số BS/IS/CF nền đã có trong PostgreSQL ai-invest và server sẽ đọc trực tiếp. Không lặp lại các facts tài chính nền này trong response LLM. "
        "Bạn là bộ phận chú giải semantic của SAG, một hệ thống lưu trữ evidence mở "
        "phục vụ retrieval, GIL, Business Quality và chatbot phân tích doanh nghiệp. "
        "Bạn không phải bộ tóm tắt tài liệu và không phải bộ tính điểm. "
        "TUYỆT ĐỐI KHÔNG xếp hạng các section, không tự chọn Top-K và không để các section cạnh tranh với nhau. "
        "Phải đánh giá từng section độc lập theo ý nghĩa và utility thực tế của chính section đó. "
        "Chỉ trả về JSON hợp lệ đúng schema. "
        "Server sẽ tự parse số trong bảng, chuẩn hóa đơn vị/kỳ, lấy evidence quote, "
        "loại bản ghi trùng, lưu dữ liệu và phân tích Business Quality/GIL. "
        "Bạn chỉ làm phần hiểu semantic mà code không thể làm chắc chắn."
    )
    user = {
        "task": "sag_gil_evidence_annotation",
        "prompt_version": prompt_version,
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
        "rules": role_rules + [
            "WIRE CONTRACT BẮT BUỘC: response chỉ gồm taxonomy_version và năm mảng semantic entity_mentions, relations, observations, section_insights, gil_disclosures. Không tạo lớp phân tích cạnh tranh, node_annotations, facts, document_facets hoặc bất kỳ mảng/field trung gian nào; các dữ liệu đó do server tạo từ bảng và PostgreSQL.",
            "GIL GRAPH CONTRACT: mỗi relation phải giữ đủ identity của hai endpoint, hướng quan hệ, flow_kind khi văn bản xác nhận và evidence line. Phân biệt ownership edge, capital-flow edge và operating transaction; không tự gắn cycle, circular_flow hoặc tunneling. Nếu tài liệu xác nhận BCTC riêng hay hợp nhất, giữ accounting scope trong evidence/metadata; không so sánh số liệu BCTC riêng với mẫu số hợp nhất.",
            "MỖI BẰNG CHỨNG CHỈ MỘT LẦN: không mô tả cùng một sự kiện vừa bằng relation vừa bằng observation/gil_disclosure/section_insight nếu không có ý nghĩa khác hẳn. Relation là cạnh graph; observation là cơ chế/nguyên nhân/rủi ro; section_insight là utility của section. Nếu một item không thêm lớp thông tin riêng thì bỏ item đó.",
            "SCOPE GATING: chỉ extract evidence phục vụ GIL. Không tạo lại Financial Quality facts từ BS/IS/CF mà PostgreSQL ai-invest đã cung cấp. Tuy nhiên vẫn phải giữ relation và amount/ownership_pct khi chứng minh ownership, related-party, loan, receivable, guarantee, investment, capital flow hoặc structural risk của GIL.",
            "GIL COVERAGE: ưu tiên entity, ownership/control, related-party, giao dịch với bên liên quan, cho vay, phải thu/phải trả, bảo lãnh, đầu tư, góp vốn, cổ tức, hướng dòng tiền, phụ thuộc, tập trung và dấu hiệu multi-hop/cycle. Chỉ tạo relation khi tài liệu nêu rõ subject, object và hướng quan hệ; không suy ra fraud hay circular flow từ một giao dịch đơn lẻ.",
            "Ưu tiên theo thứ tự: evidence trực tiếp > giá trị phân tích thực tế > không trùng > độ đầy đủ. Không tạo item chỉ để tăng số lượng; nội dung không chắc chắn thì bỏ qua.",
            "Phân tích theo analytical_sections: mỗi section gồm heading, toàn bộ text trong phạm vi section, các bảng liên quan, table facts deterministic và movement candidates. Không xử lý table/text như hai nhiệm vụ semantic độc lập.",
            "SECTION INSIGHT — Đây là lớp semantic riêng của SAG để mô tả giá trị phân tích của TỪNG analytical section, sau khi đọc heading + toàn bộ text + các table trong section như một nội dung thống nhất. Duyệt TOÀN BỘ section từ đầu đến cuối; không dừng sau vài section, không xếp hạng, không Top-K, không quota và không gộp các section khác nhau chỉ vì cùng chủ đề. Với MỖI section, tự hỏi: (1) section cho biết doanh nghiệp tạo doanh thu/lợi nhuận hoặc nguồn tiền thế nào; (2) vốn được đầu tư, tài trợ, kiểm soát hoặc phân phối ra sao; (3) có evidence nền tảng về tiền, thanh khoản, vốn lưu động, vay/lãi vay, tài sản, thuế hoặc nghĩa vụ không; (4) có biến động, thay đổi chính sách, phụ thuộc, tập trung hay rủi ro cần truy hồi/phân tích không; (5) có cấu trúc sở hữu, giao dịch bên liên quan hoặc quan hệ kinh tế quan trọng không; (6) có chủ đề đặc thù ngành được chính tài liệu chứng minh không. Nếu section trả lời rõ ÍT NHẤT MỘT câu hỏi bằng evidence trực tiếp, tạo ĐÚNG MỘT SectionInsight cho section đó, kể cả khi con số đã có deterministic facts; facts không thay thế ý nghĩa semantic. SectionInsight phải trả lời ngắn gọn ba ý: section chứng minh điều gì, điều đó có ý nghĩa thực tế gì, và phục vụ câu hỏi phân tích nào. Insight phải là diễn giải có căn cứ của cả section, không phải tên bảng, định nghĩa bảng, mô tả rằng bảng 'trình bày/cho biết', chép lại row, hay lặp lại nguyên văn một observation. Chỉ bỏ SectionInsight khi section không có utility thực tế, là thủ tục/header/rác, hoặc trùng đồng thời cả evidence và ý nghĩa với insight khác; không bỏ chỉ vì cùng chủ đề, có table facts, hoặc không có một câu giải thích biến động riêng. Nếu section không trả lời được câu hỏi nào thì chỉ lưu facts/observation theo rule tương ứng. Các output khác không được dùng để thay thế hoặc làm mất SectionInsight có utility.",
            "Entity mention chỉ trả canonical_name, entity_type và evidence. Không trả raw_text nếu giống canonical_name; không trả raw_label, industry_context, extraction_rationale hoặc taxonomy_candidate khi không có giá trị đặc biệt. Server sẽ bổ sung các field nullable khi cần.",
            "Relation chỉ trả subject, object, relation_type, flow_kind, amount_vnd hoặc ownership_pct khi có giá trị thực và evidence. flow_kind chỉ dùng khi văn bản xác nhận ý nghĩa kinh tế: loan_disbursement, loan_repayment, receivable_balance, payable_balance, guarantee, investment, dividend, capital_contribution, asset_transfer, operating_transaction hoặc other. Không trả field null hay metadata lặp.",
            "TUYỆT ĐỐI không tạo fact từ bất kỳ dòng nào thuộc bảng Markdown, kể cả tổng cộng, dòng so sánh, bảng vốn chủ sở hữu, bảng doanh thu/chi phí, bảng đầu tư, bảng thuế hoặc bảng cổ phiếu. Server sẽ parse và tạo facts từ toàn bộ bảng bằng code deterministic.",
            "Chỉ tạo fact định lượng từ đoạn văn ngoài bảng khi đoạn văn chứa một mệnh đề số liệu độc lập mà server chưa thể lấy chắc chắn từ bảng. Trước khi tạo, phải kiểm tra giá trị đó không xuất hiện trong bảng. Nếu câu vừa có số liệu vừa có nguyên nhân/ý nghĩa, tách fact cho số liệu và observation cho nguyên nhân/ý nghĩa; không dồn toàn bộ vào observation.",
            "Bảng ownership, công ty con/liên kết, bên liên quan, vay, cho vay, bảo lãnh, phải thu/phải trả: chỉ tạo relation khi subject, object và hướng quan hệ được nêu rõ. Amount và ownership_pct chỉ điền khi evidence xác nhận.",
            "Server đã cung cấp movement_candidates được tính thuần túy từ các dòng bảng. Hãy dùng chúng như checklist để tìm câu text/thuyết minh giải thích nguyên nhân hoặc cơ chế. Chỉ tạo observation khi tài liệu có câu giải thích thật; nếu không tìm thấy câu giải thích thì không tạo observation 'chưa tìm thấy nguyên nhân'. Bảng và movement vẫn được giữ làm evidence/facts deterministic.",
            "Phân biệt rõ: Fact = số liệu nguyên tử; Observation = nguyên nhân/diễn giải/rủi ro/cơ chế; SectionInsight = một insight ngắn về utility và ý nghĩa của section, có thể tổng hợp text với table. Không dùng facts để chứa nội dung semantic, không chép từng row và không tạo row_semantics/analytical_signals riêng.",
            "Đừng chỉ diễn đạt lại định nghĩa của bảng. Với bảng có biến động, analytical_purpose phải nói bảng giúp trả lời câu hỏi phân tích nào và các row/signals phải chỉ ra thay đổi, mức độ tập trung, dòng tiền, exposure hoặc cơ chế kinh doanh được chứng minh bởi bảng.",
            "Không tạo graph signal từ một dòng cô lập nếu dòng đó không chứng minh entity/quan hệ/cơ chế hoặc so sánh cần thiết. Không tự suy ra quan hệ nhân quả, ranking, tổng hoặc durability.",
            "table_semantic_candidates chỉ là định vị table; analytical_sections là context chính và đã chứa heading cùng toàn bộ nội dung section. Với section có utility, trả tối đa một SectionInsight compact gồm insight và evidence; insight phải bắt đầu từ ý định/chủ đề của section, nêu rõ điều gì được chứng minh, ý nghĩa thực tế và dùng cho câu hỏi phân tích nào, không mở đầu bằng mô tả 'bảng này' và không dùng câu chung chung như 'giúp hiểu'. Có thể tổng hợp text với một hay nhiều table. Không trả table_title/table_role/analytical_purpose, row_semantics, analytical_signals, analytical_question, quote hoặc node_id; server sẽ hydrate phần cấu trúc.",
            "Mỗi observation chỉ trả statement, topic_tags nếu thật sự hữu ích, confidence và evidence. Subject/predicate/object, attributes, rationale và taxonomy_mapping không do LLM trả. Statement phải là nguyên nhân, cơ chế, rủi ro hoặc thay đổi chính sách được nêu trực tiếp trong text; không tạo observation chỉ để nhắc lại bảng hoặc báo rằng không tìm thấy nguyên nhân.",
            "Không tạo document_facets nếu tài liệu không thực sự có capability đó. Facet là nhãn nội dung mở, không phải nhãn loại tài liệu.",
            "Không chấm GIL hay lợi thế cạnh tranh; không tự tính tỷ lệ, không tự chuẩn hóa tiền tệ. Code xử lý các phần deterministic này.",
            "Evidence chỉ trả line_start/line_end 1-based, inclusive, nhỏ nhất nhưng đủ chứng minh item; không trả quote/node_id.",
            "Mọi fact/entity/relation phải khớp allowed_enums. Khái niệm ngoài enum dùng 'other' kèm raw_label và taxonomy_candidate; không được bỏ khái niệm chỉ vì chưa có taxonomy.",
            "Luôn trả đủ các top-level key của schema với [] nếu không có item. Không trả {}.",
        ],
        "allowed_enums": {
            "entity_type": [item.value for item in EntityType],
            "fact_type": [item.value for item in FactType],
            "relation_type": [item.value for item in RelationType],
        },
        "json_schema": llm_wire_json_schema(governance=use_entity_refs),
        "movement_candidates": movement_candidates,
        "table_semantic_candidates": table_candidates,
        "analytical_sections": analytical_sections,
    }
    if is_governance:
        user["task"] = "sag_governance_graph_annotation"
        user["rules"] = role_rules + governance_rules + [
            "GATING RULE BẮT BUỘC: các dòng trong danh sách 'related organizations/ tổ chức có liên quan' chỉ được tạo entity_type=related_party hoặc affiliate và relation_type=affiliated_with nếu có quan hệ; tuyệt đối không tạo subsidiary, controls hoặc owns từ danh sách này. Chỉ dùng subsidiary/controls/owns khi ngay evidence đó ghi rõ công ty con, kiểm soát hoặc sở hữu và có căn cứ tương ứng. Đây là kiểm tra đúng/sai, không phải tùy chọn diễn giải.",
            "NÉN QUAN HỆ GOVERNANCE: mỗi entity_mentions phải có entity_ref ngắn, duy nhất trong response (e1, e2, ...), cùng canonical_name một lần. Trong relations, luôn dùng subject_ref và object_ref trỏ tới entity_ref; không lặp subject/object là tên pháp nhân. Chỉ dùng subject/object khi không thể tham chiếu; mọi ref phải tồn tại trong entity_mentions. Đây chỉ là nén wire output; server sẽ khôi phục tên đầy đủ và không làm mất relation hay evidence.",
        ]
    elif is_annual:
        user["rules"] = user["rules"] + [
            "ANNUAL COMPACT OUTPUT: entity_mentions mỗi entity một lần; relations chỉ dùng subject_ref/object_ref, không gửi lại subject/object bằng tên dài. Mỗi cặp (subject_ref, object_ref, relation_type, flow_kind) chỉ xuất một relation duy nhất trong toàn response, bất kể xuất hiện bao nhiêu lần trong tài liệu. Không lặp cùng evidence trong observation, section_insight hoặc gil_disclosure; mỗi record phải có utility riêng.",
            "ANNUAL OUTPUT BUDGET: không cố lấp đủ mảng hoặc tạo bản ghi cho mọi dòng/bảng. Chỉ trả evidence semantic có giá trị cho GIL; facts bảng, số liệu nền, node annotation và facet không thuộc response. Một relation ngắn, một evidence span ngắn; không chép nội dung tài liệu vào signal.",
            "NÉN WIRE CHO ANNUAL: mỗi entity_mentions phải có entity_ref ngắn, duy nhất (e1, e2, ...), cùng canonical_name một lần. Trong relations, dùng subject_ref/object_ref trỏ tới entity_ref và không lặp tên pháp nhân; mọi ref phải tồn tại trong entity_mentions. Đây chỉ là nén format, server sẽ hydrate lại subject/object đầy đủ; không được bỏ relation, fact, observation hoặc evidence vì lý do nén.",
        ]
    # Replace the historical long rule list with one compact contract.  This
    # keeps role-specific rules while preventing duplicated/conflicting rules
    # from reaching the LLM.
    common_gil_rules = [
        "PHẠM VI: Chỉ extract evidence phục vụ GIL: entity, ownership/control, related-party, insider/affiliate, loan, receivable/payable, guarantee, investment, transaction, dependency và structural risk.",
        "ĐỌC THEO SECTION: đọc heading, toàn bộ text và các bảng trong cùng section như một nội dung thống nhất; không xử lý table và text như hai nhiệm vụ semantic độc lập.",
        "ENTITY/RELATION: chỉ tạo entity hoặc relation khi chủ thể, đối tượng và hướng quan hệ được tài liệu xác nhận trực tiếp. Không suy ra fraud, tunneling hay circular flow từ một dòng đơn lẻ.",
        "PHÂN LOẠI: owns/invests_in là cấu trúc; lends_to/creditor_of/guarantees_for/transacts_with là dòng giao dịch hoặc công nợ; không dùng một loại để thay thế loại khác. Nếu có thể xác định rõ bản chất kinh tế thì trả thêm flow_kind một lần cho relation; không tự suy đoán khi tài liệu không nói rõ.",
        "OBSERVATION: chỉ ghi nguyên nhân, cơ chế, dependency, concentration, risk hoặc thay đổi chính sách được nêu trong text; không chép row và không tạo câu 'không tìm thấy nguyên nhân'.",
        "SECTION INSIGHT: chỉ tạo tối đa một insight cho section có utility GIL thực tế; phải nêu điều được chứng minh, ý nghĩa và câu hỏi phân tích mà nó phục vụ. Không xếp hạng, Top-K hoặc quota.",
        "KHÔNG LẶP: một evidence chỉ xuất hiện một lần ở lớp phù hợp nhất. Không lặp cùng entity name trong relation; dùng entity_ref ngắn khi role hỗ trợ.",
        "FACTS: không tạo facts từ bảng Markdown và không lặp BS/IS/CF nền. Server sẽ parse bảng, chuẩn hóa tiền/kỳ, deduplicate và tính ratio deterministic.",
        "OUTPUT: chỉ trả taxonomy_version, entity_mentions, relations, observations, section_insights và gil_disclosures; entity/relation/observation chỉ trả field compact cùng evidence line_start/line_end.",
        "NGUYÊN TẮC: evidence trực tiếp > giá trị phân tích > không trùng > đầy đủ. Nội dung không chắc chắn hoặc không tạo thêm giá trị GIL thì bỏ qua; không tạo item để tăng số lượng.",
    ]
    user["rules"] = common_gil_rules + role_rules
    return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]


def audit_section_coverage_contract(markdown: str, manifest: ExtractionManifestIn) -> dict[str, Any]:
    """Deprecated: section coverage is intentionally not part of production."""
    return {}


async def audit_section_selection(
    issuer: Issuer,
    document: Document,
    markdown: str,
) -> dict[str, Any]:
    """Ask the LLM to explain every section decision without persisting it."""
    prompt_version = prompt_version_for_document(document)
    candidates = detect_table_semantic_candidates(markdown)
    movements = detect_material_table_movements(markdown)
    sections = build_analytical_section_index(markdown, candidates, movements)
    system = (
        "Bạn là auditor của SAG. Đây là audit chẩn đoán, không phải extraction production. "
        "Phải đánh giá ĐỘC LẬP từng analytical section; TUYỆT ĐỐI KHÔNG xếp hạng, không chọn Top-K "
        "và không để section này cạnh tranh với section khác. Quyết định keep/drop chỉ dựa trên utility "
        "thực tế của chính section đó đối với retrieval và phân tích doanh nghiệp."
    )
    user = {
        "task": "audit_every_section_keep_drop_reason",
        "prompt_version": prompt_version,
        "ticker": issuer.ticker,
        "document_id": document.id,
        "instruction": (
            "Duyệt tất cả section trong section_contexts, không bỏ qua section nào. "
            "Với mỗi section, trả decision keep hoặc drop, reason cụ thể dựa trên nội dung, "
            "và missing_value nếu drop (section bị drop có thể vẫn có facts nhưng không có semantic utility). "
            "Keep cả analytical signal lẫn analytical context nền tảng; drop chỉ khi section thực sự không "
            "tạo thêm cách hiểu/truy xuất hữu ích hoặc chỉ lặp lại. Không dùng số lượng insight làm tiêu chí."
        ),
        "section_contexts": [
            {
                "section_index": index,
                "heading": section.get("heading"),
                "line_start": section.get("content_line_start"),
                "line_end": section.get("content_line_end"),
                "table_ranges": section.get("table_ranges", []),
                "context": section.get("context", ""),
            }
            for index, section in enumerate(sections)
        ],
        "output_schema": {
            "sections": [
                {
                    "section_index": 0,
                    "decision": "keep|drop",
                    "reason": "lý do cụ thể",
                    "missing_value": "giá trị bị mất nếu drop, hoặc null",
                }
            ]
        },
    }
    result = await LLMClient(settings).complete_extraction_json([
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ])
    try:
        parsed = json.loads(result.content)
    except json.JSONDecodeError:
        parsed = {"raw": result.content}
    return {
        "prompt_version": prompt_version,
        "section_count": len(sections),
        "usage": {
            "input_tokens": int(getattr(result.usage, "input_tokens", 0) or 0) if result.usage else 0,
            "output_tokens": int(getattr(result.usage, "output_tokens", 0) or 0) if result.usage else 0,
        },
        "audit": parsed,
        "raw_response": result.content,
    }


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


def normalize_manifest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Make an empty JSON-object response safe without suppressing grounded extraction.

    The model may decide that no semantic item is safe to emit and return {}.
    Empty collections are a valid manifest; the server then adds deterministic
    table facts and node coverage. Unknown keys remain forbidden by Pydantic.
    """
    normalized = dict(payload)
    # Accept the pre-v17/pre-section contract during migration, but normalize
    # it immediately to the canonical section-level collection.
    if "section_insights" not in normalized and isinstance(normalized.get("table_semantics"), list):
        normalized["section_insights"] = normalized.pop("table_semantics")
    for key in _MANIFEST_COLLECTION_KEYS:
        if key not in normalized:
            normalized[key] = []
    return normalized


def drop_table_backed_observations(markdown: str, manifest: ExtractionManifestIn) -> int:
    """Keep observations semantic: numeric/table evidence belongs to code facts.

    A table can still receive ``TableSemantic`` annotations.  It must not also
    become an LLM observation that merely paraphrases a row (for example,
    ``Công ty trả cổ tức bằng tiền``).
    """
    lines = markdown.splitlines()
    kept: list[ObservationIn] = []
    dropped = 0
    for item in manifest.observations:
        start = item.evidence.line_start
        end = min(item.evidence.line_end, len(lines))
        evidence_lines = lines[max(0, start - 1):end]
        if evidence_lines and any(line.strip().startswith("|") for line in evidence_lines):
            dropped += 1
            continue
        kept.append(item)
    manifest.observations = kept
    return dropped


def filter_table_semantics_by_value(markdown: str, manifest: ExtractionManifestIn) -> int:
    """Legacy compatibility shim; semantic value belongs to the LLM."""
    return 0


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
            # Unknown table rows remain available in the canonical Markdown
            # and table node, but must not pollute the queryable facts graph.
            # In particular, headers/auxiliary rows classified as
            # ``unmapped_table_line_item`` are not semantic facts.
            if taxonomy_candidate == "unmapped_table_line_item":
                skipped += 1
                continue
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
                evidence=EvidenceRef(node_id=node.node_id, quote=quote, line_start=line_no, line_end=line_no),
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


def deduplicate_manifest_items(manifest: ExtractionManifestIn) -> dict[str, int]:
    """Canonicalize duplicate output after extraction; never filter by usefulness.

    The model may emit broad, grounded candidates. This pass only collapses
    semantically identical records, preserving the strongest candidate when
    duplicate records disagree on optional detail.
    """

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
        previous = kept_facts[previous_index]
        # Deterministic table evidence is preferred over a duplicate LLM hint.
        if item.extraction_rationale == "Deterministic markdown table extraction." and previous.extraction_rationale != item.extraction_rationale:
            kept_facts[previous_index] = item
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


def _infer_accounting_scope(markdown: str, document: Document) -> str:
    """Resolve scope conservatively; UNKNOWN is safer than mixing scopes."""
    sample = f"{document.filename} {markdown[:12000]}".casefold()
    if any(token in sample for token in ("báo cáo tài chính riêng", "bctc riêng", "standalone", "separate financial")):
        return "STANDALONE"
    if any(token in sample for token in ("báo cáo tài chính hợp nhất", "bctc hợp nhất", "consolidated financial")):
        return "CONSOLIDATED"
    return "UNKNOWN"


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


def detect_material_table_movements(markdown: str, *, max_candidates: int = 32) -> list[dict[str, Any]]:
    """Find review targets in comparative tables without asking the LLM to calculate.

    This is deliberately a prompt aid, not a persisted fact layer.  It gives the
    model a bounded checklist of large row-to-row changes so semantic annotation
    does not depend on the model noticing every important number in a long note.
    The original table line remains the only evidence source.
    """
    candidates: list[dict[str, Any]] = []
    for line_no, cells, headers in _iter_markdown_table_body_rows(markdown):
        if _is_separator_row(cells) or len(cells) < 3:
            continue
        row_label = _row_label(cells)
        if not row_label or _looks_like_table_header(row_label, cells):
            continue
        numeric: list[tuple[int, float, str]] = []
        for index, cell in enumerate(cells[1:], start=1):
            parsed = _parse_table_value(cell)
            if parsed is None or _is_accounting_code_cell(cells, index, parsed[1]):
                continue
            numeric.append((index, parsed[0], parsed[1]))
        if len(numeric) < 2:
            continue
        left_index, left_value, left_text = numeric[0]
        right_index, right_value, right_text = numeric[1]
        if left_value == right_value:
            continue
        delta = left_value - right_value
        denominator = abs(right_value)
        relative_pct = (delta / denominator * 100.0) if denominator else None
        # A zero prior is material whenever a new non-zero amount appears;
        # otherwise require either a 10% movement or a large absolute change.
        material = denominator == 0 or abs(relative_pct or 0.0) >= 10.0
        if not material:
            continue
        candidates.append({
            "line": line_no,
            "row_label": row_label[:256],
            "left_header": headers.get(left_index, f"column_{left_index}")[:160],
            "left_value": left_text,
            "right_header": headers.get(right_index, f"column_{right_index}")[:160],
            "right_value": right_text,
            "delta": round(delta, 6),
            "relative_change_pct": round(relative_pct, 2) if relative_pct is not None else None,
            "review_target": "Kiểm tra section context để xác định có ý nghĩa hoặc giải thích thực tế hay không; không tự tạo observation nếu tài liệu không có evidence.",
        })
    candidates.sort(key=lambda item: (item["relative_change_pct"] is None, -abs(item["relative_change_pct"] or 0.0)))
    return candidates[:max_candidates]


def detect_table_semantic_candidates(markdown: str, *, max_candidates: int = 24) -> list[dict[str, Any]]:
    """Build a neutral table inventory; the LLM decides semantic value."""
    priority_terms = {
        "ownership": ("cong ty con", "cong ty lien ket", "so huu", "ben lien quan"),
        "investment": ("dau tu vao cong ty con", "dau tu tai chinh", "cho vay"),
        "working_capital": ("phai thu", "phai tra", "ton kho", "tien mat", "tien gui"),
        "earnings": ("doanh thu cung cap", "gia von", "doanh thu hoat dong tai chinh", "chi phi di vay"),
        "capital": ("von chu so huu", "von gop", "co phieu"),
        "reclassification": ("phan loai lai", "dieu chinh"),
        "tax": ("thue", "phai nop nha nuoc"),
    }
    rows = list(_iter_markdown_table_rows(markdown))
    blocks: list[list[tuple[int, list[str]]]] = []
    current: list[tuple[int, list[str]]] = []
    previous = 0
    for line_no, cells in rows:
        if current and line_no != previous + 1:
            blocks.append(current)
            current = []
        current.append((line_no, cells))
        previous = line_no
    if current:
        blocks.append(current)

    movement_lines = {item["line"] for item in detect_material_table_movements(markdown, max_candidates=64)}
    candidates: list[dict[str, Any]] = []
    for block in blocks:
        if not any(_is_separator_row(cells) for _line, cells in block):
            continue
        start, end = block[0][0], block[-1][0]
        text = _fold_text(" ".join(" ".join(cells) for _line, cells in block))
        matched = [name for name, terms in priority_terms.items() if any(term in text for term in terms)]
        has_material_movement = any(start <= line <= end for line in movement_lines)
        title = next((cell.strip() for _line, cells in block[:2] for cell in cells if cell.strip() and not _TABLE_SEPARATOR_RE.match(cell)), "table")
        row_labels = []
        for _line, cells in block[2:]:
            label = _row_label(cells)
            if label and label not in row_labels:
                row_labels.append(label[:160])
        candidates.append({
            "table_start": start,
            "table_end": end,
            "title_hint": title[:200],
            "topic_hints": matched,
            "material_movement": has_material_movement,
            "row_labels": row_labels[:6],
            "candidate_reason": "numeric_comparison_or_topic_hint" if (has_material_movement or matched) else "table_structure",
        })
    candidates.sort(key=lambda item: item["table_start"])
    return candidates[:max_candidates]


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


def _fold_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return stripped.replace("đ", "d")


def _iter_manifest_refs(manifest: ExtractionManifestIn):
    """Yield (path, evidence) for every evidence-carrying item in stable order."""
    collections = (
        ("entity_mentions", manifest.entity_mentions),
        ("facts", manifest.facts),
        ("relations", manifest.relations),
        # Legacy input only: kept so old manifests can be validated/read, but
        # the extraction wire schema and persistence path omit this collection.
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
    """Resolve LLM evidence by cited line, keeping opaque node IDs outside the prompt."""
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
        if evidence.line_start is None:
            raise ValueError(f"{path} thiếu evidence.line_hint")
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
    table_indices = sorted(
        {int(item["path"].split(".")[1]) for item in failures if item["path"].startswith("section_insights.")},
        reverse=True,
    )
    for index in table_indices:
        if 0 <= index < len(manifest.section_insights):
            manifest.section_insights.pop(index)
            dropped.append(f"section_insights.{index}")
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
        ref.line_start = item.line_hint
        ref.line_end = item.line_hint
    # Full re-validation: the repaired manifest must ground completely.
    _validate_manifest_shape_and_references(markdown, nodes, manifest)
    return expected, usage


async def extract_and_persist_manifest(
    session: AsyncSession,
    issuer: Issuer,
    document: Document,
    markdown: str,
    nodes: list[DocumentTreeNode],
    statement_markdown: str | None = None,
) -> ExtractionResult:
    llm = LLMClient(settings)
    prompt_version = prompt_version_for_document(document)
    accounting_scope = _infer_accounting_scope(markdown, document)
    if not llm.extraction_configured:
        return ExtractionResult(
            status=ProcessingStageStatus.INCOMPLETE.value,
            error="LLM extraction chưa được cấu hình",
            metadata={"mode": "llm_manifest", "prompt_version": prompt_version, "configured": False},
        )

    messages = build_extraction_prompt(issuer, document, markdown, nodes)
    total_usage = 0
    started_at = time.perf_counter()
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
                        "duration_ms": round((time.perf_counter() - started_at) * 1000),
                        "audit_raw_llm_response": result.content if settings.extraction_audit_enabled else None,
                        "audit_input_tokens": int(getattr(result.usage, "input_tokens", 0) or 0) if result.usage else 0,
                        "audit_output_tokens": int(getattr(result.usage, "output_tokens", 0) or 0) if result.usage else 0,
                        "audit_reasoning_tokens": int(getattr(result.usage, "reasoning_tokens", 0) or 0) if result.usage else 0,
                        "suggested_action": "increase_max_tokens",
                        "prompt_version": prompt_version,
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
        payload = normalize_manifest_payload(payload)
        document_role = str(document.doc_role or "").upper()
        use_entity_refs = document_role in {"GOVERNANCE_REPORT", "ANNUAL_BACKBONE"}
        payload, dropped_wire_fields = compact_llm_payload_for_validation(payload, governance=use_entity_refs)
        payload = expand_compact_manifest_payload(payload, governance=use_entity_refs)
        coerced = coerce_unknown_enums(payload)
        manifest = ExtractionManifestIn.model_validate(payload)
        metadata: dict[str, Any] = {
            "mode": "llm_manifest",
            "attempts": 1,
            "finish_reason": result.finish_reason,
            "prompt_version": prompt_version,
            "taxonomy_version": TAXONOMY_VERSION,
            "duration_ms": round((time.perf_counter() - started_at) * 1000),
            "accounting_scope": accounting_scope,
        }
        if settings.extraction_audit_enabled:
            usage = result.usage
            metadata["audit_raw_llm_response"] = result.content
            metadata["audit_input_tokens"] = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
            metadata["audit_output_tokens"] = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
            metadata["audit_reasoning_tokens"] = int(getattr(usage, "reasoning_tokens", 0) or 0) if usage else 0
        if coerced:
            metadata["coerced_enum_count"] = len(coerced)
            metadata["coerced_enums"] = coerced[:32]
        if dropped_wire_fields:
            metadata["llm_wire_fields_dropped"] = dropped_wire_fields
        resolve_llm_evidence_nodes(markdown, nodes, manifest)
        grounding_stats = sanitize_manifest_value_grounding(manifest)
        table_stats = enrich_manifest_with_table_facts(document, markdown, nodes, manifest)
        dedup_stats = deduplicate_manifest_items(manifest)
        facet_stats = enrich_manifest_facets_from_observations(manifest)
        missing_annotation_count = fill_missing_node_annotations(nodes, manifest)
        metadata.update(grounding_stats)
        metadata.update(table_stats)
        metadata.update(dedup_stats)
        metadata.update(facet_stats)
        metadata["deterministic_node_annotations_added"] = missing_annotation_count
        metadata["section_insights_count"] = len(manifest.section_insights)
        metadata["section_insights"] = [item.model_dump(mode="json") for item in manifest.section_insights]
        try:
            _validate_manifest_shape_and_references(markdown, nodes, manifest)
        except ValueError:
            failures = _collect_grounding_failures(markdown, nodes, manifest)
            if not failures:
                raise
            dropped = _drop_ungrounded_manifest_items(manifest, failures)
            if dropped:
                metadata["dropped_ungrounded_paths"] = dropped
                if settings.extraction_audit_enabled:
                    metadata["dropped_ungrounded_items"] = failures
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
            counts = await validate_and_persist_manifest(
                session, issuer, document, markdown, nodes, manifest,
                statement_markdown=statement_markdown,
            )
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
    statement_markdown: str | None = None,
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
    deterministic_statement_facts = await _persist_statement_table_facts(
        session, issuer, document, statement_markdown
    ) if statement_markdown else 0
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

    fact_count = deterministic_statement_facts
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
                    "accounting_scope": _infer_accounting_scope(markdown, document),
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
                    "accounting_scope": _infer_accounting_scope(markdown, document),
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
        "observation_count": observation_count,
        "facet_count": facet_count,
        "evidence_count": len(evidence_cache),
    }


async def _persist_statement_table_facts(
    session: AsyncSession,
    issuer: Issuer,
    document: Document,
    markdown: str,
) -> int:
    """Persist BS/IS/CF rows before the statement prefix is cleaned away."""
    lines = markdown.splitlines()
    notes_line = next(
        (index for index, line in enumerate(lines, start=1)
         if "thuyet minh bao cao tai chinh" in _fold_text(line.lstrip("# "))),
        len(lines) + 1,
    )
    scope = _infer_accounting_scope(markdown, document)
    count = 0
    for line_no, cells, headers in _iter_markdown_table_body_rows(markdown):
        if line_no >= notes_line or len(cells) < 2:
            continue
        row_label = _row_label(cells)
        if not row_label or _looks_like_table_header(row_label, cells):
            continue
        for index, cell in enumerate(cells[1:], start=1):
            parsed = _parse_table_value(cell)
            if parsed is None or "ma so" in _fold_text(headers.get(index, "")):
                continue
            value_numeric, value_text = parsed
            fact_type, semantic_key, taxonomy_candidate = _classify_table_fact("", row_label)
            if taxonomy_candidate == "unmapped_table_line_item":
                continue
            header = headers.get(index, "")
            period_start, period_end, as_of = _period_from_column(header)
            quote = lines[line_no - 1].strip()
            node_id = f"statement_table_{line_no}"
            span = EvidenceSpan(
                id=new_id(),
                document_id=document.id,
                issuer_id=issuer.id,
                node_id=node_id,
                start_line=line_no,
                end_line=line_no,
                quote_hash=sha256_text(quote),
                metadata_json={"source": "DETERMINISTIC_STATEMENT_TABLE"},
            )
            session.add(span)
            session.add(Fact(
                issuer_id=issuer.id,
                document_id=document.id,
                node_id=node_id,
                evidence_span_id=span.id,
                fact_type=fact_type.value,
                semantic_key=semantic_key,
                label=_table_fact_label(row_label, header),
                value_text=value_text,
                value_numeric=value_numeric,
                unit=_unit_from_context(header, row_label, value_text),
                currency="VND" if _unit_from_context(header, row_label, value_text) == "VND" else None,
                period_start=period_start or document.period_start,
                period_end=period_end or document.period_end,
                as_of=as_of or document.period_end,
                validation_status=ValidationStatus.VALIDATED.value,
                raw_label=row_label[:256],
                taxonomy_candidate=taxonomy_candidate,
                metadata_json={
                    "source": "DETERMINISTIC_STATEMENT_TABLE",
                    "statement_scope": scope,
                    "statement_line": line_no,
                    "statement_column": header,
                    "taxonomy_version": TAXONOMY_VERSION,
                },
            ))
            count += 1
    return count


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
            "raw_llm_quote": evidence._raw_llm_quote,
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
