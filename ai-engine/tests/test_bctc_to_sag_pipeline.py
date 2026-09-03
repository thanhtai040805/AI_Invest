"""Unit Tests for BctcToSagPipeline (Closed-Loop Ingestion & GIL Evaluation)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.domain.services.bctc_to_sag_pipeline import BctcToSagPipeline
from app.domain.services.document_selector import ActiveDocument, TickerDocumentSet


def test_bctc_to_sag_pipeline_mock_execution():
    """Kiểm tra Pipeline thực thi chuẩn: nạp đủ tài liệu vàng, gọi GIL và cập nhật PostgreSQL."""
    async def _test():
        ticker = "TEST_HPG"

        # Mock ActiveDocumentSelector
        mock_selector = MagicMock()
        mock_selector.select_active_documents.return_value = TickerDocumentSet(
            ticker=ticker,
            annual_audited=ActiveDocument(
                doc_id=101,
                ticker=ticker,
                doc_type="financial_statement",
                title="BCTC Riêng Kiểm Toán 2025",
                published_date="2026-03-15",
                pdf_url="https://r2.test/ann.pdf",
                role="ANNUAL_BACKBONE",
                fiscal_year=2025,
            ),
            latest_quarter=ActiveDocument(
                doc_id=102,
                ticker=ticker,
                doc_type="financial_statement",
                title="BCTC Riêng Quý 1 2026",
                published_date="2026-04-20",
                pdf_url="https://r2.test/q1.pdf",
                role="LATEST_QUARTER",
                fiscal_year=2026,
                fiscal_quarter=1,
            ),
            governance_report=ActiveDocument(
                doc_id=103,
                ticker=ticker,
                doc_type="governance_report",
                title="Báo Cáo Quản Trị Năm 2025",
                published_date="2026-01-30",
                pdf_url="https://r2.test/bcqt.pdf",
                role="GOVERNANCE_REPORT",
                fiscal_year=2025,
            ),
        )

        # Mock SAGConnector
        mock_connector = MagicMock()
        mock_connector.ingest_bctc_document = AsyncMock(return_value={
            "status": "SUCCESS",
            "id": "doc_test_123",
        })
        mock_connector.get_gil_relationships = AsyncMock(return_value={
            "ticker": ticker,
            "gil_flag": "PASS",
            "risk_level": "LOW",
            "rpt_ratio": 0.05,
            "cycles_detected": 0,
        })

        pipeline = BctcToSagPipeline(selector=mock_selector, connector=mock_connector)
        result = await pipeline.process_ticker(
            ticker=ticker,
            equity_vnd=100_000_000_000.0,
            mock_markdowns={
                "ANNUAL_BACKBONE": "# BCTC Kiem Toan 2025",
                "LATEST_QUARTER": "# BCTC Q1 2026",
                "GOVERNANCE_REPORT": "# Bao Cao Quan Tri 2025",
            },
        )

        assert result["status"] == "SUCCESS"
        assert result["documents_count"] == 3
        assert result["gil_flag"] == "PASS"
        assert mock_connector.ingest_bctc_document.await_count == 3
        mock_connector.get_gil_relationships.assert_awaited_once_with(
            ticker=ticker,
            equity_vnd=100_000_000_000.0,
        )

        # Teardown dọn dẹp CSDL
        try:
            from app.infrastructure.database.connection import get_raw_connection
            conn = get_raw_connection()
            with conn.cursor() as cur:
                cur.execute("DELETE FROM universe_securities WHERE ticker = %s;", (ticker,))
            conn.commit()
            conn.close()
        except Exception:
            pass

    asyncio.run(_test())
