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
from datetime import date, datetime
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


@dataclass(frozen=True, slots=True)
class CanonicalBlockRecord:
    """Normalized representation of a `canonical_blocks` row + `DocumentVersion` PIT date."""

    document_version_id: str
    page_from: int
    page_to: int
    normalized_text: str
    sub_doc_slot: str | None = None
    doc_id: str | None = None
    published_date: date | None = None
    block_id: str | None = None
    ordinal: int = 0


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
) -> dict[str, list[CanonicalBlockRecord]]:
    """Load `canonical_blocks` + `document_versions` PIT metadata for exact verification."""
    ver_ids = sorted({vid for vid in document_version_ids if vid})
    if not ver_ids:
        return {}

    from sag_api.db.models import CanonicalBlock, DocumentVersion

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
            DocumentVersion.metadata_json,
        )
        .join(DocumentVersion, DocumentVersion.id == CanonicalBlock.document_version_id)
        .where(CanonicalBlock.document_version_id.in_(ver_ids))
        .order_by(CanonicalBlock.document_version_id.asc(), CanonicalBlock.ordinal.asc())
    )
    rows = (await session.execute(stmt)).all()
    by_ver: dict[str, list[CanonicalBlockRecord]] = {}
    for blk_id, ordinal, ver_id, p_from, p_to, norm_txt, doc_id, src_pub, valid_from, meta in rows:
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
            )
        )
    return by_ver


def _table_matches_quote(
    norm_quote: str,
    table: Mapping[str, Any],
    as_of_date: date,
) -> bool:
    """Verify quote against a hydrated table's normalized content while enforcing PIT."""
    vf = _parse_date(table.get("valid_from"))
    if vf is not None and vf > as_of_date:
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

            eligible_blocks: list[CanonicalBlockRecord] = []
            for blk in blocks:
                if blk.published_date is not None and blk.published_date > as_of_date:
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
