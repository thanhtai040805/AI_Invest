"""Dedup & Temporal Service for SAG Routing RAG (Phase 2B).

Follows Ponytail minimalism:
- 4-tier deduplication (File exact hash, block exact hash, MinHash/LSH near-dedup, semantic candidate guard).
- Block-type specific similarity thresholds: 0.85 for narrative text, 0.95 for code and tables.
- 5 semantic relations: EQUIVALENT, SUPPORTS, CONTRADICTS, SUPERSEDES, RELATED.
- Strict non-auto-merge for CONTRADICTS / SUPERSEDES (candidates only with evidence mapping).
- Non-overlapping bi-temporal validity tracking: selects active predecessor by temporal validity interval
  (valid_from <= cur_start < valid_to) rather than naive version_no, preventing validity overlap on out-of-order ingest.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import re
import uuid
from collections import OrderedDict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from weakref import WeakKeyDictionary

from sqlalchemy import desc, inspect, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.logging import get_logger
from sag_api.db.models.document import Document
from sag_api.db.models.routing_rag import (
    CanonicalBlock,
    DocumentVersion,
    KnowledgeGraphEdge,
    StageRun,
)

log = get_logger("services.dedup_temporal")

# Standard edge relation taxonomy for Phase 2B
RELATION_EQUIVALENT = "EQUIVALENT"
RELATION_SUPPORTS = "SUPPORTS"
RELATION_CONTRADICTS = "CONTRADICTS"
RELATION_SUPERSEDES = "SUPERSEDES"
RELATION_RELATED = "RELATED"

VALID_RELATIONS = {
    RELATION_EQUIVALENT,
    RELATION_SUPPORTS,
    RELATION_CONTRADICTS,
    RELATION_SUPERSEDES,
    RELATION_RELATED,
}


_MINHASH_PREFIXES = tuple(hashlib.md5(f"{i}:".encode()) for i in range(256))


def compute_minhash_signature(tokens: Sequence[str], num_perm: int = 128) -> list[int]:
    """Compute 128-permutation MinHash signature for token k-shingles (k=5 per Phase 2B plan)."""
    if not tokens:
        return [0] * num_perm
    k = min(5, len(tokens))
    shingles = {" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}
    if not shingles:
        shingles = set(tokens)

    encoded = [shingle.encode() for shingle in shingles]
    from_bytes = int.from_bytes
    prefixes = _MINHASH_PREFIXES
    n_prefixes = len(prefixes)
    signature = []
    for i in range(num_perm):
        # 32-bit unsigned sentinel (0xFFFFFFFF) covering the full 8-hex-char MD5 space
        min_val = 0xFFFFFFFF
        prefix = prefixes[i] if i < n_prefixes else hashlib.md5(f"{i}:".encode())
        prefix_copy = prefix.copy
        for shingle in encoded:
            digest = prefix_copy()
            digest.update(shingle)
            h = from_bytes(digest.digest()[:4], "big")
            if h < min_val:
                min_val = h
        signature.append(min_val)
    return signature


_MINHASH_TOKEN_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u0e00-\u0eff\u1780-\u17ff\u1000-\u109f]"
    r"|\w+"
)


def tokenize_for_minhash(text: str) -> list[str]:
    """Tokenize text into universal lexical units for 5-shingle MinHash.

    Continuous scripts without whitespace word boundaries (CJK, Kana, Thai,
    Lao, Khmer, Myanmar) emit individual characters so k=5 forms overlapping
    5-character shingles; spaced scripts emit alphanumeric word tokens so k=5
    forms overlapping 5-word shingles.
    """
    if not text:
        return []
    return _MINHASH_TOKEN_RE.findall(text.casefold())


_MINHASH_CACHE: OrderedDict[str, list[int]] = OrderedDict()
_MINHASH_LOCKS = WeakKeyDictionary()
_MINHASH_ALGORITHM = "md5-128-k5-v2"


async def _block_signatures(blocks, *, persist_ids):
    """Cache derived signatures without changing canonical text/hash/provenance."""
    from sag_api.services.cpu_worker import run_cpu

    lock = _MINHASH_LOCKS.setdefault(asyncio.get_running_loop(), asyncio.Lock())
    async with lock:
        keys = {b.id: hashlib.sha256(b.normalized_text.encode()).hexdigest() for b in blocks}
        found, missing = {}, {}
        for block in blocks:
            key = keys[block.id]
            stored = block.dedup_signature_json if isinstance(block.dedup_signature_json, dict) else {}
            signature = stored.get("signature")
            if (stored.get("algorithm") == _MINHASH_ALGORITHM and stored.get("content_hash") == key
                    and isinstance(signature, list) and len(signature) == 128
                    and all(type(v) is int and 0 <= v <= 0xFFFFFFFF for v in signature)
                    and stored.get("checksum") == _signature_checksum(signature)):
                found[key] = signature
            elif key in _MINHASH_CACHE:
                found[key] = _MINHASH_CACHE[key]
                _MINHASH_CACHE.move_to_end(key)
            else:
                missing[key] = block.normalized_text
        if missing:
            found.update(await run_cpu("minhash", {"texts": missing}))
        for key, signature in found.items():
            _MINHASH_CACHE[key] = signature
            _MINHASH_CACHE.move_to_end(key)
        while len(_MINHASH_CACHE) > 8192:
            _MINHASH_CACHE.popitem(last=False)
        for block in blocks:
            if block.id in persist_ids:
                block.dedup_signature_json = {"algorithm": _MINHASH_ALGORITHM, "content_hash": keys[block.id],
                                              "signature": found[keys[block.id]],
                                              "checksum": _signature_checksum(found[keys[block.id]])}
        return {b.id: found[keys[b.id]] for b in blocks}


def _signature_checksum(signature):
    return hashlib.sha256(b"".join(value.to_bytes(4, "big") for value in signature)).hexdigest()


def estimate_minhash_similarity(sig1: Sequence[int], sig2: Sequence[int]) -> float:
    """Estimate Jaccard similarity between two MinHash signatures."""
    if not sig1 or not sig2 or len(sig1) != len(sig2):
        return 0.0
    matches = sum(1 for a, b in zip(sig1, sig2, strict=True) if a == b)
    return matches / len(sig1)


def compute_cosine_similarity(vec1: Sequence[float], vec2: Sequence[float]) -> float:
    """Compute cosine similarity between two float vectors for Tier 4 semantic evaluation."""
    import math
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(vec1, vec2, strict=True))
    norm1 = math.sqrt(sum(float(a) * float(a) for a in vec1))
    norm2 = math.sqrt(sum(float(b) * float(b) for b in vec2))
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return dot / (norm1 * norm2)


def compute_text_similarity(text1: str, text2: str) -> float:
    """Compute lexical similarity using universal tokenization and MinHash estimator.

    For short/medium passages (< 80 tokens) where a 1-token edit invalidates up to
    k=5 overlapping shingles (artificially depressing 5-shingle Jaccard to (N-9)/(N+1)),
    applies exact token-sequence alignment when MinHash already confirms >= 0.35 overlap.
    """
    t1 = tokenize_for_minhash(text1)
    t2 = tokenize_for_minhash(text2)
    if not t1 or not t2:
        return 0.0
    sig1 = compute_minhash_signature(t1, num_perm=128)
    sig2 = compute_minhash_signature(t2, num_perm=128)
    sim = estimate_minhash_similarity(sig1, sig2)
    if 0.35 <= sim < 0.85 and min(len(t1), len(t2)) < 80:
        sim = max(sim, difflib.SequenceMatcher(None, t1, t2, autojunk=False).ratio())
    return sim


# ---------------------------------------------------------------------------
# Pillar 3: Symmetric Structural Diff (Delta) for Relation Classification
# ---------------------------------------------------------------------------
_THOUSANDS_COMMA_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_EN_WON_T_RE = re.compile(r"\bwon['’]t\b")
_EN_CAN_T_RE = re.compile(r"\b(?:can['’]t|cannot)\b")
_EN_N_T_RE = re.compile(r"\b(\w+?)n['’]t\b")
_DIFF_TOKEN_RE = re.compile(
    r"v\d+(?:\.\d+)+"
    r"|\d+(?:[./:-]\d+)+%?"
    r"|\d+(?:\.\d+)?%?"
    r"|[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u0e00-\u0eff\u1780-\u17ff\u1000-\u109f]"
    r"|\w+"
)
_QUANT_TOKEN_RE = re.compile(r"^(?:v\d+(?:\.\d+)+|\d+(?:[./:-]\d+)*%?)$")

_SCALE_UNITS: dict[str, str] = {
    "b": "b", "bytes": "b", "byte": "b",
    "kb": "kb", "kib": "kib",
    "mb": "mb", "megabyte": "mb", "megabytes": "mb", "mib": "mib",
    "gb": "gb", "gigabyte": "gb", "gigabytes": "gb", "gib": "gib",
    "tb": "tb", "tib": "tib",
    "ms": "ms", "millisecond": "ms", "milliseconds": "ms",
    "s": "s", "sec": "s", "secs": "s", "second": "s", "seconds": "s", "giây": "s", "秒": "s",
    "m": "min", "min": "min", "mins": "min", "minute": "min", "minutes": "min", "phút": "min", "分": "min",
    "h": "hr", "hr": "hr", "hrs": "hr", "hour": "hr", "hours": "hr", "giờ": "hr", "時間": "hr", "小时": "hr",
    "d": "day", "day": "day", "days": "day", "ngày": "day", "日": "day", "天": "day",
}

# Compact universal polarity operators evaluated ONLY inside narrow edit hunks
_POLARITY_PARTICLES: frozenset[str] = frozenset({
    "not", "no", "never", "non", "none", "neither", "nor", "without",
    "không", "chẳng", "chưa", "cấm",
    "не", "ни", "без", "нет",
    "nicht", "kein", "keine", "keinen", "keinem", "keiner", "ohne",
    "pas", "jamais", "sans", "aucun", "aucune",
    "nunca", "jamás", "jamas", "sin",
    "mai", "senza", "niet", "geen", "nie", "bez",
    "لا", "غير", "بدون", "नहीं", "बिना",
    "ไม่", "ห้าม", "មិន", "ឥត", "မ",
    "不", "非", "無", "无", "未", "勿", "莫", "否",
    "안", "못", "불", "비", "무", "미",
})

_CONTINUOUS_NEG_MARKERS: frozenset[str] = frozenset({
    "ไม่", "ไม่ได้", "ห้าม", "មិន", "ឥត",
    "不", "非", "無", "无", "未", "否", "不可", "不得", "禁止", "严禁", "嚴禁",
    "불", "비", "무", "미", "불가", "금지",
})

_NEUTRAL_AUX: frozenset[str] = frozenset({
    "is", "are", "was", "were", "be", "been", "being", "am",
    "can", "could", "will", "would", "shall", "should", "must", "may", "might",
    "do", "does", "did", "have", "has", "had",
    "được", "bị", "có", "là", "đã", "sẽ", "đang", "phải",
})

_NEG_MORPH_PREFIXES: tuple[str, ...] = (
    "non", "dis", "un", "in", "im", "il", "ir", "не", "без", "бес", "des",
)

_STATE_ANTONYM_PAIRS: frozenset[tuple[str, str]] = frozenset(
    pair
    for a, b in (
        ("true", "false"),
        ("enable", "disable"),
        ("enabled", "disabled"),
        ("allow", "deny"),
        ("allow", "forbid"),
        ("allow", "prohibit"),
        ("allowed", "denied"),
        ("allowed", "forbidden"),
        ("allowed", "prohibited"),
        ("permitted", "prohibited"),
        ("permitted", "forbidden"),
        ("permitted", "denied"),
        ("mandatory", "optional"),
        ("required", "prohibited"),
        ("required", "forbidden"),
        ("active", "inactive"),
        ("on", "off"),
        ("yes", "no"),
        ("cho", "cấm"),
    )
    for pair in ((a, b), (b, a))
)

_CONTINUOUS_ANTONYM_PAIRS: frozenset[tuple[str, str]] = frozenset(
    pair
    for a, b in (
        ("允许", "禁止"), ("允许", "不允许"), ("允许", "严禁"),
        ("允許", "禁止"), ("允許", "不允許"),
        ("可", "不可"), ("可能", "不可能"),
        ("有效", "无效"), ("有効", "無効"),
        ("許可", "禁止"), ("許可", "不許可"),
        ("合法", "非法"), ("合法", "不合法"),
        ("必須", "不要"), ("必须", "禁止"),
        ("อนุญาต", "ห้าม"), ("อนุญาต", "ไม่อนุญาต"),
    )
    for pair in ((a, b), (b, a))
)

_LIFECYCLE_SUPERSEDE_TOKENS: frozenset[str] = frozenset({
    "deprecated", "superseded", "supersedes", "replaces", "obsolete",
})
_LIFECYCLE_SUPERSEDE_JOINED: frozenset[str] = frozenset({
    "thaythế", "bãibỏ", "hếthiệulực", "廃止", "已废弃", "已废止", "废止",
})


def _tokenize_for_diff(text: str) -> list[str]:
    norm = _THOUSANDS_COMMA_RE.sub("", text.casefold())
    norm = _EN_WON_T_RE.sub("will not", norm)
    norm = _EN_CAN_T_RE.sub("can not", norm)
    norm = _EN_N_T_RE.sub(r"\1 not", norm)
    return _DIFF_TOKEN_RE.findall(norm)


def _same_inflected_stem(w1: str, w2: str) -> bool:
    if w1 == w2:
        return True
    short, long = (w1, w2) if len(w1) <= len(w2) else (w2, w1)
    if len(short) < 3:
        return False
    if long in (f"{short}s", f"{short}es", f"{short}d", f"{short}ed"):
        return True
    if short.endswith("y") and long in (f"{short[:-1]}ies", f"{short[:-1]}ied"):
        return True
    return False


def _is_negation_prefix_pair(w1: str, w2: str) -> bool:
    short, long = (w1, w2) if len(w1) <= len(w2) else (w2, w1)
    if len(short) >= 4:
        for pfx in _NEG_MORPH_PREFIXES:
            if long == f"{pfx}{short}":
                return True
    # Shared root with en-/dis- (e.g. enable/disable, enabled/disabled)
    for a, b in ((w1, w2), (w2, w1)):
        if a.startswith("en") and b.startswith("dis") and len(a) >= 6 and a[2:] == b[3:]:
            return True
    return False


def _decompose_polarity_hunk(tokens: list[str]) -> tuple[int, list[str]]:
    sign = 1
    rem: list[str] = []
    for tok in tokens:
        if tok in _POLARITY_PARTICLES:
            sign *= -1
        elif tok in _NEUTRAL_AUX:
            continue
        else:
            rem.append(tok)
    return sign, rem


def _hunk_is_polarity_flip(h1: list[str], h2: list[str]) -> bool:
    """Check whether a localized SequenceMatcher edit hunk inverts predicate polarity."""
    if not h1 and not h2:
        return False
    if max(len(h1), len(h2)) > 5:
        return False

    j1 = "".join(h1)
    j2 = "".join(h2)
    if (not h1 and j2 in _CONTINUOUS_NEG_MARKERS) or (not h2 and j1 in _CONTINUOUS_NEG_MARKERS):
        return True
    if (j1, j2) in _CONTINUOUS_ANTONYM_PAIRS:
        return True

    if max(len(h1), len(h2)) > 3:
        return False

    sign1, rem1 = _decompose_polarity_hunk(h1)
    sign2, rem2 = _decompose_polarity_hunk(h2)
    if not rem1 and not rem2:
        return sign1 != sign2
    if len(rem1) == 1 and len(rem2) == 1:
        w1, w2 = rem1[0], rem2[0]
        if _same_inflected_stem(w1, w2):
            return sign1 != sign2
        if _is_negation_prefix_pair(w1, w2) or (w1, w2) in _STATE_ANTONYM_PAIRS:
            return sign1 != (sign2 * -1)
    return False


def _hunk_has_lifecycle_supersede(h1: list[str], h2: list[str]) -> bool:
    if len(h1) > 6 or len(h2) > 6:
        return False
    s1 = any(t in _LIFECYCLE_SUPERSEDE_TOKENS for t in h1) or "".join(h1) in _LIFECYCLE_SUPERSEDE_JOINED
    s2 = any(t in _LIFECYCLE_SUPERSEDE_TOKENS for t in h2) or "".join(h2) in _LIFECYCLE_SUPERSEDE_JOINED
    return s1 != s2


def detect_semantic_signals(
    text1: str,
    text2: str,
    *,
    same_document: bool | None = None,
) -> tuple[bool, bool, bool]:
    """Detect contradiction, support, or supersede signals via Symmetric Structural Diff (Delta).

    Evaluates ONLY the localized SequenceMatcher edit hunks between two high-similarity
    passages so pre-existing negations or appended clauses never cause false positives:
    1. Quantitative / version / scale-unit replacement in aligned slots:
       - `SUPERSEDES` when `same_document is True` (version threshold/date update)
       - `CONTRADICTS` when cross-document or unspecified
    2. Multiplicative polarity inversion on localized hunks (`CONTRADICTS`).
    3. Explicit lifecycle deprecation/supersede marker newly added in diff (`SUPERSEDES`).

    Returns:
        (is_contradiction, is_support, is_supersede)
    """
    toks1 = _tokenize_for_diff(text1)
    toks2 = _tokenize_for_diff(text2)
    if not toks1 or not toks2:
        return False, False, False

    is_contradiction = False
    is_support = False
    is_supersede = False

    matcher = difflib.SequenceMatcher(None, toks1, toks2, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        h1 = toks1[i1:i2]
        h2 = toks2[j1:j2]

        if tag == "replace":
            q1 = [t for t in h1 if _QUANT_TOKEN_RE.fullmatch(t)]
            q2 = [t for t in h2 if _QUANT_TOKEN_RE.fullmatch(t)]
            quant_changed = bool(q1 and q2 and q1 != q2)
            if (
                not quant_changed
                and len(h1) == 1
                and len(h2) == 1
                and i1 > 0
                and _QUANT_TOKEN_RE.fullmatch(toks1[i1 - 1])
                and h1[0] in _SCALE_UNITS
                and h2[0] in _SCALE_UNITS
                and _SCALE_UNITS[h1[0]] != _SCALE_UNITS[h2[0]]
            ):
                quant_changed = True

            if quant_changed:
                if same_document is True:
                    is_supersede = True
                else:
                    is_contradiction = True

        if _hunk_is_polarity_flip(h1, h2):
            is_contradiction = True

        if _hunk_has_lifecycle_supersede(h1, h2):
            is_supersede = True

    return is_contradiction, is_support, is_supersede


# ---------------------------------------------------------------------------
# Pillar 1: Sub-Quadratic MinHash LSH + Random-Hyperplane SimHash Vector LSH
# ---------------------------------------------------------------------------
_SIMHASH_PLANES_CACHE: dict[tuple[int, int, int], tuple[tuple[tuple[int, float], ...], ...]] = {}


def _simhash_sparse_planes(
    dim: int,
    *,
    total_planes: int = 32,
    max_sample_coords: int = 128,
) -> tuple[tuple[tuple[int, float], ...], ...]:
    """Build deterministic rotational-invariant hyperplane projections for SimHash LSH."""
    key = (dim, total_planes, max_sample_coords)
    cached = _SIMHASH_PLANES_CACHE.get(key)
    if cached is not None:
        return cached

    sample_len = min(dim, max_sample_coords)
    stride = max(1, dim // sample_len)
    planes: list[tuple[tuple[int, float], ...]] = []
    for p in range(total_planes):
        entries: list[tuple[int, float]] = []
        seen_idx: set[int] = set()
        block_idx = 0
        coord_cursor = (p * 37) % dim
        while len(entries) < sample_len:
            digest = hashlib.sha256(f"dedup-simhash:{dim}:{p}:{block_idx}".encode()).digest()
            block_idx += 1
            for offset in range(0, 32, 4):
                if len(entries) >= sample_len:
                    break
                while coord_cursor in seen_idx:
                    coord_cursor = (coord_cursor + 1) % dim
                seen_idx.add(coord_cursor)
                val = (
                    digest[offset]
                    + digest[offset + 1]
                    + digest[offset + 2]
                    + digest[offset + 3]
                    - 510.0
                )
                entries.append((coord_cursor, val if val != 0.0 else 0.5))
                coord_cursor = (coord_cursor + stride) % dim
        planes.append(tuple(entries))

    result = tuple(planes)
    _SIMHASH_PLANES_CACHE[key] = result
    return result


def _compute_simhash_bits(
    vec: Sequence[float],
    planes: tuple[tuple[tuple[int, float], ...], ...],
) -> tuple[int, ...]:
    return tuple(
        1 if sum(float(vec[idx]) * weight for idx, weight in plane) >= 0.0 else 0
        for plane in planes
    )


def build_vector_lsh_index(
    embeddings_by_id: dict[str, Sequence[float]],
    *,
    num_bands: int = 8,
    bits_per_band: int = 4,
) -> dict[int, dict[tuple[int, tuple[int, ...]], list[str]]]:
    """Index dense vectors by dimension into multi-band SimHash hyperplane buckets."""
    by_dim: dict[int, dict[tuple[int, tuple[int, ...]], list[str]]] = {}
    total_planes = num_bands * bits_per_band
    for item_id, vec in embeddings_by_id.items():
        if not vec:
            continue
        dim = len(vec)
        planes = _simhash_sparse_planes(dim, total_planes=total_planes)
        bits = _compute_simhash_bits(vec, planes)
        buckets = by_dim.setdefault(dim, {})
        for b in range(num_bands):
            band_key = (b, bits[b * bits_per_band : (b + 1) * bits_per_band])
            buckets.setdefault(band_key, []).append(item_id)
    return by_dim


def query_vector_lsh_candidates(
    query_vec: Sequence[float],
    vector_lsh_by_dim: dict[int, dict[tuple[int, tuple[int, ...]], list[str]]],
    *,
    num_bands: int = 8,
    bits_per_band: int = 4,
) -> set[str]:
    """Retrieve candidate IDs colliding in at least one SimHash hyperplane band."""
    if not query_vec:
        return set()
    dim = len(query_vec)
    buckets = vector_lsh_by_dim.get(dim)
    if not buckets:
        return set()
    planes = _simhash_sparse_planes(dim, total_planes=num_bands * bits_per_band)
    bits = _compute_simhash_bits(query_vec, planes)
    candidates: set[str] = set()
    for b in range(num_bands):
        band_key = (b, bits[b * bits_per_band : (b + 1) * bits_per_band])
        if band_key in buckets:
            candidates.update(buckets[band_key])
    return candidates


def build_lsh_band_index(
    signatures_by_id: dict[str, list[int]],
    *,
    num_bands: int = 16,
    rows_per_band: int = 8,
) -> dict[tuple[int, tuple], list[str]]:
    """Index MinHash signatures into LSH band hash buckets for sub-quadratic candidate search."""
    buckets: dict[tuple[int, tuple], list[str]] = {}
    for item_id, sig in signatures_by_id.items():
        if len(sig) < num_bands * rows_per_band:
            continue
        for b in range(num_bands):
            band_key = (b, tuple(sig[b * rows_per_band : (b + 1) * rows_per_band]))
            if band_key not in buckets:
                buckets[band_key] = []
            buckets[band_key].append(item_id)
    return buckets


def query_lsh_candidates(
    query_sig: list[int],
    lsh_buckets: dict[tuple[int, tuple], list[str]],
    *,
    num_bands: int = 16,
    rows_per_band: int = 8,
) -> set[str]:
    """Retrieve candidate IDs sharing at least one LSH band collision with query signature."""
    candidates = set()
    if len(query_sig) < num_bands * rows_per_band:
        return candidates
    for b in range(num_bands):
        band_key = (b, tuple(query_sig[b * rows_per_band : (b + 1) * rows_per_band]))
        if band_key in lsh_buckets:
            candidates.update(lsh_buckets[band_key])
    return candidates


def get_block_type_threshold(block_type: str) -> float:
    """Return near-dedup similarity threshold by data type according to Phase 2B plan.

    Narrative text (heading, paragraph, list, caption): 0.85
    Structured data (code, table): 0.95 to avoid false merges on common boilerplates.
    """
    if block_type in {"code", "table"}:
        return 0.95
    return 0.85


def classify_relation_candidate(
    sim_score: float,
    is_contradiction: bool = False,
    is_supersede: bool = False,
    is_support: bool = False,
) -> str:
    """Map similarity and semantic signals to one of the 5 canonical relation types.

    Never auto-merge CONTRADICTS or SUPERSEDES.
    """
    if is_supersede:
        return RELATION_SUPERSEDES
    if is_contradiction:
        return RELATION_CONTRADICTS
    if is_support:
        return RELATION_SUPPORTS
    if sim_score >= 0.85:
        return RELATION_EQUIVALENT
    if sim_score >= 0.50:
        return RELATION_RELATED
    return RELATION_RELATED


async def register_relation_candidate(
    session: AsyncSession,
    *,
    project_id: str,
    source_unit_id: str,
    target_unit_id: str,
    edge_type: str,
    weight: float,
    evidence_mapping: dict[str, Any] | None = None,
) -> KnowledgeGraphEdge:
    """Record a semantic edge candidate.

    Enforces that CONTRADICTS and SUPERSEDES relations remain isolated candidates
    with provenance evidence, without destructive auto-merge.
    """
    if edge_type not in VALID_RELATIONS:
        raise ValueError(f"Invalid relation type: {edge_type}. Must be one of {VALID_RELATIONS}")

    metadata = evidence_mapping or {}
    metadata.setdefault("is_candidate", True)
    metadata.setdefault("auto_merged", False)

    # In case of CONTRADICTS / SUPERSEDES, record strict audit trail
    if edge_type in {RELATION_CONTRADICTS, RELATION_SUPERSEDES}:
        metadata["requires_human_or_eval_resolution"] = True

    edge = KnowledgeGraphEdge(
        id=str(uuid.uuid4()),
        project_id=project_id,
        source_unit_id=source_unit_id,
        target_unit_id=target_unit_id,
        edge_type=edge_type,
        weight=weight,
        calibrated_weight=weight,
        metadata_json=metadata,
    )
    session.add(edge)
    return edge


async def resolve_temporal_supersedes(
    session: AsyncSession,
    *,
    current_version: DocumentVersion,
    document_id: str,
) -> dict[str, Any]:
    """Link versions along temporal axes without overlapping validity intervals.

    Selects active predecessor according to time validity (valid_from <= cur_start < valid_to),
    rather than naive version_no sorting, preventing validity overlap on out-of-order ingest.
    """
    stmt = (
        select(DocumentVersion)
        .where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.id != current_version.id,
            DocumentVersion.search_status == "SEARCH_READY",
        )
        .order_by(DocumentVersion.valid_from.asc())
    )
    res = await session.execute(stmt)
    all_prev = list(res.scalars().all())

    now_utc = datetime.now(UTC)
    cur_start = current_version.source_published_at or current_version.observed_at or now_utc
    if cur_start.tzinfo is None:
        cur_start = cur_start.replace(tzinfo=UTC)

    max_valid_to = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)

    if not all_prev:
        current_version.valid_from = cur_start
        current_version.valid_to = max_valid_to
        current_version.supersedes_id = None
        current_version.knowledge_status = "NOT_STARTED"
        return {"action": "INITIAL", "active_version_id": current_version.id}

    # Find the version active at cur_start: valid_from <= cur_start < valid_to
    active_at_start = None
    for v in all_prev:
        raw_from = (
            getattr(v, "source_published_at", None)
            or getattr(v, "observed_at", None)
            or getattr(v, "valid_from", None)
            or getattr(v, "created_at", None)
        )
        v_from = raw_from.replace(tzinfo=UTC) if raw_from and raw_from.tzinfo is None else raw_from
        raw_to = v.valid_to or max_valid_to
        v_to = raw_to.replace(tzinfo=UTC) if raw_to and raw_to.tzinfo is None else raw_to
        if v_from and v_to and v_from <= cur_start < v_to:
            active_at_start = v
            break

    if active_at_start is not None:
        # Normal newer or mid-timeline version: current supersedes active_at_start
        orig_active_valid_to = active_at_start.valid_to or max_valid_to
        current_version.supersedes_id = active_at_start.id
        current_version.valid_from = cur_start
        current_version.valid_to = orig_active_valid_to
        if v_from:
            active_at_start.valid_from = v_from
        active_at_start.valid_to = cur_start
        session.add(active_at_start)

        # Rewire successor versions that were pointing to active_at_start
        # (e.g. v1 -> v2, inserting v_mid between their dates leaves v2 pointing to v_mid, maintaining continuous chain)
        for v in all_prev:
            if v.id != current_version.id and v.supersedes_id == active_at_start.id:
                raw_v = (
                    getattr(v, "source_published_at", None)
                    or getattr(v, "observed_at", None)
                    or getattr(v, "valid_from", None)
                )
                v_start = raw_v.replace(tzinfo=UTC) if raw_v and raw_v.tzinfo is None else raw_v
                if v_start and v_start >= cur_start:
                    v.supersedes_id = current_version.id
                    session.add(v)

        action = "SUPERSEDED"
        active_v = next(
            (
                v
                for v in all_prev
                if v.valid_to
                and (v.valid_to.replace(tzinfo=UTC) if v.valid_to.tzinfo is None else v.valid_to) >= max_valid_to
            ),
            current_version,
        )
        active_id = active_v.id
    else:
        # Out-of-order older version arriving late, or gap in timeline
        future_vers = []
        past_vers = []
        for v in all_prev:
            raw_from = (
                getattr(v, "source_published_at", None)
                or getattr(v, "observed_at", None)
                or getattr(v, "valid_from", None)
                or getattr(v, "created_at", None)
            )
            vf = raw_from.replace(tzinfo=UTC) if raw_from and raw_from.tzinfo is None else raw_from
            raw_to = v.valid_to or max_valid_to
            vt = raw_to.replace(tzinfo=UTC) if raw_to and raw_to.tzinfo is None else raw_to
            if vf and vf > cur_start:
                future_vers.append((v, vf))
            elif vt and vt <= cur_start:
                past_vers.append((v, vt))

        current_version.valid_from = cur_start
        if past_vers:
            latest_past_v, _ = max(past_vers, key=lambda x: x[1])
            current_version.supersedes_id = latest_past_v.id
        else:
            current_version.supersedes_id = None

        if future_vers:
            earliest_future_v, earliest_future_from = min(future_vers, key=lambda x: x[1])
            current_version.valid_to = earliest_future_from
            past_ids = {pv.id for pv, _ in past_vers} if past_vers else set()
            if not earliest_future_v.supersedes_id or earliest_future_v.supersedes_id in past_ids:
                earliest_future_v.supersedes_id = current_version.id
                session.add(earliest_future_v)
        else:
            current_version.valid_to = max_valid_to

        action = "OUT_OF_ORDER_ARCHIVED"
        active_v = next((v for v in all_prev if v.valid_to and v.valid_to >= max_valid_to), current_version)
        active_id = active_v.id

    # Strictly guarantee non-overlapping intervals across all versions
    for v in all_prev:
        if v.id == current_version.id:
            continue
        v_from = v.valid_from.replace(tzinfo=UTC) if v.valid_from and v.valid_from.tzinfo is None else v.valid_from
        v_to = v.valid_to.replace(tzinfo=UTC) if v.valid_to and v.valid_to.tzinfo is None else v.valid_to
        c_from = current_version.valid_from
        c_to = current_version.valid_to

        if v_from < c_from and v_to > c_from:
            v.valid_to = c_from
            session.add(v)
        elif c_from <= v_from < c_to:
            current_version.valid_to = v_from

    # Enrichment freezes validity/lineage; changed predecessors/successors need a new knowledge input.
    for version in [*all_prev, current_version]:
        if any(
            inspect(version).attrs[field].history.has_changes()
            for field in ("valid_from", "valid_to", "supersedes_id")
        ):
            version.knowledge_status = "NOT_STARTED"

    return {
        "action": action,
        "active_version_id": active_id,
        "current_version_id": current_version.id,
        "supersedes_id": current_version.supersedes_id,
    }


def _dedup_candidates(curr_blocks, prior_blocks, signatures, block_embeddings, current_version_id):
    exact_block_matches = 0
    near_dup_candidates = []
    prior_hashes = {b.content_hash for b in prior_blocks if b.content_hash}
    # Precompute MinHash + optional SimHash vector indices once for sub-quadratic O(1) lookup
    prior_sig_by_id: dict[str, list[int]] = {}
    prior_by_id: dict[str, CanonicalBlock] = {}
    prior_vec_by_id: dict[str, Sequence[float]] = {}
    for pb in prior_blocks:
        prior_by_id[pb.id] = pb
        prior_sig_by_id[pb.id] = signatures[pb.id]
        if block_embeddings and pb.id in block_embeddings and block_embeddings[pb.id]:
            prior_vec_by_id[pb.id] = block_embeddings[pb.id]

    lsh_index = build_lsh_band_index(prior_sig_by_id, num_bands=32, rows_per_band=4)
    vector_lsh_index = (
        build_vector_lsh_index(prior_vec_by_id, num_bands=8, bits_per_band=4)
        if prior_vec_by_id
        else {}
    )

    for b in curr_blocks:
        if b.content_hash and b.content_hash in prior_hashes:
            exact_block_matches += 1
        else:
            threshold = get_block_type_threshold(b.block_type)
            b_sig = signatures[b.id]

            # Sub-quadratic candidate union: lexical MinHash LSH + Tier-4 SimHash Vector LSH (never O(N^2) full scan)
            candidate_ids = query_lsh_candidates(b_sig, lsh_index, num_bands=32, rows_per_band=4)
            if vector_lsh_index and block_embeddings and b.id in block_embeddings:
                candidate_ids |= query_vector_lsh_candidates(
                    block_embeddings[b.id],
                    vector_lsh_index,
                    num_bands=8,
                    bits_per_band=4,
                )
            eval_blocks = [prior_by_id[cid] for cid in candidate_ids if cid in prior_by_id]
            if not eval_blocks:
                continue

            b_doc_id = getattr(b, "document_id", None)
            b_tokens: list[str] | None = None
            # Score bounded LSH candidates and rank deterministically to select the best match
            block_candidates: list[dict[str, Any]] = []
            for pb in eval_blocks:
                pb_sig = prior_sig_by_id.get(pb.id)
                sim = (
                    estimate_minhash_similarity(b_sig, pb_sig)
                    if pb_sig
                    else compute_text_similarity(b.normalized_text, pb.normalized_text)
                )
                if pb_sig and 0.35 <= sim < threshold:
                    if b_tokens is None:
                        b_tokens = tokenize_for_minhash(b.normalized_text)
                    if 0 < len(b_tokens) < 80:
                        pb_tokens = tokenize_for_minhash(pb.normalized_text)
                        if 0 < len(pb_tokens) < 80:
                            sim = max(
                                sim,
                                difflib.SequenceMatcher(None, b_tokens, pb_tokens, autojunk=False).ratio(),
                            )

                cosine_sim = 0.0
                if block_embeddings and b.id in block_embeddings and pb.id in block_embeddings:
                    cosine_sim = compute_cosine_similarity(block_embeddings[b.id], block_embeddings[pb.id])

                is_lexical_match = sim >= threshold
                is_semantic_match = cosine_sim >= 0.90

                if is_lexical_match or is_semantic_match:
                    pb_doc_id = getattr(pb, "document_id", None)
                    same_doc = (b_doc_id == pb_doc_id) if (b_doc_id and pb_doc_id) else None
                    is_contra, is_supp, is_super = detect_semantic_signals(
                        b.normalized_text,
                        pb.normalized_text,
                        same_document=same_doc,
                    )
                    effective_sim = max(sim, cosine_sim)
                    rel = classify_relation_candidate(
                        effective_sim,
                        is_contradiction=is_contra,
                        is_supersede=is_super,
                        is_support=is_supp,
                    )
                    evidence_note = (
                        f"MinHash similarity {sim:.3f} >= {threshold}"
                        if is_lexical_match
                        else f"Tier 4 Cosine similarity {cosine_sim:.3f} >= 0.90"
                    )
                    block_candidates.append({
                        "source_block_id": b.id,
                        "target_block_id": pb.id,
                        "source_version_id": current_version_id,
                        "target_version_id": pb.document_version_id,
                        "block_type": b.block_type,
                        "threshold": threshold,
                        "similarity_score": round(effective_sim, 3),
                        "similarity": round(effective_sim, 3),
                        "relation": rel,
                        "relation_type": rel,
                        "is_contradiction": is_contra,
                        "is_support": is_supp,
                        "is_supersede": is_super,
                        "auto_merged": False,
                        "evidence": f"{evidence_note} on {b.block_type} [relation={rel}]",
                    })

            if block_candidates:
                # Rank candidates by (similarity_score, target_block_id) descending to select best match stably
                block_candidates.sort(key=lambda c: (c["similarity_score"], str(c["target_block_id"])), reverse=True)
                near_dup_candidates.append(block_candidates[0])

    return exact_block_matches, near_dup_candidates


async def run_dedup_and_temporal_stage(
    session: AsyncSession,
    *,
    document_version: DocumentVersion,
    document_id: str,
    project_id: str,
    run_id: str | None = None,
    embedder: Any | None = None,
) -> dict[str, Any]:
    """Execute Phase 2B stage: Exact Dedup, Near Dedup, and Temporal Supersedes with audit."""
    start_time = datetime.now(UTC)

    # 1. Exact Version Dedup Check (by file_hash)
    exact_dup_stmt = (
        select(DocumentVersion)
        .where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.id != document_version.id,
            DocumentVersion.file_hash == document_version.file_hash,
        )
        .order_by(desc(DocumentVersion.created_at))
    )
    exact_dup_version = (await session.execute(exact_dup_stmt)).scalars().first()
    is_exact_dup_version = exact_dup_version is not None

    # 2. Block-Level Exact & Near Dedup Check with Block-Type Specific Thresholds
    curr_blocks = (
        await session.execute(
            select(CanonicalBlock)
            .where(CanonicalBlock.document_version_id == document_version.id)
            .order_by(CanonicalBlock.ordinal)
        )
    ).scalars().all()

    # Prior blocks scoped strictly to active, non-superseded, non-failed versions in project
    prior_rows = (
        await session.execute(
            select(CanonicalBlock, DocumentVersion.document_id)
            .join(DocumentVersion, CanonicalBlock.document_version_id == DocumentVersion.id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                or_(
                    Document.project_id == project_id,
                    Document.id == document_id,
                ) if project_id else (Document.id == document_id),
                DocumentVersion.id != document_version.id,
                Document.is_active.is_(True),
                DocumentVersion.status != "FAILED",
                DocumentVersion.search_status != "FAILED",
                DocumentVersion.valid_to > start_time,
            )
        )
    ).all()
    prior_blocks = [row[0] for row in prior_rows]
    prior_doc_by_block_id = {row[0].id: row[1] for row in prior_rows}

    signatures = await _block_signatures(prior_blocks + curr_blocks, persist_ids={b.id for b in curr_blocks})

    if not prior_blocks or not curr_blocks:
        exact_block_matches, near_dup_candidates = 0, []
    else:
        # Optional Tier 4: Precompute embeddings if embedder available, or reuse $0-cost RAM cache
        block_embeddings: dict[str, list[float]] = {}
        if embedder is None:
            try:
                from sag_api.services.search_index_service import get_cached_text_embedding

                curr_cached = {
                    b.id: vec
                    for b in curr_blocks
                    if (vec := get_cached_text_embedding(b.normalized_text)) is not None
                }
                if curr_cached:
                    prior_cached = {
                        pb.id: vec
                        for pb in prior_blocks
                        if (vec := get_cached_text_embedding(pb.normalized_text)) is not None
                    }
                    if prior_cached:
                        block_embeddings.update(curr_cached)
                        block_embeddings.update(prior_cached)
            except Exception:
                pass
        else:
            try:
                from sag_api.services.search_index_service import (
                    get_cached_text_embedding,
                    put_cached_text_embedding,
                )

                texts_to_embed = [b.normalized_text for b in curr_blocks] + [pb.normalized_text for pb in prior_blocks]
                ids_to_embed = [b.id for b in curr_blocks] + [pb.id for pb in prior_blocks]
                if texts_to_embed:
                    if hasattr(embedder, "batch_generate"):
                        vecs_by_idx: dict[int, list[float]] = {}
                        missing_indices: list[int] = []
                        missing_texts: list[str] = []
                        for idx, txt in enumerate(texts_to_embed):
                            cached = get_cached_text_embedding(txt)
                            if cached is not None:
                                vecs_by_idx[idx] = cached
                            else:
                                missing_indices.append(idx)
                                missing_texts.append(txt)
                        if missing_texts:
                            fetched = await embedder.batch_generate(missing_texts)
                            for m_idx, txt, vec in zip(missing_indices, missing_texts, fetched, strict=False):
                                if vec:
                                    put_cached_text_embedding(txt, vec)
                                vecs_by_idx[m_idx] = vec
                        vecs = [vecs_by_idx.get(i, []) for i in range(len(texts_to_embed))]
                    elif hasattr(embedder, "generate"):
                        vecs = [await embedder.generate(t) for t in texts_to_embed]
                    elif callable(embedder):
                        import inspect
                        if inspect.iscoroutinefunction(embedder):
                            vecs = [await embedder(t) for t in texts_to_embed]
                        else:
                            vecs = [embedder(t) for t in texts_to_embed]
                    else:
                        vecs = []
                    for bid, vec in zip(ids_to_embed, vecs, strict=False):
                        if vec:
                            block_embeddings[bid] = vec
            except Exception as emb_err:
                log.warning("Could not compute block embeddings for Tier 4 dedup: %s", emb_err)

        from sag_api.services.cpu_worker import run_cpu
        fields = ("id", "normalized_text", "content_hash", "block_type", "document_version_id")
        exact_block_matches, near_dup_candidates = await run_cpu("dedup_candidates", {
            "current": [
                {**{key: getattr(b, key) for key in fields}, "document_id": document_id}
                for b in curr_blocks
            ],
            "prior": [
                {**{key: getattr(pb, key) for key in fields}, "document_id": prior_doc_by_block_id.get(pb.id)}
                for pb in prior_blocks
            ],
            "signatures": signatures, "embeddings": block_embeddings, "version_id": document_version.id,
        })

    # Publish validity changes only after the replacement index is verified.
    temporal_result = {"action": "PENDING_INDEX", "active_version_id": None}

    # 4. Update DocumentVersion metadata with Evidence Mapping & Dedup audit
    meta = dict(document_version.metadata_json or {})
    meta.update({
        "is_exact_duplicate": is_exact_dup_version,
        "exact_duplicate_of": exact_dup_version.id if exact_dup_version else None,
        "exact_block_matches": exact_block_matches,
        "near_duplicate_candidates_count": len(near_dup_candidates),
        "dedup_candidates": near_dup_candidates,
        "dedup_relations": list({c["relation_type"] for c in near_dup_candidates}),
    })
    document_version.metadata_json = meta
    session.add(document_version)

    duration_ms = (datetime.now(UTC) - start_time).total_seconds() * 1000.0

    # 5. Record StageRun
    if run_id:
        stage_run = StageRun(
            id=str(uuid.uuid4()),
            run_id=run_id,
            stage="DEDUP_TEMPORAL",
            status="SUCCESS",
            duration_ms=duration_ms,
            metrics_json={
                "exact_duplicate_found": is_exact_dup_version,
                "exact_duplicate_of": exact_dup_version.id if exact_dup_version else None,
                "exact_block_matches": exact_block_matches,
                "near_duplicate_candidates_count": len(near_dup_candidates),
                "near_duplicate_candidates": near_dup_candidates,
                "temporal_action": temporal_result["action"],
                "active_version_id": temporal_result["active_version_id"],
            },
        )
        session.add(stage_run)

    return {
        "exact_duplicate_found": is_exact_dup_version,
        "exact_duplicate_of": exact_dup_version.id if exact_dup_version else None,
        "exact_block_matches": exact_block_matches,
        "near_duplicate_candidates": near_dup_candidates,
        "temporal_action": temporal_result["action"],
        **temporal_result,
    }
