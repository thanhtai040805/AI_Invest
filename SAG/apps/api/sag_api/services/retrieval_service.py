"""Shared bounded retrieval, reranking, and evidence-grounded search answers."""

from __future__ import annotations

import asyncio
import math
import re
import threading
import time
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

from sag_api.core.config import FIXED_SEARCH_TOP_K, settings
from sag_api.core.logging import get_logger
from sag_api.sag import RetrievedSection, SearchOutcome
from sag_api.services.evidence_service import (
    SearchSource,
    build_evidence_pack,
    no_answer_text,
    resolve_traceable_evidence,
)
from sag_api.services.query_analysis import (
    QueryAnalysis,
    analyze_query,
    decompose_query_clauses,
    extract_query_features,
    extract_salient_query_terms,
    normalize_lexical_text,
)

log = get_logger("retrieval")


EventScoreMap = dict[tuple[str, str], float]
DocumentSourceExclusions = dict[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class _HiddenDerivatives:
    source_ids: frozenset[str] = frozenset()
    source_keys: frozenset[tuple[str, str]] = frozenset()
    source_config_ids: frozenset[str] = frozenset()
    event_keys: frozenset[tuple[str, str]] = frozenset()


async def _hidden_document_derivatives(
    sources: list[SearchSource],
) -> _HiddenDerivatives:
    """Read the persisted logical-delete/reprocess barrier for retrieval."""
    from sqlalchemy import select

    from sag_api.core.db import SessionLocal
    from sag_api.db.models import Document, Job
    from sag_api.enums import DocumentStatus, JobStatus, JobType

    source_ids = [source.id for source in sources]
    if not source_ids:
        return _HiddenDerivatives()
    config_by_source = {
        source.id: source.sag_source_config_id for source in sources
    }
    hidden_document_ids: set[str] = set()
    hidden_source_ids: set[str] = set()
    hidden_source_keys: set[tuple[str, str]] = set()
    hidden_configs: set[str] = set()
    hidden_event_keys: set[tuple[str, str]] = set()
    async with SessionLocal() as session:
        documents = list(
            (
                await session.scalars(
                    select(Document).where(
                        Document.source_id.in_(source_ids),
                        Document.status.in_(
                            [DocumentStatus.DELETING, DocumentStatus.DELETE_FAILED]
                        ),
                    )
                )
            ).all()
        )
        active_control_jobs = list(
            (
                await session.scalars(
                    select(Job).where(
                        Job.source_id.in_(source_ids),
                        Job.type.in_([JobType.DELETE_DOCUMENT, JobType.REPROCESS_DOCUMENT]),
                        Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                    )
                )
            ).all()
        )
        failed_control_jobs = list(
            (
                await session.scalars(
                    select(Job)
                    .join(Document, Job.document_id == Document.id)
                    .where(
                        Job.source_id.in_(source_ids),
                        Job.type.in_([JobType.DELETE_DOCUMENT, JobType.REPROCESS_DOCUMENT]),
                        Job.status == JobStatus.FAILED,
                        Document.status.in_([DocumentStatus.FAILED, DocumentStatus.DELETE_FAILED]),
                    )
                )
            ).all()
        )
        active_control_jobs.extend(failed_control_jobs)
        hidden_document_ids.update(document.id for document in documents)
        hidden_document_ids.update(
            job.document_id for job in active_control_jobs if job.document_id
        )
        if hidden_document_ids:
            histories = list(
                (
                    await session.scalars(
                        select(Job).where(Job.document_id.in_(hidden_document_ids))
                    )
                ).all()
            )
        else:
            histories = []

    for document in documents:
        config_id = config_by_source.get(document.source_id)
        if document.sag_source_id:
            hidden_source_ids.add(document.sag_source_id)
            if config_id:
                hidden_source_keys.add((config_id, document.sag_source_id))
        if config_id:
            hidden_configs.add(config_id)
    for job in [*active_control_jobs, *histories]:
        payload = job.payload or {}
        checkpoint = payload.get("process_checkpoint")
        if isinstance(checkpoint, dict):
            value = checkpoint.get("source_id")
            if isinstance(value, str) and value.strip():
                normalized_source_id = value.strip()
                hidden_source_ids.add(normalized_source_id)
                config_id = config_by_source.get(job.source_id or "")
                if config_id:
                    hidden_source_keys.add((config_id, normalized_source_id))
            config_id = config_by_source.get(job.source_id or "")
            if config_id:
                hidden_event_keys.update(
                    (config_id, event_id.strip())
                    for event_id in checkpoint.get("event_ids", [])
                    if isinstance(event_id, str) and event_id.strip()
                )
        derived_source_ids = {
            value.strip()
            for value in payload.get("derived_source_ids", [])
            if isinstance(value, str) and value.strip()
        }
        hidden_source_ids.update(derived_source_ids)
        config_id = config_by_source.get(job.source_id or "")
        if config_id:
            hidden_source_keys.update(
                (config_id, source_id) for source_id in derived_source_ids
            )
            hidden_configs.add(config_id)
    return _HiddenDerivatives(
        source_ids=frozenset(hidden_source_ids),
        source_keys=frozenset(hidden_source_keys),
        source_config_ids=frozenset(hidden_configs),
        event_keys=frozenset(hidden_event_keys),
    )


def _document_source_exclusions(
    hidden: _HiddenDerivatives,
) -> DocumentSourceExclusions:
    """Scope hidden engine article IDs to their owning source config."""
    values: dict[str, set[str]] = {}
    for source_config_id, source_id in hidden.source_keys:
        values.setdefault(source_config_id, set()).add(source_id)
    return {
        source_config_id: tuple(sorted(source_ids))
        for source_config_id, source_ids in values.items()
    }


_BOILERPLATE = (
    "all rights reserved",
    "copyright notice",
    "click to load more",
    "disclaimer",
    "bản quyền thuộc về",
    "tuyên bố miễn trừ trách nhiệm",
)
_CITATION_RE = re.compile(r"\[(\d+)]")
_RRF_K = 60
_SEMANTIC_RELATIVE_RELEVANCE_FLOOR = 0.68


def _section_key(section: RetrievedSection) -> tuple[str, str]:
    source = (section.source_config_id or section.source_id or "").strip()
    chunk = (section.chunk_id or "").strip()
    if chunk:
        return source, chunk
    fingerprint = normalize_lexical_text(f"{section.heading}\n{section.content}")[:240]
    return source, fingerprint


from sag_api.services.search_index_service import COMMON_STOPWORDS

_LEXICAL_STOPWORDS = COMMON_STOPWORDS
_CONTINUOUS_SCRIPT_TERM_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\u0e00-\u0e7f\u0e80-\u0eff\u1780-\u17ff\u1000-\u109f]"
)


def _normalize_spaced_lexical_text(value: str) -> str:
    tokens = re.findall(r"[^\W_]+", unicodedata.normalize("NFC", value or "").lower(), flags=re.UNICODE)
    return f" {' '.join(tokens)} " if tokens else ""


def _ascii_lexical_root(word: str) -> str:
    if not (len(word) >= 5 and word.isascii() and word.isalpha()):
        return word
    w = word
    if len(w) >= 6 and w.endswith("ies"):
        w = w[:-3] + "y"
    elif len(w) >= 5 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]
    for suffix in ("ations", "ation", "ingly", "edly", "ing", "ed"):
        if len(w) >= len(suffix) + 4 and w.endswith(suffix) and not w.endswith("eed"):
            w = w[: -len(suffix)]
            break
    if len(w) >= 5 and w.endswith("e") and not w.endswith(("ee", "le")):
        w = w[:-1]
    return w if len(w) >= 4 else word


def _extract_ascii_roots(spaced_text: str) -> frozenset[str]:
    if not spaced_text:
        return frozenset()
    return frozenset(
        _ascii_lexical_root(tok)
        for tok in spaced_text.split()
        if len(tok) >= 4 and tok.isascii() and tok.isalpha()
    )


def _normalize_query_term(term: str) -> str:
    if not term:
        return ""
    if _CONTINUOUS_SCRIPT_TERM_RE.search(term) or any(ch.isdigit() for ch in term):
        return normalize_lexical_text(term)
    spaced = _normalize_spaced_lexical_text(term).strip()
    return spaced if spaced else normalize_lexical_text(term)


def _term_matches_text(
    term: str,
    text: str,
    *,
    spaceless_text: str | None = None,
    root_tokens: frozenset[str] | None = None,
) -> bool:
    if not term or not text:
        return False
    # Continuous scripts (CJK/Kana/Thai) or digit-bearing / glued identifiers match spaceless haystack
    if _CONTINUOUS_SCRIPT_TERM_RE.search(term) or any(ch.isdigit() for ch in term):
        compact = spaceless_text if spaceless_text is not None else text.replace(" ", "")
        return term in compact
    if text.startswith(" ") and text.endswith(" "):
        if f" {term} " in text:
            return True
        if " " in term:
            glued = term.replace(" ", "")
            return bool(glued) and f" {glued} " in text
        if (
            len(term) >= 5
            and term.endswith("s")
            and not term.endswith(("ss", "us", "is"))
            and f" {term[:-1]} " in text
        ):
            return True
        if len(term) >= 4 and not term.endswith("s") and f" {term}s " in text:
            return True
        if len(term) >= 5 and term.isascii() and term.isalpha():
            t_root = _ascii_lexical_root(term)
            effective_roots = root_tokens if root_tokens is not None else _extract_ascii_roots(text)
            if t_root in effective_roots:
                return True
        return False
    if term in text:
        return True
    if len(term) >= 5 and term.endswith("s") and not term.endswith(("ss", "us", "is")) and term[:-1] in text:
        return True
    return False


def _phrase_matches_text(phrase: str, spaced_phrase: str, text: str, *, spaceless_text: str | None = None) -> bool:
    if not phrase or len(phrase) < 2 or not text:
        return False
    if _CONTINUOUS_SCRIPT_TERM_RE.search(phrase):
        compact = spaceless_text if spaceless_text is not None else text.replace(" ", "")
        return phrase in compact
    if spaced_phrase and text.startswith(" ") and text.endswith(" "):
        return spaced_phrase in text
    return phrase in text


def _lexical_relevance(
    query: str,
    section: RetrievedSection,
    *,
    analysis: QueryAnalysis | None = None,
) -> float:
    effective = analysis or analyze_query(
        query,
        segmentation_enabled=settings.search_chinese_segmentation_enabled,
    )
    heading = _normalize_spaced_lexical_text(section.heading)
    content = _normalize_spaced_lexical_text(section.content)
    text = f"{heading}{content[1:]}" if heading and content else (heading or content)
    if not text:
        return 0.0
    heading_compact = heading.replace(" ", "")
    text_compact = text.replace(" ", "")
    heading_roots = _extract_ascii_roots(heading)
    text_roots = _extract_ascii_roots(text)

    raw_terms = [_normalize_query_term(term) for term in effective.scoring_terms]
    raw_terms = [term for term in raw_terms if term]
    filtered_terms = [term for term in raw_terms if term not in _LEXICAL_STOPWORDS]
    terms = filtered_terms if filtered_terms else raw_terms
    from sag_api.services.query_analysis import _remove_query_noise

    phrase = effective.normalized_phrase
    spaced_phrase = _normalize_spaced_lexical_text(_remove_query_noise(query))

    score = 0.0
    if _phrase_matches_text(phrase, spaced_phrase, text, spaceless_text=text_compact):
        score += 0.55
        if _phrase_matches_text(phrase, spaced_phrase, heading, spaceless_text=heading_compact):
            score += 0.2
    if terms:
        matched = sum(
            _term_matches_text(term, text, spaceless_text=text_compact, root_tokens=text_roots)
            for term in terms
        )
        heading_matched = sum(
            _term_matches_text(term, heading, spaceless_text=heading_compact, root_tokens=heading_roots)
            for term in terms
        )
        score += 0.35 * matched / len(terms)
        score += 0.15 * heading_matched / len(terms)
    return min(1.0, score)


def _terms_from_query_slice(slice_text: str) -> tuple[str, ...]:
    analyzed = analyze_query(
        slice_text,
        segmentation_enabled=settings.search_chinese_segmentation_enabled,
    )
    raw = [_normalize_query_term(term) for term in analyzed.scoring_terms]
    raw = [term for term in raw if term]
    filtered = [term for term in raw if term not in _LEXICAL_STOPWORDS]
    return tuple(filtered if filtered else raw)


def _adjacent_proximity_score(terms: tuple[str, ...], text: str, *, spaceless_text: str | None = None) -> float:
    if not text or len(terms) < 2:
        return 0.0
    compact = spaceless_text if spaceless_text is not None else text.replace(" ", "")
    is_spaced = text.startswith(" ") and text.endswith(" ")
    hits = 0.0
    pairs = len(terms) - 1
    for idx in range(pairs):
        t1, t2 = terms[idx], terms[idx + 1]
        if is_spaced and not (_CONTINUOUS_SCRIPT_TERM_RE.search(t1) or _CONTINUOUS_SCRIPT_TERM_RE.search(t2)):
            if f" {t1} {t2} " in text or f" {t2} {t1} " in text:
                hits += 1.0
                continue
            pos1 = text.find(f" {t1} ")
            pos2 = text.find(f" {t2} ")
            if pos1 != -1 and pos2 != -1 and abs(pos1 - pos2) <= max(56, len(t1) + len(t2) + 32):
                hits += 0.65
        else:
            if f"{t1}{t2}" in compact or f"{t2}{t1}" in compact:
                hits += 1.0
                continue
            pos1 = compact.find(t1)
            pos2 = compact.find(t2)
            if pos1 != -1 and pos2 != -1 and abs(pos1 - pos2) <= max(48, len(t1) + len(t2) + 24):
                hits += 0.65
    return min(1.0, hits / pairs)


def _score_term_sequence(
    terms: tuple[str, ...],
    *,
    text: str,
    heading: str,
    phrase: str,
    spaced_phrase: str = "",
    term_weights: dict[str, float],
    raw_heading: str = "",
    spaceless_text: str | None = None,
    spaceless_heading: str | None = None,
    text_roots: frozenset[str] | None = None,
    heading_roots: frozenset[str] | None = None,
) -> float:
    if not text or not terms:
        return 0.0
    text_compact = spaceless_text if spaceless_text is not None else text.replace(" ", "")
    head_compact = spaceless_heading if spaceless_heading is not None else heading.replace(" ", "")
    eff_text_roots = text_roots if text_roots is not None else _extract_ascii_roots(text)
    eff_head_roots = heading_roots if heading_roots is not None else _extract_ascii_roots(heading)
    total_w = sum(term_weights.get(t, 1.0) for t in terms) or 1.0
    matched_w = sum(
        term_weights.get(t, 1.0)
        for t in terms
        if _term_matches_text(t, text, spaceless_text=text_compact, root_tokens=eff_text_roots)
    )
    head_w = sum(
        term_weights.get(t, 1.0)
        for t in terms
        if _term_matches_text(t, heading, spaceless_text=head_compact, root_tokens=eff_head_roots)
    )
    cov_text = matched_w / total_w
    cov_head = head_w / total_w
    prox_text = (
        1.0
        if _phrase_matches_text(phrase, spaced_phrase, text, spaceless_text=text_compact)
        else _adjacent_proximity_score(terms, text, spaceless_text=text_compact)
    )
    prox_head = (
        1.0
        if _phrase_matches_text(phrase, spaced_phrase, heading, spaceless_text=head_compact)
        else _adjacent_proximity_score(terms, heading, spaceless_text=head_compact)
    )
    if raw_heading and terms:
        # Check if the leaf heading title (after the last structural separator) is 100% matched
        # in the query and contains the query's highest-IDF distinguishing term.
        clause_title = re.split(r"(?:\s+>\s+|\s+-\s+|:\s+|\.\s+)", raw_heading)[-1].strip()
        title_terms = [
            normalize_lexical_text(w)
            for w in re.findall(r"[^\W_]+", clause_title.lower(), flags=re.UNICODE)
        ]
        title_informative = [t for t in title_terms if len(t) >= 2 and t not in _LEXICAL_STOPWORDS]
        expanded_single_terms = set(terms)
        for t in terms:
            if " " in t:
                expanded_single_terms.update(t.split())
        if len(title_informative) >= 2 and set(title_informative) <= expanded_single_terms:
            max_w = max((term_weights.get(t, 1.0) for t in terms), default=1.0)
            if any(term_weights.get(t, 1.0) >= 0.62 * max_w for t in title_informative):
                cov_head = max(cov_head, 0.96)
                prox_head = 1.0
    return min(
        1.0,
        0.50 * cov_text + 0.22 * cov_head + 0.18 * prox_text + 0.10 * prox_head,
    )


def _document_identity(section: RetrievedSection) -> str:
    return str(
        section.document_id
        or section.document_version_id
        or section.document_name
        or section.chunk_id
        or ""
    )


def _compute_document_aware_effective_ranks(
    ranks: dict[tuple[str, str], int],
    representatives: dict[tuple[str, str], RetrievedSection],
) -> dict[tuple[str, str], float]:
    """Combine chunk rank and distinct-document rank so long multi-chunk docs cannot bury doc #2/#3."""
    ordered = sorted(ranks.items(), key=lambda item: item[1])
    doc_first_rank: dict[str, int] = {}
    doc_seen_count: dict[str, int] = {}
    effective_ranks: dict[tuple[str, str], float] = {}
    for key, chunk_rank in ordered:
        sec = representatives.get(key)
        d_id = _document_identity(sec) if sec is not None else str(key)
        if d_id not in doc_first_rank:
            doc_first_rank[d_id] = len(doc_first_rank)
        within_doc_idx = doc_seen_count.get(d_id, 0)
        doc_seen_count[d_id] = within_doc_idx + 1
        doc_rank = doc_first_rank[d_id]
        effective_ranks[key] = 0.5 * float(chunk_rank) + 0.5 * float(doc_rank + 2 * within_doc_idx)
    return effective_ranks


def _is_boilerplate(section: RetrievedSection) -> bool:
    text = f"{section.heading}\n{section.content}"
    return sum(marker in text for marker in _BOILERPLATE) >= 2


@dataclass(frozen=True, slots=True)
class RerankResult:
    sections: list[RetrievedSection]
    candidate_count: int
    relevant_count: int
    filtered_count: int
    lexical_count: int


def rerank_sections(
    query: str,
    semantic: list[RetrievedSection],
    *,
    lexical: list[RetrievedSection] | None = None,
    exact_lexical_keys: set[tuple[str, str]] | None = None,
    limit: int | None,
    analysis: QueryAnalysis | None = None,
) -> RerankResult:
    """Fuse retriever rank lists without assuming their scores share a scale."""

    effective = analysis or analyze_query(
        query,
        segmentation_enabled=settings.search_chinese_segmentation_enabled,
    )
    lexical = lexical or []
    lexical_keys = {_section_key(section) for section in lexical}
    exact_keys = (
        lexical_keys
        if exact_lexical_keys is None
        else lexical_keys.intersection(exact_lexical_keys)
    )
    def unique_rankings(
        sections: list[RetrievedSection],
        *,
        preferred_keys: set[tuple[str, str]] | None = None,
    ) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], RetrievedSection]]:
        ranks: dict[tuple[str, str], int] = {}
        representatives: dict[tuple[str, str], RetrievedSection] = {}
        for section in sections:
            key = _section_key(section)
            if not key[1]:
                continue
            if key not in ranks:
                ranks[key] = len(ranks)
                representatives[key] = section
                continue
            previous = representatives[key]
            previous_preference = (
                len(previous.content.strip()),
                normalize_lexical_text(previous.heading),
                normalize_lexical_text(previous.content),
                previous.heading,
                previous.content,
            )
            current_preference = (
                len(section.content.strip()),
                normalize_lexical_text(section.heading),
                normalize_lexical_text(section.content),
                section.heading,
                section.content,
            )
            best_score = max(
                float(previous.score or 0.0),
                float(section.score or 0.0),
            )
            if current_preference > previous_preference:
                representatives[key] = section.model_copy(
                    update={"score": best_score}
                )
            else:
                representatives[key] = previous.model_copy(
                    update={"score": best_score}
                )
        preferred_keys = preferred_keys or set()
        ordered_keys = sorted(
            ranks,
            key=lambda key: (key not in preferred_keys, ranks[key], key),
        )
        ranks = {key: rank for rank, key in enumerate(ordered_keys)}
        return ranks, representatives

    semantic_ranks, semantic_by_key = unique_rankings(semantic)
    lexical_ranks, lexical_by_key = unique_rankings(lexical, preferred_keys=exact_keys)
    semantic_eff_ranks = _compute_document_aware_effective_ranks(semantic_ranks, semantic_by_key)
    lexical_eff_ranks = _compute_document_aware_effective_ranks(lexical_ranks, lexical_by_key)
    semantic_scores: dict[tuple[str, str], float] = {}
    for section in semantic:
        key = _section_key(section)
        if not key[1]:
            continue
        score = float(section.score or 0.0)
        if math.isfinite(score):
            semantic_scores[key] = max(semantic_scores.get(key, 0.0), score, 0.0)
    candidate_keys = set(semantic_ranks) | set(lexical_ranks)
    if not candidate_keys:
        return RerankResult([], 0, 0, 0, len(lexical))

    merged: dict[tuple[str, str], RetrievedSection] = {}
    for key in candidate_keys:
        choices = [
            candidate
            for candidate in (semantic_by_key.get(key), lexical_by_key.get(key))
            if candidate is not None
        ]
        merged[key] = max(
            choices,
            key=lambda section: (
                len(section.content.strip()),
                normalize_lexical_text(section.heading),
                normalize_lexical_text(section.content),
                section.heading,
                section.content,
            ),
        )

    raw_terms = [_normalize_query_term(term) for term in effective.scoring_terms]
    raw_terms = [term for term in raw_terms if term]
    filtered_terms = [term for term in raw_terms if term not in _LEXICAL_STOPWORDS]
    query_term_list = tuple(filtered_terms if filtered_terms else raw_terms)
    salient_terms = extract_salient_query_terms(query)
    anchor_terms = frozenset(
        norm
        for term in (
            *effective.features.exact_terms,
            *effective.features.identifier_terms,
            *effective.features.path_terms,
        )
        if (norm := _normalize_query_term(term))
    )
    clauses = decompose_query_clauses(query)
    if anchor_terms:
        query_term_list = tuple(dict.fromkeys([*query_term_list, *sorted(anchor_terms)]))
    clause_term_lists = (
        tuple(ct for c in clauses if (ct := _terms_from_query_slice(c)))
        if len(clauses) >= 2
        else ()
    )

    doc_texts: dict[str, tuple[str, str, set[str]]] = {}
    section_norm_cache: dict[
        tuple[str, str], tuple[str, str, str, str, frozenset[str], frozenset[str]]
    ] = {}
    for key, section in merged.items():
        contextual_heading = " ".join(
            part
            for part in (section.document_name, section.section_path, section.heading)
            if part
        )
        head_spaced = _normalize_spaced_lexical_text(contextual_heading or section.heading)
        body_spaced = _normalize_spaced_lexical_text(section.content)
        full_spaced = (
            f"{head_spaced}{body_spaced[1:]}"
            if head_spaced and body_spaced
            else (head_spaced or body_spaced)
        )
        head_compact = head_spaced.replace(" ", "")
        full_compact = full_spaced.replace(" ", "")
        head_roots = _extract_ascii_roots(head_spaced)
        full_roots = _extract_ascii_roots(full_spaced)
        section_norm_cache[key] = (
            head_spaced,
            full_spaced,
            head_compact,
            full_compact,
            head_roots,
            full_roots,
        )
        d_id = _document_identity(section)
        prev_spaced, prev_compact, prev_roots = doc_texts.get(d_id, ("", "", set()))
        next_spaced = (
            f"{prev_spaced}{full_spaced[1:]}"
            if prev_spaced and full_spaced
            else (prev_spaced or full_spaced)
        )
        prev_roots.update(full_roots)
        doc_texts[d_id] = (next_spaced, f"{prev_compact}{full_compact}", prev_roots)

    doc_frozen_roots: dict[str, frozenset[str]] = {
        d_id: frozenset(d_roots) for d_id, (_s, _c, d_roots) in doc_texts.items()
    }
    all_distinct_terms = set(query_term_list)
    for ct in clause_term_lists:
        all_distinct_terms.update(ct)
    n_docs = max(1, len(doc_texts))
    term_weights: dict[str, float] = {}
    for term in all_distinct_terms:
        df = sum(
            1
            for d_id, (d_spaced, d_compact, _r) in doc_texts.items()
            if _term_matches_text(
                term,
                d_spaced,
                spaceless_text=d_compact,
                root_tokens=doc_frozen_roots[d_id],
            )
        )
        idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5)) + 1.0
        term_compact = term.replace(" ", "")
        if term in anchor_terms or any(
            term_compact in a.replace(" ", "") or a.replace(" ", "") in term_compact
            for a in anchor_terms
            if len(a) >= 4
        ):
            idf *= 1.65
        elif term in salient_terms or term_compact in salient_terms or any(c.isdigit() for c in term):
            idf *= 1.45
        term_weights[term] = idf

    from sag_api.services.query_analysis import _remove_query_noise

    spaced_query_phrase = _normalize_spaced_lexical_text(_remove_query_noise(query))
    top_semantic_score = max(semantic_scores.values(), default=0.0)
    lexical_scores: dict[tuple[str, str], float] = {}
    for key, section in merged.items():
        contextual_heading = " ".join(
            part
            for part in (section.document_name, section.section_path, section.heading)
            if part
        )
        contextual_section = (
            section.model_copy(update={"heading": contextual_heading})
            if contextual_heading and contextual_heading != section.heading
            else section
        )
        legacy_lex = max(
            _lexical_relevance(query, section, analysis=effective),
            _lexical_relevance(query, contextual_section, analysis=effective),
        )
        head_spaced, full_spaced, head_compact, full_compact, head_roots, full_roots = section_norm_cache[key]
        interaction_full = _score_term_sequence(
            query_term_list,
            text=full_spaced,
            heading=head_spaced,
            phrase=effective.normalized_phrase,
            spaced_phrase=spaced_query_phrase,
            term_weights=term_weights,
            raw_heading=contextual_heading or section.heading or "",
            spaceless_text=full_compact,
            spaceless_heading=head_compact,
            text_roots=full_roots,
            heading_roots=head_roots,
        )
        clause_best = (
            max(
                (
                    _score_term_sequence(
                        ct,
                        text=full_spaced,
                        heading=head_spaced,
                        phrase="",
                        spaced_phrase="",
                        term_weights=term_weights,
                        raw_heading=contextual_heading or section.heading or "",
                        spaceless_text=full_compact,
                        spaceless_heading=head_compact,
                        text_roots=full_roots,
                        heading_roots=head_roots,
                    )
                    for ct in clause_term_lists
                ),
                default=0.0,
            )
            if clause_term_lists
            else 0.0
        )
        lexical_scores[key] = max(
            legacy_lex,
            interaction_full,
            0.92 * clause_best,
        )

    top_lexical_score = max(lexical_scores.values(), default=0.0)
    interaction_lex_ranks: dict[tuple[str, str], int] = {
        k: idx
        for idx, (k, _val) in enumerate(
            sorted(lexical_scores.items(), key=lambda item: (-item[1], item[0]))
        )
    }
    active_retrievers = int(bool(semantic_ranks)) + int(bool(lexical_ranks))
    ideal_rrf_score = active_retrievers / (_RRF_K + 1)
    ranked: list[tuple[float, int, int, int, tuple[str, str], RetrievedSection]] = []

    for key in candidate_keys:
        section = merged[key]
        semantic_rank = semantic_ranks.get(key)
        lexical_rank = lexical_ranks.get(key)
        sem_eff = semantic_eff_ranks.get(key)
        lex_eff = lexical_eff_ranks.get(key)
        raw = semantic_scores.get(key, 0.0)
        relative_semantic_score = raw / top_semantic_score if top_semantic_score > 0 else 0.0
        lexical_score = lexical_scores[key]
        if (
            lex_eff is not None
            and top_lexical_score >= 0.65
            and lexical_score >= 0.95 * top_lexical_score
            and interaction_lex_ranks.get(key, 99) <= 1
        ):
            lex_eff = min(lex_eff, interaction_lex_ranks[key])
        exact = key in exact_keys
        lexical_match = lexical_rank is not None
        if _is_boilerplate(section) and not lexical_match and lexical_score < 0.35:
            continue
        reciprocal_rank_score = sum(
            1.0 / (_RRF_K + eff_rank + 1.0)
            for eff_rank in (sem_eff, lex_eff)
            if eff_rank is not None
        )
        fused_score = min(1.0, reciprocal_rank_score / ideal_rrf_score)
        hybrid_score = min(
            1.0,
            0.56 * fused_score + 0.32 * lexical_score + 0.12 * relative_semantic_score,
        )
        # Each candidate may pass through either retrieval signal. The relative
        # semantic gate stays active even when another candidate has a lexical hit.
        relevant = (
            lexical_match
            or lexical_score >= 0.2
            or relative_semantic_score >= _SEMANTIC_RELATIVE_RELEVANCE_FLOOR
        )
        if not relevant:
            continue
        rank_sum = sum(rank for rank in (semantic_rank, lexical_rank) if rank is not None)
        retriever_coverage = int(semantic_rank is not None) + int(lexical_rank is not None)
        ranked.append(
            (hybrid_score, retriever_coverage, int(exact), rank_sum, key, section)
        )

    query_lower = query.lower()
    allows_superseded = any(
        cue in query_lower
        for cue in (
            "superseded",
            "deprecated",
            "obsolete",
            "archived",
            "historical",
            "outdated",
            "earlier",
            "previous",
            "bản cũ",
            "đã thay thế",
            "trước khi điều chỉnh",
        )
    )
    if not allows_superseded and ranked:
        def _is_superseded(sec: RetrievedSection) -> bool:
            meta = f"{sec.document_name or ''}\n{sec.section_path or ''}\n{sec.heading or ''}".lower()
            if any(
                marker in meta
                for marker in (
                    "superseded",
                    "[deprecated]",
                    "(deprecated)",
                    "[obsolete]",
                    "(obsolete)",
                    "[archived]",
                    "(archived)",
                )
            ):
                return True
            body = (sec.content or "").lower()
            body_norm = normalize_lexical_text(body)
            return (
                'status: "superseded"' in body
                or "status: superseded" in body
                or "status: deprecated" in body
                or "statussuperseded" in body_norm
                or "statusdeprecated" in body_norm
            )

        active_ranked = [item for item in ranked if not _is_superseded(item[5])]
        if active_ranked:
            ranked = active_ranked

    ranked.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3], item[4]))
    selected = [
        section.model_copy(update={"score": round(score, 6), "rank": index})
        for index, (score, _coverage, _exact, _rank_sum, _key, section) in enumerate(
            ranked if limit is None else ranked[: max(1, limit)]
        )
    ]
    return RerankResult(
        sections=selected,
        candidate_count=len(candidate_keys),
        relevant_count=len(ranked),
        filtered_count=len(candidate_keys) - len(ranked),
        lexical_count=len(lexical),
    )


def _content_shingles(section: RetrievedSection) -> frozenset[str]:
    normalized = normalize_lexical_text(f"{section.heading}\n{section.content}")
    if len(normalized) > 4096:
        # Keep the similarity signature bounded while sampling the whole evidence;
        # prefix-only truncation can collapse sections with different conclusions.
        window_size = 1024
        last_start = len(normalized) - window_size
        starts = (0, last_start // 3, (last_start * 2) // 3, last_start)
        normalized = "\n".join(
            normalized[start : start + window_size]
            for start in dict.fromkeys(starts)
        )
    if len(normalized) < 3:
        return frozenset({normalized}) if normalized else frozenset()
    shingle_len = 6 if len(normalized) >= 6 else 3
    return frozenset(
        normalized[index : index + shingle_len]
        for index in range(len(normalized) - shingle_len + 1)
    )


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def select_diverse_sections(
    query: str,
    candidates: list[RetrievedSection],
    *,
    limit: int | None,
    diversity_weight: float | None = None,
    near_duplicate_similarity: float | None = None,
    per_document_limit: int | None = None,
    _cancelled: threading.Event | None = None,
) -> tuple[list[RetrievedSection], dict[str, Any]]:
    """Collapse near-identical text and apply deterministic MMR without dropping query anchors."""
    if per_document_limit is not None and per_document_limit < 1:
        raise ValueError("per_document_limit must be positive")
    unbounded_limit = limit is None
    if limit is None:
        limit = len(candidates)
    if limit <= 0 or not candidates:
        return [], {"candidate_count": len(candidates), "deduplicated_count": 0, "anchor_coverage": {}}

    diversity = (
        settings.search_mmr_diversity_weight
        if diversity_weight is None
        else float(diversity_weight)
    )
    duplicate_threshold = (
        settings.search_near_duplicate_similarity
        if near_duplicate_similarity is None
        else float(near_duplicate_similarity)
    )
    if not math.isfinite(diversity) or not 0.0 <= diversity <= 1.0:
        raise ValueError("MMR diversity weight must be between zero and one")
    if not math.isfinite(duplicate_threshold) or not 0.9 <= duplicate_threshold <= 1.0:
        raise ValueError("Near-duplicate similarity must be between 0.9 and one")

    features = extract_query_features(query)
    required = tuple(
        dict.fromkeys(
            value
            for term in (*features.exact_terms, *features.identifier_terms, *features.path_terms)
            if (value := normalize_lexical_text(term))
        )
    )

    def anchor_coverage(section: RetrievedSection) -> frozenset[str]:
        text = normalize_lexical_text(
            f"{section.document_name or ''}\n{section.section_path or ''}\n{section.heading}\n{section.content}"
        )
        matched: set[str] = set()
        for term in required:
            if term in text:
                matched.add(term)
            elif (m_num := re.search(r"\d{4,}", term)) and m_num.group(0) in text:
                matched.add(term)
        return frozenset(matched)

    def representative_key(section: RetrievedSection) -> tuple[Any, ...]:
        return (
            len(anchor_coverage(section)),
            float(section.score or 0.0),
            -int(section.rank or 0),
            section.source_id or "",
            section.document_version_id or "",
            section.chunk_id or "",
        )

    ordered = sorted(
        candidates,
        key=lambda section: (
            -float(section.score or 0.0),
            int(section.rank or 0),
            section.source_id or "",
            section.chunk_id or "",
        ),
    )
    unique: list[RetrievedSection] = []
    unique_shingles: list[frozenset[str]] = []
    unique_coverage: list[frozenset[str]] = []
    def check_cancelled() -> None:
        if _cancelled is not None and _cancelled.is_set():
            raise InterruptedError("Diversity selection cancelled")

    for section in ordered:
        check_cancelled()
        shingles = _content_shingles(section)
        coverage = anchor_coverage(section)
        duplicate_index = None
        for index, previous in enumerate(unique_shingles):
            if index % 32 == 0:
                check_cancelled()
            if coverage == unique_coverage[index] and _jaccard(shingles, previous) >= duplicate_threshold:
                duplicate_index = index
                break
        if duplicate_index is None:
            unique.append(section)
            unique_shingles.append(shingles)
            unique_coverage.append(coverage)
        elif representative_key(section) > representative_key(unique[duplicate_index]):
            unique[duplicate_index] = section
            unique_shingles[duplicate_index] = shingles

    def doc_key(section: RetrievedSection) -> str:
        return str(
            section.document_id
            or section.document_version_id
            or section.document_name
            or section.chunk_id
            or ""
        )

    selected_indices: list[int] = []
    selected_anchors: set[str] = set()
    for anchor in required:
        if anchor in selected_anchors or len(selected_indices) >= limit:
            continue
        best = next(
            (
                index
                for index, coverage in enumerate(unique_coverage)
                if anchor in coverage and index not in selected_indices and (len(unique) <= limit or float(unique[index].score or 0.0) >= min(0.55, 0.75 * float(unique[0].score or 0.0)))
            ),
            None,
        )
        if best is not None:
            selected_indices.append(best)
            selected_anchors.update(unique_coverage[best])

    if required:
        for idx, cov in enumerate(unique_coverage):
            if len({doc_key(unique[i]) for i in selected_indices}) >= min(limit, 2) or len(selected_indices) >= limit:
                break
            if not cov or idx in selected_indices or float(unique[idx].score or 0.0) < 0.55:
                continue
            if doc_key(unique[idx]) not in {doc_key(unique[i]) for i in selected_indices}:
                selected_indices.append(idx)
                selected_anchors.update(cov)

    ordered_docs: list[str] = []
    doc_best_idx: dict[str, int] = {}
    doc_best_score: dict[str, float] = {}
    doc_chunk_counts: dict[str, int] = {}
    for idx, sec in enumerate(unique):
        d_key = doc_key(sec)
        doc_chunk_counts[d_key] = doc_chunk_counts.get(d_key, 0) + 1
        if d_key not in doc_best_idx:
            ordered_docs.append(d_key)
            doc_best_idx[d_key] = idx
            doc_best_score[d_key] = float(sec.score or 0.0)

    top_doc_score = doc_best_score.get(ordered_docs[0], 0.0) if ordered_docs else 0.0
    admission_ratio = (
        float(settings.search_doc_admission_ratio)
        if unbounded_limit
        else min(0.75, float(settings.search_doc_admission_ratio))
        if features.multi_hop or per_document_limit == 1
        else max(0.94, float(settings.search_doc_admission_ratio))
    )
    salient_doc_terms = {
        term for term in extract_salient_query_terms(query) if len(term) >= 3
    }
    admitted_docs: set[str] = {doc_key(unique[i]) for i in selected_indices}
    if ordered_docs:
        admitted_docs.add(ordered_docs[0])
        allow_wide_docs = unbounded_limit or features.multi_hop or per_document_limit == 1
        max_initial_docs = limit if allow_wide_docs else min(limit, 2)
        for doc_rank_idx, d_key in enumerate(ordered_docs[1:], start=1):
            if len(admitted_docs) >= max_initial_docs:
                break
            d_score = doc_best_score.get(d_key, 0.0)
            eff_ratio = (
                admission_ratio
                if (
                    unbounded_limit
                    or (not features.multi_hop and per_document_limit != 1)
                    or doc_rank_idx == 1
                    or len(unique) <= limit + 1
                )
                else max(admission_ratio, 0.82)
            )
            doc_name_tokens = {
                normalize_lexical_text(tok)
                for tok in re.findall(
                    r"[^\W_]+",
                    str(unique[doc_best_idx[d_key]].document_name or ""),
                    flags=re.UNICODE,
                )
            }
            salient_doc_matches = salient_doc_terms & doc_name_tokens if salient_doc_terms else set()
            matches_salient_doc_name = (
                bool(salient_doc_matches)
                if allow_wide_docs
                else len(salient_doc_matches) >= 2
            )
            salient_doc_floor = (
                max(0.35, round(0.72 * top_doc_score, 6))
                if allow_wide_docs
                else max(0.55, round(0.88 * top_doc_score, 6))
            )
            if d_score >= max(0.20, round(eff_ratio * top_doc_score, 6)) or (
                matches_salient_doc_name and d_score >= salient_doc_floor
            ):
                admitted_docs.add(d_key)

    def can_select_from_doc(d_key: str) -> bool:
        if per_document_limit is None:
            return True
        chosen_for_doc = sum(1 for chosen in selected_indices if doc_key(unique[chosen]) == d_key)
        return chosen_for_doc < per_document_limit

    initial_admitted_docs = set(admitted_docs)
    for d_key in ordered_docs:
        if len(selected_indices) >= limit:
            break
        if d_key not in admitted_docs or not can_select_from_doc(d_key):
            continue
        if any(doc_key(unique[i]) == d_key for i in selected_indices):
            continue
        b_idx = doc_best_idx[d_key]
        if b_idx not in selected_indices:
            selected_indices.append(b_idx)
            selected_anchors.update(unique_coverage[b_idx])

    fallback_doc_floor = max(0.20, admission_ratio * top_doc_score)
    same_doc_penalty_weight = (
        max(0.08, float(settings.search_same_doc_penalty_weight))
        if features.multi_hop
        else float(settings.search_same_doc_penalty_weight)
    )
    compared = [0] * len(unique)
    redundancy = [0.0] * len(unique)

    def max_redundancy(index: int) -> float:
        # Each pair is unchanged across MMR rounds; compare only newly selected evidence.
        for offset, chosen in enumerate(selected_indices[compared[index]:]):
            if offset % 32 == 0:
                check_cancelled()
            redundancy[index] = max(redundancy[index], _jaccard(unique_shingles[index], unique_shingles[chosen]))
        compared[index] = len(selected_indices)
        return redundancy[index]

    while len(selected_indices) < min(limit, len(unique)):
        check_cancelled()
        remaining = [
            index
            for index in range(len(unique))
            if index not in selected_indices
            and doc_key(unique[index]) in admitted_docs
            and can_select_from_doc(doc_key(unique[index]))
            and (
                doc_key(unique[index]) in initial_admitted_docs
                or index == doc_best_idx.get(doc_key(unique[index]))
            )
        ]
        if not remaining:
            all_initial_single_chunk = (
                per_document_limit == 1
                or all(doc_chunk_counts.get(d, 0) <= 1 for d in initial_admitted_docs)
            )
            allow_wide_sample = (
                unbounded_limit
                or features.multi_hop
                or per_document_limit == 1
                or len(unique) <= limit + 1
            )
            eff_floor = (
                max(0.20, 0.40 * top_doc_score)
                if (per_document_limit == 1 and all_initial_single_chunk)
                else fallback_doc_floor
                if (unbounded_limit or per_document_limit == 1 or (features.multi_hop and len(unique) <= limit + 1))
                else max(0.45, 0.78 * top_doc_score)
                if allow_wide_sample
                else max(0.55, 0.88 * top_doc_score)
            )
            next_doc = next(
                (
                    d_key
                    for d_key in ordered_docs
                    if d_key not in admitted_docs
                    and can_select_from_doc(d_key)
                    and doc_best_score.get(d_key, 0.0) >= eff_floor
                ),
                None,
            )
            if next_doc is not None:
                admitted_docs.add(next_doc)
                remaining = [
                    index
                    for index in range(len(unique))
                    if index not in selected_indices
                    and doc_key(unique[index]) in admitted_docs
                    and can_select_from_doc(doc_key(unique[index]))
                    and (
                        doc_key(unique[index]) in initial_admitted_docs
                        or index == doc_best_idx.get(doc_key(unique[index]))
                    )
                ]
            elif allow_wide_sample:
                remaining = [
                    index
                    for index in range(len(unique))
                    if index not in selected_indices
                    and doc_key(unique[index]) in admitted_docs
                    and can_select_from_doc(doc_key(unique[index]))
                ]
            if not remaining:
                break
        best = max(
            remaining,
            key=lambda index: (
                (1.0 - diversity) * max(0.0, min(1.0, float(unique[index].score or 0.0)))
                - diversity
                * (
                    max_redundancy(index)
                    ** 2
                )
                - (
                    same_doc_penalty_weight
                    * sum(
                        1
                        for chosen in selected_indices
                        if doc_key(unique[chosen]) == doc_key(unique[index])
                    )
                ),
                -int(unique[index].rank or 0),
                -index,
            ),
        )
        selected_indices.append(best)
        selected_anchors.update(unique_coverage[best])

    selected = [unique[index].model_copy(update={"rank": rank}) for rank, index in enumerate(selected_indices)]
    covered = set().union(*(anchor_coverage(section) for section in selected)) if selected else set()
    return selected, {
        "candidate_count": len(candidates),
        "deduplicated_count": len(candidates) - len(unique),
        "diversity_weight": diversity,
        "near_duplicate_similarity": duplicate_threshold,
        "required_anchor_count": len(required),
        "covered_anchor_count": len(covered),
        "anchor_coverage": (
            "not_required"
            if not required
            else "complete"
            if set(required) <= covered
            else "missing_required_anchor"
        ),
    }


async def _lexical_sections(
    engine_manager: Any,
    sources: list[SearchSource],
    *,
    analysis: QueryAnalysis,
    candidate_limit: int,
    exclude_source_ids_by_config: DocumentSourceExclusions | None = None,
) -> tuple[list[RetrievedSection], set[tuple[str, str]]]:
    grep_chunks = getattr(engine_manager, "grep_chunks", None)
    terms = analysis.lookup_terms
    if not callable(grep_chunks) or not terms:
        return [], set()

    semaphore = asyncio.Semaphore(max(1, settings.search_source_concurrency))
    expanded_keys = {
        normalize_lexical_text(term)
        for term in analysis.expanded_terms
    }

    async def one(
        source: SearchSource,
        term: str,
    ) -> tuple[list[RetrievedSection], bool]:
        async with semaphore:
            try:
                options: dict[str, Any] = {
                    "source": source,
                    "limit": candidate_limit,
                }
                if (
                    getattr(
                        engine_manager,
                        "supports_document_source_exclusions",
                        False,
                    )
                    and exclude_source_ids_by_config
                ):
                    options["exclude_source_ids"] = (
                        exclude_source_ids_by_config.get(
                            source.sag_source_config_id,
                            (),
                        )
                    )
                rows = await grep_chunks(
                    source.sag_source_config_id,
                    term,
                    **options,
                )
            except Exception:  # noqa: BLE001
                return [], False
        sections = [
            RetrievedSection(
                chunk_id=row.get("chunk_id"),
                heading=row.get("heading") or "Exact match",
                content=row.get("snippet") or "",
                score=max(0.8, 1.0 - index * 0.02),
                rank=index,
                source_id=row.get("source_id"),
                source_config_id=source.sag_source_config_id,
            )
            for index, row in enumerate(rows)
        ]
        return sections, normalize_lexical_text(term) not in expanded_keys

    groups = await asyncio.gather(*(one(source, term) for source in sources for term in terms))
    sections = [section for group, _strong in groups for section in group]
    exact_keys = {
        _section_key(section)
        for group, strong in groups
        if strong
        for section in group
    }
    return sections, exact_keys


async def retrieve_relevant_sections(
    engine_manager: Any,
    sources: list[SearchSource],
    query: str,
    *,
    strategy: str | None = None,
    top_k: int | None = None,
) -> SearchOutcome:
    """One retrieval contract for search UI and the Agent's search_context tool."""
    from sag_api.services.retrieval_plan import TargetUnit, build_retrieval_plan, group_sections_by_document

    total_start = time.perf_counter()
    analysis = analyze_query(
        query,
        segmentation_enabled=settings.search_chinese_segmentation_enabled,
    )
    retrieval_plan = build_retrieval_plan(
        query,
        requested_top_k=top_k,
        clauses=analysis.features.clauses,
        multi_hop=analysis.features.multi_hop,
    )
    effective_top_k = retrieval_plan.cardinality.effective_n
    requested_limit = min(effective_top_k, settings.search_unit_candidate_limit)
    candidate_limit = min(settings.search_unit_candidate_limit, max(requested_limit * 4, 20))
    targets = [(source.sag_source_config_id, source) for source in sources]
    engine_start = time.perf_counter()
    hidden = await _hidden_document_derivatives(sources)
    exclusions = _document_source_exclusions(hidden)
    search_options: dict[str, Any] = {
        "strategy": strategy,
        "top_k": candidate_limit,
    }
    if (
        getattr(
            engine_manager,
            "supports_document_source_exclusions",
            False,
        )
        and exclusions
    ):
        search_options["exclude_source_ids_by_config"] = exclusions
    outcome, lexical_recall = await asyncio.gather(
        engine_manager.search_many(targets, query, **search_options),
        _lexical_sections(
            engine_manager,
            sources,
            analysis=analysis,
            candidate_limit=candidate_limit,
            exclude_source_ids_by_config=exclusions,
        ),
    )
    lexical, exact_lexical_keys = lexical_recall
    # A delete can commit while either engine query is in flight. Re-read the
    # persisted barrier before returning evidence so the delete is linearized.
    hidden = await _hidden_document_derivatives(sources)
    engine_latency_ms = round((time.perf_counter() - engine_start) * 1000, 2)
    def visible(section: RetrievedSection) -> bool:
        if section.source_id:
            return section.source_id not in hidden.source_ids
        return section.source_config_id not in hidden.source_config_ids

    semantic_sections = [section for section in outcome.sections if visible(section)]
    lexical_sections = [section for section in lexical if visible(section)]
    hidden_count = len(outcome.sections) + len(lexical) - len(semantic_sections) - len(lexical_sections)
    rerank_start = time.perf_counter()
    rerank_pool_limit = (
        candidate_limit
        if retrieval_plan.target == TargetUnit.DOCUMENT
        else requested_limit
    )
    reranked = rerank_sections(
        query,
        semantic_sections,
        lexical=lexical_sections,
        exact_lexical_keys=exact_lexical_keys,
        limit=rerank_pool_limit,
        analysis=analysis,
    )
    final_sections = (
        group_sections_by_document(reranked.sections, limit=requested_limit)
        if retrieval_plan.target == TargetUnit.DOCUMENT
        else reranked.sections[:requested_limit]
    )
    rerank_latency_ms = round((time.perf_counter() - rerank_start) * 1000, 2)
    total_latency_ms = round((time.perf_counter() - total_start) * 1000, 2)
    stats = {
        **outcome.stats,
        "requested_top_k": requested_limit,
        "automatic_top_k": top_k is None and retrieval_plan.cardinality.count_source.value != "DEFAULT",
        "effective_top_k": len(final_sections),
        "retrieval_plan": retrieval_plan.as_trace(),
        "budget_exhausted": (
            len(final_sections) >= settings.search_unit_candidate_limit
            or len(outcome.sections) >= settings.search_unit_candidate_limit
            or len(lexical) >= settings.search_unit_candidate_limit
        ),
        "candidate_top_k": candidate_limit,
        "candidates": reranked.candidate_count,
        "relevant": reranked.relevant_count,
        "filtered_irrelevant": reranked.filtered_count,
        "lexical_candidates": reranked.lexical_count,
        "semantic_candidates": len(semantic_sections),
        "fusion_method": "rrf",
        "fusion_retrievers": int(bool(semantic_sections)) + int(bool(lexical_sections)),
        "chinese_segmentation_used": analysis.chinese_segmentation_used,
        "lexical_term_count": len(analysis.lookup_terms),
        "has_more": False,
        "logically_deleted_filtered": hidden_count,
        "latency_engine_ms": engine_latency_ms,
        "latency_rerank_ms": rerank_latency_ms,
        "latency_total_ms": total_latency_ms,
    }
    return SearchOutcome(
        query=outcome.query or query,
        sections=final_sections,
        stats=stats,
    )


async def recall_event_scores(
    engine_manager: Any,
    query: str,
    sources_by_config: dict[str, SearchSource],
    *,
    limit: int | None = None,
) -> EventScoreMap:
    """Best-effort direct event recall shared by Search and the Agent.

    Chunks remain the traceable evidence path.  Event recall supplies the
    semantic result layer (title + summary) so a long document does not lose
    its extracted events merely because the best matching chunks are located
    elsewhere in the document.
    """

    search = getattr(engine_manager, "search_event_scores", None)
    if not callable(search):
        return {}
    hidden = await _hidden_document_derivatives(list(sources_by_config.values()))
    try:
        result = await search(query, sources_by_config, limit=limit)
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001
        log.warning("事项向量召回失败，继续使用原文块结果：%s", error)
        return {}
    if not isinstance(result, dict):
        return {}

    scores: EventScoreMap = {}
    for raw_key, raw_score in result.items():
        if not isinstance(raw_key, tuple) or len(raw_key) != 2:
            continue
        source_config_id = str(raw_key[0] or "").strip()
        event_id = str(raw_key[1] or "").strip()
        if not source_config_id or not event_id:
            continue
        if (source_config_id, event_id) in hidden.event_keys:
            continue
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            continue
        scores[(source_config_id, event_id)] = max(0.0, min(1.0, score))
    return scores


def _best_excerpt(
    query: str,
    section: RetrievedSection,
    limit: int = 260,
    *,
    analysis: QueryAnalysis | None = None,
) -> str:
    effective = analysis or analyze_query(
        query,
        segmentation_enabled=settings.search_chinese_segmentation_enabled,
    )
    content = re.sub(r"\s+", " ", section.content).strip()
    if not content:
        return section.heading.strip()
    sentences = [part.strip() for part in re.split(r"(?<=[。！？.!?])", content) if part.strip()]
    terms = [normalize_lexical_text(term) for term in effective.scoring_terms]
    best = max(
        sentences or [content],
        key=lambda sentence: sum(term in normalize_lexical_text(sentence) for term in terms),
    )
    return best[:limit] + ("…" if len(best) > limit else "")


def fallback_search_answer(query: str, sections: list[RetrievedSection]) -> str:
    if not sections:
        return ""
    analysis = analyze_query(
        query,
        segmentation_enabled=settings.search_chinese_segmentation_enabled,
    )
    lines = [
        f"- {_best_excerpt(query, section, analysis=analysis)} [{index}]"
        for index, section in enumerate(sections[:4], 1)
    ]
    return "Based on directly relevant evidence:\n" + "\n".join(lines)


_MULTI_CITATION_BRACKET_RE = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)+)]")
_ABSTENTION_PREFIX_RE = re.compile(
    r"^\s*(?:insufficient[_\s-]evidence\b|"
    r"the\s+(?:provided\s+|available\s+|retrieved\s+)?(?:evidence|documents?|context|sources?)\s+"
    r"(?:do(?:es)?\s+not|cannot)\s+(?:contain|specify|mention|provide|include|state|answer)\b|"
    r"based\s+on\s+the\s+(?:provided\s+|available\s+)?(?:evidence|documents?|context),\s+there\s+is\s+no\s+(?:information|mention)\b|"
    r"(?:bằng\s+chứng|tài\s+liệu)\s+(?:được\s+cung\s+cấp\s+)?không\s+(?:chứa|đề\s+cập|nêu|có\s+thông\s+tin)\b)",
    re.IGNORECASE,
)
_UNGROUNDED_REFUSAL_CUES = (
    "does not contain",
    "does not specify",
    "does not mention",
    "does not provide",
    "do not contain",
    "do not specify",
    "do not mention",
    "not mentioned in the provided",
    "not specified in the provided",
    "cannot be determined from the provided",
    "insufficient evidence",
    "không có thông tin",
    "không đề cập",
)


def _normalize_citation_brackets(text: str) -> str:
    return _MULTI_CITATION_BRACKET_RE.sub(
        lambda m: " ".join(f"[{num.strip()}]" for num in re.split(r"[,;]", m.group(1)) if num.strip()),
        text,
    )


def _is_abstention_response(answer: str) -> bool:
    text = answer.strip()
    if not text:
        return False
    lowered = text.lower()
    if "insufficient_evidence" in lowered or _ABSTENTION_PREFIX_RE.search(text):
        return True
    normalized = _normalize_citation_brackets(text)
    if not _CITATION_RE.findall(normalized) and any(cue in lowered for cue in _UNGROUNDED_REFUSAL_CUES):
        return True
    return False


def _validated_answer(answer: str, section_count: int) -> str | None:
    text = _normalize_citation_brackets(answer.strip())
    if not text:
        return None
    references = [int(value) for value in _CITATION_RE.findall(text)]
    valid_refs = [value for value in references if 1 <= value <= section_count]
    if not valid_refs:
        return None
    if len(valid_refs) != len(references):
        text = _CITATION_RE.sub(
            lambda m: m.group(0) if 1 <= int(m.group(1)) <= section_count else "",
            text,
        ).strip()
    return text


def _citations_used_by_answer(answer: str, citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    numbers = {int(value) for value in _CITATION_RE.findall(_normalize_citation_brackets(answer))}

    return [citation for citation in citations if citation.get("n") in numbers]


def _build_search_citations(
    sections: list[RetrievedSection],
    sources: list[SearchSource],
) -> list[dict[str, Any]]:
    from sag_api.generation import build_citations

    refs = {
        source.sag_source_config_id: {"id": source.id, "name": source.name}
        for source in sources
    }
    return build_citations(sections, refs)


@dataclass(frozen=True, slots=True)
class SearchAnswerUpdate:
    kind: Literal["delta", "completed"]
    text: str
    status: Literal["answered", "no_answer"] = "answered"
    no_answer_reason: str | None = None
    citations: list[dict[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class SearchAnswer:
    text: str
    citations: list[dict[str, Any]]
    status: Literal["answered", "no_answer", "skipped"]
    no_answer_reason: str | None = None


async def synthesize_search_answer(
    query: str,
    sections: list[RetrievedSection],
    *,
    llm: Any | None,
    sources: list[SearchSource] | None = None,
) -> SearchAnswer:
    """Answer the actual question from selected evidence; never summarize the raw candidate pool."""

    verified_sections = await resolve_traceable_evidence(sections, sources or []) if sources is not None else sections
    pack = build_evidence_pack(query, verified_sections)
    if pack.status != "sufficient":
        return SearchAnswer(no_answer_text(), [], "no_answer", pack.no_answer_reason)

    citations = _build_search_citations(pack.sections, sources or [])
    if len(citations) != len(pack.sections) or any(not citation.get("source_id") for citation in citations):
        return SearchAnswer(no_answer_text(), [], "no_answer", "weak_evidence")
    fallback = fallback_search_answer(query, pack.sections)
    if llm is None or not getattr(llm, "configured", False):
        return SearchAnswer(fallback, _citations_used_by_answer(fallback, citations), "answered")

    from sag_api.core.token_usage import token_usage_scope

    try:
        with token_usage_scope(component="answer_generation"):
            answer = await llm.complete(pack.messages)
    except Exception as error:  # noqa: BLE001
        log.warning("搜索答案生成失败，回退证据摘要：%s", error)
        return SearchAnswer(fallback, _citations_used_by_answer(fallback, citations), "answered")
    if _is_abstention_response(answer):
        return SearchAnswer(no_answer_text(), [], "no_answer", "insufficient_evidence")
    validated = _validated_answer(answer, len(pack.sections))
    canonical = validated or fallback
    return SearchAnswer(canonical, _citations_used_by_answer(canonical, citations), "answered")


async def stream_synthesize_search_answer(
    query: str,
    sections: list[RetrievedSection],
    *,
    llm: Any | None,
    sources: list[SearchSource] | None = None,
) -> AsyncIterator[SearchAnswerUpdate]:
    """Buffer provider output; only publish a canonical, provenance-backed answer."""

    verified_sections = await resolve_traceable_evidence(sections, sources or []) if sources is not None else sections
    pack = build_evidence_pack(query, verified_sections)
    if pack.status != "sufficient":
        yield SearchAnswerUpdate(
            kind="completed",
            text=no_answer_text(),
            status="no_answer",
            no_answer_reason=pack.no_answer_reason,
            citations=[],
        )
        return

    citations = _build_search_citations(pack.sections, sources or [])
    if len(citations) != len(pack.sections) or any(not citation.get("source_id") for citation in citations):
        yield SearchAnswerUpdate(
            kind="completed",
            text=no_answer_text(),
            status="no_answer",
            no_answer_reason="weak_evidence",
            citations=[],
        )
        return
    fallback = fallback_search_answer(query, pack.sections)
    if llm is None or not getattr(llm, "configured", False):
        used_citations = _citations_used_by_answer(fallback, citations)
        yield SearchAnswerUpdate(kind="delta", text=fallback)
        yield SearchAnswerUpdate(kind="completed", text=fallback, citations=used_citations)
        return

    from sag_api.core.token_usage import token_usage_scope

    parts: list[str] = []
    try:
        with token_usage_scope(component="answer_generation"):
            async for delta in llm.stream_complete(pack.messages):
                if delta:
                    parts.append(delta)
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001
        log.warning("搜索答案流生成失败，回退证据摘要：%s", error)
        fallback_citations = _citations_used_by_answer(fallback, citations)
        yield SearchAnswerUpdate(
            kind="completed",
            text=fallback,
            citations=fallback_citations,
        )
        return

    raw_answer = "".join(parts)
    if _is_abstention_response(raw_answer):
        yield SearchAnswerUpdate(
            kind="completed",
            text=no_answer_text(),
            status="no_answer",
            no_answer_reason="insufficient_evidence",
            citations=[],
        )
        return
    validated = _validated_answer(raw_answer, len(pack.sections))
    answer = validated or fallback
    used_citations = _citations_used_by_answer(answer, citations)
    # Provider deltas remain private until citation syntax is checked against
    # this exact pack. Only the canonical text is sent as a single delta.
    yield SearchAnswerUpdate(kind="delta", text=answer)
    yield SearchAnswerUpdate(kind="completed", text=answer, citations=used_citations)
