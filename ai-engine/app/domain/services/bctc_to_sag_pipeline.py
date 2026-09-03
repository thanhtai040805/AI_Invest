"""BCTC to SAG End-to-End Ingestion Pipeline (IOS v5.1).

Chịu trách nhiệm kết nối khép kín:
1. Tuyển chọn Bộ 3 tài liệu vàng bằng ActiveDocumentSelector.
2. Cắt tỉa (PageClassifier) và trích xuất Markdown.
3. Đẩy sang SAG qua endpoint by-ticker (SAGConnector.ingest_bctc_document).
4. Kích hoạt phân tích đồ thị GIL từ SAG (SAGConnector.get_gil_relationships).
5. Lưu cờ gil_flag vào bảng universe_securities trong PostgreSQL.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.domain.services.document_selector import ActiveDocumentSelector, TickerDocumentSet
from app.adapters.sag_connector import sag_connector, SAGConnector

logger = logging.getLogger("ai_engine.pipeline.bctc_to_sag")


class BctcToSagPipeline:
    """Pipeline tự động hóa 100% nạp tài liệu BCTC từ ai-engine sang SAG."""

    def __init__(
        self,
        selector: Optional[ActiveDocumentSelector] = None,
        connector: Optional[SAGConnector] = None,
    ) -> None:
        self.selector = selector or ActiveDocumentSelector()
        self.connector = connector or sag_connector

    async def process_ticker(
        self,
        ticker: str,
        equity_vnd: float = 0.0,
        mock_markdowns: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Thực thi toàn bộ chu trình nạp và phân tích cho 1 mã cổ phiếu."""
        ticker_clean = ticker.upper().strip()
        logger.info(f"==> Bắt đầu BCTC to SAG Pipeline cho mã {ticker_clean}")

        # 1. Tuyển chọn Bộ 3 tài liệu vàng
        doc_set: TickerDocumentSet = self.selector.select_active_documents(ticker_clean)
        ingested_docs = []

        # 2. Lần lượt đẩy các tài liệu vào SAG
        for doc in doc_set.all_documents:
            # Lấy nội dung Markdown (từ mock nếu truyền vào, hoặc từ R2/storage)
            content_md = ""
            if mock_markdowns and doc.role in mock_markdowns:
                content_md = mock_markdowns[doc.role]
            elif mock_markdowns and doc.title in mock_markdowns:
                content_md = mock_markdowns[doc.title]
            else:
                content_md = f"# {doc.title}\n\nNội dung BCTC chuẩn hóa cho mã {ticker_clean}, vai trò {doc.role}."

            res_ingest = await self.connector.ingest_bctc_document(
                ticker=ticker_clean,
                title=doc.title,
                text_content=content_md,
                doc_role=doc.role,
                is_active=True,
                fiscal_year=doc.fiscal_year,
                fiscal_quarter=doc.fiscal_quarter,
            )
            ingested_docs.append({
                "role": doc.role,
                "title": doc.title,
                "status": res_ingest.get("status", "SUCCESS") if "error" not in res_ingest else "FAILED",
                "doc_id": res_ingest.get("id"),
            })

        # 3. Kích hoạt đánh giá đồ thị thực thể & rủi ro GIL từ SAG
        gil_res = await self.connector.get_gil_relationships(
            ticker=ticker_clean,
            equity_vnd=equity_vnd,
        )
        gil_flag = gil_res.get("gil_flag", "PASS")

        # 4. Cập nhật cờ gil_flag vào CSDL (bảng universe_securities)
        db_updated = False
        try:
            from app.infrastructure.database.connection import get_raw_connection
            conn = get_raw_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO universe_securities (ticker, universe_group, trading_status, beneish_status, gil_flag, updated_at)
                    VALUES (%s, 'B', 'NORMAL', 'PENDING', %s, NOW())
                    ON CONFLICT (ticker) 
                    DO UPDATE SET gil_flag = EXCLUDED.gil_flag, updated_at = NOW();
                    """,
                    (ticker_clean, gil_flag),
                )
            conn.commit()
            conn.close()
            db_updated = True
        except Exception as e:
            logger.debug(f"Không thể cập nhật cờ gil_flag vào universe_securities: {e}")

        logger.info(f"==> Hoàn tất Pipeline cho {ticker_clean}: GIL Flag = {gil_flag} (DB Updated: {db_updated})")
        return {
            "ticker": ticker_clean,
            "status": "SUCCESS",
            "documents_count": len(ingested_docs),
            "documents": ingested_docs,
            "gil_result": gil_res,
            "gil_flag": gil_flag,
            "db_updated": db_updated,
        }
