"""Core SAG-Native User Memory Pipeline (W0-W4), Versioned Outbox, Personal Graph,
Top-3 Linked Sessions, Cascade Delete + Tombstone Guard, and 0-LLM Staleness Hook.

Strictly isolated by `(tenant_id, user_id)` and governed by:
- 5-type Epistemic Allowlist (`STYLE_PREF_SAFE`, `USER_INVESTING_CRITERIA`,
  `USER_EXTERNAL_HOLDING`, `INVESTMENT_THESIS`, `SESSION_EPISODE_DIGEST`).
- Hard Denylist & deterministic post-sanitizer (non-ASSERTED modalities, low confidence,
  prompt injection, corporate BCTC figures, `STREAM_INTERRUPTED_UNVERIFIED`, Stage-7
  hallucinated assistant turns).
- Dual-dialect locking (`pg_advisory_xact_lock` on PG16 / `asyncio.Lock` on SQLite).
- Hard protection of `pinned_by_user` / `USER_CONFIRMED` memories from silent overwrite.
- 3-state versioned Outbox (`PENDING_UPSERT | PENDING_PATCH | PENDING_DELETE`, `sync_version`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import unicodedata
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import and_, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.logging import get_logger
from sag_api.db.models.user_memory import (
    INF_DATETIME,
    UserMemoryAuditLog,
    UserMemoryEdge,
    UserMemoryEntity,
    UserMemoryEpisode,
    UserMemoryItem,
    UserMemoryJob,
)
from sag_api.research.kg_entity_linker import (
    NON_TICKER_ACRONYMS,
    compile_issuer_registry_from_rows,
    extract_mentioned_tickers,
)
from sag_api.research.user_memory_qdrant import (
    build_user_memory_patch_payload,
    delete_user_memory_points,
    patch_user_memory_payload,
    search_user_memories_hybrid,
    upsert_user_memory_point,
)
from sag_api.services.search_index_service import (
    get_cached_text_embedding,
    put_cached_text_embedding,
)

log = get_logger("user_memory_pipeline")

PROMPT_VERSION = "umem_v1"

ALLOWED_MEMORY_TYPES: frozenset[str] = frozenset(
    {
        "STYLE_PREF_SAFE",
        "USER_INVESTING_CRITERIA",
        "USER_EXTERNAL_HOLDING",
        "INVESTMENT_THESIS",
        "SESSION_EPISODE_DIGEST",
    }
)
ATEMPORAL_MEMORY_TYPES: frozenset[str] = frozenset({"STYLE_PREF_SAFE"})

MEMORY_HALF_LIFE_DAYS: dict[str, float] = {
    "STYLE_PREF_SAFE": 365.0,
    "USER_INVESTING_CRITERIA": 365.0,
    "USER_EXTERNAL_HOLDING": 90.0,
    "INVESTMENT_THESIS": 60.0,
    "SESSION_EPISODE_DIGEST": 30.0,
}

# Closed 16-relation personal graph ontology
CLOSED_GRAPH_RELATIONS: frozenset[str] = frozenset(
    {
        # EXT_POS conflict group
        "HOLDS_EXTERNAL",
        "SOLD_EXIT",
        "WATCHES_EXTERNAL",
        "AVOIDS",
        # STANCE conflict group
        "BULLISH_ON",
        "BEARISH_ON",
        "NEUTRAL_SKEPTICAL",
        # Criteria / Risk / Catalyst
        "PREFERS_CRITERION",
        "AVOIDS_SECTOR",
        "CONCERNED_ABOUT_RISK",
        "MONITORING_CATALYST",
        # Session links
        "SESSION_DISCUSSED",
        "SESSION_COMPARED",
        "SESSION_CONTINUED_FROM",
        "SESSION_THESIS_EVOLUTION",
        "SESSION_RELATED_THEME",
    }
)

RELATION_CONFLICT_GROUP: dict[str, str] = {
    "HOLDS_EXTERNAL": "EXT_POS",
    "SOLD_EXIT": "EXT_POS",
    "WATCHES_EXTERNAL": "EXT_POS",
    "AVOIDS": "EXT_POS",
    "BULLISH_ON": "STANCE",
    "BEARISH_ON": "STANCE",
    "NEUTRAL_SKEPTICAL": "STANCE",
}

_STAGE7_HALLUCINATION_CODES: frozenset[str] = frozenset(
    {
        "HALLUCINATED_CITATION_ID",
        "HALLUCINATED_MEMORY_ID",
        "UNSUPPORTED_NUMERIC_CLAIM",
        "CITATION_CONTENT_MISMATCH",
    }
)

_EXPLICIT_REMEMBER_RE = re.compile(
    r"(?:ghi\s+nhớ|nhớ\s+giúp|từ\s+nay|luôn\s+nhớ|đừng\s+quên|lưu\s+lại\s+tiêu\s+chí|"
    r"remember\s+(?:that|this)|always\s+format|from\s+now\s+on)",
    re.IGNORECASE | re.UNICODE,
)

_PREFERENCE_OR_PORTFOLIO_CUE_RE = re.compile(
    r"(?:tránh|ưu\s+tiên|chỉ\s+lọc|tiêu\s+chí|khẩu\s+vị|trình\s+bày|đơn\s+vị|"
    r"tỷ\s+vnd|gạch\s+đầu\s+dòng|đang\s+giữ|đang\s+nắm|đã\s+bán|chốt\s+lời|cắt\s+lỗ|"
    r"bên\s+(?:ssi|vps|tcbs|vndirect|hsc|mbs|vcbs)|giá\s+vốn|luận\s+điểm|lo\s+ngại|"
    r"kỳ\s+vọng|lạc\s+quan|bi\s+quan|thận\s+trọng|ghi\s+nhớ|từ\s+nay|p/e|p/b|roe|d/e|"
    r"prefer|avoid|criteria|holding|sold|bullish|bearish|thesis|format)",
    re.IGNORECASE | re.UNICODE,
)

_HYPOTHETICAL_SCENARIO_RE = re.compile(
    r"(?:\b(?:giả\s+sử|giả\s+định(?:\s+rằng)?|thử\s+giả\s+định|nếu\s+(?:tôi|mình|em|anh)\s+(?:mua|bán|nắm|giữ|có|đầu\s+tư)|"
    r"ví\s+dụ\s+(?:tôi|mình)\s+(?:mua|có|nắm|giữ)|nếu\s+như\s+mua|"
    r"what\s+if\s+i\s+(?:bought|buy|held|hold|sold)|suppose\s+i\s+(?:bought|hold|owned)|hypothetically)\b)",
    re.IGNORECASE | re.UNICODE,
)

_PROMPT_INJECTION_RE = re.compile(
    r"(?:ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|rules|prompts?)|"
    r"disregard\s+(?:all\s+)?(?:previous|system|safety)|"
    r"bỏ\s+qua\s+(?:mọi|tất\s+cả|các)\s+(?:hướng\s+dẫn|quy\s+tắc|lệnh|kiểm\s+chứng|stage\s*7)|"
    r"system\s+prompt|you\s+are\s+now|jailbreak|override\s+safety|"
    r"</?\s*USER_MEMORY|</?\s*SYSTEM|"
    r"luôn\s+(?:khuyên|khuyến\s+nghị)\s+(?:mua|tất\s+tay)|"
    r"bịa\s+(?:số\s+liệu|trích\s+dẫn)|always\s+recommend\s+buy|"
    r"reveal\s+(?:your|the)\s+(?:system\s+)?prompt)",
    re.IGNORECASE | re.UNICODE,
)

_CORP_BCTC_NUMERIC_FACT_RE = re.compile(
    r"(?:(?:doanh\s+thu|lợi\s+nhuận(?:\s+sau\s+thuế)?|lnst|ln\s*tt|cfo|dòng\s+tiền|"
    r"tổng\s+tài\s+sản|vốn\s+chủ\s+sở\s+hữu|vốn\s+hóa|nợ\s+vay|hàng\s+tồn\s+kho|"
    r"biên\s+(?:gộp|lợi\s+nhuận|ròng)|eps|giá\s+cổ\s+phiếu|thị\s+giá|p/e\s+hiện\s+tại|p/b\s+hiện\s+tại)"
    r"[^\n.;,]{0,40}?\b(?:đạt|là|=|ở\s+mức|lên|tăng\s+lên|giảm\s+về)\s*[+-]?\d+(?:[.,]\d+)?\s*"
    r"(?:%|tỷ|tỉ|triệu|nghìn\s+tỷ|ngàn\s+tỷ|x|lần|vnd|vnđ|đồng|usd)?)",
    re.IGNORECASE | re.UNICODE,
)

_STYLE_MARKET_LEAK_RE = re.compile(
    r"(?:\b(?:scb|vạn\s+thịnh\s+phát|trái\s+phiếu|bđs|bất\s+động\s+sản|ngân\s+hàng|thép|chứng\s+khoán|"
    r"dầu\s+khí|cổ\s+phiếu|mã\s+ck|p/e|p/b|roe|roa|d/e|cfo|lãi\s+suất|tỷ\s+giá|fed|sbv|"
    r"biên\s+gộp|cổ\s+tức|doanh\s+thu|lợi\s+nhuận|nợ\s+vay|tăng\s+trưởng|định\s+giá|đầu\s+tư|danh\s+mục|"
    r"chỉ\s+lọc|tránh|mua|bán|nắm\s+giữ|khủng\s+hoảng|phá\s+sản|sự\s+kiện|chu\s+kỳ|thị\s+trường|"
    r"lạm\s+phát|tín\s+dụng|nợ\s+xấu|bctc|ebitda|eps|cagr|fcff|dcf|vn30|hose|hnx|upcom|"
    r"penny|bluechip|midcap|bank|real\s+estate|steel|macro|dividend|margin|valuation)\b"
    r"|\b(?:19|20)\d{2}\b)",
    re.IGNORECASE | re.UNICODE,
)

_UPPER_3_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Z]{3})(?![A-Za-z0-9_])")
_MARKDOWN_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")

_SQLITE_USER_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def normalize_memory_text(text_in: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text_in or "")).strip()


def compute_content_hash(content_text: str) -> str:
    norm = normalize_memory_text(content_text).lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def is_backtest_date(as_of_date: date, *, reference_today: date | None = None) -> bool:
    today = reference_today or date.today()
    return (today - as_of_date).days > 1


def strip_numeric_tables_for_outline(assistant_text: str, *, max_chars: int = 900) -> str:
    """Build `<ASSISTANT_TOPIC_OUTLINE>` stripping raw numeric markdown tables and dense BCTC numbers."""
    if not assistant_text:
        return ""
    kept_lines: list[str] = []
    for raw_line in assistant_text.splitlines():
        if _MARKDOWN_TABLE_LINE_RE.match(raw_line):
            continue
        clean = raw_line.strip()
        if not clean:
            continue
        # Strip inline numbers + financial units from assistant text so extractor gets topic outline only
        scrubbed = re.sub(
            r"\b[+-]?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?\s*(?:%|bps|tỷ|tỉ|triệu|nghìn\s+tỷ|ngàn\s+tỷ|vnd|vnđ|đồng|usd|x|lần)\b",
            "[NUMERIC_REDACTED]",
            clean,
            flags=re.IGNORECASE | re.UNICODE,
        )
        kept_lines.append(scrubbed)
    joined = "\n".join(kept_lines)
    return joined[:max_chars]


@asynccontextmanager
async def user_memory_scope_lock(
    session: AsyncSession,
    tenant_id: str,
    user_id: str,
) -> AsyncIterator[None]:
    """Per-user lock: `pg_advisory_xact_lock` on PostgreSQL 16, `asyncio.Lock` on SQLite."""
    bind = session.get_bind()
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
    if dialect_name == "postgresql":
        digest = hashlib.sha256(f"sag:umem:{tenant_id.strip()}:{user_id.strip()}".encode()).digest()
        lock_key = int.from_bytes(digest[:8], "big", signed=True)
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key})
        yield
        return
    key = (tenant_id.strip(), user_id.strip())
    lock = _SQLITE_USER_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        yield


@dataclass
class SanitizedMemoryCandidate:
    memory_type: str
    pit_sensitivity: str
    scope: str
    content_text: str
    content_hash: str
    canonical_slot_key: str | None = None
    tickers: list[str] = field(default_factory=list)
    structured_payload: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.85
    source_authority: str = "USER_DECLARED"
    polarity: str = "POSITIVE"
    explicit_override: bool = False
    bound_doc_version_ids: list[str] = field(default_factory=list)


@dataclass
class ExtractedTurnBundle:
    memories: list[SanitizedMemoryCandidate] = field(default_factory=list)
    rejected_reasons: list[dict[str, str]] = field(default_factory=list)
    graph_triples: list[dict[str, Any]] = field(default_factory=list)
    episode_summary: str = ""
    tickers: list[str] = field(default_factory=list)
    sectors: list[str] = field(default_factory=list)
    macro_factors: list[str] = field(default_factory=list)
    topic_tags: list[str] = field(default_factory=list)


def _normalize_tickers(
    raw_tickers: Sequence[Any],
    text_source: str,
    *,
    issuer_rows: Sequence[Mapping[str, Any]] | None = None,
    known_tickers: Sequence[str] | None = None,
) -> list[str]:
    compiled = compile_issuer_registry_from_rows(issuer_rows) if issuer_rows else None
    found: list[str] = []
    seen: set[str] = set()

    def _add(sym: str) -> None:
        s = str(sym or "").strip().upper()
        if not s or len(s) != 3 or not s.isalpha() or not s.isascii():
            return
        if s in NON_TICKER_ACRONYMS:
            return
        if s not in seen:
            seen.add(s)
            found.append(s)

    for t in raw_tickers or ():
        _add(str(t))

    if compiled or known_tickers:
        for t in extract_mentioned_tickers(
            text_source,
            known_tickers=known_tickers,
            compiled_issuers=compiled,
        ):
            _add(t)
    else:
        for m in _UPPER_3_RE.finditer(text_source or ""):
            _add(m.group(1))
    return found


def sanitize_extracted_candidate(
    raw: Mapping[str, Any],
    *,
    user_turn_text: str = "",
    assistant_clean_for_digest: bool = True,
    issuer_rows: Sequence[Mapping[str, Any]] | None = None,
    known_tickers: Sequence[str] | None = None,
    cited_doc_version_ids: Sequence[str] = (),
) -> tuple[SanitizedMemoryCandidate | None, str | None]:
    """Deterministic post-sanitizer enforcing Epistemic Allowlist + Hard Denylist."""
    mem_type = str(raw.get("memory_type") or "").strip().upper()
    if mem_type not in ALLOWED_MEMORY_TYPES:
        return None, f"DENYLIST_MEMORY_TYPE:{mem_type or 'EMPTY'}"

    if mem_type == "SESSION_EPISODE_DIGEST" and not assistant_clean_for_digest:
        return None, "STAGE7_OR_INTERRUPTED_NO_DIGEST"

    modality = str(raw.get("modality") or "ASSERTED").strip().upper()
    if modality != "ASSERTED":
        return None, f"NON_ASSERTED_MODALITY:{modality}"

    try:
        conf = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    if conf < 0.70:
        return None, f"LOW_CONFIDENCE:{conf:.2f}"

    content = normalize_memory_text(str(raw.get("content_text") or ""))
    if len(content) < 4:
        return None, "EMPTY_CONTENT"

    if "STREAM_INTERRUPTED_UNVERIFIED" in content or "STREAM_INTERRUPTED_UNVERIFIED" in user_turn_text:
        return None, "STREAM_INTERRUPTED_UNVERIFIED"

    if _PROMPT_INJECTION_RE.search(content) or _PROMPT_INJECTION_RE.search(user_turn_text):
        return None, "PROMPT_INJECTION_BLOCKED"

    # Deterministic Vietnamese/English hypothetical scenario guard
    if mem_type in ("USER_EXTERNAL_HOLDING", "INVESTMENT_THESIS", "USER_INVESTING_CRITERIA"):
        if _HYPOTHETICAL_SCENARIO_RE.search(content) or _HYPOTHETICAL_SCENARIO_RE.search(user_turn_text):
            return None, "HYPOTHETICAL_SCENARIO_BLOCKED"

    tickers = _normalize_tickers(
        raw.get("tickers") or (),
        content,
        issuer_rows=issuer_rows,
        known_tickers=known_tickers,
    )

    # STYLE_PREF_SAFE MUST be strictly atemporal pure format (0 tickers, 0 market events/years/investing rules)
    if mem_type == "STYLE_PREF_SAFE":
        if tickers or _STYLE_MARKET_LEAK_RE.search(content):
            # Reclassify market-bound rule to USER_INVESTING_CRITERIA so Backtest Default-Deny blocks it!
            mem_type = "USER_INVESTING_CRITERIA"

    # Corporate BCTC numeric denylist across ALL memory types: never store company financials as user memory
    if _CORP_BCTC_NUMERIC_FACT_RE.search(content):
        return None, "CORP_BCTC_NUMERIC_DENYLIST"

    pit_sensitivity = "ATEMPORAL_FORMAT" if mem_type in ATEMPORAL_MEMORY_TYPES else "MARKET_BOUND"
    raw_scope = str(raw.get("scope") or "").strip().upper()
    if raw_scope not in {"USER_GLOBAL", "TICKER_SCOPED", "SESSION_SCOPED"}:
        if mem_type == "SESSION_EPISODE_DIGEST":
            scope = "SESSION_SCOPED"
        elif tickers:
            scope = "TICKER_SCOPED"
        else:
            scope = "USER_GLOBAL"
    else:
        scope = raw_scope

    if mem_type == "USER_EXTERNAL_HOLDING":
        source_authority = "USER_DECLARED_EXTERNAL"
    elif mem_type == "SESSION_EPISODE_DIGEST":
        source_authority = "SYSTEM_DIGEST"
    else:
        source_authority = "USER_DECLARED"

    slot_key = str(raw.get("canonical_slot_key") or "").strip() or None
    if slot_key:
        slot_key = slot_key[:160]

    polarity = str(raw.get("polarity") or "POSITIVE").strip().upper()
    if polarity not in {"POSITIVE", "NEGATIVE"}:
        polarity = "POSITIVE"

    explicit_override = bool(raw.get("explicit_override", False))
    structured_payload = raw.get("structured_payload")
    if not isinstance(structured_payload, Mapping):
        structured_payload = {}
    else:
        structured_payload = dict(structured_payload)
    structured_payload.setdefault("polarity", polarity)

    bound_docs: list[str] = []
    if mem_type in ("INVESTMENT_THESIS", "SESSION_EPISODE_DIGEST"):
        raw_docs = list(raw.get("bound_doc_version_ids") or ()) + list(cited_doc_version_ids or ())
        bound_docs = list(dict.fromkeys(str(d).strip() for d in raw_docs if str(d).strip()))[:32]

    return (
        SanitizedMemoryCandidate(
            memory_type=mem_type,
            pit_sensitivity=pit_sensitivity,
            scope=scope,
            content_text=content[:1200],
            content_hash=compute_content_hash(content[:1200]),
            canonical_slot_key=slot_key,
            tickers=tickers,
            structured_payload=structured_payload,
            confidence=round(min(1.0, max(0.0, conf)), 4),
            source_authority=source_authority,
            polarity=polarity,
            explicit_override=explicit_override,
            bound_doc_version_ids=bound_docs,
        ),
        None,
    )


def sanitize_user_patch_content(
    content_text: str,
    *,
    current_memory_type: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate & reclassify user-edited memory content (blocks prompt injection, BCTC facts & PIT escalation)."""
    clean = normalize_memory_text(content_text)[:1200]
    if len(clean) < 2:
        return None, "EMPTY_CONTENT"
    if "STREAM_INTERRUPTED_UNVERIFIED" in clean:
        return None, "STREAM_INTERRUPTED_UNVERIFIED"
    if _PROMPT_INJECTION_RE.search(clean):
        return None, "PROMPT_INJECTION_BLOCKED"
    if _CORP_BCTC_NUMERIC_FACT_RE.search(clean):
        return None, "CORP_BCTC_NUMERIC_DENYLIST"

    tickers = _normalize_tickers((), clean)
    mem_type = str(current_memory_type or "USER_INVESTING_CRITERIA").strip().upper()
    if mem_type == "STYLE_PREF_SAFE" and (tickers or _STYLE_MARKET_LEAK_RE.search(clean)):
        mem_type = "USER_INVESTING_CRITERIA"
    pit_sensitivity = "ATEMPORAL_FORMAT" if mem_type in ATEMPORAL_MEMORY_TYPES else "MARKET_BOUND"
    return (
        {
            "content_text": clean,
            "content_hash": compute_content_hash(clean),
            "memory_type": mem_type,
            "pit_sensitivity": pit_sensitivity,
            "tickers": tickers,
        },
        None,
    )


def _strip_json_fences(raw_text: str) -> str:
    txt = (raw_text or "").strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```(?:json)?\s*", "", txt, flags=re.IGNORECASE)
        txt = re.sub(r"\s*```$", "", txt)
    return txt.strip()


# ── W0: Eligibility Gate & Idempotent Job Enqueue ────────────────────────────


async def enqueue_ingest_turn_job(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    assistant_message_id: str,
    user_content: str,
    assistant_content: str = "",
    parent_session_id: str | None = None,
    session_title: str | None = None,
    chat_mode: str = "GENERAL",
    user_message_id: str | None = None,
    turn_index: int = 1,
    turn_as_of_date: date | None = None,
    memory_mode: str = "auto",
    tickers: Sequence[str] = (),
    sectors: Sequence[str] = (),
    macro_factors: Sequence[str] = (),
    cited_doc_version_ids: Sequence[str] = (),
    blindspot_codes: Sequence[str] = (),
    stage7_hallucinated: bool = False,
    stream_interrupted: bool = False,
    reference_today: date | None = None,
) -> dict[str, Any]:
    """W0 Gate (0 LLM, independent of Kev-4b): idempotent enqueue into `user_memory_jobs`."""
    if memory_mode in ("incognito", "read_only"):
        return {
            "accepted": False,
            "job_id": None,
            "deduplicated": False,
            "is_backtest_sandbox": False,
            "assistant_clean_for_digest": False,
            "reason": f"memory_mode_{memory_mode}",
        }
    if getattr(settings, "user_memory_mode", "on") == "off":
        return {
            "accepted": False,
            "job_id": None,
            "deduplicated": False,
            "is_backtest_sandbox": False,
            "assistant_clean_for_digest": False,
            "reason": "user_memory_mode_off",
        }

    clean_user = normalize_memory_text(user_content)
    has_ticker_or_digit = bool(
        tickers or re.search(r"\d", clean_user) or _UPPER_3_RE.search(clean_user)
    )
    has_cue = bool(_PREFERENCE_OR_PORTFOLIO_CUE_RE.search(clean_user))
    if len(clean_user) < 15 and not has_ticker_or_digit and not has_cue:
        return {
            "accepted": False,
            "job_id": None,
            "deduplicated": False,
            "is_backtest_sandbox": False,
            "assistant_clean_for_digest": False,
            "reason": "trivial_chitchat_skipped",
        }

    today = reference_today or date.today()
    effective_as_of = turn_as_of_date or today
    sandbox = is_backtest_date(effective_as_of, reference_today=today)

    stage7_flagged = bool(stage7_hallucinated) or bool(
        set(str(c).strip().upper() for c in blindspot_codes) & _STAGE7_HALLUCINATION_CODES
    )
    interrupted = bool(stream_interrupted) or ("STREAM_INTERRUPTED_UNVERIFIED" in (assistant_content or ""))
    assistant_clean = (not stage7_flagged) and (not interrupted) and bool((assistant_content or "").strip())

    # If turn is only a failed assistant answer AND user turn has no preference/portfolio/thesis cue,
    # still allow enqueue if user turn is substantive (>= 15 chars) so pure user preferences are never lost.
    idem_raw = f"{tenant_id.strip()}:{user_id.strip()}:{session_id.strip()}:{assistant_message_id.strip()}"
    idempotency_key = hashlib.sha256(idem_raw.encode("utf-8")).hexdigest()

    existing = await session.scalar(
        select(UserMemoryJob).where(UserMemoryJob.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return {
            "accepted": True,
            "job_id": existing.job_id,
            "deduplicated": True,
            "is_backtest_sandbox": sandbox,
            "assistant_clean_for_digest": assistant_clean,
            "reason": "idempotent_duplicate",
        }

    now = datetime.now(UTC)
    explicit_remember = bool(_EXPLICIT_REMEMBER_RE.search(clean_user))
    debounce_s = 0.0 if explicit_remember else float(getattr(settings, "user_memory_debounce_seconds", 8.0))
    available_at = now + timedelta(seconds=max(0.0, debounce_s))

    payload = {
        "session_id": session_id.strip(),
        "parent_session_id": (parent_session_id or "").strip() or None,
        "session_title": (session_title or "").strip()[:256] or None,
        "chat_mode": (chat_mode or "GENERAL").strip().upper(),
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id.strip(),
        "user_content": clean_user[:8000],
        "assistant_topic_outline": (
            strip_numeric_tables_for_outline(assistant_content) if assistant_clean else ""
        ),
        "assistant_clean_for_digest": assistant_clean,
        "turn_index": int(turn_index),
        "turn_as_of_date": effective_as_of.isoformat(),
        "is_backtest_sandbox": sandbox,
        "tickers": list(dict.fromkeys(str(t).strip().upper() for t in tickers if str(t).strip()))[:32],
        "sectors": list(dict.fromkeys(str(s).strip() for s in sectors if str(s).strip()))[:24],
        "macro_factors": list(dict.fromkeys(str(m).strip() for m in macro_factors if str(m).strip()))[:24],
        "cited_doc_version_ids": list(
            dict.fromkeys(str(d).strip() for d in cited_doc_version_ids if str(d).strip())
        )[:64],
    }

    job = UserMemoryJob(
        job_id=str(uuid.uuid4()),
        idempotency_key=idempotency_key,
        job_kind="INGEST_TURN",
        tenant_id=tenant_id.strip(),
        user_id=user_id.strip(),
        session_id=session_id.strip(),
        payload_json=payload,
        status="QUEUED",
        attempts=0,
        available_at=available_at,
    )
    try:
        async with session.begin_nested():
            session.add(job)
            await session.flush()
    except IntegrityError:
        existing_race = await session.scalar(
            select(UserMemoryJob).where(UserMemoryJob.idempotency_key == idempotency_key)
        )
        return {
            "accepted": True,
            "job_id": existing_race.job_id if existing_race is not None else None,
            "deduplicated": True,
            "is_backtest_sandbox": sandbox,
            "assistant_clean_for_digest": assistant_clean,
            "reason": "idempotent_duplicate",
        }
    return {
        "accepted": True,
        "job_id": job.job_id,
        "deduplicated": False,
        "is_backtest_sandbox": sandbox,
        "assistant_clean_for_digest": assistant_clean,
        "reason": "enqueued",
    }


# ── W1: Single-Pass Structured Extractor ─────────────────────────────────────

_W1_EXTRACTOR_SYSTEM_PROMPT = (
    "Bạn là bộ trích xuất ký ức dài hạn nhà đầu tư (SAG Mem0-g Tri-Temporal Extractor).\n"
    "CHỈ trích xuất 5 loại hợp lệ (Allowlist): "
    "STYLE_PREF_SAFE (thuần định dạng, KHÔNG có mã/số/sự kiện thị trường), "
    "USER_INVESTING_CRITERIA (ngưỡng lọc P/E, ROE, D/E, ngành tránh/ưu tiên), "
    "USER_EXTERNAL_HOLDING (vị thế tài khoản ngoài như SSI/VPS do user tự khai báo), "
    "INVESTMENT_THESIS (luận điểm, lo ngại rủi ro, quan điểm tăng/giảm của user), "
    "SESSION_EPISODE_DIGEST (tóm tắt 1 câu chủ đề phiên — KHÔNG chứa con số BCTC).\n"
    "QUY TẮC CỨNG (DENYLIST):\n"
    "1. CHỈ tin lời nhà đầu tư trong <USER_TURN_ONLY> cho sở thích/tiêu chí/vị thế/luận điểm; "
    "<ASSISTANT_TOPIC_OUTLINE> chỉ dùng để biết chủ đề phiên.\n"
    "2. TUYỆT ĐỐI KHÔNG lưu con số BCTC/doanh thu/lợi nhuận/giá thị trường của doanh nghiệp.\n"
    "3. Gắn `modality`: ASSERTED (khẳng định thật), HYPOTHETICAL (giả sử/nếu tôi mua), hoặc QUESTION (chỉ hỏi).\n"
    "4. Giữ nguyên phủ định tiếng Việt (`không thích`, `tránh`, `đã bán hết`, `chưa mua`) "
    "và gán `polarity`: POSITIVE | NEGATIVE.\n"
    "5. Gắn `explicit_override: true` CHỈ khi user ra lệnh tường minh thay đổi/bỏ quy tắc cũ.\n"
    "6. `graph_triples` chỉ dùng 16 quan hệ: HOLDS_EXTERNAL, SOLD_EXIT, WATCHES_EXTERNAL, AVOIDS, "
    "BULLISH_ON, BEARISH_ON, NEUTRAL_SKEPTICAL, PREFERS_CRITERION, AVOIDS_SECTOR, CONCERNED_ABOUT_RISK, "
    "MONITORING_CATALYST, SESSION_DISCUSSED, SESSION_COMPARED, SESSION_CONTINUED_FROM, "
    "SESSION_THESIS_EVOLUTION, SESSION_RELATED_THEME.\n"
    "Trả về DUY NHẤT JSON object theo schema: "
    '{"memories":[{"memory_type":str,"modality":"ASSERTED"|"HYPOTHETICAL"|"QUESTION","polarity":"POSITIVE"|"NEGATIVE",'
    '"content_text":str,"canonical_slot_key":str|null,"tickers":[str],"confidence":float,"explicit_override":bool,'
    '"structured_payload":object}],"graph_triples":[{"src_type":"USER"|"TICKER"|"SECTOR"|"MACRO_FACTOR"|"THEME"|"SESSION",'
    '"src_key":str,"src_name":str,"relation":str,"dst_type":str,"dst_key":str,"dst_name":str,"fact_text":str}],'
    '"episode_summary":str,"tickers":[str],"sectors":[str],"macro_factors":[str],"topic_tags":[str]}'
)


async def extract_turn_memories_with_llm(
    *,
    llm: Any,
    user_content: str,
    assistant_topic_outline: str,
    assistant_clean_for_digest: bool,
    turn_as_of_date: date,
    hint_tickers: Sequence[str] = (),
    hint_sectors: Sequence[str] = (),
    hint_macro_factors: Sequence[str] = (),
    cited_doc_version_ids: Sequence[str] = (),
    issuer_rows: Sequence[Mapping[str, Any]] | None = None,
    known_tickers: Sequence[str] | None = None,
) -> ExtractedTurnBundle:
    """W1 Single-Pass Structured Extractor separating `<USER_TURN_ONLY>` vs `<ASSISTANT_TOPIC_OUTLINE>`."""
    user_msg = (
        f"Ngày hiệu lực PIT phiên (turn_as_of_date): {turn_as_of_date.isoformat()}\n"
        f"Mã gợi ý từ phiên: {', '.join(hint_tickers) if hint_tickers else 'none'}\n\n"
        f"<USER_TURN_ONLY>\n{user_content}\n</USER_TURN_ONLY>\n\n"
        f"<ASSISTANT_TOPIC_OUTLINE>\n"
        f"{assistant_topic_outline if assistant_clean_for_digest else '[DISABLED_UNVERIFIED_TURN]'}\n"
        f"</ASSISTANT_TOPIC_OUTLINE>"
    )
    model_override = (getattr(settings, "user_memory_extractor_model", None) or "").strip() or None
    raw_output = ""
    if llm is not None and getattr(llm, "configured", True) and hasattr(llm, "complete"):
        kwargs: dict[str, Any] = {"response_format": {"type": "json_object"}}
        if model_override:
            kwargs["model_override"] = model_override
        try:
            raw_output = await llm.complete(
                [
                    {"role": "system", "content": _W1_EXTRACTOR_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                **kwargs,
            )
        except TypeError:
            raw_output = await llm.complete(
                [
                    {"role": "system", "content": _W1_EXTRACTOR_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                response_format={"type": "json_object"},
            )

    parsed: dict[str, Any] = {}
    if raw_output:
        try:
            obj = json.loads(_strip_json_fences(raw_output))
            if isinstance(obj, dict):
                parsed = obj
        except (ValueError, TypeError):
            parsed = {}

    bundle = ExtractedTurnBundle(
        episode_summary=(
            normalize_memory_text(str(parsed.get("episode_summary") or ""))[:400]
            if assistant_clean_for_digest
            else ""
        ),
        tickers=_normalize_tickers(
            [*hint_tickers, *(parsed.get("tickers") or ())],
            f"{user_content}\n{assistant_topic_outline if assistant_clean_for_digest else ''}",
            issuer_rows=issuer_rows,
            known_tickers=known_tickers,
        ),
        sectors=list(
            dict.fromkeys(
                str(s).strip()
                for s in [*hint_sectors, *(parsed.get("sectors") or ())]
                if str(s).strip()
            )
        )[:16],
        macro_factors=list(
            dict.fromkeys(
                str(m).strip()
                for m in [*hint_macro_factors, *(parsed.get("macro_factors") or ())]
                if str(m).strip()
            )
        )[:16],
        topic_tags=list(
            dict.fromkeys(str(t).strip() for t in (parsed.get("topic_tags") or ()) if str(t).strip())
        )[:16],
    )

    for raw_item in parsed.get("memories") or ():
        if not isinstance(raw_item, Mapping):
            continue
        sanitized, err = sanitize_extracted_candidate(
            raw_item,
            user_turn_text=user_content,
            assistant_clean_for_digest=assistant_clean_for_digest,
            issuer_rows=issuer_rows,
            known_tickers=known_tickers,
            cited_doc_version_ids=cited_doc_version_ids,
        )
        if sanitized is not None:
            bundle.memories.append(sanitized)
        elif err:
            bundle.rejected_reasons.append(
                {
                    "reason": err,
                    "excerpt": str(raw_item.get("content_text") or "")[:120],
                }
            )

    # Validate graph triples against closed 16-relation ontology & denylist
    if not _PROMPT_INJECTION_RE.search(user_content) and not _HYPOTHETICAL_SCENARIO_RE.search(user_content):
        for raw_tr in parsed.get("graph_triples") or ():
            if not isinstance(raw_tr, Mapping):
                continue
            rel = str(raw_tr.get("relation") or raw_tr.get("relation_type") or "").strip().upper()
            if rel not in CLOSED_GRAPH_RELATIONS:
                continue
            src_key = str(raw_tr.get("src_key") or "USER:SELF").strip()
            dst_key = str(raw_tr.get("dst_key") or "").strip()
            if not src_key or not dst_key:
                continue
            bundle.graph_triples.append(
                {
                    "src_type": str(raw_tr.get("src_type") or "USER").strip().upper(),
                    "src_key": src_key[:160],
                    "src_name": str(raw_tr.get("src_name") or src_key).strip()[:160],
                    "relation": rel,
                    "dst_type": str(raw_tr.get("dst_type") or "TICKER").strip().upper(),
                    "dst_key": dst_key[:160],
                    "dst_name": str(raw_tr.get("dst_name") or dst_key).strip()[:160],
                    "fact_text": normalize_memory_text(str(raw_tr.get("fact_text") or ""))[:400],
                }
            )

    return bundle


# ── W2/W3: Tri-Temporal Reconciliation & Protection of Pinned Memories ───────

_W3_RECONCILE_SYSTEM_PROMPT = (
    "Bạn là bộ hợp nhất ký ức Tri-Temporal (SAG Mem0-g Reconciliation).\n"
    "Với mỗi candidate mới và danh sách existing memories tương tự/xung đột, chọn đúng 1 hành động:\n"
    "- ADD: ký ức mới độc lập, giữ nguyên cũ.\n"
    "- UPDATE_STATE: thực tế/quan điểm của user đã thay đổi theo thời gian (đóng valid_to của cũ, tạo mới).\n"
    "- CORRECT_ERROR: sửa lỗi trích xuất trước đó (đóng tx_to của cũ, tạo mới).\n"
    "- INVALIDATE: vô hiệu hóa ký ức cũ mà không thay bằng cái mới.\n"
    "- REINFORCE: trùng ý nghĩa với ký ức cũ -> tăng reinforcement_count.\n"
    "- NOOP: bỏ qua candidate.\n"
    "QUAN TRỌNG: Nếu existing memory có `pinned_by_user=true` hoặc `source_authority='USER_CONFIRMED'`, "
    "KHÔNG ĐƯỢC chọn UPDATE_STATE/CORRECT_ERROR/INVALIDATE trừ khi candidate có `explicit_override=true`.\n"
    'Trả về JSON object: {"decisions":[{"candidate_index":int,'
    '"action":"ADD"|"UPDATE_STATE"|"CORRECT_ERROR"|"INVALIDATE"|"REINFORCE"|"NOOP",'
    '"target_memory_id":str|null,"reason":str}]}'
)


def _deterministic_reconcile_fallback(
    candidate: SanitizedMemoryCandidate,
    existing_items: Sequence[UserMemoryItem],
) -> dict[str, Any]:
    """Deterministic fallback when W3 LLM is unavailable: reconcile slot/stance conflicts cleanly."""
    for old in existing_items:
        if old.content_hash == candidate.content_hash:
            return {
                "action": "REINFORCE",
                "target_memory_id": old.memory_id,
                "reason": "exact_content_match",
            }
        if candidate.canonical_slot_key and old.canonical_slot_key == candidate.canonical_slot_key:
            return {
                "action": "UPDATE_STATE",
                "target_memory_id": old.memory_id,
                "reason": f"slot_updated:{candidate.canonical_slot_key}",
            }
        if (
            old.memory_type == candidate.memory_type
            and candidate.tickers
            and set(old.tickers_json or ()) & set(candidate.tickers)
        ):
            old_pol = str((old.structured_payload_json or {}).get("polarity") or "POSITIVE").upper()
            if old_pol != candidate.polarity or candidate.memory_type in (
                "USER_EXTERNAL_HOLDING",
                "INVESTMENT_THESIS",
            ):
                return {
                    "action": "UPDATE_STATE",
                    "target_memory_id": old.memory_id,
                    "reason": "ticker_state_or_stance_changed",
                }
    return {"action": "ADD", "target_memory_id": None, "reason": "independent_memory"}


async def _run_w3_batched_reconciliation(
    *,
    llm: Any,
    batch: Sequence[tuple[int, SanitizedMemoryCandidate, list[UserMemoryItem]]],
) -> dict[int, dict[str, Any]]:
    """Run 1 batched LLM call for all candidates that have conflict/duplicate matches."""
    if not batch:
        return {}
    if llm is None or not getattr(llm, "configured", True) or not hasattr(llm, "complete"):
        return {
            idx: _deterministic_reconcile_fallback(cand, existing)
            for idx, cand, existing in batch
        }

    prompt_items = []
    for idx, cand, existing in batch:
        prompt_items.append(
            {
                "candidate_index": idx,
                "candidate": {
                    "memory_type": cand.memory_type,
                    "canonical_slot_key": cand.canonical_slot_key,
                    "content_text": cand.content_text,
                    "tickers": cand.tickers,
                    "polarity": cand.polarity,
                    "confidence": cand.confidence,
                    "explicit_override": cand.explicit_override,
                },
                "existing_candidates": [
                    {
                        "memory_id": e.memory_id,
                        "memory_type": e.memory_type,
                        "canonical_slot_key": e.canonical_slot_key,
                        "content_text": e.content_text,
                        "tickers": list(e.tickers_json or []),
                        "source_authority": e.source_authority,
                        "pinned_by_user": bool(e.pinned_by_user),
                        "source_as_of_date": e.source_as_of_date.isoformat(),
                    }
                    for e in existing[:6]
                ],
            }
        )

    model_override = (getattr(settings, "user_memory_extractor_model", None) or "").strip() or None
    kwargs: dict[str, Any] = {"response_format": {"type": "json_object"}}
    if model_override:
        kwargs["model_override"] = model_override
    try:
        raw = await llm.complete(
            [
                {"role": "system", "content": _W3_RECONCILE_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"items": prompt_items}, ensure_ascii=False)},
            ],
            **kwargs,
        )
    except TypeError:
        raw = await llm.complete(
            [
                {"role": "system", "content": _W3_RECONCILE_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"items": prompt_items}, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("W3 batched reconciliation LLM failed (%s); falling back deterministically", type(exc).__name__)
        return {
            idx: _deterministic_reconcile_fallback(cand, existing)
            for idx, cand, existing in batch
        }

    decisions: dict[int, dict[str, Any]] = {}
    try:
        parsed = json.loads(_strip_json_fences(raw))
        for dec in parsed.get("decisions") or ():
            if not isinstance(dec, Mapping):
                continue
            cidx = int(dec.get("candidate_index", -1))
            action = str(dec.get("action") or "ADD").strip().upper()
            if action not in {"ADD", "UPDATE_STATE", "CORRECT_ERROR", "INVALIDATE", "REINFORCE", "NOOP"}:
                action = "ADD"
            decisions[cidx] = {
                "action": action,
                "target_memory_id": str(dec.get("target_memory_id") or "").strip() or None,
                "reason": str(dec.get("reason") or "")[:240],
            }
    except (ValueError, TypeError):
        pass

    for idx, cand, existing in batch:
        if idx not in decisions:
            decisions[idx] = _deterministic_reconcile_fallback(cand, existing)
    return decisions


def _audit_entry(
    *,
    tenant_id: str,
    user_id: str,
    memory_id: str | None,
    action: str,
    actor: str = "WORKER",
    before_json: dict[str, Any] | None = None,
    after_json: dict[str, Any] | None = None,
    reason: str | None = None,
    llm_model: str | None = None,
) -> UserMemoryAuditLog:
    return UserMemoryAuditLog(
        audit_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        user_id=user_id,
        memory_id=memory_id,
        action=action,
        actor=actor,
        llm_model=llm_model,
        prompt_version=PROMPT_VERSION,
        before_json=before_json,
        after_json=after_json,
        reason=(reason or "")[:500] or None,
    )


def _memory_snapshot(item: UserMemoryItem) -> dict[str, Any]:
    return {
        "memory_id": item.memory_id,
        "memory_type": item.memory_type,
        "content_text": item.content_text,
        "canonical_slot_key": item.canonical_slot_key,
        "pinned_by_user": bool(item.pinned_by_user),
        "source_authority": item.source_authority,
        "valid_to": item.valid_to.isoformat() if item.valid_to else None,
        "tx_to": item.tx_to.isoformat() if item.tx_to else None,
        "staleness_status": item.staleness_status,
        "sync_version": int(item.sync_version or 1),
    }


async def _upsert_entity(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    entity_type: str,
    canonical_key: str,
    display_name: str,
) -> UserMemoryEntity:
    existing = await session.scalar(
        select(UserMemoryEntity).where(
            UserMemoryEntity.tenant_id == tenant_id,
            UserMemoryEntity.user_id == user_id,
            UserMemoryEntity.canonical_key == canonical_key,
        )
    )
    if existing is not None:
        if display_name and len(display_name) > len(existing.display_name or ""):
            existing.display_name = display_name[:160]
        return existing
    ent = UserMemoryEntity(
        entity_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        user_id=user_id,
        entity_type=entity_type[:24],
        canonical_key=canonical_key[:160],
        display_name=(display_name or canonical_key)[:160],
        aliases_json=[],
    )
    session.add(ent)
    await session.flush()
    return ent


async def _upsert_personal_edge(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    src_entity: UserMemoryEntity,
    dst_entity: UserMemoryEntity,
    relation_type: str,
    fact_text: str,
    memory_id: str | None,
    origin_session_id: str | None,
    source_as_of_date: date,
    is_backtest_sandbox: bool,
    weight: float = 1.0,
    now: datetime | None = None,
) -> UserMemoryEdge | None:
    rel = relation_type.strip().upper()
    if rel not in CLOSED_GRAPH_RELATIONS:
        return None
    ts_now = now or datetime.now(UTC)
    conflict_group = RELATION_CONFLICT_GROUP.get(rel)

    # Check if an identical active edge already exists
    existing_same = await session.scalar(
        select(UserMemoryEdge).where(
            UserMemoryEdge.tenant_id == tenant_id,
            UserMemoryEdge.user_id == user_id,
            UserMemoryEdge.src_entity_id == src_entity.entity_id,
            UserMemoryEdge.dst_entity_id == dst_entity.entity_id,
            UserMemoryEdge.relation_type == rel,
            UserMemoryEdge.is_backtest_sandbox.is_(is_backtest_sandbox),
            UserMemoryEdge.valid_to >= INF_DATETIME,
            UserMemoryEdge.tx_to >= INF_DATETIME,
        )
    )
    if existing_same is not None:
        existing_same.weight = max(float(existing_same.weight or 0.0), float(weight))
        if fact_text:
            existing_same.fact_text = fact_text[:400]
        if memory_id:
            existing_same.memory_id = memory_id
        return existing_same

    new_edge = UserMemoryEdge(
        edge_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        user_id=user_id,
        src_entity_id=src_entity.entity_id,
        dst_entity_id=dst_entity.entity_id,
        relation_type=rel,
        conflict_group=conflict_group,
        weight=float(weight),
        fact_text=fact_text[:400],
        memory_id=memory_id,
        origin_session_id=origin_session_id,
        source_as_of_date=source_as_of_date,
        is_backtest_sandbox=is_backtest_sandbox,
        valid_from=ts_now,
        valid_to=INF_DATETIME,
        tx_from=ts_now,
        tx_to=INF_DATETIME,
    )
    session.add(new_edge)
    await session.flush()

    if conflict_group:
        conflicting = (
            await session.scalars(
                select(UserMemoryEdge).where(
                    UserMemoryEdge.tenant_id == tenant_id,
                    UserMemoryEdge.user_id == user_id,
                    UserMemoryEdge.src_entity_id == src_entity.entity_id,
                    UserMemoryEdge.dst_entity_id == dst_entity.entity_id,
                    UserMemoryEdge.conflict_group == conflict_group,
                    UserMemoryEdge.edge_id != new_edge.edge_id,
                    UserMemoryEdge.is_backtest_sandbox.is_(is_backtest_sandbox),
                    UserMemoryEdge.valid_to >= INF_DATETIME,
                    UserMemoryEdge.tx_to >= INF_DATETIME,
                )
            )
        ).all()
        for old_edge in conflicting:
            old_edge.valid_to = ts_now
            old_edge.superseded_by_edge_id = new_edge.edge_id

    return new_edge


def _jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa = {str(x).strip().lower() for x in a if str(x).strip()}
    sb = {str(x).strip().lower() for x in b if str(x).strip()}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / float(len(sa | sb))


def _text_cosine_sim(
    text_a: str,
    text_b: str,
    *,
    vec_a: Sequence[float] | None = None,
    vec_b: Sequence[float] | None = None,
) -> float:
    if vec_a and vec_b and len(vec_a) == len(vec_b):
        dot = sum(float(x) * float(y) for x, y in zip(vec_a, vec_b, strict=True))
        na = math.sqrt(sum(float(x) * float(x) for x in vec_a))
        nb = math.sqrt(sum(float(y) * float(y) for y in vec_b))
        if na > 1e-12 and nb > 1e-12:
            return max(0.0, min(1.0, dot / (na * nb)))
    toks_a = Counter(re.findall(r"[^\W_]{2,}", (text_a or "").lower(), flags=re.UNICODE))
    toks_b = Counter(re.findall(r"[^\W_]{2,}", (text_b or "").lower(), flags=re.UNICODE))
    if not toks_a or not toks_b:
        return 0.0
    common = set(toks_a) & set(toks_b)
    if not common:
        return 0.0
    dot = sum(toks_a[k] * toks_b[k] for k in common)
    na = math.sqrt(sum(v * v for v in toks_a.values()))
    nb = math.sqrt(sum(v * v for v in toks_b.values()))
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


async def link_related_sessions_for_episode(
    session: AsyncSession,
    *,
    episode: UserMemoryEpisode,
    now: datetime | None = None,
    top_k: int = 3,
    min_score: float = 0.52,
) -> list[dict[str, Any]]:
    """W4 Anti-Hairball Top-3 Session Linker.

    Scores `0.45*cos + 0.30*Jaccard(tickers) + 0.25*Jaccard(macro ∪ sector) >= 0.52`.
    Strictly PIT-safe (`other.source_as_of_date <= episode.source_as_of_date`, same sandbox),
    and always creates an explicit `SESSION_CONTINUED_FROM` edge when `parent_session_id` is set.
    """
    ts_now = now or datetime.now(UTC)
    src_sess_ent = await _upsert_entity(
        session,
        tenant_id=episode.tenant_id,
        user_id=episode.user_id,
        entity_type="SESSION",
        canonical_key=f"SESSION:{episode.external_session_id}",
        display_name=episode.session_title or f"Session {episode.external_session_id[:8]}",
    )

    # 1. Explicit continuation edge if parent_session_id is provided
    created_links: list[dict[str, Any]] = []
    if episode.parent_session_id and episode.parent_session_id != episode.external_session_id:
        parent_ep = await session.scalar(
            select(UserMemoryEpisode).where(
                UserMemoryEpisode.tenant_id == episode.tenant_id,
                UserMemoryEpisode.user_id == episode.user_id,
                UserMemoryEpisode.external_session_id == episode.parent_session_id,
                UserMemoryEpisode.status == "ACTIVE",
                UserMemoryEpisode.is_backtest_sandbox.is_(bool(episode.is_backtest_sandbox)),
                UserMemoryEpisode.source_as_of_date <= episode.source_as_of_date,
            )
        )
        if parent_ep is not None:
            parent_ent = await _upsert_entity(
                session,
                tenant_id=episode.tenant_id,
                user_id=episode.user_id,
                entity_type="SESSION",
                canonical_key=f"SESSION:{parent_ep.external_session_id}",
                display_name=parent_ep.session_title or f"Session {parent_ep.external_session_id[:8]}",
            )
            await _upsert_personal_edge(
                session,
                tenant_id=episode.tenant_id,
                user_id=episode.user_id,
                src_entity=src_sess_ent,
                dst_entity=parent_ent,
                relation_type="SESSION_CONTINUED_FROM",
                fact_text=f"Tiếp nối phiên {parent_ep.session_title or parent_ep.external_session_id}",
                memory_id=None,
                origin_session_id=episode.external_session_id,
                source_as_of_date=episode.source_as_of_date,
                is_backtest_sandbox=bool(episode.is_backtest_sandbox),
                weight=1.0,
                now=ts_now,
            )
            created_links.append(
                {
                    "session_id": parent_ep.external_session_id,
                    "relation_type": "SESSION_CONTINUED_FROM",
                    "score": 1.0,
                }
            )

    # 2. Candidate episodes for semantic + ticker + macro/sector Top-3 linking (strict PIT!)
    candidates = (
        await session.scalars(
            select(UserMemoryEpisode)
            .where(
                UserMemoryEpisode.tenant_id == episode.tenant_id,
                UserMemoryEpisode.user_id == episode.user_id,
                UserMemoryEpisode.episode_id != episode.episode_id,
                UserMemoryEpisode.status == "ACTIVE",
                UserMemoryEpisode.is_backtest_sandbox.is_(bool(episode.is_backtest_sandbox)),
                UserMemoryEpisode.source_as_of_date <= episode.source_as_of_date,
            )
            .order_by(UserMemoryEpisode.source_as_of_date.desc(), UserMemoryEpisode.updated_at.desc())
            .limit(200)
        )
    ).all()

    cur_text = " ".join(
        [
            episode.session_title or "",
            episode.episode_summary or "",
            " ".join(episode.tickers_json or []),
            " ".join(episode.sectors_json or []),
            " ".join(episode.macro_factors_json or []),
            " ".join(episode.topic_tags_json or []),
        ]
    ).strip()
    cur_vec = get_cached_text_embedding(cur_text) if cur_text else None
    cur_macro_sector = [*(episode.macro_factors_json or []), *(episode.sectors_json or [])]

    scored: list[tuple[float, UserMemoryEpisode, str]] = []
    for other in candidates:
        if episode.parent_session_id and other.external_session_id == episode.parent_session_id:
            continue
        other_text = " ".join(
            [
                other.session_title or "",
                other.episode_summary or "",
                " ".join(other.tickers_json or []),
                " ".join(other.sectors_json or []),
                " ".join(other.macro_factors_json or []),
                " ".join(other.topic_tags_json or []),
            ]
        ).strip()
        other_vec = get_cached_text_embedding(other_text) if other_text else None
        cos_sim = _text_cosine_sim(cur_text, other_text, vec_a=cur_vec, vec_b=other_vec)
        j_tickers = _jaccard(episode.tickers_json or [], other.tickers_json or [])
        other_macro_sector = [*(other.macro_factors_json or []), *(other.sectors_json or [])]
        j_macro_sector = _jaccard(cur_macro_sector, other_macro_sector)

        link_score = round(0.45 * cos_sim + 0.30 * j_tickers + 0.25 * j_macro_sector, 4)
        if link_score >= min_score:
            rel_type = "SESSION_THESIS_EVOLUTION" if j_tickers > 0.0 else "SESSION_RELATED_THEME"
            scored.append((link_score, other, rel_type))

    scored.sort(key=lambda item: (-item[0], -item[1].source_as_of_date.toordinal()))
    top_matches = scored[: max(1, int(top_k))]
    chosen_dst_keys = {f"SESSION:{other.external_session_id}" for _, other, _ in top_matches}

    # Close any older automatic semantic links from this session that fell outside Top-3
    existing_auto = (
        await session.scalars(
            select(UserMemoryEdge).where(
                UserMemoryEdge.tenant_id == episode.tenant_id,
                UserMemoryEdge.user_id == episode.user_id,
                UserMemoryEdge.src_entity_id == src_sess_ent.entity_id,
                UserMemoryEdge.relation_type.in_(("SESSION_THESIS_EVOLUTION", "SESSION_RELATED_THEME")),
                UserMemoryEdge.valid_to >= INF_DATETIME,
                UserMemoryEdge.tx_to >= INF_DATETIME,
            )
        )
    ).all()
    if existing_auto:
        dst_ids = [e.dst_entity_id for e in existing_auto]
        dst_entities = {
            ent.entity_id: ent.canonical_key
            for ent in (
                await session.scalars(
                    select(UserMemoryEntity).where(UserMemoryEntity.entity_id.in_(dst_ids))
                )
            ).all()
        }
        for edge in existing_auto:
            if dst_entities.get(edge.dst_entity_id) not in chosen_dst_keys:
                edge.valid_to = ts_now

    for score, other, rel_type in top_matches:
        dst_ent = await _upsert_entity(
            session,
            tenant_id=episode.tenant_id,
            user_id=episode.user_id,
            entity_type="SESSION",
            canonical_key=f"SESSION:{other.external_session_id}",
            display_name=other.session_title or f"Session {other.external_session_id[:8]}",
        )
        shared_tks = sorted(set(episode.tickers_json or []) & set(other.tickers_json or []))
        other_ms = [*(other.macro_factors_json or []), *(other.sectors_json or [])]
        shared_ms = sorted(set(cur_macro_sector) & set(other_ms))
        await _upsert_personal_edge(
            session,
            tenant_id=episode.tenant_id,
            user_id=episode.user_id,
            src_entity=src_sess_ent,
            dst_entity=dst_ent,
            relation_type=rel_type,
            fact_text=f"Liên kết ({score:.2f}) qua tickers={shared_tks} macro/sector={shared_ms}",
            memory_id=None,
            origin_session_id=episode.external_session_id,
            source_as_of_date=episode.source_as_of_date,
            is_backtest_sandbox=bool(episode.is_backtest_sandbox),
            weight=score,
            now=ts_now,
        )
        created_links.append(
            {
                "session_id": other.external_session_id,
                "relation_type": rel_type,
                "score": score,
            }
        )

    return created_links


async def _enforce_user_quotas(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    now: datetime,
) -> int:
    """Enforce per-user active memory & episode quotas (`2,000` items / `500` episodes)."""
    max_items = int(getattr(settings, "user_memory_max_items_per_user", 2000))
    active_count = int(
        await session.scalar(
            select(func.count())
            .select_from(UserMemoryItem)
            .where(
                UserMemoryItem.tenant_id == tenant_id,
                UserMemoryItem.user_id == user_id,
                UserMemoryItem.valid_to >= INF_DATETIME,
                UserMemoryItem.tx_to >= INF_DATETIME,
            )
        )
        or 0
    )
    evicted = 0
    if active_count > max_items:
        overflow = active_count - max_items
        victims = (
            await session.scalars(
                select(UserMemoryItem)
                .where(
                    UserMemoryItem.tenant_id == tenant_id,
                    UserMemoryItem.user_id == user_id,
                    UserMemoryItem.valid_to >= INF_DATETIME,
                    UserMemoryItem.tx_to >= INF_DATETIME,
                    UserMemoryItem.pinned_by_user.is_(False),
                )
                .order_by(UserMemoryItem.reinforcement_count.asc(), UserMemoryItem.created_at.asc())
                .limit(overflow)
            )
        ).all()
        for victim in victims:
            victim.tx_to = now
            victim.invalidation_reason = "QUOTA_EVICTED"
            victim.qdrant_sync_status = "PENDING_DELETE"
            victim.sync_version = int(victim.sync_version or 1) + 1
            session.add(
                _audit_entry(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    memory_id=victim.memory_id,
                    action="QUOTA_EVICT",
                    actor="SYSTEM",
                    reason=f"Exceeded user_memory_max_items_per_user={max_items}",
                )
            )
            evicted += 1

    max_episodes = int(getattr(settings, "user_memory_max_episodes_per_user", 500))
    ep_count = int(
        await session.scalar(
            select(func.count())
            .select_from(UserMemoryEpisode)
            .where(
                UserMemoryEpisode.tenant_id == tenant_id,
                UserMemoryEpisode.user_id == user_id,
                UserMemoryEpisode.status == "ACTIVE",
            )
        )
        or 0
    )
    if ep_count > max_episodes:
        ep_overflow = ep_count - max_episodes
        old_eps = (
            await session.scalars(
                select(UserMemoryEpisode)
                .where(
                    UserMemoryEpisode.tenant_id == tenant_id,
                    UserMemoryEpisode.user_id == user_id,
                    UserMemoryEpisode.status == "ACTIVE",
                )
                .order_by(UserMemoryEpisode.updated_at.asc())
                .limit(ep_overflow)
            )
        ).all()
        for old_ep in old_eps:
            old_ep.status = "ARCHIVED"

    return evicted


async def _embed_single_text(embedder: Any, text_in: str) -> list[float] | None:
    if not text_in or embedder is None:
        return None
    model_sig = str(getattr(embedder, "model", "") or settings.embedding_model or "")
    cached = get_cached_text_embedding(text_in, model_signature=model_sig)
    if cached is not None:
        return cached
    vec: list[float] | None = None
    if hasattr(embedder, "batch_generate"):
        vecs = await embedder.batch_generate([text_in])
        if vecs:
            vec = [float(x) for x in vecs[0]]
    elif hasattr(embedder, "generate"):
        raw = await embedder.generate(text_in)
        if raw:
            vec = [float(x) for x in raw]
    elif hasattr(embedder, "embed_query"):
        raw = embedder.embed_query(text_in)
        if asyncio.iscoroutine(raw):
            raw = await raw
        if raw:
            vec = [float(x) for x in raw]
    elif callable(embedder):
        raw = embedder([text_in])
        if asyncio.iscoroutine(raw):
            raw = await raw
        if raw:
            first = raw[0] if isinstance(raw[0], (list, tuple)) else raw
            vec = [float(x) for x in first]
    if vec:
        put_cached_text_embedding(text_in, vec, model_signature=model_sig)
    return vec


async def process_ingest_turn_payload(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    payload: Mapping[str, Any],
    llm: Any = None,
    qdrant_client: httpx.AsyncClient | None = None,
    embedder: Any = None,
    extracted_override: ExtractedTurnBundle | None = None,
) -> dict[str, Any]:
    """Execute W1 -> W2 -> W3 -> W4 atomically for one turn payload."""
    session_id = str(payload.get("session_id") or "").strip()
    user_content = str(payload.get("user_content") or "")
    assistant_outline = str(payload.get("assistant_topic_outline") or "")
    assistant_clean = bool(payload.get("assistant_clean_for_digest", True))
    turn_as_of_date = date.fromisoformat(str(payload.get("turn_as_of_date") or date.today().isoformat())[:10])
    is_sandbox = bool(payload.get("is_backtest_sandbox", False))
    hint_tickers = list(payload.get("tickers") or [])
    hint_sectors = list(payload.get("sectors") or [])
    hint_macros = list(payload.get("macro_factors") or [])
    cited_doc_vers = list(payload.get("cited_doc_version_ids") or [])
    user_msg_id = payload.get("user_message_id")
    asst_msg_id = payload.get("assistant_message_id")
    turn_index = int(payload.get("turn_index") or 1)
    parent_session_id = payload.get("parent_session_id")
    session_title = payload.get("session_title")
    chat_mode = str(payload.get("chat_mode") or "GENERAL")

    # Load dynamic issuer registry rows from KgNode for accurate ticker normalization (0 hardcoded)
    issuer_rows: list[dict[str, Any]] = []
    try:
        from sag_api.db.models.stock_research import KgNode

        kg_rows = (
            await session.execute(
                select(KgNode.ticker, KgNode.label)
                .where(KgNode.node_type == "COMPANY", KgNode.ticker.is_not(None))
                .limit(1000)
            )
        ).all()
        issuer_rows = [{"symbol": str(tk), "name": str(lbl)} for tk, lbl in kg_rows if tk]
    except Exception:  # noqa: BLE001
        issuer_rows = []

    # ── W1: Extract outside DB lock ──────────────────────────────────────────
    if extracted_override is not None:
        bundle = extracted_override
    else:
        bundle = await extract_turn_memories_with_llm(
            llm=llm,
            user_content=user_content,
            assistant_topic_outline=assistant_outline,
            assistant_clean_for_digest=assistant_clean,
            turn_as_of_date=turn_as_of_date,
            hint_tickers=hint_tickers,
            hint_sectors=hint_sectors,
            hint_macro_factors=hint_macros,
            cited_doc_version_ids=cited_doc_vers,
            issuer_rows=issuer_rows,
        )

    # Pre-fetch top-5 Qdrant neighbors outside lock for W2 semantic conflict discovery (optional)
    qdrant_neighbors_by_idx: dict[int, list[str]] = {}
    if qdrant_client is not None and bundle.memories:
        for idx, cand in enumerate(bundle.memories):
            dense_vec = await _embed_single_text(embedder, cand.content_text) if embedder is not None else None
            hits = await search_user_memories_hybrid(
                qdrant_client,
                tenant_id=tenant_id,
                user_id=user_id,
                query_text=cand.content_text,
                as_of_date=turn_as_of_date,
                is_backtest=is_sandbox,
                dense_vector=dense_vec,
                limit=5,
            )
            neighbor_ids = [
                str(h["memory_id"])
                for h in hits
                if float(h.get("dense_cos") or 0.0) >= 0.72 or float(h.get("bm25_norm") or 0.0) >= 0.72
            ]
            if neighbor_ids:
                qdrant_neighbors_by_idx[idx] = neighbor_ids

    stats: dict[str, Any] = {
        "status": "OK",
        "added": 0,
        "updated": 0,
        "corrected": 0,
        "invalidated": 0,
        "reinforced": 0,
        "protected_pinned_skips": 0,
        "fast_path_count": 0,
        "w3_llm_called": False,
        "rejected_count": len(bundle.rejected_reasons),
        "linked_sessions_count": 0,
    }
    now = datetime.now(UTC)

    # ── W2 + W3 + W4 under strict per-user lock ──────────────────────────────
    async with user_memory_scope_lock(session, tenant_id, user_id):
        # Tombstone Check: NEVER resurrect memories for a deleted session!
        episode = await session.scalar(
            select(UserMemoryEpisode).where(
                UserMemoryEpisode.tenant_id == tenant_id,
                UserMemoryEpisode.user_id == user_id,
                UserMemoryEpisode.external_session_id == session_id,
            )
        )
        if episode is not None and episode.status == "DELETED":
            stats["status"] = "TOMBSTONED_SKIPPED"
            return stats

        # Upsert active episode
        msg_ids_new = [m for m in (user_msg_id, asst_msg_id) if m]
        if episode is None:
            episode = UserMemoryEpisode(
                episode_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                user_id=user_id,
                external_session_id=session_id,
                parent_session_id=parent_session_id,
                session_title=session_title,
                chat_mode=chat_mode,
                turn_range_start=turn_index,
                turn_range_end=turn_index,
                source_message_ids_json=msg_ids_new,
                tickers_json=list(bundle.tickers),
                sectors_json=list(bundle.sectors),
                macro_factors_json=list(bundle.macro_factors),
                topic_tags_json=list(bundle.topic_tags),
                cited_doc_version_ids_json=list(cited_doc_vers) if assistant_clean else [],
                episode_summary=bundle.episode_summary if assistant_clean else "",
                staleness_status="FRESH",
                source_as_of_date=turn_as_of_date,
                is_backtest_sandbox=is_sandbox,
                status="ACTIVE",
            )
            session.add(episode)
            await session.flush()
        else:
            episode.turn_range_end = max(int(episode.turn_range_end or 1), turn_index)
            if session_title and not episode.session_title:
                episode.session_title = session_title
            if parent_session_id and not episode.parent_session_id:
                episode.parent_session_id = parent_session_id
            episode.source_message_ids_json = list(
                dict.fromkeys([*(episode.source_message_ids_json or []), *msg_ids_new])
            )[:120]
            episode.tickers_json = list(dict.fromkeys([*(episode.tickers_json or []), *bundle.tickers]))[:32]
            episode.sectors_json = list(dict.fromkeys([*(episode.sectors_json or []), *bundle.sectors]))[:24]
            episode.macro_factors_json = list(
                dict.fromkeys([*(episode.macro_factors_json or []), *bundle.macro_factors])
            )[:24]
            episode.topic_tags_json = list(
                dict.fromkeys([*(episode.topic_tags_json or []), *bundle.topic_tags])
            )[:24]
            if assistant_clean:
                episode.cited_doc_version_ids_json = list(
                    dict.fromkeys([*(episode.cited_doc_version_ids_json or []), *cited_doc_vers])
                )[:64]
                if bundle.episode_summary:
                    episode.episode_summary = bundle.episode_summary

        # Load active items for this user + sandbox for fast W2 lookups
        active_items = (
            await session.scalars(
                select(UserMemoryItem).where(
                    UserMemoryItem.tenant_id == tenant_id,
                    UserMemoryItem.user_id == user_id,
                    UserMemoryItem.is_backtest_sandbox.is_(is_sandbox),
                    UserMemoryItem.valid_to >= INF_DATETIME,
                    UserMemoryItem.tx_to >= INF_DATETIME,
                )
            )
        ).all()
        active_by_id: dict[str, UserMemoryItem] = {i.memory_id: i for i in active_items}
        active_by_hash: dict[str, UserMemoryItem] = {i.content_hash: i for i in active_items}

        # Pre-load active `(src_key, dst_key, conflict_group)` -> memory_ids for W2 graph conflict discovery
        conflict_group_mem_ids_by_dst_ticker: dict[str, set[str]] = {}
        triple_conflict_queries: set[tuple[str, str, str]] = set()
        for tr in bundle.graph_triples:
            cg = RELATION_CONFLICT_GROUP.get(str(tr.get("relation") or "").upper())
            if cg and tr.get("src_key") and tr.get("dst_key"):
                triple_conflict_queries.add((str(tr["src_key"]), str(tr["dst_key"]), cg))
        if triple_conflict_queries:
            involved_keys = {k for src_k, dst_k, _ in triple_conflict_queries for k in (src_k, dst_k)}
            ent_rows = (
                await session.scalars(
                    select(UserMemoryEntity).where(
                        UserMemoryEntity.tenant_id == tenant_id,
                        UserMemoryEntity.user_id == user_id,
                        UserMemoryEntity.canonical_key.in_(involved_keys),
                    )
                )
            ).all()
            eid_by_key = {e.canonical_key: e.entity_id for e in ent_rows}
            key_by_eid = {e.entity_id: e.canonical_key for e in ent_rows}
            for src_k, dst_k, cg in triple_conflict_queries:
                s_eid = eid_by_key.get(src_k)
                d_eid = eid_by_key.get(dst_k)
                if not s_eid or not d_eid:
                    continue
                conf_edges = (
                    await session.scalars(
                        select(UserMemoryEdge).where(
                            UserMemoryEdge.tenant_id == tenant_id,
                            UserMemoryEdge.user_id == user_id,
                            UserMemoryEdge.src_entity_id == s_eid,
                            UserMemoryEdge.dst_entity_id == d_eid,
                            UserMemoryEdge.conflict_group == cg,
                            UserMemoryEdge.is_backtest_sandbox.is_(is_sandbox),
                            UserMemoryEdge.valid_to >= INF_DATETIME,
                            UserMemoryEdge.tx_to >= INF_DATETIME,
                        )
                    )
                ).all()
                dst_tk = dst_k.split(":", 1)[1].upper() if dst_k.startswith("TICKER:") else "*"
                for ce in conf_edges:
                    if ce.memory_id and ce.memory_id in active_by_id:
                        conflict_group_mem_ids_by_dst_ticker.setdefault(dst_tk, set()).add(ce.memory_id)
                        if key_by_eid.get(ce.dst_entity_id, "").startswith("TICKER:"):
                            conflict_group_mem_ids_by_dst_ticker.setdefault("*", set()).add(ce.memory_id)

        # W2 Classification: Fast-Path REINFORCE / Fast-Path ADD vs W3 Reconciliation Batch
        w3_batch: list[tuple[int, SanitizedMemoryCandidate, list[UserMemoryItem]]] = []
        fast_adds: list[SanitizedMemoryCandidate] = []
        created_memory_ids: list[tuple[SanitizedMemoryCandidate, UserMemoryItem]] = []

        for idx, cand in enumerate(bundle.memories):
            # 1) Exact content_hash -> Fast-Path REINFORCE (0 W3 LLM)
            exact = active_by_hash.get(cand.content_hash)
            if exact is not None:
                exact.reinforcement_count = int(exact.reinforcement_count or 1) + 1
                exact.last_accessed_at = now
                session.add(
                    _audit_entry(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        memory_id=exact.memory_id,
                        action="REINFORCE",
                        actor="WORKER",
                        reason="exact_content_hash_fast_path",
                    )
                )
                stats["reinforced"] += 1
                stats["fast_path_count"] += 1
                created_memory_ids.append((cand, exact))
                continue

            # 2) Find conflict / duplicate candidates: canonical_slot_key, (src, dst, conflict_group),
            #    ticker+type overlap, or Qdrant top-5
            cand_matches: dict[str, UserMemoryItem] = {}
            for existing in active_items:
                if cand.canonical_slot_key and existing.canonical_slot_key == cand.canonical_slot_key:
                    cand_matches[existing.memory_id] = existing
                elif (
                    existing.memory_type == cand.memory_type
                    and cand.tickers
                    and (set(existing.tickers_json or []) & set(cand.tickers))
                ):
                    cand_matches[existing.memory_id] = existing
                elif (
                    existing.memory_type == cand.memory_type
                    and not cand.tickers
                    and _text_cosine_sim(existing.content_text, cand.content_text) >= 0.68
                ):
                    cand_matches[existing.memory_id] = existing

            for tk in cand.tickers:
                for cmid in conflict_group_mem_ids_by_dst_ticker.get(tk.upper(), ()):
                    if cmid in active_by_id:
                        cand_matches[cmid] = active_by_id[cmid]
            if not cand.tickers:
                for cmid in conflict_group_mem_ids_by_dst_ticker.get("*", ()):
                    if cmid in active_by_id:
                        cand_matches[cmid] = active_by_id[cmid]

            for nid in qdrant_neighbors_by_idx.get(idx, ()):
                if nid in active_by_id:
                    cand_matches[nid] = active_by_id[nid]

            if not cand_matches:
                # Fast-Path ADD (0 W3 LLM calls!)
                fast_adds.append(cand)
                stats["fast_path_count"] += 1
            else:
                w3_batch.append((idx, cand, list(cand_matches.values())))

        def _create_new_item(
            cand: SanitizedMemoryCandidate,
            *,
            supersedes_id: str | None = None,
        ) -> UserMemoryItem:
            item = UserMemoryItem(
                memory_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                user_id=user_id,
                scope=cand.scope,
                memory_type=cand.memory_type,
                pit_sensitivity=cand.pit_sensitivity,
                canonical_slot_key=cand.canonical_slot_key,
                content_text=cand.content_text,
                structured_payload_json=dict(cand.structured_payload),
                tickers_json=list(cand.tickers),
                bound_doc_version_ids_json=list(cand.bound_doc_version_ids),
                confidence=cand.confidence,
                source_authority=cand.source_authority,
                origin_episode_id=episode.episode_id,
                origin_session_id=session_id,
                origin_message_id=user_msg_id or asst_msg_id,
                source_as_of_date=turn_as_of_date,
                is_backtest_sandbox=is_sandbox,
                valid_from=now,
                valid_to=INF_DATETIME,
                tx_from=now,
                tx_to=INF_DATETIME,
                supersedes_memory_id=supersedes_id,
                merged_from_ids_json=[],
                staleness_status="FRESH",
                reinforcement_count=1,
                pinned_by_user=False,
                content_hash=cand.content_hash,
                embedding_model_signature=settings.embedding_model,
                qdrant_point_id=str(uuid.uuid4()),
                qdrant_sync_status="PENDING_UPSERT",
                sync_version=1,
            )
            session.add(item)
            active_by_id[item.memory_id] = item
            active_by_hash[item.content_hash] = item
            return item

        for cand in fast_adds:
            new_item = _create_new_item(cand)
            session.add(
                _audit_entry(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    memory_id=new_item.memory_id,
                    action="ADD",
                    actor="WORKER",
                    after_json=_memory_snapshot(new_item),
                    reason="w2_fast_path_add",
                )
            )
            stats["added"] += 1
            created_memory_ids.append((cand, new_item))

        # ── W3: Batched Tri-Temporal Reconciliation ──────────────────────────
        if w3_batch:
            stats["w3_llm_called"] = bool(llm is not None and getattr(llm, "configured", True))
            decisions = await _run_w3_batched_reconciliation(llm=llm, batch=w3_batch)
            for idx, cand, existing_list in w3_batch:
                dec = decisions.get(idx, {"action": "ADD", "target_memory_id": None, "reason": "default"})
                action = str(dec.get("action") or "ADD").upper()
                target_id = dec.get("target_memory_id")
                reason = str(dec.get("reason") or "")
                target = (
                    active_by_id.get(str(target_id))
                    if target_id
                    else (existing_list[0] if existing_list else None)
                )

                # HARD PROTECTION of `pinned_by_user` / `USER_CONFIRMED`
                if (
                    target is not None
                    and action in {"UPDATE_STATE", "CORRECT_ERROR", "INVALIDATE"}
                    and (bool(target.pinned_by_user) or target.source_authority == "USER_CONFIRMED")
                    and not (cand.explicit_override and cand.confidence >= 0.90)
                ):
                    session.add(
                        _audit_entry(
                            tenant_id=tenant_id,
                            user_id=user_id,
                            memory_id=target.memory_id,
                            action="PROTECTED_PINNED_SKIP",
                            actor="WORKER",
                            before_json=_memory_snapshot(target),
                            reason=(
                                f"Blocked silent {action} on pinned/USER_CONFIRMED memory "
                                f"(explicit_override={cand.explicit_override}, conf={cand.confidence:.2f})"
                            ),
                        )
                    )
                    stats["protected_pinned_skips"] += 1
                    continue

                if action == "NOOP":
                    continue
                if action == "REINFORCE" and target is not None:
                    target.reinforcement_count = int(target.reinforcement_count or 1) + 1
                    target.last_accessed_at = now
                    session.add(
                        _audit_entry(
                            tenant_id=tenant_id,
                            user_id=user_id,
                            memory_id=target.memory_id,
                            action="REINFORCE",
                            actor="WORKER",
                            reason=reason or "w3_reinforce",
                        )
                    )
                    stats["reinforced"] += 1
                    created_memory_ids.append((cand, target))
                    continue
                if action == "UPDATE_STATE" and target is not None:
                    before = _memory_snapshot(target)
                    target.valid_to = now
                    target.invalidation_reason = reason or "STATE_UPDATED"
                    target.qdrant_sync_status = "PENDING_PATCH"
                    target.sync_version = int(target.sync_version or 1) + 1
                    new_item = _create_new_item(cand, supersedes_id=target.memory_id)
                    session.add(
                        _audit_entry(
                            tenant_id=tenant_id,
                            user_id=user_id,
                            memory_id=new_item.memory_id,
                            action="UPDATE_STATE",
                            actor="WORKER",
                            before_json=before,
                            after_json=_memory_snapshot(new_item),
                            reason=reason or "w3_update_state",
                        )
                    )
                    stats["updated"] += 1
                    created_memory_ids.append((cand, new_item))
                    continue
                if action == "CORRECT_ERROR" and target is not None:
                    before = _memory_snapshot(target)
                    target.tx_to = now
                    target.invalidation_reason = reason or "ERROR_CORRECTED"
                    target.qdrant_sync_status = "PENDING_PATCH"
                    target.sync_version = int(target.sync_version or 1) + 1
                    new_item = _create_new_item(cand, supersedes_id=target.memory_id)
                    session.add(
                        _audit_entry(
                            tenant_id=tenant_id,
                            user_id=user_id,
                            memory_id=new_item.memory_id,
                            action="CORRECT_ERROR",
                            actor="WORKER",
                            before_json=before,
                            after_json=_memory_snapshot(new_item),
                            reason=reason or "w3_correct_error",
                        )
                    )
                    stats["corrected"] += 1
                    created_memory_ids.append((cand, new_item))
                    continue
                if action == "INVALIDATE" and target is not None:
                    before = _memory_snapshot(target)
                    target.valid_to = now
                    target.invalidation_reason = reason or "INVALIDATED"
                    target.qdrant_sync_status = "PENDING_PATCH"
                    target.sync_version = int(target.sync_version or 1) + 1
                    session.add(
                        _audit_entry(
                            tenant_id=tenant_id,
                            user_id=user_id,
                            memory_id=target.memory_id,
                            action="INVALIDATE",
                            actor="WORKER",
                            before_json=before,
                            reason=reason or "w3_invalidate",
                        )
                    )
                    stats["invalidated"] += 1
                    continue

                # Default ADD
                new_item = _create_new_item(cand)
                session.add(
                    _audit_entry(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        memory_id=new_item.memory_id,
                        action="ADD",
                        actor="WORKER",
                        after_json=_memory_snapshot(new_item),
                        reason=reason or "w3_add",
                    )
                )
                stats["added"] += 1
                created_memory_ids.append((cand, new_item))

        await session.flush()

        # ── W4: Personal Graph Entities/Edges & Top-3 Linked Sessions ────────
        self_ent = await _upsert_entity(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            entity_type="USER",
            canonical_key="USER:SELF",
            display_name="Nhà đầu tư",
        )
        sess_ent = await _upsert_entity(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            entity_type="SESSION",
            canonical_key=f"SESSION:{session_id}",
            display_name=episode.session_title or f"Session {session_id[:8]}",
        )

        # Link session to discussed tickers, sectors, macro factors
        for tk in episode.tickers_json or []:
            tk_ent = await _upsert_entity(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type="TICKER",
                canonical_key=f"TICKER:{tk}",
                display_name=tk,
            )
            await _upsert_personal_edge(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                src_entity=sess_ent,
                dst_entity=tk_ent,
                relation_type="SESSION_DISCUSSED",
                fact_text=episode.episode_summary[:240] or f"Thảo luận {tk}",
                memory_id=None,
                origin_session_id=session_id,
                source_as_of_date=turn_as_of_date,
                is_backtest_sandbox=is_sandbox,
                now=now,
            )

        for sec_name in episode.sectors_json or []:
            sec_ent = await _upsert_entity(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type="SECTOR",
                canonical_key=f"SECTOR:{sec_name}",
                display_name=sec_name,
            )
            await _upsert_personal_edge(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                src_entity=sess_ent,
                dst_entity=sec_ent,
                relation_type="SESSION_DISCUSSED",
                fact_text=f"Ngành {sec_name}",
                memory_id=None,
                origin_session_id=session_id,
                source_as_of_date=turn_as_of_date,
                is_backtest_sandbox=is_sandbox,
                now=now,
            )

        for mf in episode.macro_factors_json or []:
            macro_key = mf if mf.startswith("MACRO:") else f"MACRO:{mf}"
            mf_ent = await _upsert_entity(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type="MACRO_FACTOR",
                canonical_key=macro_key,
                display_name=mf,
            )
            await _upsert_personal_edge(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                src_entity=sess_ent,
                dst_entity=mf_ent,
                relation_type="SESSION_DISCUSSED",
                fact_text=f"Nhân tố vĩ mô {mf}",
                memory_id=None,
                origin_session_id=session_id,
                source_as_of_date=turn_as_of_date,
                is_backtest_sandbox=is_sandbox,
                now=now,
            )

        # Upsert LLM-extracted closed-ontology graph triples (binding each triple to its matching memory!)
        def _match_triple_memory_id(tr_obj: Mapping[str, Any]) -> str | None:
            if not created_memory_ids:
                return None
            if len(created_memory_ids) == 1:
                return created_memory_ids[0][1].memory_id
            tr_tickers: set[str] = set()
            for kfield in ("dst_key", "src_key"):
                raw_k = str(tr_obj.get(kfield) or "")
                if raw_k.upper().startswith("TICKER:"):
                    tr_tickers.add(raw_k.split(":", 1)[1].strip().upper())
            if tr_tickers:
                for cand_obj, mem_obj in created_memory_ids:
                    if tr_tickers & {t.upper() for t in (cand_obj.tickers or ())}:
                        return mem_obj.memory_id
            tr_blob = f"{tr_obj.get('dst_name', '')} {tr_obj.get('fact_text', '')}".strip()
            best_sim = 0.0
            best_mid: str | None = None
            for _cand_obj, mem_obj in created_memory_ids:
                sim = _text_cosine_sim(tr_blob, mem_obj.content_text)
                if sim > best_sim:
                    best_sim = sim
                    best_mid = mem_obj.memory_id
            return best_mid if best_sim >= 0.15 else created_memory_ids[0][1].memory_id

        for tr in bundle.graph_triples:
            src_ent = (
                self_ent
                if tr["src_key"] == "USER:SELF"
                else await _upsert_entity(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    entity_type=tr["src_type"],
                    canonical_key=tr["src_key"],
                    display_name=tr["src_name"],
                )
            )
            dst_ent = await _upsert_entity(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type=tr["dst_type"],
                canonical_key=tr["dst_key"],
                display_name=tr["dst_name"],
            )
            await _upsert_personal_edge(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                src_entity=src_ent,
                dst_entity=dst_ent,
                relation_type=tr["relation"],
                fact_text=tr["fact_text"],
                memory_id=_match_triple_memory_id(tr),
                origin_session_id=session_id,
                source_as_of_date=turn_as_of_date,
                is_backtest_sandbox=is_sandbox,
                now=now,
            )

        links = await link_related_sessions_for_episode(session, episode=episode, now=now)
        stats["linked_sessions_count"] = len(links)
        await _enforce_user_quotas(session, tenant_id=tenant_id, user_id=user_id, now=now)
        await session.flush()

    await session.commit()

    # Best-effort immediate Outbox flush (failures stay pending for Outbox Sweep retry — 0 zombies!)
    if qdrant_client is not None:
        outbox_res = await flush_user_memory_outbox(
            session,
            qdrant_client=qdrant_client,
            embedder=embedder,
            tenant_id=tenant_id,
            user_id=user_id,
            limit=50,
        )
        stats["outbox"] = outbox_res

    return stats


# ── 3-State Versioned Qdrant Outbox Sweep (Zero-Zombie Sync) ─────────────────


async def flush_user_memory_outbox(
    session: AsyncSession,
    *,
    qdrant_client: httpx.AsyncClient | None,
    embedder: Any = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
    limit: int = 100,
    max_attempts: int = 8,
) -> dict[str, int]:
    """Flush `PENDING_UPSERT`, `PENDING_PATCH`, and `PENDING_DELETE` rows to Qdrant.

    Verifies `sync_version` via conditional UPDATE (`WHERE memory_id = :mid AND sync_version = :ver`)
    before marking `SYNCED`, preventing concurrent updates/deletes from leaving Qdrant zombies.
    """
    summary = {"synced": 0, "failed": 0, "skipped_version_conflict": 0}
    if qdrant_client is None:
        return summary

    filters: list[Any] = [
        UserMemoryItem.qdrant_sync_status.in_(("PENDING_UPSERT", "PENDING_PATCH", "PENDING_DELETE")),
        UserMemoryItem.sync_attempts < max_attempts,
    ]
    if tenant_id:
        filters.append(UserMemoryItem.tenant_id == tenant_id)
    if user_id:
        filters.append(UserMemoryItem.user_id == user_id)

    pending_rows = (
        await session.scalars(
            select(UserMemoryItem)
            .where(*filters)
            .order_by(UserMemoryItem.updated_at.asc())
            .limit(limit)
        )
    ).all()

    for row in pending_rows:
        expected_version = int(row.sync_version or 1)
        sync_op = str(row.qdrant_sync_status)
        try:
            if sync_op == "PENDING_DELETE":
                await delete_user_memory_points(qdrant_client, point_ids=[row.qdrant_point_id])
            elif sync_op == "PENDING_PATCH":
                await patch_user_memory_payload(
                    qdrant_client,
                    point_id=row.qdrant_point_id,
                    payload_patch=build_user_memory_patch_payload(row),
                )
            else:  # PENDING_UPSERT
                dense_vec = await _embed_single_text(embedder, row.content_text)
                if dense_vec is None:
                    # Without an embedder we cannot write a dense vector; skip silently without failing permanently
                    continue
                await upsert_user_memory_point(
                    qdrant_client,
                    item=row,
                    dense_vector=dense_vec,
                )

            # Version-checked conditional UPDATE so a concurrent mutation isn't clobbered!
            res = await session.execute(
                update(UserMemoryItem)
                .where(
                    UserMemoryItem.memory_id == row.memory_id,
                    UserMemoryItem.sync_version == expected_version,
                )
                .values(
                    qdrant_sync_status="SYNCED",
                    last_sync_error=None,
                )
            )
            await session.commit()
            if (res.rowcount or 0) > 0:
                summary["synced"] += 1
            else:
                summary["skipped_version_conflict"] += 1
                # Anti-zombie guard: if a slow PENDING_UPSERT finished AFTER a concurrent DELETE/PATCH
                # already marked SYNCED at a higher sync_version, reconcile Qdrant immediately!
                if sync_op == "PENDING_UPSERT":
                    latest = await session.scalar(
                        select(UserMemoryItem)
                        .where(UserMemoryItem.memory_id == row.memory_id)
                        .execution_options(populate_existing=True)
                    )
                    if latest is not None and latest.qdrant_sync_status == "SYNCED":
                        if latest.tx_to and latest.tx_to < INF_DATETIME:
                            await delete_user_memory_points(qdrant_client, point_ids=[latest.qdrant_point_id])
                        elif latest.valid_to and latest.valid_to < INF_DATETIME:
                            await patch_user_memory_payload(
                                qdrant_client,
                                point_id=latest.qdrant_point_id,
                                payload_patch=build_user_memory_patch_payload(latest),
                            )
        except Exception as exc:  # noqa: BLE001
            err_label = f"{type(exc).__name__}: {str(exc)[:180]}"
            new_attempts = int(row.sync_attempts or 0) + 1
            new_status = "FAILED" if new_attempts >= max_attempts else sync_op
            await session.execute(
                update(UserMemoryItem)
                .where(
                    UserMemoryItem.memory_id == row.memory_id,
                    UserMemoryItem.sync_version == expected_version,
                )
                .values(
                    sync_attempts=new_attempts,
                    last_sync_error=err_label,
                    qdrant_sync_status=new_status,
                )
            )
            await session.commit()
            summary["failed"] += 1
            log.warning("UserMemory Outbox sync failed (%s) memory_id=%s", err_label, row.memory_id)

    return summary


# ── Cascade Delete Session + Tombstone Race Guard ────────────────────────────


async def cascade_delete_session_memories(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    reason: str = "USER_DELETED_SESSION",
    qdrant_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Tombstone episode + soft-delete all memories & edges from `session_id` under per-user lock.

    If called before an in-flight `INGEST_TURN` commits, the tombstone (`status='DELETED'`)
    prevents resurrection when W2 runs.
    """
    now = datetime.now(UTC)
    clean_sid = session_id.strip()

    async with user_memory_scope_lock(session, tenant_id, user_id):
        episode = await session.scalar(
            select(UserMemoryEpisode).where(
                UserMemoryEpisode.tenant_id == tenant_id,
                UserMemoryEpisode.user_id == user_id,
                UserMemoryEpisode.external_session_id == clean_sid,
            )
        )
        if episode is None:
            episode = UserMemoryEpisode(
                episode_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                user_id=user_id,
                external_session_id=clean_sid,
                source_as_of_date=date.today(),
                status="DELETED",
                episode_summary="",
            )
            session.add(episode)
        else:
            episode.status = "DELETED"

        items = (
            await session.scalars(
                select(UserMemoryItem).where(
                    UserMemoryItem.tenant_id == tenant_id,
                    UserMemoryItem.user_id == user_id,
                    UserMemoryItem.origin_session_id == clean_sid,
                    UserMemoryItem.tx_to >= INF_DATETIME,
                )
            )
        ).all()
        for item in items:
            item.tx_to = now
            item.valid_to = min(item.valid_to, now) if item.valid_to else now
            item.invalidation_reason = reason
            item.qdrant_sync_status = "PENDING_DELETE"
            item.sync_version = int(item.sync_version or 1) + 1
            session.add(
                _audit_entry(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    memory_id=item.memory_id,
                    action="CASCADE_DELETE_SESSION",
                    actor="USER",
                    reason=reason,
                )
            )

        sess_entity = await session.scalar(
            select(UserMemoryEntity).where(
                UserMemoryEntity.tenant_id == tenant_id,
                UserMemoryEntity.user_id == user_id,
                UserMemoryEntity.canonical_key == f"SESSION:{clean_sid}",
            )
        )
        edge_filters: list[Any] = [
            UserMemoryEdge.tenant_id == tenant_id,
            UserMemoryEdge.user_id == user_id,
            UserMemoryEdge.tx_to >= INF_DATETIME,
        ]
        if sess_entity is not None:
            edge_filters.append(
                (UserMemoryEdge.origin_session_id == clean_sid)
                | (UserMemoryEdge.src_entity_id == sess_entity.entity_id)
                | (UserMemoryEdge.dst_entity_id == sess_entity.entity_id)
            )
        else:
            edge_filters.append(UserMemoryEdge.origin_session_id == clean_sid)

        edges = (await session.scalars(select(UserMemoryEdge).where(and_(*edge_filters)))).all()
        for edge in edges:
            edge.tx_to = now
            edge.valid_to = min(edge.valid_to, now) if edge.valid_to else now

        # Cancel any queued/retry INGEST_TURN jobs for the deleted session
        await session.execute(
            update(UserMemoryJob)
            .where(
                UserMemoryJob.tenant_id == tenant_id,
                UserMemoryJob.user_id == user_id,
                UserMemoryJob.session_id == clean_sid,
                UserMemoryJob.job_kind == "INGEST_TURN",
                UserMemoryJob.status.in_(("QUEUED", "RETRY")),
            )
            .values(status="SUCCEEDED", error="CANCELLED_BY_SESSION_DELETE")
        )
        await session.flush()

    await session.commit()

    if qdrant_client is not None:
        await flush_user_memory_outbox(
            session,
            qdrant_client=qdrant_client,
            tenant_id=tenant_id,
            user_id=user_id,
            limit=100,
        )

    return {
        "session_id": clean_sid,
        "tombstoned": True,
        "retracted_memories": len(items),
        "closed_edges": len(edges),
    }


# ── 0-LLM DocumentVersion Staleness Hook ─────────────────────────────────────


async def propagate_document_version_staleness(
    session: AsyncSession,
    superseded_version_ids: Sequence[str],
    *,
    reason_status: str = "STALE_NEW_FILING",
    reason_detail: str | None = None,
) -> dict[str, int]:
    """Deterministic 0-LLM staleness hook when a `DocumentVersion` is superseded or restated.

    Marks dependent active `INVESTMENT_THESIS` and `SESSION_EPISODE_DIGEST` memories (plus
    `user_memory_episodes`) with `STALE_NEW_FILING` / `STALE_RESTATED` and schedules
    `PENDING_PATCH` (`sync_version += 1`) for Qdrant.
    """
    target_ids = {str(vid).strip() for vid in (superseded_version_ids or ()) if str(vid).strip()}
    if not target_ids:
        return {"stale_memories": 0, "stale_episodes": 0}

    status_val = reason_status if reason_status in {"STALE_NEW_FILING", "STALE_RESTATED"} else "STALE_NEW_FILING"
    detail_text = (
        reason_detail
        or f"Tài liệu tham chiếu ({', '.join(sorted(target_ids)[:4])}) đã có bản cập nhật/đính chính mới."
    )

    stale_mem_count = 0
    stale_ep_count = 0

    try:
        active_theses = (
            await session.scalars(
                select(UserMemoryItem).where(
                    UserMemoryItem.memory_type.in_(("INVESTMENT_THESIS", "SESSION_EPISODE_DIGEST")),
                    UserMemoryItem.valid_to >= INF_DATETIME,
                    UserMemoryItem.tx_to >= INF_DATETIME,
                )
            )
        ).all()
        for mem in active_theses:
            bound = {str(v).strip() for v in (mem.bound_doc_version_ids_json or []) if str(v).strip()}
            if bound & target_ids:
                if mem.staleness_status != status_val:
                    mem.staleness_status = status_val
                    mem.staleness_reason = detail_text
                    if mem.qdrant_sync_status != "PENDING_UPSERT":
                        mem.qdrant_sync_status = "PENDING_PATCH"
                    mem.sync_version = int(mem.sync_version or 1) + 1
                    session.add(
                        _audit_entry(
                            tenant_id=mem.tenant_id,
                            user_id=mem.user_id,
                            memory_id=mem.memory_id,
                            action=status_val,
                            actor="SYSTEM",
                            reason=detail_text,
                        )
                    )
                    stale_mem_count += 1

        active_episodes = (
            await session.scalars(
                select(UserMemoryEpisode).where(UserMemoryEpisode.status == "ACTIVE")
            )
        ).all()
        for ep in active_episodes:
            cited = {str(v).strip() for v in (ep.cited_doc_version_ids_json or []) if str(v).strip()}
            if cited & target_ids:
                if ep.staleness_status != status_val:
                    ep.staleness_status = status_val
                    ep.staleness_reason = detail_text
                    stale_ep_count += 1
        if stale_mem_count or stale_ep_count:
            await session.flush()
    except Exception as exc:  # noqa: BLE001
        log.warning("propagate_document_version_staleness skipped (%s)", type(exc).__name__)

    return {"stale_memories": stale_mem_count, "stale_episodes": stale_ep_count}
