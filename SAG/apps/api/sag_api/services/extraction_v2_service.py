from __future__ import annotations

import json
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
    DocumentTreeNode,
    Entity,
    EntityAlias,
    EntityMention,
    EvidenceSpan,
    Fact,
    Issuer,
    MoatSignal,
    Relation,
    ReviewQueueItem,
)
from sag_api.enums import EntityType, FactType, MoatPillar, ProcessingStageStatus, RelationType, ValidationStatus
from sag_api.generation.llm import LLMClient
from sag_api.services.document_structure_service import sha256_text

TAXONOMY_VERSION = "financial-evidence-taxonomy-v2"
EXTRACTION_PROMPT_VERSION = "financial-full-document-extraction-v2"


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
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


class ExtractionManifestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taxonomy_version: str = TAXONOMY_VERSION
    node_annotations: list[NodeAnnotationIn]
    entity_mentions: list[EntityMentionIn] = Field(default_factory=list)
    facts: list[FactIn] = Field(default_factory=list)
    relations: list[RelationIn] = Field(default_factory=list)
    moat_signals: list[MoatSignalIn] = Field(default_factory=list)
    gil_disclosures: list[GilDisclosureIn] = Field(default_factory=list)


@dataclass(frozen=True)
class ExtractionResult:
    status: str
    fact_count: int = 0
    relation_count: int = 0
    moat_signal_count: int = 0
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
    node_manifest = [
        {
            "node_id": node.node_id,
            "parent_id": node.parent_id,
            "level": node.level,
            "heading_path": node.heading_path,
            "node_kind": node.node_kind,
            "start_line": node.start_line,
            "end_line": node.end_line,
            "excluded_from_analysis": bool((node.metadata_json or {}).get("excluded_from_analysis")),
            "exclusion_reason": (node.metadata_json or {}).get("exclusion_reason"),
        }
        for node in nodes
    ]
    system = (
        "You are SAG v2, a financial evidence extraction engine for Vietnamese public-company reports. "
        "Return only valid JSON matching the provided schema. Do not invent taxonomy enum values. "
        "Use OTHER plus taxonomy_candidate for industry-specific concepts. Do not score MOAT or GIL. "
        "Every extracted item must cite an exact quote inside the referenced node. "
        "Quotes should be long enough to appear exactly once in that node. "
        "If a quote may repeat in a table, include line_hint with the exact Markdown line number."
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
            "Annotate every node exactly once, including relevance NONE.",
            "Create facts only when there is a concrete financial/economic disclosure.",
            "Create directed relations only when subject, object and direction are explicit.",
            "TRANSACTS_WITH is not a capital-flow edge.",
            "Use exact quotes copied from the node without line numbers. Include enough surrounding words/table labels so the quote appears exactly once in the referenced node.",
            "For table numbers or short repeated values, include evidence.line_hint using the Markdown line number from markdown_with_line_numbers.",
            "Never output enum values outside allowed_enums. Examples: cash_balance, inventory_balance, operating_cash_flow, production_volume, market_share, board_member, audit_opinion must use fact_type/entity_type/relation_type='other' plus raw_label and taxonomy_candidate.",
            "If you are unsure whether a label fits an enum, use 'other' and put the original concept in taxonomy_candidate.",
        ],
        "allowed_enums": {
            "entity_type": [item.value for item in EntityType],
            "fact_type": [item.value for item in FactType],
            "relation_type": [item.value for item in RelationType],
            "moat_pillar": [item.value for item in MoatPillar],
        },
        "json_schema": extraction_json_schema(),
        "node_manifest": node_manifest,
        "markdown_with_line_numbers": numbered_markdown,
    }
    return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]


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
    last_error = ""
    for attempt in range(2):
        try:
            result = await llm.complete_extraction_json(messages)
            total_usage += _usage_total(result.usage)
            if result.finish_reason and result.finish_reason not in {"stop", "tool_calls"}:
                if result.finish_reason == "length":
                    # Option A: output bị cắt do vượt max_tokens — retry cùng prompt
                    # chỉ làm dài thêm messages nên trả INCOMPLETE ngay, gợi ý
                    # extraction theo cụm node thay vì retry nguyên document.
                    return ExtractionResult(
                        status=ProcessingStageStatus.INCOMPLETE.value,
                        token_usage=total_usage,
                        error=(
                            "finish_reason=length: manifest vượt max_tokens "
                            f"(doc_role={document.doc_role}, nodes={len(nodes)}). "
                            "Gợi ý: tăng SAG_LLM_MAX_TOKENS cho ANNUAL_BACKBONE "
                            "hoặc chạy extraction theo cụm node (chunked)."
                        )[:2000],
                        metadata={
                            "mode": "llm_manifest",
                            "attempts": attempt + 1,
                            "finish_reason": "length",
                            "suggested_action": "chunked_extraction",
                            "prompt_version": EXTRACTION_PROMPT_VERSION,
                            "taxonomy_version": TAXONOMY_VERSION,
                        },
                    )
                raise ValueError(f"finish_reason={result.finish_reason}")
            manifest = ExtractionManifestIn.model_validate_json(result.content)
            _validate_manifest_shape_and_references(markdown, nodes, manifest)
            async with session.begin_nested():
                counts = await validate_and_persist_manifest(session, issuer, document, markdown, nodes, manifest)
            return ExtractionResult(
                status=ProcessingStageStatus.COMPLETE.value,
                token_usage=total_usage,
                metadata={
                    "mode": "llm_manifest",
                    "attempts": attempt + 1,
                    "finish_reason": result.finish_reason,
                    "prompt_version": EXTRACTION_PROMPT_VERSION,
                    "taxonomy_version": TAXONOMY_VERSION,
                },
                **counts,
            )
        except (json.JSONDecodeError, PydanticValidationError, ValueError) as exc:
            last_error = str(exc)
            messages = [
                *messages,
                {"role": "assistant", "content": "The previous JSON was rejected by server validation."},
                {
                    "role": "user",
                    "content": (
                        "Retry once. Return only complete JSON matching the schema. "
                        "Fix the exact server validation error. If the error says an enum is invalid, replace it with 'other' and keep the original concept in raw_label/taxonomy_candidate. "
                        "If the error says a quote is missing or non-unique, copy a longer exact quote from the same node or add evidence.line_hint. "
                        f"Server validation error: {last_error[:2000]}"
                    ),
                },
            ]

    return ExtractionResult(
        status=ProcessingStageStatus.INCOMPLETE.value,
        token_usage=total_usage,
        error=last_error[:2000],
        metadata={"mode": "llm_manifest", "attempts": 2, "error": last_error[:2000]},
    )


def _validate_manifest_shape_and_references(
    markdown: str,
    nodes: list[DocumentTreeNode],
    manifest: ExtractionManifestIn,
) -> None:
    """Pure validation before opening a DB savepoint.

    A rejected manifest should not roll back a SQLAlchemy nested transaction
    before the retry prompt is assembled; otherwise async ORM objects may be
    expired and later attribute access can trigger `greenlet_spawn` errors.
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
    for ref in refs:
        node = nodes_by_id.get(ref.node_id)
        if node is None:
            raise ValueError(f"evidence node_id không tồn tại: {ref.node_id}")
        _locate_quote(markdown, node, ref.quote, ref.line_hint)


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

    await session.flush()
    return {
        "fact_count": fact_count,
        "relation_count": relation_count,
        "moat_signal_count": moat_signal_count,
        "evidence_count": len(evidence_cache),
    }


async def _delete_previous_extraction(session: AsyncSession, document_id: str) -> None:
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
    key = (evidence.node_id, evidence.quote)
    if key in cache:
        return cache[key]
    node = nodes_by_id.get(evidence.node_id)
    if node is None:
        raise ValueError(f"evidence node_id không tồn tại: {evidence.node_id}")
    location = _locate_quote(markdown, node, evidence.quote, evidence.line_hint)
    span = EvidenceSpan(
        issuer_id=issuer.id,
        document_id=document.id,
        node_id=evidence.node_id,
        start_line=location["start_line"],
        end_line=location["end_line"],
        start_char=location["start_char"],
        end_char=location["end_char"],
        quote_hash=sha256_text(str(location["resolved_quote"])),
        metadata_json={
            "quote": location["resolved_quote"],
            "llm_quote": evidence.quote,
            "quote_resolution": location["resolution"],
            "taxonomy_version": TAXONOMY_VERSION,
            "prompt_version": EXTRACTION_PROMPT_VERSION,
        },
    )
    session.add(span)
    await session.flush()
    cache[key] = span
    return span


def _locate_quote(markdown: str, node: DocumentTreeNode, quote: str, line_hint: int | None = None) -> dict[str, int]:
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
        fallback = _fallback_line_match(lines, node, quote, line_hint)
        if fallback is not None:
            return fallback
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
