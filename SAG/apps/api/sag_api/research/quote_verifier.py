"""Post-Synthesis Citation & `quote_hash` Verifier (Fail-Closed).

Verifies every citation emitted during Research Chat synthesis:
1. `sha256(normalize_ws(quote)) == quote_hash`
2. Exact normalized substring match in `canonical_blocks` for the cited
   `document_version_id`, preserving `page` (`page_from <= page <= page_to`)
   and `sub_doc_slot`.
3. Strict Point-in-Time check: `published_date <= as_of_date`.

Fail-closed behavior:
- Any citation failing hash, substring, page/slot, or PIT check is marked
  `verified=False`, stripped from verified citations, and triggers an explicit
  `UNVERIFIED_CITATION` or `OUT_OF_PIT_WINDOW` blindspot.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def normalize_ws(text: str) -> str:
    """Collapse whitespace and normalize Unicode NFC for deterministic quote verification."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "")).strip()


def compute_quote_hash(quote: str) -> str:
    """Compute SHA-256 hex digest of normalized quote text."""
    return hashlib.sha256(normalize_ws(quote).encode("utf-8")).hexdigest()


def _parse_date(val: Any) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    raw = str(val).strip()
    if not raw:
        return None
    return date.fromisoformat(raw[:10])


def _coerce_utc_datetime(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo is not None else val.replace(tzinfo=UTC)
    if isinstance(val, date):
        return datetime(val.year, val.month, val.day, tzinfo=UTC)
    raw = str(val).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    except ValueError:
        d = _parse_date(raw)
        return datetime(d.year, d.month, d.day, tzinfo=UTC) if d is not None else None


@dataclass(frozen=True, slots=True)
class CanonicalBlockRecord:
    """Normalized representation of a `canonical_blocks` row + `DocumentVersion` PIT window."""

    document_version_id: str
    page_from: int
    page_to: int
    normalized_text: str
    sub_doc_slot: str | None = None
    doc_id: str | None = None
    published_date: date | None = None
    block_id: str | None = None
    ordinal: int = 0
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    is_active: bool = True


_INLINE_CITATION_BADGE_RE = re.compile(r"\[(C\d+)\]")
_INLINE_MEM_BADGE_RE = re.compile(r"\[MEM:(M\d+)\]")
_INLINE_PORT_BADGE_RE = re.compile(r"\[PORT:([A-Z0-9._-]{1,16})\]")
_INLINE_SESS_BADGE_RE = re.compile(r"\[SESS:(S\d+)\]")
_FINANCIAL_BADGE_RE = re.compile(
    r"\[(?:C\d+(?:\s+UNVERIFIED_CLAIM)?|TBL:[^\]]+|CALC-\d+|KG:[^\]]+|SCR:[^\]]+|DISC-\d+)\]"
)


@dataclass
class QuoteVerificationResult:
    """Outcome of post-synthesis `quote_hash` verification."""

    verified_citations: list[dict[str, Any]] = field(default_factory=list)
    rejected_citations: list[dict[str, Any]] = field(default_factory=list)
    all_citations: list[dict[str, Any]] = field(default_factory=list)
    blindspots: list[dict[str, str]] = field(default_factory=list)
    sanitized_answer: str = ""
    verified_rate: float = 1.0


async def load_canonical_blocks_for_versions(
    session: AsyncSession,
    document_version_ids: Sequence[str],
    *,
    as_of_date: date | None = None,
) -> dict[str, list[CanonicalBlockRecord]]:
    """Load `canonical_blocks` + active `Document` / `DocumentVersion` PIT window for verification."""
    ver_ids = sorted({vid for vid in document_version_ids if vid})
    if not ver_ids:
        return {}

    from sag_api.db.models import CanonicalBlock, Document, DocumentVersion

    filters: list[Any] = [
        CanonicalBlock.document_version_id.in_(ver_ids),
        Document.is_active.is_(True),
        DocumentVersion.status != "REVOKED",
    ]
    if as_of_date is not None:
        as_of_dt = datetime(as_of_date.year, as_of_date.month, as_of_date.day, 23, 59, 59, tzinfo=UTC)
        filters.extend(
            [
                DocumentVersion.valid_from <= as_of_dt,
                DocumentVersion.valid_to > as_of_dt,
            ]
        )

    stmt = (
        select(
            CanonicalBlock.id,
            CanonicalBlock.ordinal,
            CanonicalBlock.document_version_id,
            CanonicalBlock.page_from,
            CanonicalBlock.page_to,
            CanonicalBlock.normalized_text,
            DocumentVersion.document_id,
            DocumentVersion.source_published_at,
            DocumentVersion.valid_from,
            DocumentVersion.valid_to,
            Document.is_active,
            DocumentVersion.metadata_json,
        )
        .join(DocumentVersion, DocumentVersion.id == CanonicalBlock.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(*filters)
        .order_by(CanonicalBlock.document_version_id.asc(), CanonicalBlock.ordinal.asc())
    )
    rows = (await session.execute(stmt)).all()
    by_ver: dict[str, list[CanonicalBlockRecord]] = {}
    for (
        blk_id,
        ordinal,
        ver_id,
        p_from,
        p_to,
        norm_txt,
        doc_id,
        src_pub,
        valid_from,
        valid_to,
        is_active,
        meta,
    ) in rows:
        slot = None
        if isinstance(meta, Mapping):
            slot = meta.get("sub_doc_slot")
        pub_dt = _parse_date(src_pub) or _parse_date(valid_from)
        by_ver.setdefault(str(ver_id), []).append(
            CanonicalBlockRecord(
                document_version_id=str(ver_id),
                page_from=int(p_from),
                page_to=int(p_to),
                normalized_text=str(norm_txt or ""),
                sub_doc_slot=str(slot) if slot else None,
                doc_id=str(doc_id) if doc_id else None,
                published_date=pub_dt,
                block_id=str(blk_id) if blk_id else None,
                ordinal=int(ordinal or 0),
                valid_from=_coerce_utc_datetime(valid_from),
                valid_to=_coerce_utc_datetime(valid_to),
                is_active=bool(is_active),
            )
        )
    return by_ver


def _table_matches_quote(
    norm_quote: str,
    table: Mapping[str, Any],
    as_of_date: date,
) -> bool:
    """Verify quote against a hydrated table's normalized content while enforcing [valid_from, valid_to) PIT."""
    as_of_dt = datetime(as_of_date.year, as_of_date.month, as_of_date.day, 23, 59, 59, tzinfo=UTC)
    vf_dt = _coerce_utc_datetime(table.get("valid_from"))
    if vf_dt is not None and vf_dt > as_of_dt:
        return False
    vt_dt = _coerce_utc_datetime(table.get("valid_to"))
    if vt_dt is not None and vt_dt <= as_of_dt:
        return False
    parts: list[str] = [str(table.get("title") or "")]
    for h in table.get("headers") or ():
        if isinstance(h, list):
            parts.extend(str(c or "") for c in h)
        else:
            parts.append(str(h or ""))
    for row in table.get("rows") or ():
        if isinstance(row, Mapping):
            parts.append(str(row.get("label") or ""))
            parts.extend(str(v) for v in (row.get("values") or ()) if v is not None)
        elif isinstance(row, list):
            parts.extend(
                str(c.get("raw_text") if isinstance(c, Mapping) else c)
                for c in row
            )
    blob = normalize_ws(" ".join(parts))
    return bool(norm_quote and norm_quote in blob)


def _claim_content_overlaps_section(claim_sentences: Sequence[str], section_text: str) -> bool:
    """Verify that at least one citing sentence shares salient tokens or numbers with the cited section."""
    if not claim_sentences or not section_text.strip():
        return True
    sec_low = normalize_ws(section_text).lower()
    sec_tokens = {
        tok
        for tok in re.findall(r"[^\W_]{3,}", sec_low, flags=re.UNICODE)
        if not tok.isdigit()
    }
    sec_nums = set(_extract_numbers_from_text(section_text))

    stop = {
        "của", "cho", "với", "trong", "này", "các", "được", "những", "theo", "tại",
        "năm", "quý", "tháng", "ngày", "đạt", "mức", "công", "tập", "đoàn", "báo", "cáo",
        "the", "and", "for", "with", "from", "that", "this", "was", "were", "has", "have",
    }
    for sent in claim_sentences:
        # If the sentence also cites structured badges, allow cross-evidence synthesis
        if any(tag in sent for tag in ("[TBL:", "[CALC-", "[SCR:", "[KG:")):
            return True
        clean = _ANY_EVIDENCE_BADGE_RE.sub(" ", sent).lower()
        cand_tokens = [
            t
            for t in re.findall(r"[^\W_]{3,}", clean, flags=re.UNICODE)
            if t not in stop and not t.isdigit()
        ]
        if not cand_tokens:
            return True
        if any(t in sec_tokens or t in sec_low for t in cand_tokens):
            return True
        for m in _GENERIC_NUMBER_RE.finditer(clean):
            for v in _parse_number_variants(m.group(1), include_ambiguous=True):
                if abs(v) in sec_nums:
                    return True
    return False


def verify_citations(
    citations: Sequence[Mapping[str, Any]],
    blocks_by_version: Mapping[str, Sequence[CanonicalBlockRecord]],
    as_of_date: date,
    *,
    answer_text: str = "",
    hydrated_tables: Sequence[Mapping[str, Any]] | None = None,
    valid_citation_ids: set[str] | None = None,
    sections_by_cid: Mapping[str, Any] | None = None,
    valid_memory_ids: set[str] | None = None,
    valid_portfolio_symbols: set[str] | None = None,
    valid_session_ids: set[str] | None = None,
) -> QuoteVerificationResult:
    """Verify every citation against `quote_hash`, `canonical_blocks`, page/slot, and PIT date.

    Fail-closed:
    - Unverified citations receive `verified=False`, are excluded from `verified_citations`,
      and trigger explicit blindspots + answer badge flagging (`[C# UNVERIFIED_CLAIM]`).
    - Out-of-range / hallucinated inline citation IDs (e.g. `[C99]`) in `answer_text` are
      rejected with `HALLUCINATED_CITATION_ID` blindspots and rewritten to `[C99 UNVERIFIED_CLAIM]`.
    """
    verified: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    all_out: list[dict[str, Any]] = []
    blindspots: list[dict[str, str]] = []
    sanitized = answer_text
    tables_by_id = {
        str(t.get("table_id")): t
        for t in (hydrated_tables or ())
        if isinstance(t, Mapping) and t.get("table_id")
    }
    known_cids: set[str] = set(valid_citation_ids or ())
    claim_sentences_by_cid: dict[str, list[str]] = {}
    if answer_text and sections_by_cid:
        for line in answer_text.splitlines():
            for seg in re.split(r"(?<=[.!?])\s+(?=[A-ZĐÀ-Ỹ0-9*-])", line.strip()):
                s_clean = seg.strip()
                if not s_clean:
                    continue
                for found_cid in _INLINE_CITATION_BADGE_RE.findall(s_clean):
                    claim_sentences_by_cid.setdefault(found_cid, []).append(s_clean)

    for raw_cit in citations:
        cit = dict(raw_cit)
        cid = str(cit.get("citation_id") or "")
        if cid:
            known_cids.add(cid)
        ver_id = str(cit.get("document_version_id") or "")
        quote = str(cit.get("quote") or "")
        expected_hash = str(cit.get("quote_hash") or "")
        page = cit.get("page")
        page_to = cit.get("page_to")
        slot = cit.get("sub_doc_slot")
        table_id = str(cit.get("table_id") or "")
        cit_pub_date = _parse_date(cit.get("published_date"))

        failure_reason: str | None = None
        failure_code = "UNVERIFIED_CITATION"

        norm_quote = normalize_ws(quote)
        if not norm_quote or not expected_hash:
            failure_reason = f"Citation {cid or '?'} missing quote text or quote_hash."
        elif compute_quote_hash(norm_quote) != expected_hash:
            failure_reason = f"Citation {cid or '?'} SHA-256 quote_hash mismatch."
        elif cit_pub_date is None or cit_pub_date > as_of_date:
            failure_code = "OUT_OF_PIT_WINDOW"
            failure_reason = (
                f"Citation {cid or '?'} violates PIT gate (published_date={cit_pub_date}, as_of_date={as_of_date})."
            )
        else:
            blocks = list(blocks_by_version.get(ver_id, ()))
            p_lo = int(page) if page is not None else None
            p_hi = int(page_to) if page_to is not None else p_lo
            p_hi_eff = p_hi if (p_lo is not None and p_hi is not None and p_hi >= p_lo) else p_lo

            as_of_dt = datetime(
                as_of_date.year, as_of_date.month, as_of_date.day, 23, 59, 59, tzinfo=UTC
            )
            eligible_blocks: list[CanonicalBlockRecord] = []
            pit_or_inactive_rejected = False
            for blk in blocks:
                if not blk.is_active:
                    pit_or_inactive_rejected = True
                    continue
                if blk.published_date is not None and blk.published_date > as_of_date:
                    pit_or_inactive_rejected = True
                    continue
                blk_vf = _coerce_utc_datetime(blk.valid_from)
                if blk_vf is not None and blk_vf > as_of_dt:
                    pit_or_inactive_rejected = True
                    continue
                blk_vt = _coerce_utc_datetime(blk.valid_to)
                if blk_vt is not None and blk_vt <= as_of_dt:
                    pit_or_inactive_rejected = True
                    continue
                if p_lo is not None and p_hi_eff is not None:
                    # Overlap check: block [page_from, page_to] overlaps citation [p_lo, p_hi_eff]
                    if blk.page_to < p_lo or blk.page_from > p_hi_eff:
                        continue
                if slot and blk.sub_doc_slot and blk.sub_doc_slot != slot:
                    continue
                eligible_blocks.append(blk)
            matched_block: CanonicalBlockRecord | None = None
            for blk in eligible_blocks:
                if norm_quote in normalize_ws(blk.normalized_text):
                    matched_block = blk
                    break

            # Allow adjacent 2-block boundary spans (e.g. heading + first paragraph in one SearchUnit)
            if matched_block is None and len(eligible_blocks) >= 2:
                for idx_b in range(len(eligible_blocks) - 1):
                    b1, b2 = eligible_blocks[idx_b], eligible_blocks[idx_b + 1]
                    if b1.ordinal and b2.ordinal and abs(b2.ordinal - b1.ordinal) > 2:
                        continue
                    combined = normalize_ws(f"{b1.normalized_text} {b2.normalized_text}")
                    if norm_quote in combined:
                        matched_block = b1
                        break

            # Allow verified table_id match when citing from a Table Search Card
            matched_table = False
            if matched_block is None and table_id and table_id in tables_by_id:
                matched_table = _table_matches_quote(norm_quote, tables_by_id[table_id], as_of_date)

            if matched_block is None and not matched_table:
                if pit_or_inactive_rejected and not eligible_blocks:
                    failure_code = "OUT_OF_PIT_WINDOW"
                    failure_reason = (
                        f"Citation {cid or '?'} references an inactive or superseded/out-of-PIT "
                        f"version={ver_id} at as_of_date={as_of_date}."
                    )
                else:
                    failure_reason = (
                        f"Citation {cid or '?'} quote not found in canonical_blocks "
                        f"for version={ver_id} page={page} slot={slot}."
                    )
            elif sections_by_cid and cid and cid in claim_sentences_by_cid and cid in sections_by_cid:
                sec_obj = sections_by_cid[cid]
                sec_blob = (
                    f"{sec_obj.get('heading', '')} {sec_obj.get('content', '')}"
                    if isinstance(sec_obj, Mapping)
                    else f"{getattr(sec_obj, 'heading', '')} {getattr(sec_obj, 'content', '')}"
                )
                if not _claim_content_overlaps_section(claim_sentences_by_cid[cid], sec_blob):
                    failure_code = "CITATION_CONTENT_MISMATCH"
                    failure_reason = (
                        f"Citation {cid} locator exists, but the citing sentence shares zero "
                        "salient terms or numbers with the cited passage."
                    )

        if failure_reason is None:
            cit["verified"] = True
            verified.append(cit)
            all_out.append(cit)
        else:
            cit["verified"] = False
            cit["rejection_reason"] = failure_reason
            rejected.append(cit)
            all_out.append(cit)
            blindspots.append(
                {
                    "code": failure_code,
                    "reason": failure_reason,
                    "scope": cid or ver_id or "citation",
                }
            )
            if cid and sanitized:
                sanitized = re.sub(
                    rf"\[{re.escape(cid)}\]",
                    f"[{cid} UNVERIFIED_CLAIM]",
                    sanitized,
                )

    # N9: Detect and reject hallucinated / out-of-range inline citation IDs (e.g. [C99]) in answer_text
    if sanitized:
        seen_hallucinated: set[str] = set()
        for inline_cid in _INLINE_CITATION_BADGE_RE.findall(sanitized):
            if inline_cid in known_cids or inline_cid in seen_hallucinated:
                continue
            seen_hallucinated.add(inline_cid)
            reason = (
                f"Citation [{inline_cid}] referenced in answer does not map to any retrieved evidence unit."
            )
            rej_item = {
                "citation_id": inline_cid,
                "doc_id": "",
                "document_version_id": "",
                "sub_doc_slot": "",
                "page": 0,
                "published_date": "",
                "quote": "",
                "quote_hash": "",
                "verified": False,
                "rejection_reason": reason,
            }
            rejected.append(rej_item)
            all_out.append(rej_item)
            blindspots.append(
                {
                    "code": "HALLUCINATED_CITATION_ID",
                    "reason": reason,
                    "scope": inline_cid,
                }
            )
            sanitized = re.sub(
                rf"\[{re.escape(inline_cid)}\]",
                f"[{inline_cid} UNVERIFIED_CLAIM]",
                sanitized,
            )

        # Validate [MEM:M#], [PORT:<SYM>], and [SESS:S#] badges against recalled context (fail-closed!)
        allowed_mems = set(valid_memory_ids or ())
        seen_bad_mem: set[str] = set()
        for mem_id in _INLINE_MEM_BADGE_RE.findall(sanitized):
            if mem_id in allowed_mems or mem_id in seen_bad_mem:
                continue
            seen_bad_mem.add(mem_id)
            blindspots.append(
                {
                    "code": "HALLUCINATED_MEMORY_ID",
                    "reason": (
                        f"Memory badge [MEM:{mem_id}] referenced in answer does not map to any "
                        "recalled user memory in this turn."
                    ),
                    "scope": f"MEM:{mem_id}",
                }
            )
            sanitized = re.sub(
                rf"\[MEM:{re.escape(mem_id)}\]",
                f"[MEM:{mem_id} UNVERIFIED_CLAIM]",
                sanitized,
            )

        allowed_ports = {str(s).strip().upper() for s in (valid_portfolio_symbols or ()) if str(s).strip()}
        seen_bad_port: set[str] = set()
        for port_sym in _INLINE_PORT_BADGE_RE.findall(sanitized):
            if port_sym in allowed_ports or port_sym in seen_bad_port:
                continue
            seen_bad_port.add(port_sym)
            blindspots.append(
                {
                    "code": "HALLUCINATED_MEMORY_ID",
                    "reason": (
                        f"Portfolio badge [PORT:{port_sym}] referenced in answer does not map to any "
                        "authorized live portfolio snapshot entry."
                    ),
                    "scope": f"PORT:{port_sym}",
                }
            )
            sanitized = re.sub(
                rf"\[PORT:{re.escape(port_sym)}\]",
                f"[PORT:{port_sym} UNVERIFIED_CLAIM]",
                sanitized,
            )

        allowed_sess = set(valid_session_ids or ())
        seen_bad_sess: set[str] = set()
        for sess_id in _INLINE_SESS_BADGE_RE.findall(sanitized):
            if sess_id in allowed_sess or sess_id in seen_bad_sess:
                continue
            seen_bad_sess.add(sess_id)
            blindspots.append(
                {
                    "code": "HALLUCINATED_MEMORY_ID",
                    "reason": (
                        f"Session badge [SESS:{sess_id}] referenced in answer does not map to any "
                        "linked session in this turn."
                    ),
                    "scope": f"SESS:{sess_id}",
                }
            )
            sanitized = re.sub(
                rf"\[SESS:{re.escape(sess_id)}\]",
                f"[SESS:{sess_id} UNVERIFIED_CLAIM]",
                sanitized,
            )

    total = len(all_out)
    rate = round(len(verified) / total, 4) if total > 0 else 1.0
    return QuoteVerificationResult(
        verified_citations=verified,
        rejected_citations=rejected,
        all_citations=all_out,
        blindspots=blindspots,
        sanitized_answer=sanitized,
        verified_rate=rate,
    )


# ── Claim-to-Evidence Numeric Cross-Check (Anti-Numeric-Hallucination) ──────

_ANY_EVIDENCE_BADGE_RE = re.compile(
    r"\[(?:C\d+(?:\s+UNVERIFIED_CLAIM)?|TBL:[^\]]+|CALC-\d+|KG:[^\]]+|SCR:[^\]]+|DISC-\d+|"
    r"MEM:M\d+(?:\s+UNVERIFIED_CLAIM)?|PORT:[A-Z0-9._-]+(?:\s+UNVERIFIED_CLAIM)?|"
    r"SESS:S\d+(?:\s+UNVERIFIED_CLAIM)?)\]"
)
_EXPLICIT_TICKER_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Z]{3})(?![A-Za-z0-9_])")
_NON_TICKER_CAP_WORDS = frozenset(
    {
        "VND", "USD", "EUR", "JPY", "CNY", "GDP", "CPI", "ROE", "ROA", "ROIC",
        "NIM", "CIR", "CAR", "CASA", "NPL", "LDR", "CFO", "CFI", "CFF", "FCF", "EPS",
        "BPS", "YOY", "QOQ", "MOM", "CAGR", "EBITDA", "EBIT", "EBT", "PBT", "PAT", "NPAT",
        "VAS", "IFRS", "BCTC", "BCTN", "LCTT", "KQKD", "CDKT", "PIT", "HOSE", "HNX",
        "UPCOM", "VSD", "SSC", "SBV", "FED", "ADB", "IMF", "ESG", "MOAT", "AGM", "TBL",
        "CALC", "SCR", "DISC", "MAX", "MIN", "AVG", "NAV", "TTM", "LTM", "YTD", "MTD",
    }
)
_PERCENT_UNIT_MARKERS = frozenset({"%", "bps", "điểm cơ bản", "điểm phần trăm", "phần trăm"})
_MULTIPLE_UNIT_MARKERS = frozenset({"lần", "x"})
_MONETARY_UNIT_MARKERS = frozenset(
    {
        "tỷ", "tỉ", "triệu", "nghìn tỷ", "ngàn tỷ", "nghìn", "ngàn",
        "trillion", "billion", "million", "bn", "mn", "vnd", "vnđ", "đồng", "usd",
    }
)
_SCREEN_PERCENT_METRICS = frozenset(
    {
        "gross_margin", "net_margin", "roe", "roa", "revenue_yoy",
        "net_income_yoy", "revenue_cagr_3y", "fcf_yield", "cash_to_assets",
    }
)
_SCREEN_MULTIPLE_METRICS = frozenset(
    {
        "pe", "pb", "ev_ebitda", "debt_to_equity", "liabilities_to_equity",
        "net_debt_to_equity", "current_ratio", "cfo_to_net_income",
    }
)
_SCREEN_MONETARY_METRICS = frozenset(
    {
        "market_cap", "net_revenue", "gross_profit", "operating_profit", "net_income",
        "net_income_parent", "total_assets", "current_assets", "cash_and_st_investments",
        "inventory", "receivables", "total_liabilities", "current_liabilities",
        "short_term_debt", "long_term_debt", "total_debt", "equity", "cfo", "capex", "fcf",
    }
)
_STRIP_DATES_AND_REFS_RE = re.compile(
    r"\b(?:"
    r"\d{4}-\d{1,2}-\d{1,2}"
    r"|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|(?:Q[1-4]|Quý\s*[1-4]|[369]T|FY)\s*[/.-]?\s*\d{2,4}"
    r"|(?:năm|year)\s+(?:19|20)\d{2}"
    r"|(?:ngày|tháng)\s+\d{1,2}"
    r"|(?:TT|VAS|IFRS|B0[1-9]|VN)\s*-?\s*\d{1,3}(?:-DN)?"
    r"|(?:Thuyết\s*minh|TM|Note|Notes|Mục|Mã\s*số|Trang|Tr\.|Page|P|Nhóm|Tầng|Hop|Layer)\s*(?:số\s*)?\d+(?:\.\d+)*"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)
_SALIENT_FINANCIAL_CLAIM_RE = re.compile(
    r"(?<![A-Za-z0-9_/-])"
    r"([+-]?\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,4})?|[+-]?\d+(?:[.,]\d{1,4})?)"
    r"(?:\s*(%|bps|điểm\s+cơ\s+bản|tỷ|tỉ|triệu|nghìn\s+tỷ|ngàn\s+tỷ|nghìn|ngàn|"
    r"trillion|billion|million|bn|mn|vnd|vnđ|đồng|usd|lần|x|mw|kwh|tấn|dwt|teu)\b|"
    r"(?=%))",
    re.IGNORECASE | re.UNICODE,
)
_PLAIN_LARGE_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_/-])([+-]?\d{1,3}(?:[.,]\d{3}){1,4}(?:[.,]\d{1,2})?)(?![A-Za-z0-9_/-])"
)
_GENERIC_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])([+-]?\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,4})?|[+-]?\d+(?:[.,]\d{1,4})?)(?![A-Za-z0-9_])"
)
_ABSTENTION_PREFIX_RE = re.compile(
    r"^\s*(?:INSUFFICIENT_EVIDENCE|NO_DOC_COVERAGE|LOW_RELEVANCE_EVIDENCE)\b",
    re.IGNORECASE,
)


@dataclass
class NumericClaimVerificationResult:
    """Outcome of claim-to-evidence numeric verification across synthesized prose."""

    unsupported_claims: list[dict[str, Any]] = field(default_factory=list)
    uncited_claims: list[dict[str, Any]] = field(default_factory=list)
    blindspots: list[dict[str, str]] = field(default_factory=list)


def _parse_number_variants(raw: str, *, include_ambiguous: bool) -> list[float]:
    """Parse Vietnamese (`16.012,5` / `16.012`) and English (`16,012.5` / `16,012`) numbers."""
    s = raw.strip().replace(" ", "").lstrip("+")
    if not s:
        return []
    sign = -1.0 if s.startswith("-") else 1.0
    body = s[1:] if s.startswith("-") else s
    if not body:
        return []

    out: list[float] = []

    def _add(val_str: str) -> None:
        try:
            v = sign * float(val_str)
            if v not in out:
                out.append(v)
        except ValueError:
            pass

    if "." in body and "," in body:
        # Whichever separator appears last is the decimal separator
        if body.rfind(".") > body.rfind(","):
            _add(body.replace(",", ""))
        else:
            _add(body.replace(".", "").replace(",", "."))
        return out

    if "," in body:
        parts = body.split(",")
        if len(parts) > 2 and all(len(p) == 3 for p in parts[1:]):
            _add("".join(parts))
        elif len(parts) == 2 and len(parts[1]) == 3:
            _add("".join(parts))
            if include_ambiguous:
                _add(f"{parts[0]}.{parts[1]}")
        else:
            _add(body.replace(",", "."))
        return out

    if "." in body:
        parts = body.split(".")
        if len(parts) > 2 and all(len(p) == 3 for p in parts[1:]):
            _add("".join(parts))
        elif len(parts) == 2 and len(parts[1]) == 3:
            # In Vietnamese reports, 16.012 is thousands; in English, 16.012 is decimal.
            _add("".join(parts))
            if include_ambiguous:
                _add(body)
        else:
            _add(body)
        return out

    _add(body)
    return out


def _classify_claim_unit(unit: str) -> str:
    low = (unit or "").strip().lower()
    if not low:
        return "RAW"
    if low in _PERCENT_UNIT_MARKERS or "%" in low or "bps" in low or "phần trăm" in low or "điểm cơ bản" in low:
        return "PERCENT"
    if low in _MULTIPLE_UNIT_MARKERS:
        return "MULTIPLE"
    if low in _MONETARY_UNIT_MARKERS or any(
        tok in low for tok in ("tỷ", "tỉ", "triệu", "vnd", "vnđ", "đồng", "usd", "billion", "million", "trillion")
    ):
        return "MONETARY"
    return "RAW"


def _expand_numeric_scales(
    val: float,
    *,
    unit_multiplier: float = 1.0,
    include_percent_scale: bool = False,
) -> list[float]:
    """Generate equivalent scale representations (base, nghìn, triệu, tỷ, nghìn tỷ) for matching."""
    av = abs(val)
    if av == 0.0:
        return [0.0]
    vals = {
        av,
        av / 1e3,
        av / 1e6,
        av / 1e9,
        av / 1e12,
        av * 1e3,
    }
    if include_percent_scale:
        vals.update({av * 100.0, av * 10000.0})
    if unit_multiplier and unit_multiplier != 1.0:
        base = av * abs(unit_multiplier)
        vals.update({base, base / 1e3, base / 1e6, base / 1e9, base / 1e12})
    return list(vals)


def _new_typed_pool() -> dict[str, list[float]]:
    return {"PERCENT": [], "MULTIPLE": [], "MONETARY": [], "RAW": []}


def _merge_typed_pools(target: dict[str, list[float]], src: Mapping[str, Sequence[float]]) -> None:
    for cls_name, vals in src.items():
        if cls_name in target and vals:
            target[cls_name].extend(vals)


def _extract_typed_numbers_from_text(text: str) -> dict[str, list[float]]:
    """Extract numbers tagged by UnitClass (`PERCENT`, `MULTIPLE`, `MONETARY`, `RAW`) from prose."""
    typed = _new_typed_pool()
    raw = text or ""
    for m in _SALIENT_FINANCIAL_CLAIM_RE.finditer(raw):
        raw_num = m.group(1)
        unit = (m.group(2) or "").strip().lower()
        if not unit and m.end() < len(raw) and raw[m.end() : m.end() + 1] == "%":
            unit = "%"
        uclass = _classify_claim_unit(unit)
        for v in _parse_number_variants(raw_num, include_ambiguous=True):
            av = abs(v)
            if uclass == "PERCENT":
                typed["PERCENT"].extend([av, av * 100.0] if av <= 2.0 else [av])
            elif uclass == "MULTIPLE":
                typed["MULTIPLE"].append(av)
            elif uclass == "MONETARY":
                typed["MONETARY"].extend(_expand_numeric_scales(av))
                typed["RAW"].extend(_expand_numeric_scales(av))
            else:
                typed["RAW"].extend(_expand_numeric_scales(av))

    for m in _GENERIC_NUMBER_RE.finditer(raw):
        for v in _parse_number_variants(m.group(1), include_ambiguous=True):
            av = abs(v)
            typed["RAW"].extend(_expand_numeric_scales(av))
            if av <= 2.5:
                typed["PERCENT"].extend([av, av * 100.0])
            if av <= 100.0:
                typed["MULTIPLE"].append(av)
    return typed


def _extract_numbers_from_text(text: str) -> list[float]:
    pool: list[float] = []
    for m in _GENERIC_NUMBER_RE.finditer(text or ""):
        for v in _parse_number_variants(m.group(1), include_ambiguous=True):
            pool.extend(_expand_numeric_scales(v))
    return pool


def _collect_typed_table_numbers(hydrated_tables: Sequence[Any]) -> dict[str, dict[str, list[float]]]:
    """Partition table numbers by `(owner_ticker, unit_class)` — never laundering VND into `%`."""
    by_ticker: dict[str, dict[str, list[float]]] = {}
    for tbl in hydrated_tables or ():
        if isinstance(tbl, Mapping):
            mult = float(tbl.get("unit_multiplier") or 1.0)
            rows = tbl.get("rows") or ()
            title = str(tbl.get("title") or "")
            footnotes = str(tbl.get("footnotes_text") or "")
            owner = str(tbl.get("project_id") or tbl.get("ticker") or "*").strip().upper() or "*"
            unit_raw = str(tbl.get("unit_raw") or "").lower()
        else:
            mult = float(getattr(tbl, "unit_multiplier", 1.0) or 1.0)
            rows = getattr(tbl, "rows", ()) or ()
            title = str(getattr(tbl, "title", "") or "")
            footnotes = str(getattr(tbl, "footnotes_text", "") or "")
            owner = (
                str(getattr(tbl, "project_id", None) or getattr(tbl, "ticker", None) or "*").strip().upper()
                or "*"
            )
            unit_raw = str(getattr(tbl, "unit_raw", "") or "").lower()

        bucket = by_ticker.setdefault(owner, _new_typed_pool())
        _merge_typed_pools(bucket, _extract_typed_numbers_from_text(f"{title} {footnotes}"))
        is_monetary_table = mult >= 1e3 or any(
            k in unit_raw for k in ("đồng", "vnd", "vnđ", "usd", "tỷ", "triệu", "nghìn")
        )

        for row in rows:
            row_label = ""
            cells: list[Any] = []
            if isinstance(row, Mapping):
                row_label = str(row.get("label") or "").lower()
                cells.extend(row.get("values") or ())
            elif isinstance(row, list) and row:
                first = row[0]
                row_label = str(first.get("raw_text") if isinstance(first, Mapping) else first).lower()
                cells.extend(row[1:])
            row_is_pct = any(k in row_label for k in ("%", "tỷ lệ", "biên", "roe", "roa", "tăng trưởng", "margin"))
            row_is_mult = any(k in row_label for k in ("lần", "p/e", "p/b", "hệ số", "ratio"))

            for cell in cells:
                nums: list[float] = []
                cell_has_pct = False
                if isinstance(cell, Mapping):
                    nv = cell.get("numeric_value")
                    if isinstance(nv, (int, float)) and not isinstance(nv, bool):
                        nums.append(float(nv))
                    rt = str(cell.get("raw_text") or "")
                    if "%" in rt:
                        cell_has_pct = True
                    if rt:
                        nums.extend(_parse_number_variants(rt, include_ambiguous=True))
                elif isinstance(cell, (int, float)) and not isinstance(cell, bool):
                    nums.append(float(cell))
                elif isinstance(cell, str):
                    if "%" in cell:
                        cell_has_pct = True
                    nums.extend(_parse_number_variants(cell, include_ambiguous=True))

                for v in nums:
                    av = abs(v)
                    if cell_has_pct or row_is_pct:
                        bucket["PERCENT"].extend([av, av * 100.0] if av <= 2.0 else [av])
                    elif row_is_mult:
                        bucket["MULTIPLE"].append(av)
                    elif is_monetary_table:
                        bucket["MONETARY"].extend(_expand_numeric_scales(av, unit_multiplier=mult))
                        bucket["RAW"].extend(_expand_numeric_scales(av, unit_multiplier=mult))
                    else:
                        bucket["MONETARY"].extend(_expand_numeric_scales(av, unit_multiplier=mult))
                        bucket["RAW"].extend(_expand_numeric_scales(av, unit_multiplier=mult))
                        if av <= 100.0:
                            bucket["MULTIPLE"].append(av)
    return by_ticker


def _collect_typed_calc_kg_and_screen_numbers(
    calculator_traces: Sequence[Any],
    graph_hops: Sequence[Any],
    screening_results: Sequence[Any] = (),
) -> dict[str, dict[str, list[float]]]:
    by_ticker: dict[str, dict[str, list[float]]] = {}

    for tr in calculator_traces or ():
        res = tr.get("result") if isinstance(tr, Mapping) else getattr(tr, "result", None)
        fmt = str(tr.get("formatted") if isinstance(tr, Mapping) else getattr(tr, "formatted", "") or "")
        inputs = tr.get("inputs") if isinstance(tr, Mapping) else getattr(tr, "inputs", None)
        owner = (
            str((tr.get("ticker") if isinstance(tr, Mapping) else getattr(tr, "ticker", None)) or "*")
            .strip()
            .upper()
            or "*"
        )
        fmt_style = str(
            (tr.get("format_style") if isinstance(tr, Mapping) else getattr(tr, "format_style", "")) or ""
        ).lower()
        bucket = by_ticker.setdefault(owner, _new_typed_pool())

        if isinstance(res, (int, float)) and not isinstance(res, bool):
            rv = abs(float(res))
            if fmt_style in ("percent", "signed_percent", "bps") or "%" in fmt or "bps" in fmt.lower():
                bucket["PERCENT"].extend([rv, rv * 100.0, rv * 10000.0])
            elif fmt_style == "ratio" or fmt.lower().endswith("x"):
                bucket["MULTIPLE"].append(rv)
            else:
                bucket["MONETARY"].extend(_expand_numeric_scales(rv))
                bucket["RAW"].extend(_expand_numeric_scales(rv))
                if rv <= 2.5:
                    bucket["PERCENT"].extend([rv, rv * 100.0])
                if rv <= 100.0:
                    bucket["MULTIPLE"].append(rv)
        if fmt:
            _merge_typed_pools(bucket, _extract_typed_numbers_from_text(fmt))
        if isinstance(inputs, Mapping):
            for inp in inputs.values():
                if isinstance(inp, Mapping) and isinstance(inp.get("value"), (int, float)):
                    mult = float(inp.get("unit_multiplier") or 1.0)
                    iv = abs(float(inp["value"]))
                    bucket["MONETARY"].extend(_expand_numeric_scales(iv, unit_multiplier=mult))
                    bucket["RAW"].extend(_expand_numeric_scales(iv, unit_multiplier=mult))
                elif isinstance(inp, (int, float)) and not isinstance(inp, bool):
                    iv = abs(float(inp))
                    bucket["MONETARY"].extend(_expand_numeric_scales(iv))
                    bucket["RAW"].extend(_expand_numeric_scales(iv))

    for hop in graph_hops or ():
        src_lbl = str(hop.get("src_label") if isinstance(hop, Mapping) else getattr(hop, "src_label", "") or "")
        dst_lbl = str(hop.get("dst_label") if isinstance(hop, Mapping) else getattr(hop, "dst_label", "") or "")
        owner = "*"
        for lbl in (dst_lbl, src_lbl):
            if lbl.upper().startswith("TICKER:"):
                owner = lbl.split(":", 1)[1].strip().upper() or "*"
                break
        bucket = by_ticker.setdefault(owner, _new_typed_pool())
        for attr in ("beta", "p_value", "fdr_q_value"):
            val = hop.get(attr) if isinstance(hop, Mapping) else getattr(hop, attr, None)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                fv = abs(float(val))
                bucket["RAW"].append(fv)
                bucket["MULTIPLE"].append(fv)
                bucket["PERCENT"].extend([fv, fv * 100.0])

    for row in screening_results or ():
        if not isinstance(row, Mapping):
            continue
        owner = str(row.get("ticker") or "*").strip().upper() or "*"
        bucket = by_ticker.setdefault(owner, _new_typed_pool())
        for col, raw_v in row.items():
            if not isinstance(raw_v, (int, float)) or isinstance(raw_v, bool):
                continue
            fv = abs(float(raw_v))
            if col in _SCREEN_PERCENT_METRICS:
                bucket["PERCENT"].extend([fv, fv * 100.0])
            elif col in _SCREEN_MULTIPLE_METRICS:
                bucket["MULTIPLE"].append(fv)
            elif col in _SCREEN_MONETARY_METRICS:
                bucket["MONETARY"].extend(_expand_numeric_scales(fv))
                bucket["RAW"].extend(_expand_numeric_scales(fv))
            elif col == "quality_score":
                bucket["RAW"].append(fv)
    return by_ticker


def _is_year_like(val: float, unit: str) -> bool:
    return not unit and 1990.0 <= val <= 2035.0 and float(int(val)) == val


def _number_supported_by_pool(
    claim_candidates: Sequence[float],
    pool: Sequence[float],
    *,
    unit_class: str = "RAW",
) -> bool:
    for raw_c in claim_candidates:
        c = abs(raw_c)
        for e in pool:
            diff = abs(c - e)
            tol = max(0.05, 0.015 * max(c, e))
            if diff <= tol:
                return True
            if unit_class in ("MONETARY", "RAW") and c >= 50.0 and diff <= 1.0 and round(c) == round(e):
                return True
    return False


def _detect_single_owner_ticker(seg: str, known_tickers: set[str]) -> str | None:
    if not known_tickers:
        return None
    found: list[str] = []
    for m in _EXPLICIT_TICKER_TOKEN_RE.finditer(seg or ""):
        cand = m.group(1)
        if cand in _NON_TICKER_CAP_WORDS:
            continue
        if cand in known_tickers and cand not in found:
            found.append(cand)
    return found[0] if len(found) == 1 else None


_MEMORY_NUMERIC_ELIGIBLE_TYPES: frozenset[str] = frozenset(
    {"USER_INVESTING_CRITERIA", "USER_EXTERNAL_HOLDING"}
)
_INNERMOST_PAREN_RE = re.compile(r"\(([^()]+)\)")
_CLAUSE_SPLIT_RE = re.compile(
    r"(?:(?<!\d),(?!\d)|[;—–]|\s+-\s+|(?<=\])\s*:\s*|(?<=\])(?=\s+[^\[\s]))"
)


def _extract_memory_typed_numbers(
    content_text: str,
    memory_type: str,
    structured_payload: Mapping[str, Any] | None = None,
) -> dict[str, list[float]]:
    """Extract strict, non-inflated typed numbers from eligible user memory items only.

    ONLY `USER_INVESTING_CRITERIA` and `USER_EXTERNAL_HOLDING` may contribute numeric pools.
    Never calls `_expand_numeric_scales` so `125.000đ` can NEVER launder `125 tỷ`.
    """
    typed = _new_typed_pool()
    if str(memory_type or "").strip().upper() not in _MEMORY_NUMERIC_ELIGIBLE_TYPES:
        return typed

    raw = content_text or ""
    for m in _SALIENT_FINANCIAL_CLAIM_RE.finditer(raw):
        raw_num = m.group(1)
        unit = (m.group(2) or "").strip().lower()
        if not unit and m.end() < len(raw) and raw[m.end() : m.end() + 1] == "%":
            unit = "%"
        uclass = _classify_claim_unit(unit)
        # Disambiguate VN dot-thousands for VND share prices/quantities (never turn 125.000đ into 125.0)
        ambig = uclass not in ("MONETARY",)
        for v in _parse_number_variants(raw_num, include_ambiguous=ambig):
            av = abs(v)
            if uclass == "PERCENT":
                typed["PERCENT"].extend([av, av * 100.0] if av <= 2.0 else [av, av / 100.0])
            elif uclass == "MULTIPLE":
                typed["MULTIPLE"].append(av)
            elif uclass == "MONETARY":
                typed["MONETARY"].append(av)
                typed["RAW"].append(av)
                if unit in ("tỷ", "tỉ", "billion", "bn"):
                    typed["MONETARY"].append(av * 1e9)
                elif unit in ("triệu", "million", "mn"):
                    typed["MONETARY"].append(av * 1e6)
            else:
                typed["RAW"].append(av)

    # Also parse plain quantities (e.g. "10.000 cổ phiếu FPT", "giá vốn 125.000đ") without scale inflation
    for m in _PLAIN_LARGE_NUMBER_RE.finditer(raw):
        for v in _parse_number_variants(m.group(1), include_ambiguous=False):
            av = abs(v)
            if av >= 1000.0:
                typed["MONETARY"].append(av)
                typed["RAW"].append(av)

    if isinstance(structured_payload, Mapping):
        for k in ("quantity", "shares"):
            val = structured_payload.get(k)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                typed["RAW"].append(abs(float(val)))
                typed["MONETARY"].append(abs(float(val)))
        for k in ("avg_cost", "market_value", "cost_basis"):
            val = structured_payload.get(k)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                typed["MONETARY"].append(abs(float(val)))
                typed["RAW"].append(abs(float(val)))
        for k in ("weight_pct", "unrealized_pnl_pct", "min_roe", "max_pe", "max_de"):
            val = structured_payload.get(k)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                av = abs(float(val))
                if "pct" in k or "roe" in k:
                    typed["PERCENT"].extend([av, av * 100.0] if av <= 2.0 else [av])
                else:
                    typed["MULTIPLE"].append(av)
    return typed


def _collect_memory_and_portfolio_pools(
    recalled_memories: Mapping[str, Any] | Sequence[Any] = (),
    portfolio_snapshots: Mapping[str, Any] | Sequence[Any] = (),
) -> tuple[dict[str, dict[str, list[float]]], dict[str, dict[str, list[float]]]]:
    mem_pools: dict[str, dict[str, list[float]]] = {}
    if isinstance(recalled_memories, Mapping):
        mem_iter = list(recalled_memories.items())
    else:
        mem_iter = []
        for idx, item in enumerate(recalled_memories or (), 1):
            bid = (
                str(item.get("badge_id") if isinstance(item, Mapping) else getattr(item, "badge_id", ""))
                or f"M{idx}"
            )
            mem_iter.append((bid, item))

    for bid, item in mem_iter:
        if isinstance(item, Mapping):
            mtype = str(item.get("memory_type") or "")
            ctext = str(item.get("content_text") or "")
            spay = item.get("structured_payload") or item.get("structured_payload_json")
        else:
            mtype = str(getattr(item, "memory_type", "") or "")
            ctext = str(getattr(item, "content_text", "") or "")
            spay = getattr(item, "structured_payload", None)
        mem_pools[str(bid).strip()] = _extract_memory_typed_numbers(
            ctext,
            mtype,
            spay if isinstance(spay, Mapping) else None,
        )

    port_pools: dict[str, dict[str, list[float]]] = {}
    if isinstance(portfolio_snapshots, Mapping):
        port_iter = list(portfolio_snapshots.items())
    else:
        port_iter = []
        for item in portfolio_snapshots or ():
            sym = (
                str(item.get("symbol") if isinstance(item, Mapping) else getattr(item, "symbol", ""))
                .strip()
                .upper()
            )
            if sym:
                port_iter.append((sym, item))

    for sym, entry in port_iter:
        clean_sym = str(sym or "").strip().upper()
        if not clean_sym:
            continue
        bucket = _new_typed_pool()
        qty = entry.get("quantity") if isinstance(entry, Mapping) else getattr(entry, "quantity", None)
        if isinstance(qty, (int, float)) and not isinstance(qty, bool):
            av = abs(float(qty))
            bucket["RAW"].append(av)
            bucket["MONETARY"].append(av)
        for col in ("avg_cost", "market_value"):
            val = entry.get(col) if isinstance(entry, Mapping) else getattr(entry, col, None)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                av = abs(float(val))
                bucket["MONETARY"].append(av)
                bucket["RAW"].append(av)
                if col == "market_value" and av >= 1e9:
                    bucket["MONETARY"].append(av / 1e9)
        for col in ("weight_pct", "unrealized_pnl_pct"):
            val = entry.get(col) if isinstance(entry, Mapping) else getattr(entry, col, None)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                av = abs(float(val))
                bucket["PERCENT"].extend([av, av * 100.0] if av <= 1.0 else [av])
        port_pools[clean_sym] = bucket

    return mem_pools, port_pools


def _extract_claims_from_text(clause_text: str) -> list[tuple[str, str, str, list[float]]]:
    cleaned = _ANY_EVIDENCE_BADGE_RE.sub(" ", clause_text)
    cleaned = re.sub(r"^\s*(?:[-*•]+|\d+[.)]|\(\d+\)|#{1,6}\s*\d*[.)]?)\s*", "", cleaned)
    cleaned = _STRIP_DATES_AND_REFS_RE.sub(" ", cleaned)

    claims: list[tuple[str, str, str, list[float]]] = []
    covered_spans: list[tuple[int, int]] = []
    for m in _SALIENT_FINANCIAL_CLAIM_RE.finditer(cleaned):
        raw_num = m.group(1)
        unit = (m.group(2) or "").strip().lower()
        if not unit and m.end() < len(cleaned) and cleaned[m.end() : m.end() + 1] == "%":
            unit = "%"
        vals = _parse_number_variants(raw_num, include_ambiguous=True)
        if not vals or all(_is_year_like(abs(v), unit) for v in vals):
            continue
        uclass = _classify_claim_unit(unit)
        claims.append((f"{raw_num} {unit}".strip(), unit, uclass, vals))
        covered_spans.append(m.span(1))

    for m in _PLAIN_LARGE_NUMBER_RE.finditer(cleaned):
        sp = m.span(1)
        if any(not (sp[1] <= cs[0] or sp[0] >= cs[1]) for cs in covered_spans):
            continue
        raw_num = m.group(1)
        vals = _parse_number_variants(raw_num, include_ambiguous=True)
        if not vals or all(_is_year_like(abs(v), "") for v in vals):
            continue
        if max(abs(v) for v in vals) < 1000.0:
            continue
        claims.append((raw_num, "", "MONETARY", vals))
    return claims


def _split_single_level_clauses(
    text: str,
) -> list[tuple[str, list[str], list[str], list[str], bool]]:
    raw_parts = [p.strip() for p in _CLAUSE_SPLIT_RE.split(text) if p and p.strip()]
    if not raw_parts:
        return []

    seg_has_fin = bool(_FINANCIAL_BADGE_RE.search(text))
    resolved: list[tuple[str, list[str], list[str], list[str], bool]] = []
    for part in raw_parts:
        cids = _INLINE_CITATION_BADGE_RE.findall(part)
        mids = _INLINE_MEM_BADGE_RE.findall(part)
        psyms = _INLINE_PORT_BADGE_RE.findall(part)
        has_fin = bool(_FINANCIAL_BADGE_RE.search(part))
        resolved.append((part, cids, mids, psyms, has_fin))

    # Step A: Forward-propagate ONLY 0-claim attribution lead-ins (e.g. "Theo [MEM:M1]," or "Tiêu chí [MEM:M1]:")
    # onto the immediately following badge-less clause.
    for i in range(len(resolved) - 1):
        txt, cids, mids, psyms, has_fin = resolved[i]
        if (mids or psyms) and not has_fin and not cids and not _extract_claims_from_text(txt):
            n_txt, n_cids, n_mids, n_psyms, n_fin = resolved[i + 1]
            if not n_cids and not n_mids and not n_psyms and not n_fin:
                resolved[i + 1] = (n_txt, [], list(mids), list(psyms), False)

    # Step B: Safe backward propagation for trailing badges ONLY:
    # - Financial badges may propagate backward across badge-less financial sub-clauses.
    # - [MEM:*]/[PORT:*] may NEVER propagate backward across a clause boundary when the sentence
    #   also contains any financial badge (blocks laundering like "FPT P/E 15x - tiêu chí 15x [MEM:M1] - [SCR:FPT]").
    for i in range(len(resolved) - 2, -1, -1):
        txt, cids, mids, psyms, has_fin = resolved[i]
        if not cids and not mids and not psyms and not has_fin:
            _, n_cids, n_mids, n_psyms, n_fin = resolved[i + 1]
            if n_fin or n_cids:
                resolved[i] = (txt, list(n_cids), [], [], n_fin)
            elif (n_mids or n_psyms) and not seg_has_fin:
                resolved[i] = (txt, [], list(n_mids), list(n_psyms), False)
    return resolved


def _split_sentence_into_badge_scoped_clauses(seg: str) -> list[tuple[str, list[str], list[str], list[str], bool]]:
    """Split a sentence containing `[MEM:*]`/`[PORT:*]` into isolated clauses.

    Each returned tuple is `(clause_text, cited_cids, mem_ids, port_syms, has_fin_badge)`.
    Balanced parentheticals containing any evidence badge are detached first so an embedded
    `(theo tiêu chí < 15x [MEM:M1])` can NEVER hijack an outer financial clause's badge scope.
    """
    detached_fragments: list[str] = []
    outer = seg
    # Detach innermost badge-bearing parentheticals iteratively (handles nested parens cleanly)
    for _ in range(8):
        changed = False

        def _detach(match: re.Match[str]) -> str:
            nonlocal changed
            inner = match.group(1)
            if _ANY_EVIDENCE_BADGE_RE.search(inner):
                detached_fragments.append(inner.strip())
                changed = True
                return " "
            return match.group(0)

        outer = _INNERMOST_PAREN_RE.sub(_detach, outer)
        if not changed:
            break

    clauses: list[tuple[str, list[str], list[str], list[str], bool]] = []
    if outer.strip():
        clauses.extend(_split_single_level_clauses(outer.strip()))
    for frag in detached_fragments:
        if frag:
            clauses.extend(_split_single_level_clauses(frag))
    return clauses


def verify_numeric_claims_against_evidence(
    answer_text: str,
    *,
    sections_by_cid: Mapping[str, Any],
    hydrated_tables: Sequence[Any] = (),
    calculator_traces: Sequence[Any] = (),
    graph_hops: Sequence[Any] = (),
    screening_results: Sequence[Any] = (),
    recalled_memories: Mapping[str, Any] | Sequence[Any] = (),
    portfolio_snapshots: Mapping[str, Any] | Sequence[Any] = (),
    max_blindspots: int = 5,
) -> NumericClaimVerificationResult:
    """Cross-check salient financial numbers partitioned strictly by clause, `(owner_ticker, unit_class)`,
    and evidence boundary.

    Clause-Scoped Partitioning guarantees:
    - Numbers in a clause carrying ONLY `[MEM:M#]` or `[PORT:<SYM>]` are checked strictly
      against that exact `M#` pool (eligible only for `USER_INVESTING_CRITERIA` /
      `USER_EXTERNAL_HOLDING`) or `PORT:<SYM>` pool — NEVER merged into BCTC/Screener/Table/Calc pools.
    - Numbers in a clause carrying `[C#]`/`[TBL:]`/`[SCR:]`/`[CALC-]`/`[KG:]` NEVER borrow
      numbers from `[MEM:M#]` or `[PORT:<SYM>]` (blocking numeric laundering attacks such as
      `"Theo tiêu chí P/E < 15x [MEM:M1], FPT có P/E 15x [SCR:FPT]"`).
    """
    text = (answer_text or "").strip()
    if not text or _ABSTENTION_PREFIX_RE.match(text):
        return NumericClaimVerificationResult()
    if (
        not sections_by_cid
        and not hydrated_tables
        and not calculator_traces
        and not graph_hops
        and not screening_results
        and not recalled_memories
        and not portfolio_snapshots
    ):
        return NumericClaimVerificationResult()

    section_typed: dict[str, dict[str, list[float]]] = {}
    section_owner: dict[str, str] = {}
    all_sections_by_ticker: dict[str, dict[str, list[float]]] = {}
    known_tickers: set[str] = set()

    for cid, sec in sections_by_cid.items():
        if isinstance(sec, Mapping):
            blob = f"{sec.get('heading', '')} {sec.get('content', '')}"
            owner = str(sec.get("ticker") or sec.get("project_id") or "*").strip().upper() or "*"
        else:
            blob = f"{getattr(sec, 'heading', '')} {getattr(sec, 'content', '')}"
            owner = (
                str(getattr(sec, "ticker", None) or getattr(sec, "project_id", None) or "*").strip().upper()
                or "*"
            )
        typed_nums = _extract_typed_numbers_from_text(blob)
        section_typed[str(cid)] = typed_nums
        section_owner[str(cid)] = owner
        if owner != "*":
            known_tickers.add(owner)
        _merge_typed_pools(all_sections_by_ticker.setdefault(owner, _new_typed_pool()), typed_nums)

    structured_by_ticker = _collect_typed_table_numbers(hydrated_tables)
    calc_kg_scr = _collect_typed_calc_kg_and_screen_numbers(
        calculator_traces, graph_hops, screening_results
    )
    for tk, pool_map in calc_kg_scr.items():
        _merge_typed_pools(structured_by_ticker.setdefault(tk, _new_typed_pool()), pool_map)
    for tk in structured_by_ticker:
        if tk != "*":
            known_tickers.add(tk)

    memory_pools_by_badge, portfolio_pools_by_symbol = _collect_memory_and_portfolio_pools(
        recalled_memories, portfolio_snapshots
    )

    def _extract_bucket_values(bucket: Mapping[str, list[float]], unit_class: str) -> list[float]:
        vals_out: list[float] = []
        if unit_class == "PERCENT":
            vals_out.extend(bucket.get("PERCENT", ()))
        elif unit_class == "MULTIPLE":
            vals_out.extend(bucket.get("MULTIPLE", ()))
        elif unit_class == "MONETARY":
            vals_out.extend(bucket.get("MONETARY", ()))
            vals_out.extend(bucket.get("RAW", ()))
        else:
            vals_out.extend(bucket.get("RAW", ()))
            vals_out.extend(bucket.get("MONETARY", ()))
            vals_out.extend(bucket.get("MULTIPLE", ()))
        return vals_out

    def _select_pool(
        *,
        cited_cids: Sequence[str],
        seg_text: str,
        unit_class: str,
    ) -> list[float]:
        single_tk = _detect_single_owner_ticker(seg_text, known_tickers)
        has_struct_badge = any(
            tag in seg_text for tag in ("[TBL:", "[CALC-", "[KG:", "[SCR:")
        )
        out: list[float] = []

        if cited_cids:
            for cid in cited_cids:
                c_owner = section_owner.get(cid, "*")
                if single_tk and c_owner != "*" and c_owner != single_tk:
                    continue
                if cid in section_typed:
                    out.extend(_extract_bucket_values(section_typed[cid], unit_class))
        else:
            for tk, b in all_sections_by_ticker.items():
                if single_tk and tk != "*" and tk != single_tk:
                    continue
                out.extend(_extract_bucket_values(b, unit_class))

        if has_struct_badge or not cited_cids or single_tk is not None:
            for tk, b in structured_by_ticker.items():
                if single_tk and tk != "*" and tk != single_tk:
                    continue
                out.extend(_extract_bucket_values(b, unit_class))
        else:
            for b in structured_by_ticker.values():
                out.extend(_extract_bucket_values(b, unit_class))
        return out

    def _select_memory_or_portfolio_pool(
        *,
        mem_ids: Sequence[str],
        port_syms: Sequence[str],
        unit_class: str,
    ) -> list[float]:
        out: list[float] = []
        for mid in mem_ids:
            bucket = memory_pools_by_badge.get(mid)
            if bucket is not None:
                out.extend(_extract_bucket_values(bucket, unit_class))
        for sym in port_syms:
            bucket = portfolio_pools_by_symbol.get(sym.upper())
            if bucket is not None:
                out.extend(_extract_bucket_values(bucket, unit_class))
        return out

    unsupported: list[dict[str, Any]] = []
    uncited: list[dict[str, Any]] = []
    blindspots: list[dict[str, str]] = []
    seen_unsupported_keys: set[str] = set()
    seen_uncited_sentences: set[str] = set()

    segments = [
        seg.strip()
        for line in text.splitlines()
        for seg in re.split(r"(?<=[.!?])\s+(?=[A-ZĐÀ-Ỹ0-9*-])", line.strip())
        if seg.strip()
    ]

    for seg in segments:
        if _ABSTENTION_PREFIX_RE.match(seg):
            continue
        has_any_badge = bool(_ANY_EVIDENCE_BADGE_RE.search(seg))
        has_mem_or_port = bool(_INLINE_MEM_BADGE_RE.search(seg) or _INLINE_PORT_BADGE_RE.search(seg))

        if has_mem_or_port:
            eval_units = _split_sentence_into_badge_scoped_clauses(seg)
        else:
            eval_units = [(seg, _INLINE_CITATION_BADGE_RE.findall(seg), [], [], bool(_FINANCIAL_BADGE_RE.search(seg)))]

        any_claim_in_seg: list[tuple[str, str, str, list[float]]] = []
        uncited_clause_claims: list[str] = []
        for clause_text, clause_cids, clause_mids, clause_psyms, clause_has_fin in eval_units:
            claims = _extract_claims_from_text(clause_text)
            if not claims:
                continue
            any_claim_in_seg.extend(claims)
            if has_mem_or_port and not (clause_cids or clause_mids or clause_psyms or clause_has_fin):
                uncited_clause_claims.append(claims[0][0])

            # Determine STRICT isolated pool for this clause:
            # - Clause with ONLY [MEM:*]/[PORT:*] (no financial badge) -> check ONLY that M#/PORT pool.
            # - Clause with ANY financial badge ([C#]/[TBL]/[SCR]/[CALC]/[KG]) -> check ONLY financial pool.
            use_mem_port_only = bool((clause_mids or clause_psyms) and not clause_has_fin and not clause_cids)

            for claim_text, unit, uclass, vals in claims:
                expanded_claim = list(vals)
                if uclass == "MONETARY":
                    if unit in ("tỷ", "tỉ", "billion", "bn"):
                        expanded_claim.extend(v * 1e9 for v in vals)
                    elif unit in ("nghìn tỷ", "ngàn tỷ", "trillion"):
                        expanded_claim.extend(v * 1e12 for v in vals)
                    elif unit in ("triệu", "million", "mn"):
                        expanded_claim.extend(v * 1e6 for v in vals)
                if use_mem_port_only:
                    active_pool = _select_memory_or_portfolio_pool(
                        mem_ids=clause_mids,
                        port_syms=clause_psyms,
                        unit_class=uclass,
                    )
                else:
                    active_pool = _select_pool(
                        cited_cids=clause_cids,
                        seg_text=clause_text,
                        unit_class=uclass,
                    )
                if not _number_supported_by_pool(expanded_claim, active_pool, unit_class=uclass):
                    ckey = f"{claim_text.lower()}:{normalize_ws(clause_text)[:60]}"
                    if ckey not in seen_unsupported_keys:
                        seen_unsupported_keys.add(ckey)
                        unsupported.append(
                            {
                                "claim_text": claim_text,
                                "unit_class": uclass,
                                "values": vals,
                                "sentence_excerpt": normalize_ws(seg)[:180],
                            }
                        )
                        if len(blindspots) < max_blindspots:
                            blindspots.append(
                                {
                                    "code": "UNSUPPORTED_NUMERIC_CLAIM",
                                    "reason": (
                                        f"Số liệu '{claim_text}' ({uclass}) trong câu trả lời không khớp với "
                                        "bằng chứng cùng mã cổ phiếu và cùng đơn vị đo đã truy xuất."
                                    ),
                                    "scope": "numeric_audit",
                                }
                            )

        if (any_claim_in_seg and not has_any_badge) or uncited_clause_claims:
            sent_key = normalize_ws(seg)[:120]
            first_uncited = uncited_clause_claims[0] if uncited_clause_claims else any_claim_in_seg[0][0]
            if sent_key not in seen_uncited_sentences:
                seen_uncited_sentences.add(sent_key)
                uncited.append(
                    {
                        "claim_text": first_uncited,
                        "sentence_excerpt": normalize_ws(seg)[:180],
                    }
                )
                if len(blindspots) < max_blindspots:
                    blindspots.append(
                        {
                            "code": "UNCITED_NUMERIC_CLAIM",
                            "reason": (
                                f"Số liệu tài chính '{first_uncited}' không có thẻ trích dẫn "
                                "([C#]/[TBL]/[CALC]/[SCR]/[KG]) đi kèm trong câu."
                            ),
                            "scope": "numeric_audit",
                        }
                    )

    return NumericClaimVerificationResult(
        unsupported_claims=unsupported,
        uncited_claims=uncited,
        blindspots=blindspots,
    )
