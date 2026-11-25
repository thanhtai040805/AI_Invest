"""HOSE Stock Research Document Selector & PIT Trap Guard (0 LLM).

Productionizes Stage 1 `block1_input_and_table_engine.py` & `sag_phase1_research_note.md` §3.2:
- 3 Core categories (`BCTC`, `BCTN`, `NQ_DHDCD`) + 2 Conditional (`CAO_BACH`, `GIAI_TRINH` / `LATEST_QUARTER`)
  + 4 Excluded (`TIN_TUC`, `GIAO_DICH_NOI_BO`, `CBTT_DINH_KY_NGAN`, `BAO_CAO_PHAN_TICH_BEN_NGOAI`, plus
  `governance_report` and holding-company `SEPARATE` BCTC when `CONSOLIDATED` exists).
- BCTN/BCTC deduplication when `BCTN` cleanly embeds audited FS & notes.
- `published_date` trap parser (announcement -> signing -> filing -> published priority; fail-closed if missing;
  catches stale fiscal-year stamps like VCI 2016 stamped 2026 and pre-period-end timestamps).
- Strict Point-in-Time (`effective_published_at <= as_of_date`) enforcement.
- Multi-source 404 fallback & safe `.zip` extraction delegation.
"""

from __future__ import annotations

import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, Protocol

import httpx

from sag_api.research.input_inspector import (
    InspectionResult,
    ZipExtractionError,
    inspect_pdf_bytes,
    safe_extract_pdf_from_archive,
    safe_extract_pdf_from_zip,
)
from sag_api.research.vn_language_gate import is_standalone_english_metadata

SelectionTier = Literal["CORE", "CONDITIONAL", "EXCLUDED"]
StatementScope = Literal["CONSOLIDATED", "SEPARATE", "UNKNOWN"]

CORE_DOC_TYPES = frozenset(
    {
        "BCTC",
        "BCTN",
        "NQ_DHDCD",
        "TAI_LIEU_DHDCD",
        "FINANCIAL_STATEMENT",
        "ANNUAL_REPORT",
        "AGM_RESOLUTION",
        "AGM_MATERIALS",
    }
)
CONDITIONAL_DOC_TYPES = frozenset(
    {
        "CAO_BACH",
        "GIAI_TRINH",
        "GIẢI_TRÌNH",
        "LATEST_QUARTER",
        "LATEST_QUARTER_NOTES",
        "TY_LE_AT_TC",
        "CAPITAL_ADEQUACY",
        "PROSPECTUS",
        "EXPLANATION",
    }
)
EXCLUDED_DOC_TYPES = frozenset(
    {
        "TIN_TUC",
        "GIAO_DICH_NOI_BO",
        "CBTT_DINH_KY_NGAN",
        "BAO_CAO_PHAN_TICH_BEN_NGOAI",
        "NEWS",
        "INSIDER_TRADING",
        "PERIODIC_SHORT_CBTT",
        "ANALYST_REPORT",
        "GOVERNANCE_REPORT",
        "BAO_CAO_QUAN_TRI",
    }
)


class MissingPublishedDateError(ValueError):
    """Fail-closed error when a document lacks any valid PIT publication/signing/filing date."""


def _strip_accents_upper(value: str) -> str:
    nfkd = unicodedata.normalize("NFKD", value or "")
    no_marks = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    no_marks = no_marks.replace("đ", "d").replace("Đ", "D")
    return re.sub(r"[\s\-]+", "_", no_marks.strip().upper())


def normalize_doc_type(raw_doc_type: str, title: str = "") -> str:
    """Normalize Core DB or Vietnamese document type labels into canonical codes."""
    norm = _strip_accents_upper(raw_doc_type)
    alias_map = {
        "ANNUAL_REPORT": "BCTN",
        "BAO_CAO_THUONG_NIEN": "BCTN",
        "BCTN": "BCTN",
        "FINANCIAL_STATEMENT": "BCTC",
        "BAO_CAO_TAI_CHINH": "BCTC",
        "BCTC": "BCTC",
        "AGM_RESOLUTION": "NQ_DHDCD",
        "NGHI_QUYET_DHDCD": "NQ_DHDCD",
        "NQ_DHDCD": "NQ_DHDCD",
        "AGM_MATERIALS": "TAI_LIEU_DHDCD",
        "TAI_LIEU_DHDCD": "TAI_LIEU_DHDCD",
        "TO_TRINH_DHDCD": "TAI_LIEU_DHDCD",
        "PROSPECTUS": "CAO_BACH",
        "BAN_CAO_BACH": "CAO_BACH",
        "CAO_BACH": "CAO_BACH",
        "EXPLANATION": "GIAI_TRINH",
        "GIAI_TRINH": "GIAI_TRINH",
        "LATEST_QUARTER": "LATEST_QUARTER",
        "LATEST_QUARTER_NOTES": "LATEST_QUARTER",
        "TY_LE_AT_TC": "TY_LE_AT_TC",
        "CAPITAL_ADEQUACY": "TY_LE_AT_TC",
        "NEWS": "TIN_TUC",
        "TIN_TUC": "TIN_TUC",
        "INSIDER_TRADING": "GIAO_DICH_NOI_BO",
        "GIAO_DICH_NOI_BO": "GIAO_DICH_NOI_BO",
        "PERIODIC_SHORT_CBTT": "CBTT_DINH_KY_NGAN",
        "CBTT_DINH_KY_NGAN": "CBTT_DINH_KY_NGAN",
        "ANALYST_REPORT": "BAO_CAO_PHAN_TICH_BEN_NGOAI",
        "BAO_CAO_PHAN_TICH_BEN_NGOAI": "BAO_CAO_PHAN_TICH_BEN_NGOAI",
        "GOVERNANCE_REPORT": "GOVERNANCE_REPORT",
        "BAO_CAO_QUAN_TRI": "GOVERNANCE_REPORT",
    }
    if norm in alias_map:
        return alias_map[norm]

    title_norm = _strip_accents_upper(title)
    if "BAO_CAO_THUONG_NIEN" in title_norm or "ANNUAL_REPORT" in title_norm:
        return "BCTN"
    if "CAO_BACH" in title_norm or "PROSPECTUS" in title_norm:
        return "CAO_BACH"
    if "GIAI_TRINH" in title_norm:
        return "GIAI_TRINH"
    if "NGHI_QUYET" in title_norm and ("DHDCD" in title_norm or "DAI_HOI" in title_norm):
        return "NQ_DHDCD"
    if ("TAI_LIEU" in title_norm or "TO_TRINH" in title_norm) and (
        "DHDCD" in title_norm or "DAI_HOI" in title_norm
    ):
        return "TAI_LIEU_DHDCD"
    if "AN_TOAN_TAI_CHINH" in title_norm or "VON_KHA_DUNG" in title_norm:
        return "TY_LE_AT_TC"
    if "BAO_CAO_TAI_CHINH" in title_norm or "BCTC" in title_norm or "KIEM_TOAN" in title_norm:
        return "BCTC"
    if "QUAN_TRI" in title_norm:
        return "GOVERNANCE_REPORT"
    if "GIAO_DICH" in title_norm and ("NOI_BO" in title_norm or "CO_DONG_LON" in title_norm):
        return "GIAO_DICH_NOI_BO"
    return norm or "UNKNOWN"


def infer_statement_scope(
    statement_scope: StatementScope | str | None,
    title: str = "",
) -> StatementScope:
    """Resolve `CONSOLIDATED` vs `SEPARATE` from explicit metadata or Vietnamese filing title."""
    scope = str(statement_scope or "UNKNOWN").strip().upper()
    if scope in ("CONSOLIDATED", "SEPARATE"):
        return scope  # type: ignore[return-value]
    title_low = (title or "").lower()
    if any(k in title_low for k in ("hợp nhất", "hop nhat", "consolidated")):
        return "CONSOLIDATED"
    if any(k in title_low for k in ("công ty mẹ", "cong ty me", "bctc riêng", "riêng lẻ", "riêng", "separate")):
        return "SEPARATE"
    return "UNKNOWN"


def classify_document_category(
    doc_type: str,
    *,
    title: str = "",
    statement_scope: StatementScope | str | None = None,
    has_consolidated: bool = True,
) -> tuple[SelectionTier, str]:
    """Classify a document into CORE, CONDITIONAL, or EXCLUDED with explicit reason."""
    canonical = normalize_doc_type(doc_type, title=title)
    scope = infer_statement_scope(statement_scope, title=title)

    if canonical in EXCLUDED_DOC_TYPES:
        return "EXCLUDED", f"EXCLUDED_DOC_TYPE:{canonical}"

    if canonical in ("BCTC", "LATEST_QUARTER"):
        if scope == "SEPARATE" and has_consolidated:
            return "EXCLUDED", "EXCLUDED_SEPARATE_PARENT_WITH_CONSOLIDATED"
        if canonical == "BCTC":
            return "CORE", "CORE_BCTC"
        return "CONDITIONAL", "CONDITIONAL_LATEST_QUARTER"

    if canonical in ("BCTN", "NQ_DHDCD", "TAI_LIEU_DHDCD"):
        return "CORE", f"CORE_{canonical}"

    if canonical in ("CAO_BACH", "GIAI_TRINH", "TY_LE_AT_TC"):
        if canonical == "GIAI_TRINH" and scope == "SEPARATE" and has_consolidated:
            return "EXCLUDED", "EXCLUDED_SEPARATE_PARENT_EXPLANATION"
        return "CONDITIONAL", f"CONDITIONAL_{canonical}"

    return "EXCLUDED", f"UNRECOGNIZED_DOC_TYPE:{canonical}"


def parse_fiscal_year_from_meta(title: str, url: str = "") -> int | None:
    """Extract fiscal year from title/URL (including `_`-delimited filenames like `bctc_2025_hn.pdf`)."""
    # Check title first, then URL filename (basename) before full URL path so upload path years
    # like `https://cdn/2026/03/bctc_2025_hn.pdf` never override the document's actual fiscal year.
    url_str = url or ""
    url_basename = url_str.rsplit("/", 1)[-1].split("?", 1)[0] if "/" in url_str else ""
    candidates_src = [title or ""]
    if url_basename:
        candidates_src.append(url_basename)
    if url_str:
        candidates_src.append(url_str)

    for src in candidates_src:
        if not src:
            continue
        m = re.search(
            r"(?:năm|nam|bctn|bctc|agm|dhdcd|đhđcđ|fy|q[1-4])[\s_\-]*(20(?:0[5-9]|1\d|2\d|3[0-5]))(?![0-9])",
            src,
            re.IGNORECASE,
        )
        if m:
            yr = int(m.group(1))
            if 2005 <= yr <= 2035:
                return yr
        # Prefer standalone fiscal year tokens not glued to ISO YYYY-MM-DD dates if possible
        m_non_date = re.search(
            r"(?<![0-9])(20(?:0[5-9]|1\d|2\d|3[0-5]))(?!(?:-[01]\d-[0-3]\d|[0-9]))",
            src,
        )
        if m_non_date:
            return int(m_non_date.group(1))
        m2 = re.search(r"(?<![0-9])(20(?:0[5-9]|1\d|2\d|3[0-5]))(?![0-9])", src)
        if m2:
            return int(m2.group(1))
    return None


def parse_fiscal_quarter_from_meta(title: str, url: str = "") -> int:
    """Extract fiscal quarter (0 for full year, 1..4 for Q1..Q4), supporting `_`-delimited filenames."""
    for src in (title or "", url or ""):
        m_q = re.search(
            r"(?<![A-Za-z0-9])(?:quý|quy|q)[\s_\-]*([1-4])(?:(?![A-Za-z0-9])|(?=20\d{2}(?![0-9])))",
            src,
            re.IGNORECASE,
        )
        if m_q:
            return int(m_q.group(1))
        m_t = re.search(
            r"(?<![A-Za-z0-9])(3|6|9|12)[\s_\-]*t[\s_\-/]*20\d{2}(?![0-9])",
            src,
            re.IGNORECASE,
        )
        if m_t:
            return int(m_t.group(1)) // 3
        if re.search(
            r"bán[\s_\-]*niên|ban[\s_\-]*nien|6[\s_\-]*tháng|6[\s_\-]*thang|(?<![A-Za-z0-9])h1(?![A-Za-z0-9])",
            src,
            re.IGNORECASE,
        ):
            return 2
    return 0


def _coerce_date(val: date | datetime | str | None) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    if not s:
        return None
    # ISO YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS
    m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m_iso:
        try:
            return date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
        except ValueError:
            return None
    # Vietnamese DD/MM/YYYY or DD-MM-YYYY
    m_vn = re.match(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$", s)
    if m_vn:
        try:
            return date(int(m_vn.group(3)), int(m_vn.group(2)), int(m_vn.group(1)))
        except ValueError:
            return None
    return None


@dataclass(frozen=True, slots=True)
class PitDateResolution:
    effective_published_at: date
    date_source: Literal["announcement_date", "signing_date", "filing_date", "published_date"]
    parsed_fiscal_year: int | None
    parsed_fiscal_quarter: int
    trap_flags: list[str] = field(default_factory=list)
    is_trapped: bool = False


def resolve_effective_published_at(
    *,
    published_date: date | datetime | str | None = None,
    announcement_date: date | datetime | str | None = None,
    signing_date: date | datetime | str | None = None,
    filing_date: date | datetime | str | None = None,
    title: str = "",
    url: str = "",
    period_end: date | datetime | str | None = None,
) -> PitDateResolution:
    """Resolve PIT publication date using strict priority and catch metadata traps.

    Priority: announcement_date -> signing_date -> filing_date -> published_date.
    Fails closed (raises MissingPublishedDateError) if no valid date is present.
    """
    candidates: list[
        tuple[
            Literal["announcement_date", "signing_date", "filing_date", "published_date"],
            date | None,
        ]
    ] = [
        ("announcement_date", _coerce_date(announcement_date)),
        ("signing_date", _coerce_date(signing_date)),
        ("filing_date", _coerce_date(filing_date)),
        ("published_date", _coerce_date(published_date)),
    ]
    chosen_source: Literal["announcement_date", "signing_date", "filing_date", "published_date"] | None = None
    chosen_date: date | None = None
    for src_name, dt_val in candidates:
        if dt_val is not None:
            chosen_source = src_name
            chosen_date = dt_val
            break

    if chosen_date is None or chosen_source is None:
        raise MissingPublishedDateError(
            f"Missing required PIT published/signing/filing date for title={title!r}"
        )

    parsed_fy = parse_fiscal_year_from_meta(title, url)
    parsed_fq = parse_fiscal_quarter_from_meta(title, url)
    p_end = _coerce_date(period_end)

    traps: list[str] = []
    if parsed_fy is not None and (chosen_date.year - parsed_fy) >= 2:
        traps.append(
            f"PUBLISHED_DATE_TRAP: date={chosen_date.isoformat()} vs parsed_fy={parsed_fy}"
        )
    if p_end is not None and chosen_date < p_end:
        traps.append(
            f"PUBLISHED_BEFORE_PERIOD_END: date={chosen_date.isoformat()} < period_end={p_end.isoformat()}"
        )

    return PitDateResolution(
        effective_published_at=chosen_date,
        date_source=chosen_source,
        parsed_fiscal_year=parsed_fy,
        parsed_fiscal_quarter=parsed_fq,
        trap_flags=traps,
        is_trapped=bool(traps),
    )


@dataclass(slots=True)
class ResearchCandidateDocument:
    doc_id: str
    ticker: str
    doc_type: str
    title: str
    urls: list[str] = field(default_factory=list)
    source: str = "core_db"
    statement_scope: StatementScope = "UNKNOWN"
    published_date: date | datetime | str | None = None
    announcement_date: date | datetime | str | None = None
    signing_date: date | datetime | str | None = None
    filing_date: date | datetime | str | None = None
    period_end: date | datetime | str | None = None
    is_extraordinary_agm: bool = False
    trigger_reason: str | None = None
    raw_bytes: bytes | None = None


@dataclass(slots=True)
class FinancialAnomalyContext:
    """Signals from Core DB 5Y financials that activate Conditional documents."""

    bctn_has_plan: bool = True
    yoy_revenue_change_pct: float | None = None
    yoy_gpm_bps_change: float | None = None
    other_income_to_pbt_ratio: float | None = None
    has_recent_capital_event: bool = False
    has_regulatory_explanation_request: bool = False

    def quarter_notes_triggered(self) -> bool:
        if self.yoy_revenue_change_pct is not None and abs(self.yoy_revenue_change_pct) >= 25.0:
            return True
        if self.yoy_gpm_bps_change is not None and abs(self.yoy_gpm_bps_change) >= 300.0:
            return True
        if self.other_income_to_pbt_ratio is not None and self.other_income_to_pbt_ratio >= 0.20:
            return True
        return False


@dataclass(slots=True)
class SelectedDocument:
    candidate: ResearchCandidateDocument
    canonical_doc_type: str
    tier: SelectionTier
    selection_reason: str
    pit_resolution: PitDateResolution
    inspection: InspectionResult | None = None
    pdf_bytes: bytes | None = None
    resolved_url: str = ""
    deduplicated_by: str | None = None


@dataclass(slots=True)
class RejectedDocument:
    doc_id: str
    ticker: str
    title: str
    raw_doc_type: str
    reason: str
    trap_flags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DocumentSelectionResult:
    ticker: str
    as_of_date: date
    selected: list[SelectedDocument] = field(default_factory=list)
    rejected: list[RejectedDocument] = field(default_factory=list)
    traps_caught: list[str] = field(default_factory=list)
    bctn_deduplicated_bctc: bool = False


class CoreDocumentStoreProtocol(Protocol):
    """Protocol abstracting Core PostgreSQL access for offline unit testing."""

    async def list_candidate_documents(
        self,
        ticker: str,
        *,
        as_of_date: date,
    ) -> list[ResearchCandidateDocument]: ...

    async def has_consolidated_statements(
        self,
        ticker: str,
        *,
        as_of_date: date,
    ) -> bool: ...


class BytesFetcherProtocol(Protocol):
    """Protocol abstracting HTTP/R2 binary downloads for offline testing."""

    async def fetch_bytes(self, url: str) -> tuple[int, bytes]: ...


class HttpxBytesFetcher:
    """Default async HTTP fetcher using httpx with redirect support."""

    def __init__(self, timeout_seconds: float = 60.0) -> None:
        self._timeout = timeout_seconds

    async def fetch_bytes(self, url: str) -> tuple[int, bytes]:
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as client:
            resp = await client.get(url)
            return resp.status_code, resp.content


@dataclass(slots=True)
class DocumentFetchResult:
    ok: bool
    content: bytes = b""
    resolved_url: str = ""
    tried_urls: list[str] = field(default_factory=list)
    attempt_errors: list[str] = field(default_factory=list)
    error: str | None = None


_ERRATA_NOTICE_TITLE_RE = re.compile(
    r"(?:đính\s+chính|dinh\s+chinh|bổ\s+sung\s+thuyết\s+minh|thay\s+thế\s+một\s+phần|"
    r"công\s+bố\s+thông\s+tin\s+bất\s+thường|cbtt\s+đính\s+chính)",
    re.IGNORECASE,
)


def _quick_pdf_substantive_check(payload: bytes, *, canonical_doc_type: str) -> tuple[bool, int]:
    """Return `(is_substantive, page_count)` for BCTC/BCTN payloads to reject short 4-page errata mirrors."""
    if canonical_doc_type not in ("BCTC", "BCTN"):
        return True, 0
    try:
        pdf_bytes = _unpack_pdf_if_zip(payload, doc_type=canonical_doc_type)
        if not pdf_bytes.startswith(b"%PDF"):
            return True, 0
        import fitz

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            n_pages = len(doc)
            if n_pages >= 15:
                return True, n_pages
            first_txt = (doc[0].get_text("text") or "")[:1200] if n_pages > 0 else ""
            # Under 15 pages for an annual BCTC/BCTN is almost always a cover/errata notice when backups exist
            if _ERRATA_NOTICE_TITLE_RE.search(first_txt) or n_pages <= 12:
                return False, n_pages
            return True, n_pages
        finally:
            doc.close()
    except Exception:  # noqa: BLE001
        return True, 0


async def fetch_document_with_fallback(
    urls: list[str],
    *,
    fetcher: BytesFetcherProtocol | None = None,
    canonical_doc_type: str = "",
) -> DocumentFetchResult:
    """Try candidate URLs sequentially, failing over on 404/network/zip errors AND short BCTC/BCTN errata notices."""
    if not urls:
        return DocumentFetchResult(ok=False, error="NO_CANDIDATE_URLS")

    active_fetcher = fetcher or HttpxBytesFetcher()
    tried_urls: list[str] = []
    attempt_errors: list[str] = []
    best_short_fallback: tuple[int, bytes, str] | None = None

    for url in urls:
        u = (url or "").strip()
        if not u:
            continue
        tried_urls.append(u)
        try:
            status_code, payload = await active_fetcher.fetch_bytes(u)
            if status_code >= 400:
                attempt_errors.append(f"{u}: HTTP {status_code}")
                continue
            if not payload:
                attempt_errors.append(f"{u}: EMPTY_BODY")
                continue
            is_sub, n_pages = _quick_pdf_substantive_check(
                payload, canonical_doc_type=canonical_doc_type
            )
            if not is_sub:
                attempt_errors.append(f"{u}: SHORT_OR_ERRATA_NOTICE({n_pages}p)")
                if best_short_fallback is None or n_pages > best_short_fallback[0]:
                    best_short_fallback = (n_pages, payload, u)
                continue
            return DocumentFetchResult(
                ok=True,
                content=payload,
                resolved_url=u,
                tried_urls=tried_urls,
                attempt_errors=attempt_errors,
            )
        except (httpx.HTTPError, OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
            attempt_errors.append(f"{u}: {type(exc).__name__}: {exc}")

    if best_short_fallback is not None:
        _, fb_payload, fb_url = best_short_fallback
        return DocumentFetchResult(
            ok=True,
            content=fb_payload,
            resolved_url=fb_url,
            tried_urls=tried_urls,
            attempt_errors=attempt_errors,
        )

    return DocumentFetchResult(
        ok=False,
        tried_urls=tried_urls,
        attempt_errors=attempt_errors,
        error="ALL_CANDIDATE_URLS_FAILED",
    )


def _unpack_pdf_if_zip(data: bytes, *, doc_type: str | None = None) -> bytes:
    if data.startswith(b"%PDF"):
        return data
    try:
        pdf_bytes, _, _ = safe_extract_pdf_from_archive(data, doc_type=doc_type)
        return pdf_bytes
    except ZipExtractionError:
        try:
            pdf_bytes, _, _ = safe_extract_pdf_from_zip(data, doc_type=doc_type)
            return pdf_bytes
        except ZipExtractionError:
            return data


def _is_conditional_activated(
    canonical_type: str,
    candidate: ResearchCandidateDocument,
    pit_res: PitDateResolution,
    anomaly_ctx: FinancialAnomalyContext,
    bctn_published_date: date | None,
    *,
    require_quarter_anomaly: bool = True,
) -> tuple[bool, str]:
    if canonical_type == "CAO_BACH":
        if anomaly_ctx.has_recent_capital_event or candidate.trigger_reason:
            return True, candidate.trigger_reason or "CAPITAL_EVENT_OR_ISSUANCE"
        return False, "CONDITIONAL_CAO_BACH_NOT_TRIGGERED"

    if canonical_type == "LATEST_QUARTER":
        if not require_quarter_anomaly:
            return True, candidate.trigger_reason or "RECENT_QUARTER_GENERAL_QA"
        if (
            anomaly_ctx.quarter_notes_triggered()
            or anomaly_ctx.has_regulatory_explanation_request
            or candidate.trigger_reason
        ):
            return True, candidate.trigger_reason or "MID_YEAR_ANOMALY_OR_EXPLANATION"
        return False, "CONDITIONAL_LATEST_QUARTER_NOT_TRIGGERED"

    if canonical_type == "GIAI_TRINH":
        if not require_quarter_anomaly:
            return True, candidate.trigger_reason or "RECENT_EARNINGS_EXPLANATION"
        if (
            anomaly_ctx.quarter_notes_triggered()
            or anomaly_ctx.has_regulatory_explanation_request
            or candidate.trigger_reason
        ):
            return True, candidate.trigger_reason or "MID_YEAR_ANOMALY_OR_EXPLANATION"
        return False, "CONDITIONAL_GIAI_TRINH_NOT_TRIGGERED"

    if canonical_type in ("NQ_DHDCD", "TAI_LIEU_DHDCD"):
        if not anomaly_ctx.bctn_has_plan:
            return True, "BCTN_MISSING_PLAN"
        if candidate.is_extraordinary_agm:
            return True, "EXTRAORDINARY_AGM"
        if bctn_published_date and pit_res.effective_published_at > bctn_published_date:
            return True, "POST_BCTN_AGM_RESOLUTION"
        return True, f"CORE_{canonical_type}_12M"

    if canonical_type == "TY_LE_AT_TC":
        return True, candidate.trigger_reason or "BROKERAGE_CAPITAL_ADEQUACY"

    return True, f"CORE_{canonical_type}"


async def select_research_documents(
    ticker: str,
    candidates: list[ResearchCandidateDocument],
    *,
    as_of_date: date,
    has_consolidated: bool | None = None,
    anomaly_context: FinancialAnomalyContext | None = None,
    fetcher: BytesFetcherProtocol | None = None,
    inspect_payloads: bool = True,
    min_fiscal_year: int | None = None,
    max_years_per_annual_type: int = 1,
    max_latest_quarters: int | None = None,
    require_quarter_anomaly: bool = True,
) -> DocumentSelectionResult:
    """Select, PIT-filter, deduplicate multi-source mirrors, inspect, and prioritize HOSE research filings.

    Golden Standard (`4-5` files/ticker):
    - 1 latest annual `BCTN` + 1 latest annual `BCTC` (deduplicated when Digital BCTN embeds full notes)
    - 1 latest `NQ_DHDCD` (`TAI_LIEU_DHDCD` used ONLY as fallback when `NQ_DHDCD` is absent for that year)
    - Up to 2 post-audited interim quarters (`Q2` Semi-Annual Reviewed + latest quarter `Q3`, dropping
      audited-superseded `Q4` and H1-superseded `Q1` automatically when `max_latest_quarters <= 2`).
    """
    eff_max_quarters = (
        max(1, min(int(max_latest_quarters), 8))
        if max_latest_quarters is not None
        else (1 if require_quarter_anomaly else 2)
    )
    ctx = anomaly_context or FinancialAnomalyContext()
    if has_consolidated is None:
        has_consolidated = any(
            normalize_doc_type(c.doc_type, c.title) in ("BCTC", "LATEST_QUARTER")
            and infer_statement_scope(c.statement_scope, c.title) == "CONSOLIDATED"
            for c in candidates
        )

    result = DocumentSelectionResult(ticker=ticker.upper(), as_of_date=as_of_date)
    preliminary: list[tuple[ResearchCandidateDocument, str, SelectionTier, str, PitDateResolution]] = []

    for cand in candidates:
        url0 = cand.urls[0] if cand.urls else ""
        is_en_meta, en_reason = is_standalone_english_metadata(cand.title, url=url0)
        if is_en_meta:
            result.rejected.append(
                RejectedDocument(
                    doc_id=cand.doc_id,
                    ticker=cand.ticker,
                    title=cand.title,
                    raw_doc_type=cand.doc_type,
                    reason=en_reason,
                )
            )
            continue
        canonical = normalize_doc_type(cand.doc_type, cand.title)
        # Reclassify quarterly / semi-annual financial statements from CORE `BCTC` -> CONDITIONAL `LATEST_QUARTER`
        if canonical == "BCTC" and parse_fiscal_quarter_from_meta(cand.title, url0) > 0:
            canonical = "LATEST_QUARTER"
        tier, reason = classify_document_category(
            canonical,
            title=cand.title,
            statement_scope=cand.statement_scope,
            has_consolidated=has_consolidated,
        )
        if tier == "EXCLUDED":
            result.rejected.append(
                RejectedDocument(
                    doc_id=cand.doc_id,
                    ticker=cand.ticker,
                    title=cand.title,
                    raw_doc_type=cand.doc_type,
                    reason=reason,
                )
            )
            continue

        try:
            pit_res = resolve_effective_published_at(
                published_date=cand.published_date,
                announcement_date=cand.announcement_date,
                signing_date=cand.signing_date,
                filing_date=cand.filing_date,
                title=cand.title,
                url=url0,
                period_end=cand.period_end,
            )
        except MissingPublishedDateError as exc:
            result.rejected.append(
                RejectedDocument(
                    doc_id=cand.doc_id,
                    ticker=cand.ticker,
                    title=cand.title,
                    raw_doc_type=cand.doc_type,
                    reason=f"MISSING_PIT_DATE:{exc}",
                )
            )
            continue

        if pit_res.is_trapped:
            result.traps_caught.extend(pit_res.trap_flags)
            result.rejected.append(
                RejectedDocument(
                    doc_id=cand.doc_id,
                    ticker=cand.ticker,
                    title=cand.title,
                    raw_doc_type=cand.doc_type,
                    reason="PUBLISHED_DATE_TRAP",
                    trap_flags=pit_res.trap_flags,
                )
            )
            continue

        if pit_res.effective_published_at > as_of_date:
            result.rejected.append(
                RejectedDocument(
                    doc_id=cand.doc_id,
                    ticker=cand.ticker,
                    title=cand.title,
                    raw_doc_type=cand.doc_type,
                    reason=(
                        f"FUTURE_PIT_EXCLUDED:{pit_res.effective_published_at.isoformat()}"
                        f">{as_of_date.isoformat()}"
                    ),
                )
            )
            continue

        if min_fiscal_year is not None and pit_res.parsed_fiscal_year is not None:
            if pit_res.parsed_fiscal_year < min_fiscal_year:
                result.rejected.append(
                    RejectedDocument(
                        doc_id=cand.doc_id,
                        ticker=cand.ticker,
                        title=cand.title,
                        raw_doc_type=cand.doc_type,
                        reason=f"STALE_FISCAL_YEAR:{pit_res.parsed_fiscal_year}<{min_fiscal_year}",
                    )
                )
                continue

        preliminary.append((cand, canonical, tier, reason, pit_res))

    # Sort newest effective_published_at first, deprioritizing errata/notes-only notices below full statements
    preliminary.sort(
        key=lambda item: (
            0
            if (
                item[1] in ("BCTC", "BCTN")
                and (
                    _ERRATA_NOTICE_TITLE_RE.search(item[0].title or "")
                    or re.search(r"^(?:thuyết\s+minh|tmbctc)\b", (item[0].title or "").strip(), re.IGNORECASE)
                )
            )
            else 1,
            item[4].effective_published_at,
            1 if "điều chỉnh" in (item[0].title or "").lower() else 0,
            1 if "soát xét" in (item[0].title or "").lower() else 0,
        ),
        reverse=True,
    )

    # Deduplicate multi-source mirrors (e.g. Vietstock + CafeF for same ticker/type/year/quarter)
    # merging their URLs into one candidate so multi-source 404 fallback works automatically.
    dedup_prelim: list[tuple[ResearchCandidateDocument, str, SelectionTier, str, PitDateResolution]] = []
    period_owner_idx: dict[tuple[str, int | None, int], int] = {}
    for item in preliminary:
        cand, canonical, tier, reason, pit_res = item
        if pit_res.parsed_fiscal_year is not None and canonical in (
            "BCTN",
            "BCTC",
            "NQ_DHDCD",
            "LATEST_QUARTER",
        ):
            agm_sub = ""
            if canonical == "NQ_DHDCD":
                t_low = (cand.title or "").lower()
                is_extra = bool(cand.is_extraordinary_agm) or any(
                    k in t_low for k in ("bất thường", "bat thuong", "lấy ý kiến", "lay y kien", "bằng văn bản")
                )
                agm_sub = "EXTRA" if is_extra else "ANNUAL"
            pkey = (
                f"{canonical}:{agm_sub}" if agm_sub else canonical,
                pit_res.parsed_fiscal_year,
                pit_res.parsed_fiscal_quarter,
            )
            if pkey in period_owner_idx:
                owner_cand = dedup_prelim[period_owner_idx[pkey]][0]
                for extra_u in cand.urls:
                    if extra_u and extra_u not in owner_cand.urls:
                        owner_cand.urls.append(extra_u)
                result.rejected.append(
                    RejectedDocument(
                        doc_id=cand.doc_id,
                        ticker=cand.ticker,
                        title=cand.title,
                        raw_doc_type=cand.doc_type,
                        reason=f"MERGED_DUPLICATE_PERIOD:{owner_cand.doc_id}",
                    )
                )
                continue
            period_owner_idx[pkey] = len(dedup_prelim)
        dedup_prelim.append(item)

    # Golden Standard filtering before recency caps:
    # 1. `TAI_LIEU_DHDCD` (AGM draft proposals) is FALLBACK-ONLY when `NQ_DHDCD` is missing for that year.
    # 2. When `eff_max_quarters <= 2`: drop unaudited quarters from years already covered by annual BCTC/BCTN
    #    (e.g. drop Q4/2025 once FY2025 audited exists), and drop Q1 when Q2 Semi-Annual Reviewed exists.
    nq_agm_years: set[int] = {
        pit_res.parsed_fiscal_year
        for _c, canonical, _t, _r, pit_res in dedup_prelim
        if canonical == "NQ_DHDCD" and pit_res.parsed_fiscal_year is not None
    }
    latest_annual_fy_vals = [
        pit_res.parsed_fiscal_year
        for _c, canonical, _t, _r, pit_res in dedup_prelim
        if canonical in ("BCTC", "BCTN") and pit_res.parsed_fiscal_year is not None
    ]
    latest_annual_fy = max(latest_annual_fy_vals) if latest_annual_fy_vals else None
    post_annual_quarters_by_fy: dict[int, set[int]] = {}
    for _c, canonical, _t, _r, pit_res in dedup_prelim:
        if canonical == "LATEST_QUARTER" and pit_res.parsed_fiscal_year is not None:
            post_annual_quarters_by_fy.setdefault(pit_res.parsed_fiscal_year, set()).add(
                pit_res.parsed_fiscal_quarter
            )

    filtered_prelim: list[
        tuple[ResearchCandidateDocument, str, SelectionTier, str, PitDateResolution]
    ] = []
    for item in dedup_prelim:
        cand, canonical, tier, reason, pit_res = item
        if canonical == "TAI_LIEU_DHDCD":
            if nq_agm_years and (
                pit_res.parsed_fiscal_year is None
                or pit_res.parsed_fiscal_year in nq_agm_years
                or pit_res.parsed_fiscal_year < max(nq_agm_years)
            ):
                result.rejected.append(
                    RejectedDocument(
                        doc_id=cand.doc_id,
                        ticker=cand.ticker,
                        title=cand.title,
                        raw_doc_type=cand.doc_type,
                        reason="SUPERSEDED_BY_NQ_DHDCD",
                    )
                )
                continue
        if canonical == "LATEST_QUARTER" and eff_max_quarters <= 2 and pit_res.parsed_fiscal_year is not None:
            if latest_annual_fy is not None and pit_res.parsed_fiscal_year <= latest_annual_fy:
                result.rejected.append(
                    RejectedDocument(
                        doc_id=cand.doc_id,
                        ticker=cand.ticker,
                        title=cand.title,
                        raw_doc_type=cand.doc_type,
                        reason=f"SUPERSEDED_BY_AUDITED_ANNUAL_FY{latest_annual_fy}",
                    )
                )
                continue
            if (
                pit_res.parsed_fiscal_quarter == 1
                and 2 in post_annual_quarters_by_fy.get(pit_res.parsed_fiscal_year, set())
            ):
                result.rejected.append(
                    RejectedDocument(
                        doc_id=cand.doc_id,
                        ticker=cand.ticker,
                        title=cand.title,
                        raw_doc_type=cand.doc_type,
                        reason=f"SUPERSEDED_BY_SEMI_ANNUAL_REVIEWED_Q2_{pit_res.parsed_fiscal_year}",
                    )
                )
                continue
        if canonical == "GIAI_TRINH" and (as_of_date - pit_res.effective_published_at).days > 450:
            result.rejected.append(
                RejectedDocument(
                    doc_id=cand.doc_id,
                    ticker=cand.ticker,
                    title=cand.title,
                    raw_doc_type=cand.doc_type,
                    reason="STALE_EXPLANATION_OVER_15M",
                )
            )
            continue
        filtered_prelim.append(item)

    # Apply per-category recency caps so old periods never crowd out core annual BCTN / BCTC / AGM
    type_counts: dict[str, int] = {}
    agm_annual_kept = 0
    agm_extra_kept = 0
    capped_prelim: list[tuple[ResearchCandidateDocument, str, SelectionTier, str, PitDateResolution]] = []
    for item in filtered_prelim:
        cand, canonical, tier, reason, pit_res = item
        if canonical == "NQ_DHDCD":
            t_low = (cand.title or "").lower()
            is_extra_agm = bool(cand.is_extraordinary_agm) or any(
                k in t_low for k in ("bất thường", "bat thuong", "lấy ý kiến", "lay y kien", "bằng văn bản")
            )
            if is_extra_agm:
                if agm_extra_kept >= 1 or (as_of_date - pit_res.effective_published_at).days > 450:
                    result.rejected.append(
                        RejectedDocument(
                            doc_id=cand.doc_id,
                            ticker=cand.ticker,
                            title=cand.title,
                            raw_doc_type=cand.doc_type,
                            reason="SUPERSEDED_BY_NEWER_EXTRAORDINARY_AGM",
                        )
                    )
                    continue
                agm_extra_kept += 1
            else:
                if agm_annual_kept >= max_years_per_annual_type:
                    result.rejected.append(
                        RejectedDocument(
                            doc_id=cand.doc_id,
                            ticker=cand.ticker,
                            title=cand.title,
                            raw_doc_type=cand.doc_type,
                            reason="SUPERSEDED_BY_NEWER_NQ_DHDCD",
                        )
                    )
                    continue
                agm_annual_kept += 1
            capped_prelim.append(item)
            continue
        if canonical == "LATEST_QUARTER":
            cap = eff_max_quarters
        elif canonical in ("BCTN", "BCTC"):
            cap = max_years_per_annual_type
        else:
            # TAI_LIEU_DHDCD (fallback only), GIAI_TRINH, TY_LE_AT_TC, CAO_BACH -> 1 latest each
            cap = 1
        cur_n = type_counts.get(canonical, 0)
        if cur_n >= cap:
            result.rejected.append(
                RejectedDocument(
                    doc_id=cand.doc_id,
                    ticker=cand.ticker,
                    title=cand.title,
                    raw_doc_type=cand.doc_type,
                    reason=f"SUPERSEDED_BY_NEWER_{canonical}",
                )
            )
            continue
        type_counts[canonical] = cur_n + 1
        capped_prelim.append(item)

    preliminary = capped_prelim

    # First pass: locate & inspect BCTN to check embedded notes, plan presence, and NOTICE_ONLY
    bctn_embedded_notes_years: dict[int | None, str] = {}
    bctn_pub_date: date | None = None
    prelim_inspected: list[
        tuple[
            ResearchCandidateDocument,
            str,
            SelectionTier,
            str,
            PitDateResolution,
            bytes | None,
            str,
            InspectionResult | None,
        ]
    ] = []

    for cand, canonical, tier, reason, pit_res in preliminary:
        raw_payload = cand.raw_bytes
        resolved_url = cand.urls[0] if cand.urls else ""
        if raw_payload is None and cand.urls and inspect_payloads:
            fetched = await fetch_document_with_fallback(
                cand.urls,
                fetcher=fetcher,
                canonical_doc_type=canonical,
            )
            if not fetched.ok:
                result.rejected.append(
                    RejectedDocument(
                        doc_id=cand.doc_id,
                        ticker=cand.ticker,
                        title=cand.title,
                        raw_doc_type=cand.doc_type,
                        reason=f"{fetched.error}:{';'.join(fetched.attempt_errors)}",
                    )
                )
                continue
            raw_payload = fetched.content
            resolved_url = fetched.resolved_url

        insp: InspectionResult | None = None
        pdf_bytes: bytes | None = None
        if raw_payload is not None and inspect_payloads:
            insp = inspect_pdf_bytes(raw_payload, source_url=resolved_url, doc_type=canonical)
            if insp.doc_class == "EXCLUDED_ENGLISH" or insp.error == "EXCLUDED_FULL_ENGLISH_DOCUMENT":
                result.rejected.append(
                    RejectedDocument(
                        doc_id=cand.doc_id,
                        ticker=cand.ticker,
                        title=cand.title,
                        raw_doc_type=cand.doc_type,
                        reason="EXCLUDED_FULL_ENGLISH_DOCUMENT",
                    )
                )
                continue
            if insp.error:
                result.rejected.append(
                    RejectedDocument(
                        doc_id=cand.doc_id,
                        ticker=cand.ticker,
                        title=cand.title,
                        raw_doc_type=cand.doc_type,
                        reason=f"INSPECTION_ERROR:{insp.error}",
                    )
                )
                continue
            pdf_bytes = _unpack_pdf_if_zip(raw_payload, doc_type=canonical)
            if canonical == "BCTN":
                if insp.doc_class == "NOTICE_ONLY":
                    ctx.bctn_has_plan = False
                elif not insp.has_plan:
                    ctx.bctn_has_plan = False
                if (
                    insp.has_embedded_notes
                    and insp.doc_class in ("DIGITAL", "HYBRID")
                    and insp.routed_branch == "A"
                ):
                    bctn_embedded_notes_years[pit_res.parsed_fiscal_year] = cand.doc_id
                if bctn_pub_date is None:
                    bctn_pub_date = pit_res.effective_published_at
        elif canonical == "BCTN" and bctn_pub_date is None:
            bctn_pub_date = pit_res.effective_published_at

        prelim_inspected.append(
            (cand, canonical, tier, reason, pit_res, pdf_bytes, resolved_url, insp)
        )

    # Second pass: apply conditional activation & BCTN/BCTC deduplication
    for cand, canonical, tier, reason, pit_res, pdf_bytes, resolved_url, insp in prelim_inspected:
        if canonical == "BCTC" and pit_res.parsed_fiscal_quarter == 0:
            dedup_owner = bctn_embedded_notes_years.get(pit_res.parsed_fiscal_year)
            if dedup_owner is not None:
                result.bctn_deduplicated_bctc = True
                result.rejected.append(
                    RejectedDocument(
                        doc_id=cand.doc_id,
                        ticker=cand.ticker,
                        title=cand.title,
                        raw_doc_type=cand.doc_type,
                        reason=f"DEDUPLICATED_BY_BCTN:{dedup_owner}",
                    )
                )
                continue

        if tier == "CONDITIONAL" or canonical in ("NQ_DHDCD", "TAI_LIEU_DHDCD"):
            active, cond_reason = _is_conditional_activated(
                canonical,
                cand,
                pit_res,
                ctx,
                bctn_pub_date,
                require_quarter_anomaly=require_quarter_anomaly,
            )
            if not active:
                result.rejected.append(
                    RejectedDocument(
                        doc_id=cand.doc_id,
                        ticker=cand.ticker,
                        title=cand.title,
                        raw_doc_type=cand.doc_type,
                        reason=cond_reason,
                    )
                )
                continue
            reason = cond_reason

        result.selected.append(
            SelectedDocument(
                candidate=cand,
                canonical_doc_type=canonical,
                tier=tier,
                selection_reason=reason,
                pit_resolution=pit_res,
                inspection=insp,
                pdf_bytes=pdf_bytes,
                resolved_url=resolved_url,
            )
        )

    _priority_order = {
        "BCTN": 0,
        "BCTC": 1,
        "NQ_DHDCD": 2,
        "TAI_LIEU_DHDCD": 2,
        "LATEST_QUARTER": 3,
        "GIAI_TRINH": 4,
        "TY_LE_AT_TC": 4,
        "CAO_BACH": 5,
    }
    result.selected.sort(
        key=lambda s: (
            _priority_order.get(s.canonical_doc_type, 9),
            -(s.pit_resolution.parsed_fiscal_year or 0),
            -s.pit_resolution.parsed_fiscal_quarter,
            -s.pit_resolution.effective_published_at.toordinal(),
        )
    )
    return result
