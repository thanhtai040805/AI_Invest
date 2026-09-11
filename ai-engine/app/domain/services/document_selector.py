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
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger("ai_engine.services.document_selector")


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
        from datetime import datetime
        year = datetime.now().year

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
                SELECT id, title, doc_type, published_date, article_pdf_urls
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
                ORDER BY published_date DESC NULLS LAST, id DESC
                LIMIT 1;
            """, (ticker,))
            row_ann = cur.fetchone()
            if not row_ann:
                # Fallback cho doanh nghiệp/ngân hàng không dùng tiêu đề riêng/công ty mẹ
                cur.execute("""
                    SELECT id, title, doc_type, published_date, article_pdf_urls
                    FROM knowledge_documents
                    WHERE symbol = %s
                      AND doc_type = 'financial_statement'
                      AND (
                          LOWER(title) LIKE '%%kiểm toán%%'
                          OR LOWER(title) LIKE '%%kiem toan%%'
                          OR LOWER(title) LIKE '%%audited%%'
                          OR LOWER(title) LIKE '%%cả năm%%'
                      )
                    ORDER BY published_date DESC NULLS LAST, id DESC
                    LIMIT 1;
                """, (ticker,))
                row_ann = cur.fetchone()

            if row_ann:
                urls_ann = tuple(dict.fromkeys(str(u).strip() for u in (row_ann.get("article_pdf_urls") or []) if str(u).strip()))
                url_ann = urls_ann[0] if urls_ann else ""
                y_ann, q_ann = parse_fiscal_period(row_ann["title"], str(row_ann["published_date"]), "ANNUAL_BACKBONE")
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
            annual_date = row_ann["published_date"] if row_ann else "2000-01-01"
            annual_id = row_ann["id"] if row_ann else -1
            cur.execute("""
                SELECT id, title, doc_type, published_date, article_pdf_urls
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
                ORDER BY published_date DESC NULLS LAST, id DESC
                LIMIT 1;
            """, (ticker, annual_date, annual_id))
            row_q = cur.fetchone()
            if not row_q:
                # Fallback nếu không có tiêu đề riêng/công ty mẹ
                cur.execute("""
                    SELECT id, title, doc_type, published_date, article_pdf_urls
                    FROM knowledge_documents
                    WHERE symbol = %s
                      AND doc_type = 'financial_statement'
                      AND published_date >= %s
                      AND id != %s
                    ORDER BY published_date DESC NULLS LAST, id DESC
                    LIMIT 1;
                """, (ticker, annual_date, annual_id))
                row_q = cur.fetchone()
            if row_q:
                urls_q = tuple(dict.fromkeys(str(u).strip() for u in (row_q.get("article_pdf_urls") or []) if str(u).strip()))
                url_q = urls_q[0] if urls_q else ""
                y_q, q_q = parse_fiscal_period(row_q["title"], str(row_q["published_date"]), "LATEST_QUARTER")
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
                SELECT id, title, doc_type, published_date, article_pdf_urls
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
                ORDER BY published_date DESC NULLS LAST, id DESC
                LIMIT 1;
            """, (ticker,))
            row_gov = cur.fetchone()
            if row_gov:
                urls_gov = tuple(dict.fromkeys(str(u).strip() for u in (row_gov.get("article_pdf_urls") or []) if str(u).strip()))
                url_gov = urls_gov[0] if urls_gov else ""
                y_gov, q_gov = parse_fiscal_period(row_gov["title"], str(row_gov["published_date"]), "GOVERNANCE_REPORT")
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
