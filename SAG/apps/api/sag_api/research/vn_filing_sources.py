"""Centralized Vietnamese Corporate Filing Connectors (`Vietstock` + `Vietcap IQ` + `CafeF`, 0 LLM).

Implements Tier-1 of `sag_vn_source_and_bilingual_cleaning_spec.md`:
1. `VietstockDocumentClient`: fetches corporate PDF/archive metadata across `doctype`
   `1` (BCTC), `2` (BCTN), `3` (Bản cáo bạch), `4` (Nghị quyết ĐHĐCĐ),
   `5` (Tài liệu ĐHĐCĐ / Tờ trình), `8` (Giải trình KQKD >10% / sau kiểm toán),
   and conditional `10` (Tỷ lệ an toàn tài chính CTCK), strictly excluding `9` (Quản trị)
   and rejecting Layer-1 standalone English filings at 0 download cost.
2. `VietcapIQPublicDateClient`: queries structured `publicDate` from Vietcap IQ's official
   REST JSON (`section=BALANCE_SHEET`) and joins deterministically on `(ticker, year, quarter)`
   to neutralize Vietstock's `LastUpdate` CMS edit-time PIT trap on BCTC filings.
3. `extract_vn_filing_signing_date`: deterministic legal release/signing date extractor for
   Vietnamese cover/signature blocks (`ngày DD tháng MM năm YYYY`, `Ngày CBTT: DD/MM/YYYY`),
   bounded by `[fiscal_year, fiscal_year + 2]` so historical founding dates never leak.
4. `CafeFFallbackClient`: attaches secondary CafeF CDN PDF URLs onto matching periods for
   automatic 404 failover.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

import httpx

from sag_api.research.input_selector import (
    ResearchCandidateDocument,
    infer_statement_scope,
    parse_fiscal_quarter_from_meta,
    parse_fiscal_year_from_meta,
)
from sag_api.research.vn_language_gate import is_standalone_english_metadata

# Vietstock `doctype` mapping (excluding `9` = Governance Report)
VIETSTOCK_DOCTYPE_MAP: dict[int, str] = {
    1: "BCTC",
    2: "BCTN",
    3: "CAO_BACH",
    4: "NQ_DHDCD",
    5: "TAI_LIEU_DHDCD",
    8: "GIAI_TRINH",
    10: "TY_LE_AT_TC",
}

DEFAULT_CORPORATE_DOCTYPES: tuple[int, ...] = (1, 2, 3, 4, 5, 8)
BROKERAGE_EXTRA_DOCTYPES: tuple[int, ...] = (10,)

_VN_LEGAL_SIGNING_DATE_RE = re.compile(
    r"(?:ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(20\d{2})|"
    r"(?:ngày\s+công\s+bố(?:\s+thông\s+tin)?|ngày\s+cbtt|ngày\s+ký|ngày\s+ban\s+hành)\s*[:\-]?\s*"
    r"(\d{1,2})[./\-](\d{1,2})[./\-](20\d{2}))",
    re.IGNORECASE,
)
_DOTNET_JSON_DATE_RE = re.compile(r"/Date\((\-?\d+)\)/")
_CAFEF_PDF_HREF_RE = re.compile(
    r"https?://[^\s\"'<>]+?\.(?:pdf|zip|rar)(?:\?[^\s\"'<>]*)?",
    re.IGNORECASE,
)


def parse_vietstock_timestamp(raw_val: Any) -> date | None:
    """Parse Vietstock date formats (`/Date(1711904400000)/`, `DD/MM/YYYY`, or ISO `YYYY-MM-DD`)."""
    if raw_val is None:
        return None
    if isinstance(raw_val, date):
        return raw_val
    s = str(raw_val).strip()
    if not s:
        return None
    m_net = _DOTNET_JSON_DATE_RE.search(s)
    if m_net:
        try:
            from datetime import UTC, datetime

            ts_sec = int(m_net.group(1)) / 1000.0
            return datetime.fromtimestamp(ts_sec, tz=UTC).date()
        except (ValueError, OSError, OverflowError):
            return None
    m_vn = re.match(r"^(\d{1,2})[./\-](\d{1,2})[./\-](20\d{2})", s)
    if m_vn:
        try:
            return date(int(m_vn.group(3)), int(m_vn.group(2)), int(m_vn.group(1)))
        except ValueError:
            return None
    m_iso = re.match(r"^(20\d{2})-(\d{2})-(\d{2})", s)
    if m_iso:
        try:
            return date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
        except ValueError:
            return None
    return None


def extract_vn_filing_signing_date(
    text: str,
    *,
    fiscal_year: int | None = None,
    as_of_date: date | None = None,
) -> date | None:
    """Extract Vietnamese legal signing/disclosure date from cover/header/signature zones.

    Filters out historical company founding/charter dates via `[fiscal_year, fiscal_year + 2]`.
    """
    if not text:
        return None
    nfc = unicodedata.normalize("NFC", text)
    # Inspect top 1,800 chars (cover/header) and bottom 1,800 chars (signature block)
    zones = nfc[:1800] + "\n" + (nfc[-1800:] if len(nfc) > 1800 else "")
    found: list[date] = []
    for m in _VN_LEGAL_SIGNING_DATE_RE.finditer(zones):
        if m.group(1):
            d_i, m_i, y_i = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            d_i, m_i, y_i = int(m.group(4)), int(m.group(5)), int(m.group(6))
        try:
            dt = date(y_i, m_i, d_i)
        except ValueError:
            continue
        if not (2005 <= dt.year <= 2035):
            continue
        if fiscal_year is not None and not (fiscal_year <= dt.year <= fiscal_year + 2):
            continue
        if as_of_date is not None and dt > as_of_date:
            continue
        found.append(dt)
    if not found:
        return None
    # Prefer the latest valid signing/disclosure date in header/signature block
    return max(found)


def parse_vietcap_public_dates_payload(payload: Any) -> dict[tuple[int, int], date]:
    """Extract `(fiscal_year, fiscal_quarter) -> publicDate` from Vietcap IQ financial-statement JSON.

    Vietcap IQ encodes `lengthReport`:
      - `5` (or `0` / `12`) -> full-year audited statement (`quarter = 0`)
      - `1`, `2`, `3`, `4`  -> quarterly / semi-annual statement (`quarter = 1..4`)
    """
    out: dict[tuple[int, int], date] = {}
    if not isinstance(payload, dict):
        return out

    data = payload.get("data")
    buckets: list[Any] = []
    if isinstance(data, list):
        buckets.extend(data)
    elif isinstance(data, dict):
        for key in ("years", "quarters", "items", "records", "financialStatements"):
            sub = data.get(key)
            if isinstance(sub, list):
                buckets.extend(sub)

    for row in buckets:
        if not isinstance(row, dict):
            continue
        raw_yr = row.get("yearReport") or row.get("fiscalYear") or row.get("year")
        raw_len = row.get("lengthReport") if "lengthReport" in row else row.get("quarter")
        raw_pub = row.get("publicDate") or row.get("announcementDate") or row.get("publishedDate")
        if raw_yr is None or raw_pub is None:
            continue
        try:
            yr = int(raw_yr)
        except (TypeError, ValueError):
            continue
        if not (2005 <= yr <= 2035):
            continue
        try:
            lr = int(raw_len) if raw_len is not None else 0
        except (TypeError, ValueError):
            lr = 0
        fq = 0 if lr in (0, 5, 12) else (lr if 1 <= lr <= 4 else 0)
        pub_dt = parse_vietstock_timestamp(raw_pub)
        if pub_dt is None:
            continue
        # Keep earliest official public disclosure date for the period unless an earlier one is missing
        prev = out.get((yr, fq))
        if prev is None or pub_dt < prev:
            out[(yr, fq)] = pub_dt

    return out


class VietcapIQPublicDateClient:
    """Fetches official BCTC `publicDate` per `(yearReport, lengthReport)` from Vietcap IQ REST API."""

    BASE_URL = "https://iq.vietcap.com.vn/api/iq-insight-service/v1/company"

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._client = http_client
        self._timeout = timeout_seconds

    async def fetch_public_dates(self, ticker: str) -> dict[tuple[int, int], date]:
        sym = ticker.strip().upper()
        url = f"{self.BASE_URL}/{sym}/financial-statement?section=BALANCE_SHEET"
        headers = {
            "Accept": "application/json",
            "Origin": "https://trading.vietcap.com.vn",
            "Referer": "https://trading.vietcap.com.vn/",
            "User-Agent": "Mozilla/5.0",
        }
        if self._client is not None:
            resp = await self._client.get(url, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return {}
        try:
            return parse_vietcap_public_dates_payload(resp.json())
        except ValueError:
            return {}


class VietstockDocumentClient:
    """Fetches structured Vietnamese corporate filings from `finance.vietstock.vn/data/getdocument`."""

    ENDPOINT = "https://finance.vietstock.vn/data/getdocument"

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._client = http_client
        self._timeout = timeout_seconds

    async def fetch_doctype_page(
        self,
        ticker: str,
        *,
        doctype: int,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        if doctype not in VIETSTOCK_DOCTYPE_MAP:
            return []
        sym = ticker.strip().upper()
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": f"https://finance.vietstock.vn/{sym}/tai-tai-lieu.htm",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "Mozilla/5.0",
        }
        params = {"code": sym, "page": page, "doctype": doctype}
        if self._client is not None:
            resp = await self._client.get(self.ENDPOINT, params=params, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
                resp = await client.get(self.ENDPOINT, params=params, headers=headers)
        if resp.status_code != 200:
            return []
        try:
            data = resp.json()
        except ValueError:
            return []
        return data if isinstance(data, list) else []

    async def collect_candidates(
        self,
        ticker: str,
        *,
        as_of_date: date,
        years_back: int = 3,
        is_brokerage: bool = False,
        max_pages_per_doctype: int = 2,
        vietcap_public_dates: dict[tuple[int, int], date] | None = None,
    ) -> tuple[list[ResearchCandidateDocument], list[str]]:
        """Collect Vietnamese-only candidate filings for `ticker` and enrich BCTC `announcement_date`."""
        sym = ticker.strip().upper()
        min_year = as_of_date.year - max(1, years_back)
        doctypes = list(DEFAULT_CORPORATE_DOCTYPES) + (
            list(BROKERAGE_EXTRA_DOCTYPES) if is_brokerage else []
        )
        pub_map = vietcap_public_dates or {}
        candidates: list[ResearchCandidateDocument] = []
        skipped_english: list[str] = []
        seen_urls: set[str] = set()

        for dt_code in doctypes:
            canonical_type = VIETSTOCK_DOCTYPE_MAP[dt_code]
            for pg in range(1, max_pages_per_doctype + 1):
                rows = await self.fetch_doctype_page(sym, doctype=dt_code, page=pg)
                if not rows:
                    break
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    url = str(row.get("Url") or row.get("FileUrl") or row.get("url") or "").strip()
                    title = str(row.get("Title") or row.get("Note") or row.get("FileName") or "").strip()
                    if not url or url in seen_urls:
                        continue
                    is_en, en_reason = is_standalone_english_metadata(title, url=url)
                    if is_en:
                        skipped_english.append(f"{row.get('DocumentID', '')}:{en_reason}")
                        continue

                    fy = parse_fiscal_year_from_meta(title, url)
                    fq = parse_fiscal_quarter_from_meta(title, url)
                    if fy is not None and fy < min_year:
                        continue

                    last_upd = parse_vietstock_timestamp(
                        row.get("UpdateTime") or row.get("LastUpdate") or row.get("PublishDate")
                    )
                    ann_dt: date | None = None
                    if canonical_type == "BCTC" and fy is not None:
                        ann_dt = pub_map.get((fy, fq))

                    seen_urls.add(url)
                    doc_id = f"vietstock:{sym}:{dt_code}:{row.get('DocumentID') or len(candidates) + 1}"
                    candidates.append(
                        ResearchCandidateDocument(
                            doc_id=doc_id,
                            ticker=sym,
                            doc_type=canonical_type,
                            title=title or f"{sym} {canonical_type} {fy or ''}".strip(),
                            urls=[url],
                            source="vietstock",
                            statement_scope=infer_statement_scope(None, title=title),
                            announcement_date=ann_dt,
                            published_date=ann_dt or last_upd,
                        )
                    )

        return candidates, skipped_english


class CafeFFallbackClient:
    """Extracts secondary CafeF PDF/archive links to attach as 404 failover URLs."""

    ENDPOINT = "https://cafef.vn/du-lieu/Ajax/CongTy/BaoCaoTaiChinh.aspx"

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._client = http_client
        self._timeout = timeout_seconds

    async def fetch_backup_urls(self, ticker: str, *, report_type: int = 1) -> list[str]:
        sym = ticker.strip().upper()
        params = {"sym": sym, "type": report_type}
        headers = {"User-Agent": "Mozilla/5.0"}
        if self._client is not None:
            resp = await self._client.get(self.ENDPOINT, params=params, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
                resp = await client.get(self.ENDPOINT, params=params, headers=headers)
        if resp.status_code != 200:
            return []
        found: list[str] = []
        for m in _CAFEF_PDF_HREF_RE.finditer(resp.text or ""):
            u = m.group(0)
            is_en, _ = is_standalone_english_metadata("", url=u)
            if not is_en and u not in found:
                found.append(u)
        return found


def attach_cafef_fallback_urls(
    candidates: list[ResearchCandidateDocument],
    cafef_urls: list[str],
) -> int:
    """Attach matching `(fiscal_year, fiscal_quarter)` CafeF URLs as failover mirrors on BCTC candidates."""
    if not candidates or not cafef_urls:
        return 0
    attached = 0
    by_period: dict[tuple[int, int], list[str]] = {}
    for u in cafef_urls:
        fy = parse_fiscal_year_from_meta("", u)
        fq = parse_fiscal_quarter_from_meta("", u)
        if fy is not None:
            by_period.setdefault((fy, fq), []).append(u)

    for cand in candidates:
        u0 = cand.urls[0] if cand.urls else ""
        fy = parse_fiscal_year_from_meta(cand.title, u0)
        fq = parse_fiscal_quarter_from_meta(cand.title, u0)
        if fy is None or cand.doc_type not in ("BCTC", "LATEST_QUARTER", "financial_statement"):
            continue
        for extra in by_period.get((fy, fq), ()):
            if extra not in cand.urls:
                cand.urls.append(extra)
                attached += 1
    return attached
