from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError as PydanticValidationError, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.db.base import new_id
from sag_api.db.models import (
    Document,
    DocumentEntity,
    DocumentEvidenceChunk,
    DocumentFact,
    DocumentRelation,
    DocumentTreeNode,
    Source,
)
from sag_api.enums import DocumentRole, DocumentStatus, ProcessingStageStatus


ACTIVE_DOCUMENT_ROLES = (
    DocumentRole.ANNUAL_BACKBONE.value,
    DocumentRole.LATEST_QUARTER.value,
    DocumentRole.GOVERNANCE_REPORT.value,
)

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_ROMAN_HEADING_RE = re.compile(r"^([IVXLCDM]{1,8})\.\s+(.{3,160})$", re.IGNORECASE)
_NUMBERED_HEADING_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})[.)]\s+(.{3,180})$")
_NOTE_HEADING_RE = re.compile(r"^(?:thuyết\s+minh|thuyet\s+minh|note)\s+(\d{1,2}[a-z]?)\b[.:]?\s*(.*)$", re.IGNORECASE)
_MONEY_RE = re.compile(r"(?P<number>\d[\d., ]{2,})\s*(?P<unit>tỷ|triệu|nghìn|đồng|vnd|%)", re.IGNORECASE)
_PCT_RE = re.compile(r"(?P<number>\d{1,3}(?:[,.]\d+)?)\s*%")

_OCR_NOISE_MARKERS = (
    "hồy phạt",
    "hòa hợp củng phát triển",
    "cong ty co phan tap doan",
    "công ty cổ phần tập đoàn",
)


@dataclass(frozen=True)
class ParsedNode:
    node_id: str
    parent_id: str | None
    level: int
    order: int
    heading: str
    heading_path: str
    node_kind: str
    start_line: int
    end_line: int
    content_hash: str
    metadata: dict[str, Any]


class ExtractionReference(BaseModel):
    node_id: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    quote: str | None = None


class NodeAnnotationIn(BaseModel):
    node_id: str
    summary: str | None = Field(default=None, max_length=1200)
    relevance: dict[str, Any] = Field(default_factory=dict)


class EntityIn(BaseModel):
    canonical_name: str = Field(min_length=1, max_length=512)
    display_name: str | None = Field(default=None, max_length=512)
    entity_type: str = Field(default="COMPANY", max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("entity_type")
    @classmethod
    def normalize_type(cls, value: str) -> str:
        return (value or "OTHER").strip().upper() or "OTHER"


class FactIn(BaseModel):
    fact_type: str = Field(default="OTHER", max_length=64)
    label: str = Field(min_length=1, max_length=512)
    value: str | None = Field(default=None, max_length=256)
    unit: str | None = Field(default=None, max_length=64)
    period: str | None = Field(default=None, max_length=64)
    evidence: ExtractionReference
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("fact_type")
    @classmethod
    def normalize_fact_type(cls, value: str) -> str:
        return (value or "OTHER").strip().upper() or "OTHER"


class RelationIn(BaseModel):
    subject: str = Field(min_length=1, max_length=512)
    object: str = Field(min_length=1, max_length=512)
    relation_type: str = Field(max_length=64)
    period: str | None = Field(default=None, max_length=64)
    amount_vnd: float | None = None
    ownership_pct: float | None = None
    verified: bool = True
    evidence: ExtractionReference
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("relation_type")
    @classmethod
    def normalize_relation_type(cls, value: str) -> str:
        return (value or "OTHER").strip().upper() or "OTHER"


class MoatSignalIn(BaseModel):
    pillar: str = Field(default="OTHER", max_length=64)
    signal: str = Field(min_length=1, max_length=512)
    direction: str = Field(default="support", max_length=32)
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence: ExtractionReference
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractionManifest(BaseModel):
    node_annotations: list[NodeAnnotationIn] = Field(default_factory=list)
    entities: list[EntityIn] = Field(default_factory=list)
    facts: list[FactIn] = Field(default_factory=list)
    relations: list[RelationIn] = Field(default_factory=list)
    moat_signals: list[MoatSignalIn] = Field(default_factory=list)


@dataclass(frozen=True)
class ManifestBuildResult:
    fact_count: int
    status: str
    token_usage: int = 0
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class EvidenceBuildResult:
    chunk_count: int
    status: str
    metadata: dict[str, Any] | None = None


def sha256_text(value: str | bytes) -> str:
    data = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def normalize_doc_role(value: str | DocumentRole | None) -> str | None:
    if value is None:
        return None
    raw = value.value if isinstance(value, DocumentRole) else str(value)
    normalized = raw.strip().upper()
    if not normalized:
        return None
    allowed = set(ACTIVE_DOCUMENT_ROLES)
    if normalized not in allowed:
        from sag_api.core.errors import ValidationError

        raise ValidationError(
            "doc_role phải là ANNUAL_BACKBONE, LATEST_QUARTER hoặc GOVERNANCE_REPORT"
        )
    return normalized


def _clean_heading(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip().strip("#").strip())


def _ascii_fold(value: str) -> str:
    import unicodedata

    normalized = unicodedata.normalize("NFD", value)
    stripped = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return stripped.replace("đ", "d").replace("Đ", "D").casefold()


def looks_like_ocr_heading_noise(line: str, seen_counts: dict[str, int] | None = None) -> bool:
    heading = _clean_heading(line)
    if len(heading) < 6:
        return False
    folded = _ascii_fold(heading)
    if any(marker in folded for marker in _OCR_NOISE_MARKERS):
        if seen_counts is None:
            return True
        return seen_counts.get(folded, 0) >= 1
    alpha = [ch for ch in heading if ch.isalpha()]
    if len(alpha) >= 8 and sum(ch.isupper() for ch in alpha) / len(alpha) > 0.85:
        return seen_counts is not None and seen_counts.get(folded, 0) >= 2
    return False


def _detect_heading(line: str, seen_counts: dict[str, int]) -> tuple[int, str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("|"):
        return None
    md = _MD_HEADING_RE.match(stripped)
    if md:
        heading = _clean_heading(md.group(2))
        folded = _ascii_fold(heading)
        seen_counts[folded] = seen_counts.get(folded, 0) + 1
        if looks_like_ocr_heading_noise(heading, seen_counts):
            return None
        return len(md.group(1)), heading, "heading"

    note = _NOTE_HEADING_RE.match(stripped)
    if note and len(stripped) <= 220:
        heading = _clean_heading(stripped)
        return 3, heading, "note"

    roman = _ROMAN_HEADING_RE.match(stripped)
    if roman and len(stripped) <= 180:
        return 2, _clean_heading(stripped), "section"

    numbered = _NUMBERED_HEADING_RE.match(stripped)
    if numbered and len(stripped) <= 200 and not _MONEY_RE.search(stripped):
        dots = numbered.group(1).count(".")
        return min(6, 3 + dots), _clean_heading(stripped), "section"
    return None


def parse_markdown_tree(
    markdown: str,
    *,
    document_id: str,
    source_id: str,
    metadata: dict[str, Any] | None = None,
) -> tuple[list[ParsedNode], dict[str, Any]]:
    lines = markdown.splitlines()
    total_lines = max(1, len(lines))
    base_meta = dict(metadata or {})
    root_heading = str(base_meta.get("title") or "Document").strip() or "Document"
    stack: list[dict[str, Any]] = []
    drafts: list[dict[str, Any]] = [
        {
            "level": 0,
            "order": 0,
            "heading": root_heading,
            "node_kind": "document",
            "start_line": 1,
            "end_line": total_lines,
            "parent_id": None,
            "path": [root_heading],
        }
    ]
    stack.append(drafts[0])
    seen_counts: dict[str, int] = {}

    for index, line in enumerate(lines, start=1):
        detected = _detect_heading(line, seen_counts)
        if detected is None:
            continue
        level, heading, node_kind = detected
        while stack and int(stack[-1]["level"]) >= level:
            stack.pop()
        parent = stack[-1] if stack else drafts[0]
        if drafts and drafts[-1] is not drafts[0] and int(drafts[-1]["end_line"]) == total_lines:
            drafts[-1]["end_line"] = max(int(drafts[-1]["start_line"]), index - 1)
        path = [*parent["path"], heading]
        draft = {
            "level": level,
            "order": len(drafts),
            "heading": heading,
            "node_kind": node_kind,
            "start_line": index,
            "end_line": total_lines,
            "parent_ref": parent,
            "path": path,
        }
        drafts.append(draft)
        stack.append(draft)

    for draft in drafts[1:]:
        following = [d for d in drafts[1:] if int(d["order"]) > int(draft["order"]) and int(d["level"]) <= int(draft["level"])]
        if following:
            draft["end_line"] = max(int(draft["start_line"]), int(following[0]["start_line"]) - 1)

    nodes: list[ParsedNode] = []
    draft_ids: dict[int, str] = {}
    for draft in drafts:
        raw_id = f"{document_id}:{draft['order']}:{draft['start_line']}:{'/'.join(draft['path'])}"
        draft_ids[id(draft)] = "n_" + sha256_text(raw_id)[:24]
    for draft in drafts:
        start = int(draft["start_line"])
        end = int(draft["end_line"])
        content = "\n".join(lines[start - 1 : end])
        parent_ref = draft.get("parent_ref")
        nodes.append(
            ParsedNode(
                node_id=draft_ids[id(draft)],
                parent_id=draft_ids[id(parent_ref)] if parent_ref is not None else None,
                level=int(draft["level"]),
                order=int(draft["order"]),
                heading=str(draft["heading"]),
                heading_path=" > ".join(str(p) for p in draft["path"]),
                node_kind=str(draft["node_kind"]),
                start_line=start,
                end_line=end,
                content_hash=sha256_text(content),
                metadata=base_meta,
            )
        )

    coverage = {
        "total_lines": total_lines,
        "covered_lines": total_lines,
        "coverage_ratio": 1.0,
        "node_count": len(nodes),
        "non_root_node_count": max(0, len(nodes) - 1),
        "ocr_noise_policy": "repeated_headers_removed",
    }
    return nodes, coverage


def hydrate_node_content(markdown: str, node: DocumentTreeNode) -> str:
    lines = markdown.splitlines()
    start = max(1, int(node.start_line or 1))
    end = min(len(lines), int(node.end_line or start))
    return "\n".join(lines[start - 1 : end])


def _period_for(document: Document) -> str | None:
    if document.fiscal_year and document.fiscal_quarter:
        return f"Q{document.fiscal_quarter}.{document.fiscal_year}"
    if document.fiscal_year:
        return f"{document.fiscal_year}"
    return None


def _parse_amount(match: re.Match[str]) -> tuple[str, str, float | None]:
    number = match.group("number").strip()
    unit = match.group("unit").lower()
    try:
        parsed = float(number.replace(" ", "").replace(".", "").replace(",", "."))
    except ValueError:
        return number, unit, None
    if "tỷ" in unit:
        return number, unit, parsed * 1_000_000_000
    if "triệu" in unit:
        return number, unit, parsed * 1_000_000
    if "nghìn" in unit:
        return number, unit, parsed * 1_000
    return number, unit, parsed


def _fact_type_for(line: str) -> str:
    folded = _ascii_fold(line)
    if "von chu so huu" in folded or "vốn chủ sở hữu" in line.casefold():
        return "EQUITY"
    if "cong ty con" in folded or "ty le so huu" in folded or "tỷ lệ sở hữu" in line.casefold():
        return "OWNERSHIP"
    if "ben lien quan" in folded or "bên liên quan" in line.casefold():
        return "RELATED_PARTY"
    if "vay" in folded or "phai thu" in folded or "phai tra" in folded:
        return "FINANCIAL_FLOW"
    if "doanh thu" in folded or "loi nhuan" in folded or "lợi nhuận" in line.casefold():
        return "PERFORMANCE"
    return "OTHER"


def _canonical_subject(source: Source) -> str:
    name = source.name or source.id
    if name.upper().startswith("BCTC_"):
        return name[5:].upper()
    return name.upper()


def _usage_total(usage: Any) -> int:
    if usage is None:
        return 0
    return int(getattr(usage, "input_tokens", 0) or 0) + int(getattr(usage, "output_tokens", 0) or 0)


def _line_span(markdown: str, start_line: int, end_line: int) -> str:
    lines = markdown.splitlines()
    start = max(1, start_line)
    end = min(len(lines), max(start, end_line))
    return "\n".join(lines[start - 1 : end])


def _reference_node(ref: ExtractionReference, nodes_by_id: dict[str, ParsedNode]) -> ParsedNode:
    node = nodes_by_id.get(ref.node_id)
    if node is None:
        raise ValueError(f"reference node_id không tồn tại: {ref.node_id}")
    if ref.end_line < ref.start_line:
        raise ValueError(f"reference line span đảo chiều: {ref.node_id}:{ref.start_line}-{ref.end_line}")
    if ref.start_line < node.start_line or ref.end_line > node.end_line:
        raise ValueError(
            f"reference line span vượt node: {ref.node_id}:{ref.start_line}-{ref.end_line} "
            f"not in {node.start_line}-{node.end_line}"
        )
    return node


def _quote_hash_for(markdown: str, ref: ExtractionReference) -> str:
    span = _line_span(markdown, ref.start_line, ref.end_line).strip()
    if ref.quote:
        quote = ref.quote.strip()
        if quote and quote not in span:
            raise ValueError(f"quote không nằm trong line span: {ref.node_id}:{ref.start_line}-{ref.end_line}")
    return sha256_text(span)


def _numbered_markdown(markdown: str) -> str:
    return "\n".join(f"{index:05d}: {line}" for index, line in enumerate(markdown.splitlines(), start=1))


def _node_manifest(nodes: list[ParsedNode]) -> list[dict[str, Any]]:
    return [
        {
            "node_id": node.node_id,
            "heading_path": node.heading_path,
            "node_kind": node.node_kind,
            "start_line": node.start_line,
            "end_line": node.end_line,
        }
        for node in nodes
    ]


def _extraction_prompt(source: Source, document: Document, markdown: str, nodes: list[ParsedNode]) -> list[dict[str, str]]:
    doc_role = normalize_doc_role(document.doc_role) or "OTHER"
    role_focus = {
        DocumentRole.ANNUAL_BACKBONE.value: "annual notes: strategy, segment economics, subsidiaries, equity, ownership, debt, related parties, dividends, accounting policies",
        DocumentRole.LATEST_QUARTER.value: "latest quarter notes: newest balances, quarter profit, equity, loans/receivables/payables, updated ownership, related-party balances",
        DocumentRole.GOVERNANCE_REPORT.value: "governance report: owners, board/executives, related-party transactions, governance risks, insider or group relationships",
    }.get(doc_role, "financial and governance evidence")
    schema_hint = {
        "node_annotations": [{"node_id": "string", "summary": "short non-verbatim summary", "relevance": {"moat": 0.0, "gil": 0.0}}],
        "entities": [{"canonical_name": "string", "display_name": "string", "entity_type": "COMPANY|SUBSIDIARY|RELATED_PARTY|PERSON|BANK|TICKER|OTHER"}],
        "facts": [{
            "fact_type": "EQUITY|OWNERSHIP|FINANCIAL_FLOW|RELATED_PARTY|PERFORMANCE|DIVIDEND|MOAT_SIGNAL|OTHER",
            "label": "short fact label",
            "value": "string|null",
            "unit": "VND|%|shares|...|null",
            "period": "string|null",
            "evidence": {"node_id": "node id", "start_line": 1, "end_line": 1, "quote": "short exact quote|null"},
            "metadata": {},
        }],
        "relations": [{
            "subject": "capital/lender/owner side",
            "object": "investee/borrower/debtor side",
            "relation_type": "OWNS|LENDS_TO|BORROWS_FROM|RECEIVABLE_FROM|PAYABLE_TO|GUARANTEES_FOR|TRANSACTS_WITH|OTHER",
            "period": "string|null",
            "amount_vnd": None,
            "ownership_pct": None,
            "verified": True,
            "evidence": {"node_id": "node id", "start_line": 1, "end_line": 1, "quote": "short exact quote|null"},
            "metadata": {},
        }],
        "moat_signals": [{
            "pillar": "INTANGIBLES|SWITCHING_COSTS|NETWORK_EFFECTS|COST_ADVANTAGE|EFFICIENT_SCALE|OTHER",
            "signal": "short signal",
            "direction": "support|counter",
            "confidence": 0.5,
            "evidence": {"node_id": "node id", "start_line": 1, "end_line": 1, "quote": "short exact quote|null"},
            "metadata": {},
        }],
    }
    system = (
        "You are SAG v2 financial extraction. Return ONLY valid JSON. "
        "Do not copy full document content. Do not create one event per heading. "
        "Extract compact manifest items with exact provenance. Use OTHER when taxonomy is unclear. "
        "Missing information is not negative evidence."
    )
    user = (
        f"Document metadata:\n"
        f"- source_id: {source.id}\n"
        f"- document_id: {document.id}\n"
        f"- ticker/source: {_canonical_subject(source)}\n"
        f"- doc_role: {doc_role}\n"
        f"- period: {_period_for(document) or 'unknown'}\n"
        f"- focus: {role_focus}\n\n"
        "Allowed nodes:\n"
        f"{json.dumps(_node_manifest(nodes), ensure_ascii=False)}\n\n"
        "Required JSON shape example (keys are mandatory, arrays may be empty):\n"
        f"{json.dumps(schema_hint, ensure_ascii=False)}\n\n"
        "Rules:\n"
        "- Every fact, relation and moat_signal must reference an existing node_id and line span inside that node.\n"
        "- Line numbers below are authoritative; use them in evidence references.\n"
        "- Normalize capital direction: owner->investee and lender/creditor->borrower/debtor.\n"
        "- TRANSACTS_WITH and GUARANTEES_FOR are not capital-flow relations unless the text states a loan/receivable/payable/investment.\n"
        "- Do not infer ticker/company/subsidiary identity only because names share a ticker substring.\n"
        "- Keep labels short. Do not copy long tables or long passages.\n\n"
        "Line-numbered Markdown:\n"
        f"{_numbered_markdown(markdown)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def rebuild_document_v2(
    session: AsyncSession,
    source: Source,
    document: Document,
    markdown: str,
    *,
    ocr_only: bool = False,
) -> None:
    document.structure_status = ProcessingStageStatus.RUNNING.value
    document.extraction_status = ProcessingStageStatus.RUNNING.value
    document.embedding_status = ProcessingStageStatus.RUNNING.value
    await session.flush()

    await session.execute(delete(DocumentRelation).where(DocumentRelation.document_id == document.id))
    await session.execute(delete(DocumentFact).where(DocumentFact.document_id == document.id))
    await session.execute(delete(DocumentEntity).where(DocumentEntity.document_id == document.id))
    await session.execute(delete(DocumentEvidenceChunk).where(DocumentEvidenceChunk.document_id == document.id))
    await session.execute(delete(DocumentTreeNode).where(DocumentTreeNode.document_id == document.id))

    nodes, coverage = parse_markdown_tree(
        markdown,
        document_id=document.id,
        source_id=source.id,
        metadata={
            "ticker": _canonical_subject(source),
            "doc_role": document.doc_role,
            "fiscal_year": document.fiscal_year,
            "fiscal_quarter": document.fiscal_quarter,
            "title": document.filename,
        },
    )
    for node in nodes:
        session.add(
            DocumentTreeNode(
                id=new_id(),
                document_id=document.id,
                source_id=source.id,
                node_id=node.node_id,
                parent_id=node.parent_id,
                level=node.level,
                order_index=node.order,
                heading=node.heading,
                heading_path=node.heading_path,
                node_kind=node.node_kind,
                start_line=node.start_line,
                end_line=node.end_line,
                content_hash=node.content_hash,
                metadata_json=node.metadata,
            )
        )

    extraction = await _build_extraction_manifest(session, source, document, markdown, nodes)
    evidence = await _build_evidence_chunks(session, source, document, markdown, nodes)
    document.processing_version = 2
    document.structure_status = ProcessingStageStatus.COMPLETE.value
    document.extraction_status = extraction.status
    document.embedding_status = evidence.status
    document.fact_count = extraction.fact_count
    document.token_usage = int(document.token_usage or 0) + extraction.token_usage
    document.coverage = {
        **coverage,
        "extraction": extraction.metadata or {},
        "embedding": evidence.metadata or {},
        "evidence_chunk_count": evidence.chunk_count,
    }


async def _build_minimal_manifest(
    session: AsyncSession,
    source: Source,
    document: Document,
    markdown: str,
    nodes: list[ParsedNode],
) -> int:
    lines = markdown.splitlines()
    period = _period_for(document)
    subject = _canonical_subject(source)
    entity_names: set[str] = {subject}
    facts = 0

    node_for_line: list[ParsedNode] = sorted(nodes, key=lambda n: (n.start_line, -n.level))
    for line_no, line in enumerate(lines, start=1):
        if not line.strip() or line.strip().startswith("|---"):
            continue
        amount_matches = list(_MONEY_RE.finditer(line))
        pct_matches = list(_PCT_RE.finditer(line))
        if not amount_matches and not pct_matches:
            continue
        node = next(
            (candidate for candidate in reversed(node_for_line) if candidate.start_line <= line_no <= candidate.end_line),
            nodes[0],
        )
        fact_type = _fact_type_for(line)
        quote_hash = sha256_text(line.strip())
        for match in amount_matches[:6]:
            number, unit, parsed_vnd = _parse_amount(match)
            session.add(
                DocumentFact(
                    id=new_id(),
                    document_id=document.id,
                    source_id=source.id,
                    node_id=node.node_id,
                    fact_type=fact_type,
                    label=line.strip()[:512],
                    value=number,
                    unit=unit,
                    period=period,
                    start_line=line_no,
                    end_line=line_no,
                    quote_hash=quote_hash,
                    metadata_json={"amount_vnd": parsed_vnd, "extraction": "deterministic_v2"},
                )
            )
            facts += 1
        for match in pct_matches[:4]:
            session.add(
                DocumentFact(
                    id=new_id(),
                    document_id=document.id,
                    source_id=source.id,
                    node_id=node.node_id,
                    fact_type=fact_type,
                    label=line.strip()[:512],
                    value=match.group("number"),
                    unit="%",
                    period=period,
                    start_line=line_no,
                    end_line=line_no,
                    quote_hash=quote_hash,
                    metadata_json={"extraction": "deterministic_v2"},
                )
            )
            facts += 1
        await _maybe_add_relation(session, source, document, node, line, line_no, quote_hash)

    for entity in sorted(entity_names):
        session.add(
            DocumentEntity(
                id=new_id(),
                document_id=document.id,
                source_id=source.id,
                canonical_name=entity.upper(),
                display_name=entity,
                entity_type="TICKER" if entity == subject else "COMPANY",
                metadata_json={"extraction": "deterministic_v2"},
            )
        )
    return facts


async def _build_extraction_manifest(
    session: AsyncSession,
    source: Source,
    document: Document,
    markdown: str,
    nodes: list[ParsedNode],
) -> ManifestBuildResult:
    from sag_api.generation.llm import LLMClient

    llm = LLMClient(settings)
    if not llm.extraction_configured:
        fact_count = await _build_minimal_manifest(session, source, document, markdown, nodes)
        return ManifestBuildResult(
            fact_count=fact_count,
            status=ProcessingStageStatus.COMPLETE.value,
            metadata={"mode": "deterministic_fallback", "fallback_reason": "llm_not_configured"},
        )

    messages = _extraction_prompt(source, document, markdown, nodes)
    total_usage = 0
    last_error = ""
    for attempt in range(2):
        try:
            result = await llm.complete_extraction_json(messages)
            total_usage += _usage_total(result.usage)
            if result.finish_reason and result.finish_reason not in {"stop", "tool_calls"}:
                raise ValueError(f"finish_reason={result.finish_reason}")
            payload = json.loads(result.content)
            manifest = ExtractionManifest.model_validate(payload)
            fact_count = await _persist_manifest(session, source, document, markdown, nodes, manifest)
            return ManifestBuildResult(
                fact_count=fact_count,
                status=ProcessingStageStatus.COMPLETE.value,
                token_usage=total_usage,
                metadata={"mode": "llm_manifest", "attempts": attempt + 1, "finish_reason": result.finish_reason},
            )
        except (json.JSONDecodeError, PydanticValidationError, ValueError) as exc:
            last_error = str(exc)
            messages = [
                *messages,
                {
                    "role": "assistant",
                    "content": "The previous response was rejected by server-side validation.",
                },
                {
                    "role": "user",
                    "content": (
                        "Retry once. Return ONLY complete valid JSON matching the schema. "
                        f"Validation error: {last_error[:1200]}"
                    ),
                },
            ]

    return ManifestBuildResult(
        fact_count=0,
        status=ProcessingStageStatus.EXTRACTION_INCOMPLETE.value,
        token_usage=total_usage,
        metadata={"mode": "llm_manifest", "attempts": 2, "error": last_error[:2000]},
    )


async def _persist_manifest(
    session: AsyncSession,
    source: Source,
    document: Document,
    markdown: str,
    nodes: list[ParsedNode],
    manifest: ExtractionManifest,
) -> int:
    nodes_by_id = {node.node_id: node for node in nodes}
    period = _period_for(document)
    entity_keys: set[str] = {_canonical_subject(source)}
    fact_count = 0

    node_rows = (
        await session.execute(select(DocumentTreeNode).where(DocumentTreeNode.document_id == document.id))
    ).scalars().all()
    node_row_by_id = {row.node_id: row for row in node_rows}
    for annotation in manifest.node_annotations:
        if annotation.node_id not in nodes_by_id:
            raise ValueError(f"annotation node_id không tồn tại: {annotation.node_id}")
        row = node_row_by_id.get(annotation.node_id)
        if row is not None:
            row.summary = annotation.summary
            row.relevance_json = annotation.relevance

    for entity in manifest.entities:
        canonical = entity.canonical_name.strip().upper()
        entity_keys.add(canonical)
        session.add(
            DocumentEntity(
                id=new_id(),
                document_id=document.id,
                source_id=source.id,
                canonical_name=canonical,
                display_name=(entity.display_name or entity.canonical_name).strip(),
                entity_type=entity.entity_type,
                metadata_json={**entity.metadata, "extraction": "llm_manifest_v2"},
            )
        )

    for fact in manifest.facts:
        _reference_node(fact.evidence, nodes_by_id)
        quote_hash = _quote_hash_for(markdown, fact.evidence)
        session.add(
            DocumentFact(
                id=new_id(),
                document_id=document.id,
                source_id=source.id,
                node_id=fact.evidence.node_id,
                fact_type=fact.fact_type,
                label=fact.label.strip()[:512],
                value=fact.value,
                unit=fact.unit,
                period=fact.period or period,
                start_line=fact.evidence.start_line,
                end_line=fact.evidence.end_line,
                quote_hash=quote_hash,
                metadata_json={**fact.metadata, "extraction": "llm_manifest_v2"},
            )
        )
        fact_count += 1

    for signal in manifest.moat_signals:
        _reference_node(signal.evidence, nodes_by_id)
        quote_hash = _quote_hash_for(markdown, signal.evidence)
        session.add(
            DocumentFact(
                id=new_id(),
                document_id=document.id,
                source_id=source.id,
                node_id=signal.evidence.node_id,
                fact_type="MOAT_SIGNAL",
                label=signal.signal.strip()[:512],
                value=signal.direction.strip().lower() or None,
                unit=signal.pillar.strip().upper() or "OTHER",
                period=period,
                start_line=signal.evidence.start_line,
                end_line=signal.evidence.end_line,
                quote_hash=quote_hash,
                metadata_json={
                    **signal.metadata,
                    "pillar": signal.pillar.strip().upper() or "OTHER",
                    "confidence": signal.confidence,
                    "extraction": "llm_manifest_v2",
                },
            )
        )
        fact_count += 1

    for relation in manifest.relations:
        _reference_node(relation.evidence, nodes_by_id)
        quote_hash = _quote_hash_for(markdown, relation.evidence)
        subject = relation.subject.strip()
        obj = relation.object.strip()
        entity_keys.add(subject.upper())
        entity_keys.add(obj.upper())
        session.add(
            DocumentRelation(
                id=new_id(),
                document_id=document.id,
                source_id=source.id,
                node_id=relation.evidence.node_id,
                subject=subject,
                object=obj,
                relation_type=relation.relation_type,
                amount_vnd=relation.amount_vnd,
                ownership_pct=relation.ownership_pct,
                verified=relation.verified,
                period=relation.period or period,
                start_line=relation.evidence.start_line,
                end_line=relation.evidence.end_line,
                quote_hash=quote_hash,
                metadata_json={**relation.metadata, "extraction": "llm_manifest_v2"},
            )
        )

    existing_entities = {
        entity.canonical_name
        for entity in (
            await session.execute(select(DocumentEntity).where(DocumentEntity.document_id == document.id))
        ).scalars().all()
    }
    for canonical in sorted(entity_keys - existing_entities):
        session.add(
            DocumentEntity(
                id=new_id(),
                document_id=document.id,
                source_id=source.id,
                canonical_name=canonical,
                display_name=canonical,
                entity_type="TICKER" if canonical == _canonical_subject(source) else "COMPANY",
                metadata_json={"extraction": "llm_manifest_v2_auto_entity"},
            )
        )
    return fact_count


def _split_text_line_ranges(lines: list[str], start_line: int, *, max_chars: int = 6000) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    current_start: int | None = None
    current_end: int | None = None
    current_chars = 0
    for offset, line in enumerate(lines):
        absolute = start_line + offset
        if not line.strip():
            if current_start is not None and current_end is not None:
                ranges.append((current_start, current_end))
                current_start = current_end = None
                current_chars = 0
            continue
        line_len = len(line) + 1
        if current_start is not None and current_chars + line_len > max_chars:
            ranges.append((current_start, current_end or absolute))
            current_start = current_end = None
            current_chars = 0
        if current_start is None:
            current_start = absolute
        current_end = absolute
        current_chars += line_len
    if current_start is not None and current_end is not None:
        ranges.append((current_start, current_end))
    return ranges


def _node_line_ranges(markdown: str, node: ParsedNode) -> list[dict[str, Any]]:
    content = _line_span(markdown, node.start_line, node.end_line)
    lines = content.splitlines()
    chunks: list[dict[str, Any]] = []
    idx = 0
    text_buffer: list[str] = []
    text_start = node.start_line

    def flush_text(until_offset: int) -> None:
        nonlocal text_buffer, text_start
        if not text_buffer:
            text_start = node.start_line + until_offset
            return
        for start, end in _split_text_line_ranges(text_buffer, text_start):
            chunks.append({"kind": "evidence", "start_line": start, "end_line": end})
        text_buffer = []
        text_start = node.start_line + until_offset

    while idx < len(lines):
        line = lines[idx]
        if not line.lstrip().startswith("|"):
            if not text_buffer:
                text_start = node.start_line + idx
            text_buffer.append(line)
            idx += 1
            continue
        flush_text(idx)
        table_start_idx = idx
        table_lines: list[str] = []
        while idx < len(lines) and lines[idx].lstrip().startswith("|"):
            table_lines.append(lines[idx])
            idx += 1
        header = table_lines[:2] if len(table_lines) >= 2 and set(table_lines[1].replace("|", "").strip()) <= {"-", ":", " "} else table_lines[:1]
        data_start = len(header)
        if not table_lines:
            continue
        if data_start >= len(table_lines):
            chunks.append({
                "kind": "table",
                "start_line": node.start_line + table_start_idx,
                "end_line": node.start_line + table_start_idx + len(table_lines) - 1,
                "table_header_hash": sha256_text("\n".join(header)),
            })
        else:
            for row_idx in range(data_start, len(table_lines)):
                chunks.append({
                    "kind": "table_row",
                    "start_line": node.start_line + table_start_idx + row_idx,
                    "end_line": node.start_line + table_start_idx + row_idx,
                    "table_header_hash": sha256_text("\n".join(header)),
                })
        text_start = node.start_line + idx

    flush_text(len(lines))
    return chunks


async def _embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    if not settings.effective_embedding_api_key:
        raise RuntimeError("embedding_api_key_not_configured")
    from litellm import aembedding

    request: dict[str, Any] = {
        "model": settings.routed_embedding_model,
        "api_key": settings.effective_embedding_api_key,
        "input": texts,
        "timeout": settings.llm_timeout_ms / 1000,
    }
    if settings.effective_embedding_base_url:
        request["api_base"] = settings.effective_embedding_base_url
    if settings.embedding_dimensions:
        request["dimensions"] = settings.embedding_dimensions
        request["allowed_openai_params"] = ["dimensions"]
    response = await aembedding(**request)
    data = response.get("data") if isinstance(response, dict) else getattr(response, "data", None)
    if not data or len(data) != len(texts):
        raise RuntimeError("embedding_response_size_mismatch")
    vectors: list[list[float]] = []
    for item in data:
        embedding = item.get("embedding") if isinstance(item, dict) else getattr(item, "embedding", None)
        if not isinstance(embedding, list) or not embedding:
            raise RuntimeError("embedding_vector_missing")
        vectors.append([float(value) for value in embedding])
    return vectors


async def _build_evidence_chunks(
    session: AsyncSession,
    source: Source,
    document: Document,
    markdown: str,
    nodes: list[ParsedNode],
) -> EvidenceBuildResult:
    await session.flush()
    period = _period_for(document)
    ticker = _canonical_subject(source)
    fact_rows = (
        await session.execute(select(DocumentFact).where(DocumentFact.document_id == document.id))
    ).scalars().all()
    labels_by_node: dict[str, list[str]] = {}
    for fact in fact_rows:
        labels_by_node.setdefault(fact.node_id, []).append(fact.label)

    pending: list[tuple[DocumentEvidenceChunk, str]] = []
    for node in nodes:
        labels = labels_by_node.get(node.node_id, [])[:12]
        node_embedding_text = "\n".join(
            part for part in [node.heading_path, " | ".join(labels)] if part
        )
        pending.append(
            (
                DocumentEvidenceChunk(
                    id=new_id(),
                    document_id=document.id,
                    source_id=source.id,
                    node_id=node.node_id,
                    chunk_id="ec_" + sha256_text(f"{document.id}:{node.node_id}:node")[:24],
                    chunk_kind="node",
                    ticker=ticker,
                    doc_role=document.doc_role,
                    period=period,
                    start_line=node.start_line,
                    end_line=node.end_line,
                    content_hash=node.content_hash,
                    embedding_text_hash=sha256_text(node_embedding_text),
                    embedding_model=settings.embedding_model,
                    embedding_dimensions=settings.embedding_dimensions,
                    active_version=bool(document.is_active),
                    processing_version=2,
                    metadata_json={
                        "retrieval_unit": "node",
                        "heading_path": node.heading_path,
                        "fact_labels": labels,
                    },
                ),
                node_embedding_text,
            )
        )
        if node.level == 0 and len(nodes) > 1:
            continue
        for index, chunk in enumerate(_node_line_ranges(markdown, node)):
            span = _line_span(markdown, int(chunk["start_line"]), int(chunk["end_line"]))
            embedding_text = f"{node.heading_path}\n{span}"
            metadata = {
                "retrieval_unit": "evidence",
                "heading_path": node.heading_path,
            }
            if "table_header_hash" in chunk:
                metadata["table_header_hash"] = chunk["table_header_hash"]
                metadata["table_policy"] = "repeat_header_at_embedding_time"
            pending.append(
                (
                    DocumentEvidenceChunk(
                        id=new_id(),
                        document_id=document.id,
                        source_id=source.id,
                        node_id=node.node_id,
                        chunk_id="ec_" + sha256_text(f"{document.id}:{node.node_id}:{index}:{chunk['start_line']}")[:24],
                        chunk_kind=str(chunk["kind"]),
                        ticker=ticker,
                        doc_role=document.doc_role,
                        period=period,
                        start_line=int(chunk["start_line"]),
                        end_line=int(chunk["end_line"]),
                        content_hash=sha256_text(span),
                        embedding_text_hash=sha256_text(embedding_text),
                        embedding_model=settings.embedding_model,
                        embedding_dimensions=settings.embedding_dimensions,
                        active_version=bool(document.is_active),
                        processing_version=2,
                        metadata_json=metadata,
                    ),
                    embedding_text,
                )
            )
    if not settings.effective_embedding_api_key:
        for row, _text in pending:
            row.metadata_json = {**(row.metadata_json or {}), "embedding": "not_configured"}
            session.add(row)
        return EvidenceBuildResult(
            chunk_count=len(pending),
            status=ProcessingStageStatus.FAILED.value,
            metadata={"mode": "metadata_only", "error": "embedding_api_key_not_configured"},
        )
    try:
        embedded = 0
        for start in range(0, len(pending), 64):
            batch = pending[start : start + 64]
            vectors = await _embed_texts([text for _row, text in batch])
            for (row, _text), vector in zip(batch, vectors, strict=True):
                row.embedding_json = vector
                row.embedding_dimensions = len(vector)
                row.metadata_json = {**(row.metadata_json or {}), "embedding": "complete"}
                session.add(row)
                embedded += 1
        return EvidenceBuildResult(
            chunk_count=len(pending),
            status=ProcessingStageStatus.COMPLETE.value,
            metadata={"mode": "litellm_embedding", "embedded_count": embedded, "model": settings.embedding_model},
        )
    except Exception as exc:  # noqa: BLE001
        for row, _text in pending:
            row.metadata_json = {**(row.metadata_json or {}), "embedding": "failed"}
            session.add(row)
        return EvidenceBuildResult(
            chunk_count=len(pending),
            status=ProcessingStageStatus.FAILED.value,
            metadata={"mode": "litellm_embedding", "error": str(exc)[:2000], "model": settings.embedding_model},
        )


async def _maybe_add_relation(
    session: AsyncSession,
    source: Source,
    document: Document,
    node: ParsedNode,
    line: str,
    line_no: int,
    quote_hash: str,
) -> None:
    folded = _ascii_fold(line)
    relation_type: str | None = None
    if "ty le so huu" in folded or "tỷ lệ sở hữu" in line.casefold():
        relation_type = "OWNS"
    elif "phai thu" in folded:
        relation_type = "RECEIVABLE_FROM"
    elif "phai tra" in folded:
        relation_type = "PAYABLE_TO"
    elif "cho vay" in folded:
        relation_type = "LOANS_TO"
    elif "vay" in folded:
        relation_type = "BORROWS_FROM"
    elif "bao lanh" in folded or "bảo lãnh" in line.casefold():
        relation_type = "GUARANTEES_FOR"
    elif "ben lien quan" in folded or "bên liên quan" in line.casefold():
        relation_type = "TRANSACTS_WITH"
    if relation_type is None:
        return

    amount = None
    first_amount = next(iter(_MONEY_RE.finditer(line)), None)
    if first_amount is not None:
        _, _, amount = _parse_amount(first_amount)
    pct = None
    first_pct = _PCT_RE.search(line)
    if first_pct is not None:
        try:
            pct = float(first_pct.group("number").replace(",", "."))
        except ValueError:
            pct = None
    session.add(
        DocumentRelation(
            id=new_id(),
            document_id=document.id,
            source_id=source.id,
            node_id=node.node_id,
            subject=_canonical_subject(source),
            object=node.heading[:512] or "UNKNOWN",
            relation_type=relation_type,
            amount_vnd=amount,
            ownership_pct=pct,
            verified=True,
            period=_period_for(document),
            start_line=line_no,
            end_line=line_no,
            quote_hash=quote_hash,
            metadata_json={"line": line.strip()[:1000], "extraction": "deterministic_v2"},
        )
    )


async def list_active_documents_by_ticker(session: AsyncSession, ticker: str) -> tuple[Source, list[Document]]:
    from sag_api.services.source_service import get_or_create_source_by_ticker

    source = await get_or_create_source_by_ticker(session, ticker)
    rows = await session.execute(
        select(Document).where(
            Document.source_id == source.id,
            Document.is_active.is_(True),
            Document.doc_role.in_(ACTIVE_DOCUMENT_ROLES),
        )
    )
    return source, list(rows.scalars().all())
