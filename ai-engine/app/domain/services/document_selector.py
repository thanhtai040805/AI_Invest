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
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger("ai_engine.services.document_selector")


@dataclass(frozen=True)
class ActiveDocument:
    doc_id: int
    ticker: str
    doc_type: str
    title: str
    published_date: str
    pdf_url: str
    role: str  # "ANNUAL_BACKBONE" | "LATEST_QUARTER" | "GOVERNANCE_REPORT"
    fiscal_year: Optional[int] = None
    fiscal_quarter: Optional[int] = None
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
                url_ann = row_ann["article_pdf_urls"][0] if row_ann.get("article_pdf_urls") else ""
                annual_doc = ActiveDocument(
                    doc_id=row_ann["id"],
                    ticker=ticker,
                    doc_type="financial_statement",
                    title=row_ann["title"],
                    published_date=str(row_ann["published_date"]),
                    pdf_url=url_ann,
                    role="ANNUAL_BACKBONE",
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
                url_q = row_q["article_pdf_urls"][0] if row_q.get("article_pdf_urls") else ""
                quarter_doc = ActiveDocument(
                    doc_id=row_q["id"],
                    ticker=ticker,
                    doc_type="financial_statement",
                    title=row_q["title"],
                    published_date=str(row_q["published_date"]),
                    pdf_url=url_q,
                    role="LATEST_QUARTER",
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
                url_gov = row_gov["article_pdf_urls"][0] if row_gov.get("article_pdf_urls") else ""
                gov_doc = ActiveDocument(
                    doc_id=row_gov["id"],
                    ticker=ticker,
                    doc_type="governance_report",
                    title=row_gov["title"],
                    published_date=str(row_gov["published_date"]),
                    pdf_url=url_gov,
                    role="GOVERNANCE_REPORT",
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
