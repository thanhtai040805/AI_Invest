"""Rebuildable E0/E1 passages, anchored candidates and bounded sparse graph."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import unicodedata
import uuid
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.db.models import (
    CanonicalBlock,
    Document,
    DocumentVersion,
    IngestionRun,
    KnowledgeEvidence,
    KnowledgeGraphBuild,
    KnowledgeJob,
    KnowledgeQueueControl,
    KnowledgeUnit,
    KnowledgeUnitEdge,
    Source,
    SourceProjectMapping,
    SourceSnapshot,
)


class KnowledgeConfig(BaseModel):
    # extra="allow" preserves exact input_checksum digests when validating legacy v1 persisted rows
    # while keeping v2 completely free of deprecated E2 fields.
    model_config = ConfigDict(frozen=True, extra="allow")

    version: str = "knowledge-v2"
    max_evidence_per_unit: int = Field(default=64, ge=1, le=256)
    max_terms: int = Field(default=64, ge=1, le=256)
    candidate_limit: int = Field(default=96, ge=1, le=256)
    posting_limit: int = Field(default=24, ge=1, le=64)
    max_degree: int = Field(default=32, ge=1, le=64)
    edge_min_score: float = Field(default=0.55, ge=0, le=1)
    # Project-local dictionary: alias -> canonical label. No global entity linking.
    aliases: tuple[tuple[str, str], ...] = ()


SIGNAL_WEIGHTS = {"semantic": 0.4, "lexical": 0.2, "entity": 0.15, "structure": 0.1, "temporal": 0.1, "citation": 0.05}

# Universal, script-independent structural & Unicode patterns (zero language-specific wordlists)
_CONTINUOUS_SCRIPT_RUN_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u0e00-\u0eff\u1780-\u17ff\u1000-\u109f]+"
)
_WORD_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_URI_RE = re.compile(r"(?:https?|[a-z][a-z0-9+.-]*):\/\/[^\s<>\]\)]*[^\s<>\]\).,;:!?]")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_UUID_IPV4_RE = re.compile(r"\b(?:[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}|(?:\d{1,3}\.){3}\d{1,3})\b")
_DATE_OR_DOC_CODE_RE = re.compile(
    r"\b(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|\d{1,6}(?:/[\w-]+){1,4})\b",
    re.UNICODE,
)
_API_PATH_RE = re.compile(r"(?<![\w$:/])/(?:[\w.{}-]+/)+[\w.{}-]*", re.UNICODE)
_NUMERIC_CODE_RE = re.compile(
    r"\b(?:[A-Z]{2,12}-\d[\w-]*\b|v\d+(?:\.\d+){1,3}\b|\d+(?:\.\d+)?%(?!\w)|\d+\.\d+(?:\.\d+)*\b|(?:19|20)\d{2}\b)"
)
_BACKTICK_CODE_RE = re.compile(r"`([^`\n]{2,80})`")
_CJK_BRACKET_RE = re.compile(r"[《「『【]([^》」』】\n]{2,80})[》」』】]")
_KATAKANA_RE = re.compile(r"[\u30a0-\u30ffー]{2,40}")
_LINE_RE = re.compile(r"[^\r\n]+")
_LINE_WORD_RE = re.compile(r"[^\W_]+(?:[-'’][^\W_]+)*", re.UNICODE)


def checksum(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def stable_id(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "sag:knowledge:" + checksum(parts)))


async def insert_once(session, model, **values):
    insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
    await session.execute(insert(model).values(**values).on_conflict_do_nothing())


async def knowledge_lock(session, key: str):
    await insert_once(session, KnowledgeQueueControl, id=key, tokens_reserved=0)
    await session.execute(
        update(KnowledgeQueueControl)
        .where(KnowledgeQueueControl.id == key)
        .values(tokens_reserved=KnowledgeQueueControl.tokens_reserved)
    )


def _is_continuous_script_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x3040 <= cp <= 0x30FF
        or 0x3400 <= cp <= 0x4DBF
        or 0x4E00 <= cp <= 0x9FFF
        or 0xF900 <= cp <= 0xFAFF
        or 0x0E00 <= cp <= 0x0EFF
        or 0x1780 <= cp <= 0x17FF
        or 0x1000 <= cp <= 0x109F
    )


def terms(text: str) -> Counter:
    """Language-agnostic lexical counter: character bigrams for continuous scripts + Unicode words."""
    folded = text.casefold()
    counts: Counter[str] = Counter()
    for c_match in _CONTINUOUS_SCRIPT_RUN_RE.finditer(folded):
        run = c_match.group(0)
        if len(run) == 1:
            counts[run] += 1
        else:
            for i in range(len(run) - 1):
                counts[run[i : i + 2]] += 1
    non_cont = _CONTINUOUS_SCRIPT_RUN_RE.sub(" ", folded)
    for word in _WORD_TOKEN_RE.findall(non_cont):
        if len(word) >= 2 or word.isdigit():
            counts[word] += 1
    return counts


def _fact(
    kind: str, text: str, start: int, end: int, block_id: str, anchor: str, confidence: float, **extra: object
) -> dict:
    return {
        "kind": kind,
        "confidence": confidence,
        "quote": text[start:end],
        "start": start,
        "end": end,
        "evidence_block_ids": [block_id],
        "source_anchor": anchor,
        "candidate": True,
        **extra,
    }


def _overlaps_any(span: tuple[int, int], protected: list[tuple[int, int]]) -> bool:
    s, e = span
    return any(ps < e and s < pe for ps, pe in protected)


def _is_upper_word(token: str) -> bool:
    return bool(token) and unicodedata.category(token[0]) in {"Lu", "Lt"}


def _is_all_caps_code(token: str) -> bool:
    if len(token) < 2:
        return False
    has_lu = False
    alnum_caps = 0
    for ch in token:
        cat = unicodedata.category(ch)
        if cat == "Ll":
            return False
        if cat == "Lu":
            has_lu = True
            alnum_caps += 1
        elif cat == "Nd":
            alnum_caps += 1
    return has_lu and alnum_caps >= 2


def _is_lower_word(token: str) -> bool:
    if not token or unicodedata.category(token[0]) != "Ll":
        return False
    return not any(unicodedata.category(ch) in {"Lu", "Lt"} for ch in token[1:])


def _is_short_num(token: str) -> bool:
    return token.isdigit() and len(token) <= 4


def _label_boundary_pattern(label: str) -> re.Pattern[str]:
    left = "" if _is_continuous_script_char(label[0]) or not (label[0].isalnum() or label[0] == "_") else r"(?<!\w)"
    right = "" if _is_continuous_script_char(label[-1]) or not (label[-1].isalnum() or label[-1] == "_") else r"(?!\w)"
    return re.compile(left + re.escape(label) + right, re.IGNORECASE | re.UNICODE)


def _extract_universal_anchors(text: str, section_path: str) -> list[tuple[str, str, int, int, float]]:
    """Extract universal structural & Unicode-script anchors with zero language-specific wordlists.

    Returns tuples of ``(tier, extractor_version, start, end, confidence)`` where
    ``label == text[start:end]``.
    """
    anchors: list[tuple[str, str, int, int, float]] = []
    seen_spans: set[tuple[int, int]] = set()
    protected: list[tuple[int, int]] = []

    def _emit(tier: str, ver: str, start: int, end: int, conf: float, *, protect_span: tuple[int, int] | None = None) -> None:
        if start < 0 or end <= start or end > len(text):
            return
        span = (start, end)
        if span not in seen_spans:
            seen_spans.add(span)
            anchors.append((tier, ver, start, end, conf))
        if protect_span is not None:
            protected.append(protect_span)

    # Pass 1: Script-independent structural identifiers & CJK/inline-code delimiters
    for m in _URI_RE.finditer(text):
        _emit("E0", "e0-structural-v2", *m.span(), 0.96, protect_span=m.span())
    for m in _EMAIL_RE.finditer(text):
        if not _overlaps_any(m.span(), protected):
            _emit("E0", "e0-structural-v2", *m.span(), 0.96, protect_span=m.span())
    for m in _UUID_IPV4_RE.finditer(text):
        if not _overlaps_any(m.span(), protected):
            _emit("E0", "e0-structural-v2", *m.span(), 0.95, protect_span=m.span())
    for m in _BACKTICK_CODE_RE.finditer(text):
        if not _overlaps_any(m.span(), protected):
            _emit("E1", "e1-structural-v2", *m.span(1), 0.80, protect_span=m.span())
    for m in _CJK_BRACKET_RE.finditer(text):
        if not _overlaps_any(m.span(), protected):
            _emit("E0", "e0-unicode-v2", *m.span(1), 0.92, protect_span=m.span())
    for m in _DATE_OR_DOC_CODE_RE.finditer(text):
        if _overlaps_any(m.span(), protected):
            continue
        raw = m.group(0)
        # Slash codes require either >= 2 slashes (e.g. 15/2026/NĐ-CP, 18/12/2015) or an alphabetic segment
        if "/" in raw and raw.count("/") == 1 and not any(ch.isalpha() for ch in raw):
            continue
        _emit("E0", "e0-structural-v2", *m.span(), 0.95, protect_span=m.span())
    for m in _API_PATH_RE.finditer(text):
        if not _overlaps_any(m.span(), protected):
            _emit("E0", "e0-structural-v2", *m.span(), 0.94, protect_span=m.span())
    for m in _NUMERIC_CODE_RE.finditer(text):
        if not _overlaps_any(m.span(), protected):
            _emit("E0", "e0-structural-v2", *m.span(), 0.92, protect_span=m.span())
    for m in _KATAKANA_RE.finditer(text):
        if not _overlaps_any(m.span(), protected):
            _emit("E0", "e0-unicode-v2", *m.span(), 0.88)

    # Pass 2: Wordlist-free Unicode bicameral (Lu/Lt/Ll/Nd) line-by-line proper-span automaton
    sec_folded = (section_path or "").casefold()
    for line_m in _LINE_RE.finditer(text):
        line_start = line_m.start()
        line = line_m.group(0)
        tokens: list[tuple[int, int, str, bool, bool]] = []
        prev_end = 0
        for idx, wm in enumerate(_LINE_WORD_RE.finditer(line)):
            ls, le = wm.span()
            gs, ge = line_start + ls, line_start + le
            tok = wm.group(0)
            between = line[prev_end:ls]
            at_sent_start = idx == 0 or any(ch in ".!?。！？…:;" for ch in between)
            is_prot = _overlaps_any((gs, ge), protected)
            tokens.append((gs, ge, tok, at_sent_start, is_prot))
            prev_end = le

        n_toks = len(tokens)
        i = 0
        while i < n_toks:
            gs, ge, tok, sent_start, is_prot = tokens[i]
            if is_prot or not _is_upper_word(tok):
                i += 1
                continue

            chain = [i]
            consec_uppers = 1
            upper_count = 1
            has_lower_bridge = False

            while len(chain) < 7:
                cur = chain[-1]
                if cur + 1 >= n_toks:
                    break
                cur_ge = tokens[cur][1]
                nxt_gs, _, nxt_tok, _, nxt_prot = tokens[cur + 1]
                if nxt_prot or text[cur_ge:nxt_gs] not in {" ", "  ", "\t"}:
                    break

                if _is_upper_word(nxt_tok):
                    chain.append(cur + 1)
                    consec_uppers += 1
                    upper_count += 1
                    continue

                if _is_short_num(nxt_tok) and _is_upper_word(tokens[cur][2]):
                    chain.append(cur + 1)
                    # Stop extending past a numbered clause/version designator (e.g. "Điều 5", "Section 6")
                    break

                # Single lowercase syllable/connector bridge between Capitalized words
                # (e.g. "Bảo tàng Hàng hải Penobscot", "Dự án A", "Rio de Janeiro", "Bank of America")
                if (
                    _is_lower_word(nxt_tok)
                    and len(nxt_tok) <= 6
                    and cur + 2 < n_toks
                    and not tokens[cur + 2][4]
                    and text[tokens[cur + 1][1] : tokens[cur + 2][0]] in {" ", "  ", "\t"}
                    and _is_upper_word(tokens[cur + 2][2])
                ):
                    # Do not bridge across conjunctions after >=2 consecutive Upper words ("Đà Nẵng and Tây Ninh")
                    can_bridge = consec_uppers == 1 or (sent_start and len(chain) == 2 and consec_uppers == 2)
                    if can_bridge and sent_start and len(chain) == 1:
                        # At sentence start ("Before the Evidence Gate" vs "Bảo tàng Hàng hải Penobscot"),
                        # only bridge token 0 if a second compound bridge follows OR the phrase is in section_path
                        has_second_bridge = (
                            cur + 4 < n_toks
                            and not tokens[cur + 3][4]
                            and not tokens[cur + 4][4]
                            and text[tokens[cur + 2][1] : tokens[cur + 3][0]] in {" ", "  ", "\t"}
                            and text[tokens[cur + 3][1] : tokens[cur + 4][0]] in {" ", "  ", "\t"}
                            and _is_lower_word(tokens[cur + 3][2])
                            and _is_upper_word(tokens[cur + 4][2])
                        )
                        cand_phrase = text[gs : tokens[cur + 2][1]].casefold()
                        if not (has_second_bridge or (sec_folded and cand_phrase in sec_folded)):
                            can_bridge = False
                    if can_bridge:
                        chain.append(cur + 1)
                        chain.append(cur + 2)
                        consec_uppers = 1
                        upper_count += 1
                        has_lower_bridge = True
                        continue
                break

            first_idx, last_idx = chain[0], chain[-1]
            span_start, span_end = tokens[first_idx][0], tokens[last_idx][1]
            if len(chain) == 1:
                if _is_all_caps_code(tok) or (not sent_start and len(tok) >= 2):
                    _emit("E0", "e0-unicode-v2", span_start, span_end, 0.92)
            else:
                _emit("E0", "e0-unicode-v2", span_start, span_end, 0.95)
                # Sentence-initial determiner/preposition strip without wordlists ("The Knowledge Governance Team" -> "Knowledge Governance Team")
                if sent_start and not _is_all_caps_code(tok) and _is_upper_word(tokens[chain[1]][2]):
                    sub_chain = chain[1:]
                    if len(sub_chain) >= 2 or _is_all_caps_code(tokens[sub_chain[0]][2]):
                        _emit("E0", "e0-unicode-v2", tokens[sub_chain[0]][0], span_end, 0.94)
                # Trailing proper-name tail after lowercase compound connector ("Tòa án Quân sự Nürnberg" -> "Nürnberg")
                if has_lower_bridge:
                    last_lower_pos = max(pos for pos, tidx in enumerate(chain) if _is_lower_word(tokens[tidx][2]))
                    tail_indices = chain[last_lower_pos + 1 :]
                    if tail_indices:
                        t_start, t_end = tokens[tail_indices[0]][0], tokens[tail_indices[-1]][1]
                        if (t_start, t_end) != (span_start, span_end) and (len(tail_indices) >= 2 or (t_end - t_start) >= 3):
                            _emit("E0", "e0-unicode-v2", t_start, t_end, 0.92)

            i = last_idx + 1

    return anchors


def extract_e0_e1(
    block: CanonicalBlock,
    config: KnowledgeConfig = KnowledgeConfig(),
    *,
    dense: Sequence[float] | None = None,
    extra_entities: Sequence[str] = (),
) -> tuple[list[dict], dict]:
    text, anchor = block.normalized_text, (block.source_anchor or "").strip() or f"block-{block.ordinal}"
    facts: list[dict] = []
    for tier, ver, m_start, m_end, conf in _extract_universal_anchors(text, block.section_path or ""):
        facts.append(
            {
                "tier": tier,
                "extractor_version": ver,
                **_fact("entity", text, m_start, m_end, block.id, anchor, conf, label=text[m_start:m_end]),
            }
        )
    for alias, label in config.aliases:
        if not alias.strip() or not label.strip():
            raise ValueError("Alias dictionary requires non-empty labels")
        for match in _label_boundary_pattern(alias.strip()).finditer(text):
            facts.append(
                {
                    "tier": "E0",
                    "extractor_version": "e0-dictionary-v1",
                    **_fact("alias", text, *match.span(), block.id, anchor, 0.9, label=label, alias=match[0]),
                }
            )

    # Structural heading titles from Canonical section_path + optional Dual-Level SAG-Engine entities
    heading_entities: list[str] = []
    if block.section_path:
        for raw_seg in block.section_path.split(">"):
            seg = raw_seg.strip()
            if 2 <= len(seg) <= 80 and seg not in heading_entities:
                heading_entities.append(seg)
    combined_extra = tuple(dict.fromkeys([*extra_entities, *heading_entities]))
    if combined_extra:
        seen_spans = {(f["start"], f["end"]) for f in facts if f["kind"] in {"entity", "alias"}}
        for raw_name in combined_extra:
            label = str(raw_name or "").strip()
            if len(label) < 2 or len(label) > 120:
                continue
            for match in _label_boundary_pattern(label).finditer(text):
                span = match.span()
                if span in seen_spans or any(s <= span[0] and span[1] <= e for s, e in seen_spans):
                    continue
                seen_spans.add(span)
                facts.append(
                    {
                        "tier": "E1",
                        "extractor_version": "e1-sag-engine-bridge-v1",
                        **_fact("entity", text, *span, block.id, anchor, 0.88, label=label),
                    }
                )

    facts = facts[: config.max_evidence_per_unit]
    entity_facts = sorted(
        (f for f in facts if f["kind"] in {"entity", "alias"}),
        key=lambda f: (f["start"], f["end"]),
    )
    labels = sorted({str(f["label"]) for f in entity_facts})

    # Guarantee at least one anchored quote per KnowledgeUnit for provenance/contradiction verification
    # without bloating KnowledgeEvidence with per-sentence claims or synthetic CO_OCCURS pairs.
    if not facts and text:
        end_idx = min(len(text), 240)
        facts.append(
            {
                "tier": "E0",
                "extractor_version": "e0-anchor-v2",
                **_fact("passage", text, 0, end_idx, block.id, anchor, 0.55),
            }
        )

    dense_list = [float(v) for v in dense] if dense else []
    if any(not math.isfinite(v) for v in dense_list):
        raise ValueError("Knowledge dense features must be finite")
    tokens = terms((block.section_path or "") + " " + text)
    sparse = sorted(tokens.items(), key=lambda item: (-item[1], -min(len(item[0]), 12), item[0]))[: config.max_terms]
    confidence = max((f["confidence"] for f in entity_facts), default=0.55)
    importance = min(
        1.0,
        0.25
        + 0.1 * len(labels)
        + (0.25 if block.block_type in {"code", "table"} else 0.0)
        + min(0.30, len(tokens) / 48.0),
    )
    return facts, {
        "dense": dense_list,
        "sparse": sparse,
        "entities": labels,
        "citations": sorted(set(_URI_RE.findall(text))),
        "local_confidence": confidence,
        "importance": importance,
    }


def evidence_payload(fact: dict, provenance: dict, valid_from: datetime, valid_to: datetime) -> dict:
    return {**fact, "provenance": provenance, "valid_from": valid_from.isoformat(), "valid_to": valid_to.isoformat()}


async def unit_checksum(session: AsyncSession, unit: KnowledgeUnit) -> str:
    facts = (
        await session.scalars(
            select(KnowledgeEvidence).where(KnowledgeEvidence.unit_id == unit.id).order_by(KnowledgeEvidence.id)
        )
    ).all()
    return _unit_checksum(unit, facts)


def _unit_checksum(unit, facts) -> str:
    return checksum(
        {
            "id": unit.id,
            "input": unit.input_checksum,
            "content_hash": unit.content_hash,
            "provenance": unit.provenance_json,
            "features": unit.features_json,
            "valid_from": unit.valid_from.isoformat(),
            "valid_to": unit.valid_to.isoformat(),
            "evidence": [[f.id, f.tier, f.kind, f.extractor_version, f.confidence, f.payload_json] for f in facts],
        }
    )


async def unit_checksums(session: AsyncSession, units: Sequence[KnowledgeUnit]) -> dict[str, str]:
    facts: dict[str, list] = defaultdict(list)
    ids = [unit.id for unit in units]
    for offset in range(0, len(ids), 1024):
        rows = (await session.scalars(select(KnowledgeEvidence).where(
            KnowledgeEvidence.unit_id.in_(ids[offset:offset + 1024]),
        ).order_by(KnowledgeEvidence.id))).all()
        for fact in rows:
            facts[fact.unit_id].append(fact)
    result = {}
    for offset in range(0, len(units), 256):
        result.update((unit.id, _unit_checksum(unit, facts[unit.id])) for unit in units[offset:offset + 256])
        await asyncio.sleep(0)
    return result


async def version_input(session: AsyncSession, version_id: str, config: KnowledgeConfig) -> tuple:
    version = await session.get(DocumentVersion, version_id, populate_existing=True)
    document = await session.get(Document, version.document_id, populate_existing=True) if version else None
    if not version or not document or not document.is_active or version.search_status not in {"READY", "SEARCH_READY"}:
        raise ValueError("Knowledge producer requires an active SEARCH_READY version")
    partition = (version.metadata_json or {}).get("security_partition_id")
    if not all(
        isinstance(v, str) and v.strip()
        for v in (document.tenant_id, document.project_id, document.source_id, partition)
    ):
        raise ValueError("Knowledge producer requires explicit tenant/project/source/partition")
    mapping = await session.scalar(
        select(SourceProjectMapping).where(
            SourceProjectMapping.source_id == document.source_id,
            SourceProjectMapping.project_id == document.project_id,
            SourceProjectMapping.state == "CONFIRMED",
        )
    )
    if not mapping:
        raise ValueError("Knowledge producer requires a confirmed Project-to-Source mapping")
    conflicting = await session.scalar(
        select(IngestionRun.id).where(
            IngestionRun.document_version_id == version.id,
            (IngestionRun.tenant_id != document.tenant_id) | (IngestionRun.project_id != document.project_id),
        )
    )
    if conflicting:
        raise ValueError("Knowledge producer scope conflicts with ingestion provenance")
    blocks = (
        await session.scalars(
            select(CanonicalBlock)
            .where(CanonicalBlock.document_version_id == version.id)
            .order_by(CanonicalBlock.ordinal)
        )
    ).all()
    if not blocks:
        raise ValueError("Knowledge producer requires canonical source artifacts")
    if any(hashlib.sha256(b.normalized_text.encode()).hexdigest() != b.content_hash for b in blocks):
        raise ValueError("Canonical artifact checksum mismatch")
    source = await session.scalar(select(SourceSnapshot).where(SourceSnapshot.document_version_id == version.id))
    if source and source.checksum_sha256 != version.file_hash:
        raise ValueError("Source snapshot checksum differs from DocumentVersion")
    provenance = {
        "tenant_id": document.tenant_id,
        "project_id": document.project_id,
        "source_id": document.source_id,
        "document_id": document.id,
        "document_version_id": version.id,
        "version_no": version.version_no,
        "security_partition_id": partition,
        "source_snapshot_id": source.id if source else None,
        "source_checksum": source.checksum_sha256 if source else version.file_hash,
        "published_at": version.source_published_at.isoformat() if version.source_published_at else None,
        "observed_at": version.observed_at.isoformat(),
        "ingested_at": version.ingested_at.isoformat() if version.ingested_at else None,
        "supersedes_id": version.supersedes_id,
    }
    digest = checksum(
        {
            "config": config.model_dump(mode="json"),
            "provenance": provenance,
            "validity": [version.valid_from.isoformat(), version.valid_to.isoformat()],
            "blocks": [
                [b.id, b.ordinal, b.block_type, b.content_hash, b.page_from, b.page_to, b.section_path, b.source_anchor]
                for b in blocks
            ],
        }
    )
    return version, blocks, provenance, digest


async def batch_version_inputs(session: AsyncSession, version_ids: Sequence[str]) -> dict[str, tuple]:
    unique_vids = sorted({str(v) for v in version_ids if v})
    if not unique_vids:
        return {}
    versions_by_id = {
        v.id: v
        for v in (
            await session.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.id.in_(unique_vids))
                .execution_options(populate_existing=True)
            )
        ).all()
    }
    doc_ids = sorted({v.document_id for v in versions_by_id.values() if v.document_id})
    docs_by_id = (
        {
            d.id: d
            for d in (
                await session.scalars(
                    select(Document).where(Document.id.in_(doc_ids)).execution_options(populate_existing=True)
                )
            ).all()
        }
        if doc_ids
        else {}
    )
    src_ids = sorted({d.source_id for d in docs_by_id.values() if d.source_id})
    proj_ids = sorted({d.project_id for d in docs_by_id.values() if d.project_id})
    confirmed_mappings = (
        {
            (m.source_id, m.project_id)
            for m in (
                await session.scalars(
                    select(SourceProjectMapping).where(
                        SourceProjectMapping.source_id.in_(src_ids),
                        SourceProjectMapping.project_id.in_(proj_ids),
                        SourceProjectMapping.state == "CONFIRMED",
                    )
                )
            ).all()
        }
        if src_ids and proj_ids
        else set()
    )
    runs_by_ver: dict[str, list[tuple[str | None, str | None]]] = defaultdict(list)
    for r_vid, r_tid, r_pid in (
        await session.execute(
            select(IngestionRun.document_version_id, IngestionRun.tenant_id, IngestionRun.project_id).where(
                IngestionRun.document_version_id.in_(unique_vids)
            )
        )
    ).all():
        runs_by_ver[r_vid].append((r_tid, r_pid))
    blocks_by_ver: dict[str, list[CanonicalBlock]] = defaultdict(list)
    for blk in (
        await session.scalars(
            select(CanonicalBlock)
            .where(CanonicalBlock.document_version_id.in_(unique_vids))
            .order_by(CanonicalBlock.document_version_id, CanonicalBlock.ordinal)
        )
    ).all():
        blocks_by_ver[blk.document_version_id].append(blk)
    snapshots_by_ver = {
        s.document_version_id: s
        for s in (
            await session.scalars(select(SourceSnapshot).where(SourceSnapshot.document_version_id.in_(unique_vids)))
        ).all()
    }
    results: dict[str, tuple] = {}
    for vid in unique_vids:
        version = versions_by_id.get(vid)
        document = docs_by_id.get(version.document_id) if version else None
        if not version or not document or not document.is_active or version.search_status not in {"READY", "SEARCH_READY"}:
            raise ValueError("Knowledge producer requires an active SEARCH_READY version")
        partition = (version.metadata_json or {}).get("security_partition_id")
        if not all(
            isinstance(v, str) and v.strip()
            for v in (document.tenant_id, document.project_id, document.source_id, partition)
        ):
            raise ValueError("Knowledge producer requires explicit tenant/project/source/partition")
        if (document.source_id, document.project_id) not in confirmed_mappings:
            raise ValueError("Knowledge producer requires a confirmed Project-to-Source mapping")
        if any(
            r_tid != document.tenant_id or r_pid != document.project_id
            for r_tid, r_pid in runs_by_ver.get(version.id, ())
        ):
            raise ValueError("Knowledge producer scope conflicts with ingestion provenance")
        blocks = blocks_by_ver.get(version.id, [])
        if not blocks:
            raise ValueError("Knowledge producer requires canonical source artifacts")
        if any(hashlib.sha256(b.normalized_text.encode()).hexdigest() != b.content_hash for b in blocks):
            raise ValueError("Canonical artifact checksum mismatch")
        source = snapshots_by_ver.get(version.id)
        if source and source.checksum_sha256 != version.file_hash:
            raise ValueError("Source snapshot checksum differs from DocumentVersion")
        config = KnowledgeConfig.model_validate((version.metadata_json or {}).get("knowledge_config", {}))
        provenance = {
            "tenant_id": document.tenant_id,
            "project_id": document.project_id,
            "source_id": document.source_id,
            "document_id": document.id,
            "document_version_id": version.id,
            "version_no": version.version_no,
            "security_partition_id": partition,
            "source_snapshot_id": source.id if source else None,
            "source_checksum": source.checksum_sha256 if source else version.file_hash,
            "published_at": version.source_published_at.isoformat() if version.source_published_at else None,
            "observed_at": version.observed_at.isoformat(),
            "ingested_at": version.ingested_at.isoformat() if version.ingested_at else None,
            "supersedes_id": version.supersedes_id,
        }
        digest = checksum(
            {
                "config": config.model_dump(mode="json"),
                "provenance": provenance,
                "validity": [version.valid_from.isoformat(), version.valid_to.isoformat()],
                "blocks": [
                    [
                        b.id,
                        b.ordinal,
                        b.block_type,
                        b.content_hash,
                        b.page_from,
                        b.page_to,
                        b.section_path,
                        b.source_anchor,
                    ]
                    for b in blocks
                ],
            }
        )
        results[vid] = ({b.id: b for b in blocks}, provenance, digest)
    return results


async def _load_sag_engine_entities(session: AsyncSession, document_id: str) -> tuple[str, ...]:
    """Harvest LLM-extracted entities from sag-engine (LightRAG Dual-Level Bridging)."""
    document = await session.get(Document, document_id)
    if not document:
        return ()
    sag_source_id = getattr(document, "sag_source_id", None)
    clean_source_id = str(sag_source_id).strip() if sag_source_id else ""
    sag_config_id = ""
    if not clean_source_id and document.source_id:
        src_obj = await session.get(Source, document.source_id)
        if src_obj and src_obj.sag_source_config_id:
            sag_config_id = str(src_obj.sag_source_config_id).strip()
    if not clean_source_id and not sag_config_id:
        return ()
    try:
        from zleap.sag.db.models import Entity, EventEntity, SourceEvent

        collected: set[str] = set()
        async with session.begin_nested():
            scope_clause = (
                (SourceEvent.source_id == clean_source_id)
                if clean_source_id
                else (SourceEvent.data_source_id == sag_config_id)
            )
            rows = (
                await session.scalars(
                    select(Entity.name)
                    .join(EventEntity, EventEntity.entity_id == Entity.id)
                    .join(SourceEvent, SourceEvent.id == EventEntity.event_id)
                    .where(
                        scope_clause,
                        SourceEvent.status.is_(None) | (SourceEvent.status != "DELETED"),
                    )
                    .distinct()
                )
            ).all()
            for name in rows:
                if isinstance(name, str) and 2 <= len(name.strip()) <= 120:
                    collected.add(name.strip())

            # Also harvest raw_entities from SourceEvent.extra_data (captures type=law/task/metric)
            extra_rows = (
                await session.scalars(
                    select(SourceEvent.extra_data).where(
                        scope_clause,
                        SourceEvent.status.is_(None) | (SourceEvent.status != "DELETED"),
                    )
                )
            ).all()
            for extra in extra_rows:
                if not isinstance(extra, dict):
                    continue
                raw_ents = (extra.get("raw_entities") or {}).get("entities") or []
                if isinstance(raw_ents, list):
                    for item in raw_ents:
                        if isinstance(item, dict):
                            ename = str(item.get("name") or "").strip()
                            if 2 <= len(ename) <= 120:
                                collected.add(ename)
        return tuple(sorted(collected))
    except Exception:  # noqa: BLE001 - optional sag-engine tables may be absent in isolated tests
        return ()


async def rebuild_knowledge_version(
    session: AsyncSession,
    version_id: str,
    config: KnowledgeConfig = KnowledgeConfig(),
    *,
    dense_by_block_id: Mapping[str, Sequence[float]] | None = None,
    extra_entities: Sequence[str] | None = None,
) -> list[KnowledgeUnit]:
    # Serialize knowledge work on its own row, rather than a long-held ingestion/readiness row.
    pre_ver = await session.get(DocumentVersion, version_id)
    pre_doc = await session.get(Document, pre_ver.document_id) if pre_ver else None
    if (
        pre_doc is not None
        and isinstance(pre_doc.tenant_id, str)
        and pre_doc.tenant_id.strip()
        and isinstance(pre_doc.project_id, str)
        and pre_doc.project_id.strip()
    ):
        await knowledge_lock(session, "project-input:" + pre_doc.tenant_id + ":" + pre_doc.project_id)
        await knowledge_lock(session, "version:" + version_id)
        version, blocks, base, digest = await version_input(session, version_id, config)
    else:
        version, blocks, base, digest = await version_input(session, version_id, config)
        await knowledge_lock(session, "project-input:" + base["tenant_id"] + ":" + base["project_id"])
        await knowledge_lock(session, "version:" + version_id)
        version, blocks, base, digest = await version_input(session, version_id, config)
    bridged_entities = (
        tuple(extra_entities)
        if extra_entities is not None
        else await _load_sag_engine_entities(session, base["document_id"])
    )
    existing = {
        u.id: u
        for u in (
            await session.scalars(select(KnowledgeUnit).where(KnowledgeUnit.document_version_id == version_id))
        ).all()
    }
    existing_checksums = await unit_checksums(session, list(existing.values())) if existing else {}
    units = []
    updated_unit_ids: list[str] = []
    new_evidence_rows: list[KnowledgeEvidence] = []
    for block in blocks:
        if block.block_type == "heading" or not block.normalized_text.strip():
            continue
        unit_id = stable_id(
            base["tenant_id"], base["project_id"], base["security_partition_id"], version_id, block.id, "passage-v1"
        )
        provenance = {
            **base,
            "evidence_block_ids": [block.id],
            "source_anchor": (block.source_anchor or "").strip() or f"block-{block.ordinal}",
            "page_from": block.page_from,
            "page_to": block.page_to,
            "section_path": block.section_path,
            "block_type": block.block_type,
        }
        input_hash = checksum({"version_input": digest, "block_id": block.id})
        unit = existing.get(unit_id)
        if dense_by_block_id is not None:
            dense_vec = [float(v) for v in dense_by_block_id.get(block.id, ())]
        elif unit is not None and isinstance(unit.features_json, dict) and unit.features_json.get("dense"):
            dense_vec = [float(v) for v in unit.features_json.get("dense", ())]
        else:
            dense_vec = None
        facts, features = extract_e0_e1(block, config, dense=dense_vec, extra_entities=bridged_entities)
        if (
            unit
            and unit.input_checksum == input_hash
            and all(
                getattr(unit, key) == base[key]
                for key in ("document_version_id", "tenant_id", "project_id", "source_id", "security_partition_id")
            )
            and unit.ordinal == block.ordinal
            and unit.text == block.normalized_text
            and unit.content_hash == block.content_hash
            and unit.provenance_json == provenance
            and unit.features_json == features
            and unit.checksum == existing_checksums.get(unit.id)
        ):
            unit.is_current = True
            units.append(unit)
            continue
        unit = unit or KnowledgeUnit(id=unit_id)
        unit.document_version_id, unit.tenant_id, unit.project_id = version_id, base["tenant_id"], base["project_id"]
        unit.source_id, unit.security_partition_id = base["source_id"], base["security_partition_id"]
        unit.ordinal, unit.text, unit.content_hash = block.ordinal, block.normalized_text, block.content_hash
        unit.input_checksum, unit.checksum, unit.is_current = input_hash, "", True
        unit.provenance_json, unit.features_json = provenance, features
        unit.valid_from, unit.valid_to = version.valid_from, version.valid_to
        session.add(unit)
        updated_unit_ids.append(unit_id)
        unit_evidence_by_id: dict[str, KnowledgeEvidence] = {}
        for fact in facts:
            payload = evidence_payload(fact, provenance, unit.valid_from, unit.valid_to)
            ev_id = stable_id(unit_id, checksum(fact))
            unit_evidence_by_id[ev_id] = KnowledgeEvidence(
                id=ev_id,
                unit_id=unit_id,
                tier=fact["tier"],
                kind=fact["kind"],
                extractor_version=fact["extractor_version"],
                confidence=fact["confidence"],
                payload_json=payload,
            )
        sorted_unit_evidence = [unit_evidence_by_id[k] for k in sorted(unit_evidence_by_id)]
        unit.checksum = _unit_checksum(unit, sorted_unit_evidence)
        new_evidence_rows.extend(sorted_unit_evidence)
        units.append(unit)
    if updated_unit_ids:
        await session.flush()
        for offset in range(0, len(updated_unit_ids), 512):
            await session.execute(
                delete(KnowledgeEvidence).where(
                    KnowledgeEvidence.unit_id.in_(updated_unit_ids[offset : offset + 512])
                )
            )
        if new_evidence_rows:
            session.add_all(new_evidence_rows)
        await session.flush()
    live = {u.id for u in units}
    for old in existing.values():
        if old.id not in live:
            old.is_current = False
    if not units:
        raise ValueError("Knowledge input has no non-heading passages")
    await session.scalar(select(DocumentVersion).where(DocumentVersion.id == version_id).with_for_update())
    _, _, _, final_digest = await version_input(session, version_id, config)
    if final_digest != digest:
        raise ValueError("Source artifacts changed during knowledge rebuild")
    version.knowledge_status = "DATA_READY"
    version.metadata_json = {
        **(version.metadata_json or {}),
        "knowledge_input_checksum": digest,
        "knowledge_config": config.model_dump(mode="json"),
    }
    await session.flush()
    return units


def _cosine(left: list, right: list) -> float | None:
    if not left or not right:
        return None
    if len(left) != len(right) or any(not math.isfinite(v) for v in left + right):
        raise ValueError("Knowledge dense features must be finite and share a dimension")
    scale_a, scale_b = max(map(abs, left)), max(map(abs, right))
    if not scale_a or not scale_b:
        return None
    a, b = [v / scale_a for v in left], [v / scale_b for v in right]
    value = math.sumprod(a, b) / math.sqrt(
        math.sumprod(a, a) * math.sumprod(b, b)
    )
    return (max(-1.0, min(1.0, value)) + 1) / 2


def _prepare_dense(vector):
    if not vector:
        return None
    if any(not math.isfinite(value) for value in vector):
        raise ValueError("Knowledge dense features must be finite")
    scale = max(map(abs, vector))
    if not scale:
        return (vector, 0.0)
    normalized = [value / scale for value in vector]
    return normalized, math.sumprod(normalized, normalized)


def _prepared_cosine(left, right):
    if left is None or right is None:
        return None
    a, norm_a = left
    b, norm_b = right
    if len(a) != len(b):
        raise ValueError("Knowledge dense features must share a dimension")
    if not norm_a or not norm_b:
        return None
    value = math.sumprod(a, b) / math.sqrt(norm_a * norm_b)
    return (max(-1.0, min(1.0, value)) + 1) / 2


@lru_cache(maxsize=8)
def _lsh_signs(dimension):
    return tuple(tuple(1 if int(hashlib.sha256(f"{p}:{i}".encode()).hexdigest()[:8], 16) % 2 else -1
                       for i in range(dimension)) for p in range(8))


def fuse_signals(signals: dict[str, float | None]) -> float:
    observed = [(SIGNAL_WEIGHTS[name], value) for name, value in signals.items() if value is not None]
    if any(not math.isfinite(value) or not 0 <= value <= 1 for _, value in observed):
        raise ValueError("Graph signals must be calibrated into [0,1]")
    return math.fsum(w * v for w, v in observed) / math.fsum(w for w, _ in observed) if observed else 0.0


def _quantile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    offset = (len(ordered) - 1) * fraction
    low, high = math.floor(offset), math.ceil(offset)
    return ordered[low] + (ordered[high] - ordered[low]) * (offset - low)


def graph_payload(units: list[KnowledgeUnit], config: KnowledgeConfig = KnowledgeConfig()) -> dict:
    scopes = {(u.tenant_id, u.project_id) for u in units}
    if len(scopes) != 1 or len({u.id for u in units}) != len(units):
        raise ValueError("Graph input requires unique Knowledge Units in one tenant/project")
    partitions: dict[str, list[KnowledgeUnit]] = defaultdict(list)
    for unit in sorted(units, key=lambda u: u.id):
        if not unit.security_partition_id:
            raise ValueError("Graph input requires an ACL partition")
        partitions[unit.security_partition_id].append(unit)
    output, calibrations = [], {}
    for partition, rows in sorted(partitions.items()):
        def _norm_ent(raw_e: str) -> str:
            return re.sub(r"[\s_-]+", " ", str(raw_e).casefold()).strip()

        unit_map = {u.id: u for u in rows}
        prepared_dense = {u.id: _prepare_dense(u.features_json.get("dense", [])) for u in rows}
        token_counts = {u.id: terms(u.text + " " + u.provenance_json["section_path"]) for u in rows}
        df = Counter(t for counts in token_counts.values() for t in counts)
        norm_ents_by_unit = {
            u.id: {_norm_ent(e) for e in u.features_json["entities"] if _norm_ent(e)} for u in rows
        }
        entity_df = Counter(e for ents in norm_ents_by_unit.values() for e in ents)
        avg_len = sum(sum(c.values()) for c in token_counts.values()) / len(rows) or 1
        postings: dict[tuple, list[str]] = defaultdict(list)
        for unit in rows:
            features, provenance = unit.features_json, unit.provenance_json
            keys = [("term", t) for t in sorted(token_counts[unit.id], key=lambda t: (df[t], t))[: config.max_terms]]
            keys += [("entity", e) for e in sorted(norm_ents_by_unit[unit.id])] + [
                ("citation", c) for c in features["citations"]
            ]
            keys += [
                ("section", unit.document_version_id, provenance["section_path"]),
                ("document", provenance["document_id"]),
                ("temporal", unit.valid_from.date().isoformat()),
            ]
            dense = features.get("dense", [])
            if dense:
                # ponytail: fixed LSH buckets; replace with partition-filtered ANN when dense recall needs it.
                signs = _lsh_signs(len(dense))
                bits = tuple(
                    math.sumprod(dense, signs[p]) >= 0
                    for p in range(8)
                )
                keys += [("dense", band, bits[band : band + 4]) for band in (0, 4)]
            for key in set(keys):
                postings[key].append(unit.id)
        by_unit: dict[str, list[tuple]] = defaultdict(list)
        for key, ids in postings.items():
            for unit_id in ids:
                by_unit[unit_id].append(key)
        pairs = set()
        for unit in rows:
            votes: Counter = Counter()
            for key in sorted(by_unit[unit.id], key=lambda k: (len(postings[k]), str(k))):
                ids = postings[key]
                index = bisect_left(ids, unit.id)
                half = config.posting_limit // 2
                start = max(0, min(index - half, len(ids) - config.posting_limit - 1))
                for other in ids[start : start + config.posting_limit + 1]:
                    if other != unit.id:
                        votes[other] += 1
            for other, _ in sorted(votes.items(), key=lambda item: (-item[1], item[0]))[: config.candidate_limit]:
                pairs.add(tuple(sorted((unit.id, other))))

        def bm25(query: Counter, target: Counter, df=df, size=len(rows), avg_len=avg_len) -> float:
            length = sum(target.values())
            return math.fsum(
                math.log(1 + (size - df[t] + 0.5) / (df[t] + 0.5))
                * target[t]
                * 2.5
                / (target[t] + 1.5 * (0.25 + 0.75 * length / avg_len))
                for t in sorted(query.keys() & target.keys())
            )

        raw = {
            pair: 0.5
            * (bm25(token_counts[pair[0]], token_counts[pair[1]]) + bm25(token_counts[pair[1]], token_counts[pair[0]]))
            for pair in sorted(pairs)
        }
        q05, q95 = _quantile(list(raw.values()), 0.05), _quantile(list(raw.values()), 0.95)
        calibrations[partition] = {
            "method": "symmetric-bm25-q05-q95-v1",
            "q05": q05,
            "q95": q95,
            "pair_count": len(raw),
            "avg_doc_len": avg_len,
            "unit_count": len(rows),
        }
        candidates = []
        for (left, right), value in raw.items():
            a, b = unit_map[left], unit_map[right]
            ap, bp = a.provenance_json, b.provenance_json
            af, bf = a.features_json, b.features_json
            ea, eb = norm_ents_by_unit[left], norm_ents_by_unit[right]
            if ea and eb:
                weights = {e: math.log(1 + len(rows) / entity_df[e]) for e in sorted(ea | eb)}
                jaccard_ent = math.fsum(weights[e] for e in sorted(ea & eb)) / math.fsum(weights.values())
                shared_ents = ea & eb
                if shared_ents:
                    max_idf = max(weights[e] for e in shared_ents) / max(1.0, math.log(1 + len(rows)))
                    entity = min(1.0, 0.45 * jaccard_ent + 0.55 * min(1.0, max_idf))
                else:
                    entity = 0.0
            else:
                entity = None
            same_doc = ap["document_id"] == bp["document_id"]
            structure = (
                0.85
                if same_doc and ap["section_path"] == bp["section_path"]
                else 0.65
                if same_doc and abs(a.ordinal - b.ordinal) <= 2
                else 0.45
                if same_doc
                else 0.25
                if a.source_id == b.source_id
                else 0.0
            )
            citations_a, citations_b = set(af["citations"]), set(bf["citations"])
            signals = {
                "semantic": _prepared_cosine(prepared_dense[left], prepared_dense[right]),
                "lexical": max(0.0, min(1.0, (value - q05) / (q95 - q05))) if q95 > q05 else float(value > 0),
                "entity": entity,
                "structure": structure,
                "temporal": float(a.valid_from < b.valid_to and b.valid_from < a.valid_to),
                "citation": (0.7 if citations_a & citations_b else 0.0) if citations_a or citations_b else None,
            }
            score = fuse_signals(signals)
            if score > 0 and score >= config.edge_min_score:
                candidates.append(
                    {
                        "source": left,
                        "target": right,
                        "partition": partition,
                        "weight": score,
                        "signals": signals,
                        "raw_lexical": value,
                    }
                )
        degree: Counter = Counter()
        same_doc_degree: Counter = Counter()
        max_same_doc_degree = max(1, config.max_degree // 2)
        for edge in sorted(candidates, key=lambda e: (-e["weight"], e["source"], e["target"])):
            u_src, u_tgt = edge["source"], edge["target"]
            if degree[u_src] >= config.max_degree or degree[u_tgt] >= config.max_degree:
                continue
            is_same_doc = (
                unit_map[u_src].provenance_json.get("document_id")
                == unit_map[u_tgt].provenance_json.get("document_id")
            )
            if is_same_doc and (
                same_doc_degree[u_src] >= max_same_doc_degree
                or same_doc_degree[u_tgt] >= max_same_doc_degree
            ):
                continue
            output.append(edge)
            degree.update((u_src, u_tgt))
            if is_same_doc:
                same_doc_degree.update((u_src, u_tgt))
    tenant, project = next(iter(scopes))
    return {
        "contract": "knowledge-graph.v1",
        "tenant_id": tenant,
        "project_id": project,
        "config": config.model_dump(mode="json"),
        "weights": SIGNAL_WEIGHTS,
        "calibrations": calibrations,
        "units": [[u.id, u.checksum] for u in sorted(units, key=lambda u: u.id)],
        "edges": sorted(output, key=lambda e: (e["source"], e["target"])),
    }


async def persist_graph(
    session: AsyncSession,
    units: list[KnowledgeUnit],
    config: KnowledgeConfig = KnowledgeConfig(),
    *,
    preverified: bool = False,
) -> KnowledgeGraphBuild:
    if not preverified:
        checksums = await unit_checksums(session, units)
        for unit in units:
            if unit.checksum != checksums[unit.id]:
                raise ValueError("Knowledge Unit/evidence checksum mismatch")
    fingerprint = checksum({"algorithm": "knowledge-graph.v1", "config": config.model_dump(mode="json"),
                            "units": sorted((u.id, u.checksum) for u in units)})
    if not units or len({(u.tenant_id, u.project_id) for u in units}) != 1:
        raise ValueError("Graph input requires one nonempty tenant/project")
    cached = await session.scalar(select(KnowledgeGraphBuild).where(
        KnowledgeGraphBuild.tenant_id == units[0].tenant_id,
        KnowledgeGraphBuild.project_id == units[0].project_id,
        KnowledgeGraphBuild.input_fingerprint == fingerprint,
    ))
    if cached:
        return await verify_graph(session, cached.id)
    from sag_api.services.cpu_worker import run_cpu

    fields = ("id", "tenant_id", "project_id", "security_partition_id", "document_version_id", "source_id",
              "ordinal", "text", "features_json", "provenance_json", "valid_from", "valid_to", "checksum")
    payload = await run_cpu("graph", {"units": [{key: getattr(u, key) for key in fields} for u in units],
                                      "config": config.model_dump(mode="json")})
    digest = checksum(payload)
    build_id = "graph-" + digest[:24]
    build = await session.get(KnowledgeGraphBuild, build_id)
    if build:
        await verify_graph(session, build_id)
        build.input_fingerprint = fingerprint
        return build
    build = KnowledgeGraphBuild(
        id=build_id,
        tenant_id=payload["tenant_id"],
        project_id=payload["project_id"],
        checksum=digest,
        manifest_json=payload,
        input_fingerprint=fingerprint,
    )
    session.add(build)
    await session.flush()
    edge_rows = [
        {
            "build_id": build_id,
            "source_unit_id": edge["source"],
            "target_unit_id": edge["target"],
            "security_partition_id": edge["partition"],
            "weight": edge["weight"],
            "signals_json": {"signals": edge["signals"], "raw_lexical": edge["raw_lexical"]},
        }
        for edge in payload["edges"]
    ]
    for offset in range(0, len(edge_rows), 1000):
        await session.execute(insert(KnowledgeUnitEdge), edge_rows[offset : offset + 1000])
    await session.flush()
    return build


async def verify_graph(session: AsyncSession, build_id: str) -> KnowledgeGraphBuild:
    build = await session.get(KnowledgeGraphBuild, build_id, populate_existing=True)
    if not build or checksum(build.manifest_json) != build.checksum or build.id != "graph-" + build.checksum[:24]:
        raise ValueError("Persisted graph manifest checksum mismatch")
    stored = (
        await session.scalars(
            select(KnowledgeUnitEdge)
            .where(KnowledgeUnitEdge.build_id == build_id)
            .order_by(KnowledgeUnitEdge.source_unit_id, KnowledgeUnitEdge.target_unit_id)
        )
    ).all()
    actual = [
        {
            "source": e.source_unit_id,
            "target": e.target_unit_id,
            "partition": e.security_partition_id,
            "weight": e.weight,
            **e.signals_json,
        }
        for e in stored
    ]
    if actual != build.manifest_json["edges"]:
        raise ValueError("Persisted graph edges mismatch")
    return build
