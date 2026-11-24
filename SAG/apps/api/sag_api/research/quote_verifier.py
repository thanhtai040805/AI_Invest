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


def verify_citations(
    citations: Sequence[Mapping[str, Any]],
    blocks_by_version: Mapping[str, Sequence[CanonicalBlockRecord]],
    as_of_date: date,
    *,
    answer_text: str = "",
    hydrated_tables: Sequence[Mapping[str, Any]] | None = None,
    valid_citation_ids: set[str] | None = None,
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
    r"\[(?:C\d+(?:\s+UNVERIFIED_CLAIM)?|TBL:[^\]]+|CALC-\d+|KG:[^\]]+|DISC-\d+)\]"
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


def _expand_numeric_scales(val: float, *, unit_multiplier: float = 1.0) -> list[float]:
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
    if unit_multiplier and unit_multiplier != 1.0:
        base = av * abs(unit_multiplier)
        vals.update({base, base / 1e3, base / 1e6, base / 1e9, base / 1e12})
    return list(vals)


def _extract_numbers_from_text(text: str) -> list[float]:
    pool: list[float] = []
    for m in _GENERIC_NUMBER_RE.finditer(text or ""):
        for v in _parse_number_variants(m.group(1), include_ambiguous=True):
            pool.extend(_expand_numeric_scales(v))
    return pool


def _collect_table_numbers(hydrated_tables: Sequence[Any]) -> list[float]:
    pool: list[float] = []
    for tbl in hydrated_tables or ():
        if isinstance(tbl, Mapping):
            mult = float(tbl.get("unit_multiplier") or 1.0)
            rows = tbl.get("rows") or ()
            title = str(tbl.get("title") or "")
            footnotes = str(tbl.get("footnotes_text") or "")
        else:
            mult = float(getattr(tbl, "unit_multiplier", 1.0) or 1.0)
            rows = getattr(tbl, "rows", ()) or ()
            title = str(getattr(tbl, "title", "") or "")
            footnotes = str(getattr(tbl, "footnotes_text", "") or "")
        pool.extend(_extract_numbers_from_text(f"{title} {footnotes}"))
        for row in rows:
            cells: list[Any] = []
            if isinstance(row, Mapping):
                cells.extend(row.get("values") or ())
            elif isinstance(row, list):
                cells.extend(row[1:])
            for cell in cells:
                if isinstance(cell, Mapping):
                    nv = cell.get("numeric_value")
                    if isinstance(nv, (int, float)) and not isinstance(nv, bool):
                        pool.extend(_expand_numeric_scales(float(nv), unit_multiplier=mult))
                    rt = cell.get("raw_text")
                    if rt:
                        for v in _parse_number_variants(str(rt), include_ambiguous=True):
                            pool.extend(_expand_numeric_scales(v, unit_multiplier=mult))
                elif isinstance(cell, (int, float)) and not isinstance(cell, bool):
                    pool.extend(_expand_numeric_scales(float(cell), unit_multiplier=mult))
                elif isinstance(cell, str):
                    for v in _parse_number_variants(cell, include_ambiguous=True):
                        pool.extend(_expand_numeric_scales(v, unit_multiplier=mult))
    return pool


def _collect_calc_and_kg_numbers(
    calculator_traces: Sequence[Any],
    graph_hops: Sequence[Any],
) -> list[float]:
    pool: list[float] = []
    for tr in calculator_traces or ():
        res = tr.get("result") if isinstance(tr, Mapping) else getattr(tr, "result", None)
        fmt = tr.get("formatted") if isinstance(tr, Mapping) else getattr(tr, "formatted", "")
        inputs = tr.get("inputs") if isinstance(tr, Mapping) else getattr(tr, "inputs", None)
        if isinstance(res, (int, float)) and not isinstance(res, bool):
            rv = abs(float(res))
            pool.extend([rv, rv * 100.0, rv * 10000.0])
        if fmt:
            pool.extend(_extract_numbers_from_text(str(fmt)))
        if isinstance(inputs, Mapping):
            for inp in inputs.values():
                if isinstance(inp, Mapping) and isinstance(inp.get("value"), (int, float)):
                    mult = float(inp.get("unit_multiplier") or 1.0)
                    pool.extend(_expand_numeric_scales(float(inp["value"]), unit_multiplier=mult))
                elif isinstance(inp, (int, float)) and not isinstance(inp, bool):
                    pool.extend(_expand_numeric_scales(float(inp)))
    for hop in graph_hops or ():
        for attr in ("beta", "p_value", "fdr_q_value"):
            val = hop.get(attr) if isinstance(hop, Mapping) else getattr(hop, attr, None)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                fv = abs(float(val))
                pool.extend([fv, fv * 100.0])
    return pool


def _is_year_like(val: float, unit: str) -> bool:
    return not unit and 1990.0 <= val <= 2035.0 and float(int(val)) == val


def _number_supported_by_pool(claim_candidates: Sequence[float], pool: Sequence[float]) -> bool:
    for raw_c in claim_candidates:
        c = abs(raw_c)
        for e in pool:
            diff = abs(c - e)
            tol = max(0.05, 0.015 * max(c, e))
            if diff <= tol:
                return True
            if c >= 10.0 and diff <= 1.0 and round(c) == round(e):
                return True
    return False


def verify_numeric_claims_against_evidence(
    answer_text: str,
    *,
    sections_by_cid: Mapping[str, Any],
    hydrated_tables: Sequence[Any] = (),
    calculator_traces: Sequence[Any] = (),
    graph_hops: Sequence[Any] = (),
    max_blindspots: int = 5,
) -> NumericClaimVerificationResult:
    """Cross-check salient financial numbers in synthesized prose against retrieved evidence.

    Catches two subtle hallucination vectors that quote_hash alone misses:
    1. `UNSUPPORTED_NUMERIC_CLAIM`: LLM writes a valid badge like `[C1]` next to an invented
       figure (e.g., `"doanh thu đạt 99,999 tỷ [C1]"`) not present in `[C1]`, hydrated tables,
       calculator traces, or KG hops (within 1.5% rounding / unit-scale tolerance).
    2. `UNCITED_NUMERIC_CLAIM`: LLM asserts a salient financial figure in a sentence that has
       zero evidence badges (`[C#]`, `[TBL:*]`, `[CALC-*]`, `[KG:*]`).
    """
    text = (answer_text or "").strip()
    if not text or _ABSTENTION_PREFIX_RE.match(text):
        return NumericClaimVerificationResult()
    if not sections_by_cid and not hydrated_tables and not calculator_traces and not graph_hops:
        return NumericClaimVerificationResult()

    section_pools: dict[str, list[float]] = {}
    all_section_pool: list[float] = []
    for cid, sec in sections_by_cid.items():
        if isinstance(sec, Mapping):
            blob = f"{sec.get('heading', '')} {sec.get('content', '')}"
        else:
            blob = f"{getattr(sec, 'heading', '')} {getattr(sec, 'content', '')}"
        nums = _extract_numbers_from_text(blob)
        section_pools[str(cid)] = nums
        all_section_pool.extend(nums)

    shared_structured_pool = _collect_table_numbers(hydrated_tables) + _collect_calc_and_kg_numbers(
        calculator_traces, graph_hops
    )
    full_evidence_pool = all_section_pool + shared_structured_pool

    unsupported: list[dict[str, Any]] = []
    uncited: list[dict[str, Any]] = []
    blindspots: list[dict[str, str]] = []
    seen_unsupported_keys: set[str] = set()
    seen_uncited_sentences: set[str] = set()

    # Split into lines/sentences while keeping decimal numbers intact
    segments = [
        seg.strip()
        for line in text.splitlines()
        for seg in re.split(r"(?<=[.!?])\s+(?=[A-ZĐÀ-Ỹ0-9*-])", line.strip())
        if seg.strip()
    ]

    for seg in segments:
        if _ABSTENTION_PREFIX_RE.match(seg):
            continue
        cited_cids = _INLINE_CITATION_BADGE_RE.findall(seg)
        has_any_badge = bool(_ANY_EVIDENCE_BADGE_RE.search(seg))

        # Remove badges, dates, VAS codes, page refs, and list prefixes before extracting claims
        cleaned = _ANY_EVIDENCE_BADGE_RE.sub(" ", seg)
        cleaned = re.sub(r"^\s*(?:[-*•]+|\d+[.)]|\(\d+\)|#{1,6}\s*\d*[.)]?)\s*", "", cleaned)
        cleaned = _STRIP_DATES_AND_REFS_RE.sub(" ", cleaned)

        claims: list[tuple[str, str, list[float]]] = []
        covered_spans: list[tuple[int, int]] = []
        for m in _SALIENT_FINANCIAL_CLAIM_RE.finditer(cleaned):
            raw_num = m.group(1)
            unit = (m.group(2) or "").strip().lower()
            if not unit and m.end() < len(cleaned) and cleaned[m.end() : m.end() + 1] == "%":
                unit = "%"
            vals = _parse_number_variants(raw_num, include_ambiguous=True)
            if not vals or all(_is_year_like(abs(v), unit) for v in vals):
                continue
            claims.append((f"{raw_num} {unit}".strip(), unit, vals))
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
            claims.append((raw_num, "", vals))

        if not claims:
            continue

        # Select evidence pool scoped to cited [C#] sections when present, plus structured evidence
        if cited_cids and "[TBL:" not in seg and "[CALC-" not in seg and "[KG:" not in seg:
            scoped_pool: list[float] = list(shared_structured_pool)
            for cid in cited_cids:
                scoped_pool.extend(section_pools.get(cid, ()))
            active_pool = scoped_pool if scoped_pool else full_evidence_pool
        else:
            active_pool = full_evidence_pool

        if not has_any_badge:
            sent_key = normalize_ws(seg)[:120]
            if sent_key not in seen_uncited_sentences:
                seen_uncited_sentences.add(sent_key)
                uncited.append(
                    {
                        "claim_text": claims[0][0],
                        "sentence_excerpt": normalize_ws(seg)[:180],
                    }
                )
                if len(blindspots) < max_blindspots:
                    blindspots.append(
                        {
                            "code": "UNCITED_NUMERIC_CLAIM",
                            "reason": (
                                f"Số liệu tài chính '{claims[0][0]}' không có thẻ trích dẫn "
                                "([C#]/[TBL]/[CALC]/[KG]) đi kèm trong câu."
                            ),
                            "scope": "numeric_audit",
                        }
                    )

        for claim_text, unit, vals in claims:
            expanded_claim = list(vals)
            if unit in ("tỷ", "tỉ", "billion", "bn"):
                expanded_claim.extend(v * 1e9 for v in vals)
            elif unit in ("nghìn tỷ", "ngàn tỷ", "trillion"):
                expanded_claim.extend(v * 1e12 for v in vals)
            elif unit in ("triệu", "million", "mn"):
                expanded_claim.extend(v * 1e6 for v in vals)
            if not _number_supported_by_pool(expanded_claim, active_pool):
                ckey = claim_text.lower()
                if ckey not in seen_unsupported_keys:
                    seen_unsupported_keys.add(ckey)
                    unsupported.append(
                        {
                            "claim_text": claim_text,
                            "values": vals,
                            "sentence_excerpt": normalize_ws(seg)[:180],
                        }
                    )
                    if len(blindspots) < max_blindspots:
                        blindspots.append(
                            {
                                "code": "UNSUPPORTED_NUMERIC_CLAIM",
                                "reason": (
                                    f"Số liệu '{claim_text}' trong câu trả lời không khớp với "
                                    "bằng chứng đã trích xuất (văn bản/bảng số/máy tính/KG)."
                                ),
                                "scope": "numeric_audit",
                            }
                        )

    return NumericClaimVerificationResult(
        unsupported_claims=unsupported,
        uncited_claims=uncited,
        blindspots=blindspots,
    )
