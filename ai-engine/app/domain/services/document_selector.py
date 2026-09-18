"""Active Document Selector Service (IOS v5.1).

Chịu trách nhiệm tuyển chọn chính xác bộ 3 tài liệu vàng cho mỗi cổ phiếu (Ticker):
1. BCTC Riêng Cả năm Kiểm toán gần nhất (Annual Backbone)
2. BCTC Riêng Quý gần nhất (Latest Quarter Delta)
3. Báo cáo Quản trị (BCQT) gần nhất (Governance Report)

Tự động thực hiện cơ chế Cửa sổ trượt (Sliding Window):
- Khi có Quý mới: Thay thế Quý cũ, lưu kho các quý trước.
- Khi có BCTC Kiểm toán năm mới: Thay thế toàn bộ xương sống cũ.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger("ai_engine.services.document_selector")


def _ascii_title(value: str) -> str:
    """Normalize Vietnamese/English titles for selector predicates."""
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value or "").lower()
        if not unicodedata.combining(ch)
    )


def _is_valid_annual_candidate(row: Dict[str, Any]) -> bool:
    title = _ascii_title(str(row.get("title") or ""))
    url = _ascii_title(" ".join(str(u) for u in (row.get("article_pdf_urls") or [])))
    searchable = f"{title} {url}"
    if any(token in searchable for token in ("quy", "6 thang", "6t", "6%20t", "6-thang", "6thang", "ban nien", "nua dau nam", "thuyet minh", "giai trinh", "tom tat")):
        return False
    return any(token in title for token in ("kiem toan", "audited", "ca nam"))


def _has_non_annual_marker(row: Dict[str, Any]) -> bool:
    text = _ascii_title(
        f"{row.get('title') or ''} {' '.join(str(u) for u in (row.get('article_pdf_urls') or []))}"
    )
    return any(token in text for token in ("thuyet minh", "giai trinh", "tom tat"))


def _row_period(row: Dict[str, Any], role: str) -> Tuple[int, Union[int, str]]:
    title = str(row.get("title") or "")
    url = _ascii_title(" ".join(str(u) for u in (row.get("article_pdf_urls") or [])))
    # CafeF occasionally labels a 2025 annual PDF as CN/2026; the filename is
    # the reliable fiscal-period evidence in that case.
    if role == "ANNUAL_BACKBONE" and row.get("source") == "cafef_docs":
        if "2025" in url and re.search(r"nam\s*2026", _ascii_title(title), re.I):
            title = re.sub(r"2026", "2025", title, count=1)
    return parse_fiscal_period(title, str(row.get("published_date") or ""), role)


def _row_urls(row: Dict[str, Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        str(url).strip()
        for url in (row.get("article_pdf_urls") or [])
        if str(url).strip()
    ))


def _cafef_alternate_urls(
    cur: Any,
    ticker: str,
    role: str,
    fiscal_year: int,
    fiscal_quarter: Union[int, str],
) -> tuple[str, ...]:
    """Return same-period CafeF URLs as source fallbacks, without trusting filenames for language."""
    cur.execute("""
        SELECT title, published_date, article_pdf_urls
        FROM knowledge_documents
        WHERE symbol = %s
          AND source = 'cafef_docs'
          AND (
              doc_type = 'financial_statement'
              OR (doc_type = 'governance_report' AND %s = 'GOVERNANCE_REPORT')
          )
    """, (ticker, role))
    matches: list[tuple[Any, tuple[str, ...]]] = []
    for row in cur.fetchall():
        title = _ascii_title(str(row.get("title") or ""))
        if role != "GOVERNANCE_REPORT" and any(token in title for token in ("hop nhat", "consolidated")):
            continue
        year, quarter = _row_period(row, role)
        if year != fiscal_year or quarter != fiscal_quarter:
            continue
        urls = _row_urls(row)
        if urls:
            matches.append((row.get("published_date"), urls))
    urls: list[str] = []
    for _, row_urls in sorted(matches, key=lambda item: (item[0] is not None, item[0]), reverse=True):
        urls.extend(row_urls)
    return tuple(dict.fromkeys(urls))


def parse_fiscal_period(
    title: str, pub_date: Optional[str] = None, role: Optional[str] = None
) -> Tuple[int, Union[int, str]]:
    """Trích xuất chính xác fiscal_year và fiscal_quarter từ tiêu đề tài liệu.
    
    Quy tắc:
    - BCTC Kiểm toán năm / Cả năm: Trích xuất năm chính xác, quarter = 'YEAR' (không phải Q4).
    - BCTC Quý: Trích xuất năm và số quý tương ứng (1, 2, 3, 4).
    - BCQT: Trích xuất năm và 6 tháng (Q2) hoặc cả năm ('YEAR').
    """
    t_lower = (title or "").lower()

    # 1. Fiscal Year
    year = None
    m_year = re.search(r"(?:năm|nam)\s*(20\d{2})", t_lower)
    if m_year:
        year = int(m_year.group(1))
    else:
        m_4digit = re.search(r"\b(20[1-3]\d)\b", t_lower)
        if m_4digit:
            year = int(m_4digit.group(1))
        elif pub_date:
            m_date = re.search(r"(20\d{2})", str(pub_date))
            if m_date:
                year = int(m_date.group(1))
    if not year:
        year = 0

    # 2. Fiscal Quarter
    m_q = re.search(r"(?:quý|quy|q)\s*([1-4])", t_lower)
    if m_q:
        quarter = int(m_q.group(1))
    elif any(k in t_lower for k in ("6 tháng", "6 thang", "bán niên", "ban nien", "nửa đầu năm", "nua dau nam")):
        quarter = "6M" if role == "GOVERNANCE_REPORT" or "quản trị" in t_lower or "quan tri" in t_lower else 2
    elif any(k in t_lower for k in ("9 tháng", "9 thang")):
        quarter = 3
    elif role == "ANNUAL_BACKBONE" or any(k in t_lower for k in ("kiểm toán", "kiem toan", "cả năm", "ca nam")):
        quarter = "YEAR"
    else:
        quarter = "YEAR" if ("năm" in t_lower or "nam" in t_lower) else ("6M" if role == "GOVERNANCE_REPORT" else 2)

    return year, quarter


@dataclass(frozen=True)
class ActiveDocument:
    doc_id: int
    ticker: str
    doc_type: str
    title: str
    published_date: str
    pdf_url: str
    role: str  # "ANNUAL_BACKBONE" | "LATEST_QUARTER" | "GOVERNANCE_REPORT"
    pdf_urls: tuple[str, ...] = ()
    fiscal_year: Optional[int] = None
    fiscal_quarter: Optional[Union[int, str]] = None
    scope: str = "SEPARATE"


@dataclass(frozen=True)
class TickerDocumentSet:
    ticker: str
    annual_audited: Optional[ActiveDocument] = None
    latest_quarter: Optional[ActiveDocument] = None
    governance_report: Optional[ActiveDocument] = None

    @property
    def all_documents(self) -> List[ActiveDocument]:
        return [d for d in (self.annual_audited, self.latest_quarter, self.governance_report) if d is not None]

    @property
    def is_complete(self) -> bool:
        """Đầy đủ cả 3 trụ cột: Kiểm toán năm + Quý mới nhất + Quản trị."""
        return bool(self.annual_audited and self.governance_report)


class ActiveDocumentSelector:
    """Bộ tuyển chọn tài liệu Active theo cơ chế Cửa sổ trượt (Sliding Window)."""

    def __init__(self, db_url: str = "postgresql://postgres:123@localhost:5432/aiinvest") -> None:
        self.db_url = db_url

    def _get_connection(self):
        return psycopg2.connect(self.db_url)

    def select_active_documents(self, ticker: str) -> TickerDocumentSet:
        """Tuyển chọn đúng 3 tài liệu vàng cho 1 mã cổ phiếu từ PostgreSQL."""
        ticker = ticker.upper().strip()
        conn = self._get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        annual_doc: Optional[ActiveDocument] = None
        quarter_doc: Optional[ActiveDocument] = None
        gov_doc: Optional[ActiveDocument] = None

        try:
            # 1. Tìm BCTC Kiểm toán năm gần nhất (Riêng / Công ty mẹ)
            cur.execute("""
                SELECT id, title, doc_type, published_date, article_pdf_urls, source
                FROM knowledge_documents
                WHERE symbol = %s
                  AND doc_type = 'financial_statement'
                  AND (
                      LOWER(title) LIKE '%%riêng%%' 
                      OR LOWER(title) LIKE '%%công ty mẹ%%' 
                      OR LOWER(title) LIKE '%%cong ty me%%'
                  )
                  AND (
                      LOWER(title) LIKE '%%kiểm toán%%'
                      OR LOWER(title) LIKE '%%kiem toan%%'
                      OR LOWER(title) LIKE '%%audited%%'
                      OR LOWER(title) LIKE '%%cả năm%%'
                  )
                  AND LOWER(title) NOT LIKE '%%6 tháng%%'
                  AND LOWER(title) NOT LIKE '%%6 thang%%'
                  AND LOWER(title) NOT LIKE '%%bán niên%%'
                  AND LOWER(title) NOT LIKE '%%ban nien%%'
                  AND LOWER(title) NOT LIKE '%%nửa đầu năm%%'
                  AND LOWER(title) NOT LIKE '%%nua dau nam%%'
                  AND LOWER(title) NOT LIKE '%%quý%%'
                  AND LOWER(title) NOT LIKE '%%quy%%'
                  AND LOWER(title) NOT LIKE '%%thuyết minh%%'
                  AND LOWER(title) NOT LIKE '%%thuyet minh%%'
                  AND LOWER(title) NOT LIKE '%%giải trình%%'
                  AND LOWER(title) NOT LIKE '%%giai trinh%%'
                  AND LOWER(title) NOT LIKE '%%tóm tắt%%'
                  AND LOWER(title) NOT LIKE '%%tom tat%%'
                ORDER BY (source = 'vnstock_docs') DESC, published_date DESC NULLS LAST, id DESC
                LIMIT 1;
            """, (ticker,))
            row_ann = cur.fetchone()
            if not row_ann:
                # Fallback cho doanh nghiệp/ngân hàng không dùng tiêu đề riêng/công ty mẹ
                cur.execute("""
                    SELECT id, title, doc_type, published_date, article_pdf_urls, source
                    FROM knowledge_documents
                    WHERE symbol = %s
                      AND doc_type = 'financial_statement'
                      AND (
                          LOWER(title) LIKE '%%kiểm toán%%'
                          OR LOWER(title) LIKE '%%kiem toan%%'
                          OR LOWER(title) LIKE '%%audited%%'
                          OR LOWER(title) LIKE '%%cả năm%%'
                      )
                      AND LOWER(title) NOT LIKE '%%6 tháng%%'
                      AND LOWER(title) NOT LIKE '%%6 thang%%'
                      AND LOWER(title) NOT LIKE '%%bán niên%%'
                      AND LOWER(title) NOT LIKE '%%ban nien%%'
                      AND LOWER(title) NOT LIKE '%%nửa đầu năm%%'
                      AND LOWER(title) NOT LIKE '%%nua dau nam%%'
                      AND LOWER(title) NOT LIKE '%%quý%%'
                      AND LOWER(title) NOT LIKE '%%quy%%'
                      AND LOWER(title) NOT LIKE '%%thuyết minh%%'
                      AND LOWER(title) NOT LIKE '%%thuyet minh%%'
                      AND LOWER(title) NOT LIKE '%%giải trình%%'
                      AND LOWER(title) NOT LIKE '%%giai trinh%%'
                      AND LOWER(title) NOT LIKE '%%tóm tắt%%'
                      AND LOWER(title) NOT LIKE '%%tom tat%%'
                    ORDER BY (source = 'vnstock_docs') DESC, published_date DESC NULLS LAST, id DESC
                    LIMIT 1;
                """, (ticker,))
                row_ann = cur.fetchone()

            # SQL title matching is intentionally only a coarse filter: OCR/crawler
            # titles may contain Vietnamese accents or mojibake. Re-select from the
            # ticker's annual candidates with Python normalization so a newer valid
            # annual report cannot be hidden behind an older cache record.
            cur.execute("""
                SELECT id, title, doc_type, published_date, article_pdf_urls, source
                FROM knowledge_documents
                WHERE symbol = %s AND doc_type = 'financial_statement'
            """, (ticker,))
            annual_candidates = []
            for candidate in cur.fetchall():
                if _is_valid_annual_candidate(candidate):
                    parsed_year, _ = _row_period(candidate, "ANNUAL_BACKBONE")
                    if parsed_year <= 0:
                        continue
                    annual_candidates.append((
                        parsed_year,
                        any(token in _ascii_title(candidate["title"]) for token in ("rieng", "cong ty me")),
                        candidate["source"] == "vnstock_docs",
                        candidate["published_date"],
                        candidate["id"],
                        candidate,
                    ))
            if annual_candidates:
                annual_candidates.sort(key=lambda item: item[:5])
                row_ann = annual_candidates[-1][5]

            if row_ann:
                y_ann, q_ann = _row_period(row_ann, "ANNUAL_BACKBONE")
                urls_ann = _row_urls(row_ann) + _cafef_alternate_urls(
                    cur, ticker, "ANNUAL_BACKBONE", y_ann, q_ann
                )
                urls_ann = tuple(dict.fromkeys(urls_ann))
                url_ann = urls_ann[0] if urls_ann else ""
                annual_doc = ActiveDocument(
                    doc_id=row_ann["id"],
                    ticker=ticker,
                    doc_type="financial_statement",
                    title=row_ann["title"],
                    published_date=str(row_ann["published_date"]),
                    pdf_url=url_ann,
                    pdf_urls=urls_ann,
                    role="ANNUAL_BACKBONE",
                    fiscal_year=y_ann,
                    fiscal_quarter=q_ann,
                    scope="SEPARATE",
                )

            # 2. Tìm BCTC Quý gần nhất (Riêng / Công ty mẹ) xuất bản SAU hoặc CÙNG NĂM với BCTC Kiểm toán
            # Publisher dates are advisory; some CafeF rows use a future date.
            annual_date = "2000-01-01"
            annual_id = row_ann["id"] if row_ann else -1
            cur.execute("""
                SELECT id, title, doc_type, published_date, article_pdf_urls, source
                FROM knowledge_documents
                WHERE symbol = %s
                  AND doc_type = 'financial_statement'
                  AND (
                      LOWER(title) LIKE '%%riêng%%' 
                      OR LOWER(title) LIKE '%%công ty mẹ%%' 
                      OR LOWER(title) LIKE '%%cong ty me%%'
                  )
                  AND published_date >= %s
                  AND id != %s
                  AND LOWER(title) NOT LIKE '%%thuyết minh%%'
                  AND LOWER(title) NOT LIKE '%%thuyet minh%%'
                  AND LOWER(title) NOT LIKE '%%giải trình%%'
                  AND LOWER(title) NOT LIKE '%%giai trinh%%'
                  AND LOWER(title) NOT LIKE '%%tóm tắt%%'
                  AND LOWER(title) NOT LIKE '%%tom tat%%'
                ORDER BY (source = 'vnstock_docs') DESC, published_date DESC NULLS LAST, id DESC
                LIMIT 1;
            """, (ticker, annual_date, annual_id))
            row_q = cur.fetchone()
            if not row_q:
                # Fallback nếu không có tiêu đề riêng/công ty mẹ (cho phép BCTC không ghi chữ riêng/mẹ, nhưng không lấy hợp nhất nếu đã có mẹ)
                cur.execute("""
                    SELECT id, title, doc_type, published_date, article_pdf_urls, source
                    FROM knowledge_documents
                    WHERE symbol = %s
                      AND doc_type = 'financial_statement'
                      AND published_date >= %s
                      AND id != %s
                      AND LOWER(title) NOT LIKE '%%thuyết minh%%'
                      AND LOWER(title) NOT LIKE '%%thuyet minh%%'
                      AND LOWER(title) NOT LIKE '%%giải trình%%'
                      AND LOWER(title) NOT LIKE '%%giai trinh%%'
                      AND LOWER(title) NOT LIKE '%%tóm tắt%%'
                      AND LOWER(title) NOT LIKE '%%tom tat%%'
                      AND LOWER(title) NOT LIKE '%%hợp nhất%%'
                      AND LOWER(title) NOT LIKE '%%hop nhat%%'
                    ORDER BY (source = 'vnstock_docs') DESC, published_date DESC NULLS LAST, id DESC
                    LIMIT 1;
                """, (ticker, annual_date, annual_id))
                row_q = cur.fetchone()
            # A published date is not enough: some sources publish a future
            # quarter label early. Keep only a real quarter at or before today.
            from datetime import date
            today = date.today()
            if row_q:
                parsed_year, parsed_quarter = _row_period(row_q, "LATEST_QUARTER")
                current_quarter = (today.month + 2) // 3
                if (
                    not isinstance(parsed_quarter, int)
                    or parsed_year > today.year
                    or (parsed_year == today.year and parsed_quarter > current_quarter)
                ):
                    row_q = None

            # Never accept the first date-ordered row: legacy providers can
            # return an old but otherwise valid quarter before newer records.
            # The candidate pass below is the sole source of truth.
            row_q = None

            # Re-select by parsed fiscal period when the date-ordered query
            # chose any row; choose the newest parsed fiscal period instead.
            if row_q is None:
                cur.execute("""
                    SELECT id, title, doc_type, published_date, article_pdf_urls, source
                    FROM knowledge_documents
                    WHERE symbol = %s
                      AND doc_type = 'financial_statement'
                      AND published_date >= %s
                      AND id != %s
                """, (ticker, annual_date, annual_id))
                candidates = []
                fallback_candidates = []
                today = date.today()
                current_quarter = (today.month + 2) // 3
                for candidate in cur.fetchall():
                    title = _ascii_title(str(candidate.get("title") or ""))
                    if _has_non_annual_marker(candidate) or "hop nhat" in title:
                        continue
                    y_candidate, q_candidate = _row_period(candidate, "LATEST_QUARTER")
                    if not isinstance(q_candidate, int) or y_candidate > today.year:
                        continue
                    if y_candidate == today.year and q_candidate > current_quarter:
                        continue
                    item = (y_candidate, q_candidate, candidate["published_date"], candidate["id"], candidate)
                    if any(token in title for token in ("rieng", "cong ty me")):
                        candidates.append(item)
                    else:
                        # Some issuers publish a non-consolidated BCTC with a
                        # generic title; the explicit consolidated exclusion
                        # above makes this safe as a final fallback.
                        fallback_candidates.append(item)
                all_candidates = candidates + fallback_candidates
                if all_candidates:
                    # Fiscal recency dominates title specificity; otherwise an
                    # old explicitly-scoped row can beat a newer generic row.
                    row_q = sorted(all_candidates, key=lambda item: item[:4])[-1][4]
            if row_q:
                y_q, q_q = _row_period(row_q, "LATEST_QUARTER")
                urls_q = _row_urls(row_q) + _cafef_alternate_urls(
                    cur, ticker, "LATEST_QUARTER", y_q, q_q
                )
                urls_q = tuple(dict.fromkeys(urls_q))
                url_q = urls_q[0] if urls_q else ""
                quarter_doc = ActiveDocument(
                    doc_id=row_q["id"],
                    ticker=ticker,
                    doc_type="financial_statement",
                    title=row_q["title"],
                    published_date=str(row_q["published_date"]),
                    pdf_url=url_q,
                    pdf_urls=urls_q,
                    role="LATEST_QUARTER",
                    fiscal_year=y_q,
                    fiscal_quarter=q_q,
                    scope="SEPARATE",
                )

            # 3. Tìm Báo cáo Quản trị gần nhất
            cur.execute("""
                SELECT id, title, doc_type, published_date, article_pdf_urls, source
                FROM knowledge_documents
                WHERE symbol = %s
                  AND (
                      doc_type = 'governance_report'
                      OR LOWER(title) LIKE '%%quản trị%%'
                      OR LOWER(title) LIKE '%%quan tri%%'
                  )
                  AND (
                      LOWER(title) LIKE '%%báo cáo tình hình quản trị%%'
                      OR LOWER(title) LIKE '%%bao cao tinh hinh quan tri%%'
                      OR doc_type = 'governance_report'
                  )
                  AND LOWER(title) NOT LIKE '%%nghị quyết%%'
                ORDER BY (source = 'vnstock_docs') DESC, published_date DESC NULLS LAST, id DESC
                LIMIT 1;
            """, (ticker,))
            row_gov = cur.fetchone()
            if row_gov:
                y_gov, q_gov = _row_period(row_gov, "GOVERNANCE_REPORT")
                urls_gov = _row_urls(row_gov) + _cafef_alternate_urls(
                    cur, ticker, "GOVERNANCE_REPORT", y_gov, q_gov
                )
                urls_gov = tuple(dict.fromkeys(urls_gov))
                url_gov = urls_gov[0] if urls_gov else ""
                gov_doc = ActiveDocument(
                    doc_id=row_gov["id"],
                    ticker=ticker,
                    doc_type="governance_report",
                    title=row_gov["title"],
                    published_date=str(row_gov["published_date"]),
                    pdf_url=url_gov,
                    pdf_urls=urls_gov,
                    role="GOVERNANCE_REPORT",
                    fiscal_year=y_gov,
                    fiscal_quarter=q_gov,
                    scope="GOVERNANCE",
                )

        finally:
            cur.close()
            conn.close()

        return TickerDocumentSet(
            ticker=ticker,
            annual_audited=annual_doc,
            latest_quarter=quarter_doc,
            governance_report=gov_doc,
        )
