import asyncio
import importlib
from unittest.mock import AsyncMock, MagicMock

from app.adapters.sag_connector import SAGConnector, sag_connector
from app.domain.pipeline.bctc_to_sag_pipeline import BctcToSagPipeline
from app.domain.rules.thesis_engine import ThesisEngine
from app.domain.forensic_assessment import ForensicAssessment
from app.domain.services.document_selector import ActiveDocument, TickerDocumentSet


def test_analysis_hold_blocks_every_sag_write_entrypoint():
    async def _test():
        connector = SAGConnector(api_base="http://sag.test")
        connector.sag_analysis_hold = True

        results = await asyncio.gather(
            connector.ingest_bctc_document("FPT", "q1", "# markdown"),
            connector.upload_bctc_pdf("FPT", b"%PDF", filename="q1.pdf"),
            connector.upload_bctc_pdf_from_r2("FPT", "r2://bucket/q1.pdf", "q1"),
            connector.upload_bctc_pdf_from_url("FPT", "https://example.test/q1.pdf", "q1"),
            connector.upload_bctc_pdf_file(
                ticker="FPT",
                pdf_path="missing.pdf",
                filename="q1.pdf",
                doc_role="LATEST_QUARTER",
                scope="SEPARATE",
                is_active=False,
                fiscal_year=None,
                fiscal_quarter=None,
            ),
        )

        assert all(result["status"] == "BLOCKED" for result in results)
        assert all(result["error_code"] == "SAG_CLOSED" for result in results)
        assert all(result["mutated"] is False for result in results)

    asyncio.run(_test())


def test_analysis_hold_blocks_all_sag_reads_without_network(monkeypatch):
    module = importlib.import_module("app.adapters.sag_connector")

    class Client:
        def __init__(self, *args, **kwargs):
            raise AssertionError("SAG HTTP client must not be created while SAG is closed")

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)

    async def _test():
        connector = SAGConnector(api_base="http://sag.test")
        connector.sag_analysis_hold = True
        results = await asyncio.gather(
            connector.get_financial_quality_assessment("FPT"),
            connector.get_gil_relationships("FPT"),
            connector.get_document_status("source", "doc"),
            connector.get_document_parsed_markdown("source", "doc"),
        )
        assert all(result is None or result["error_code"] == "SAG_CLOSED" for result in results)

    asyncio.run(_test())


def test_pipeline_returns_terminal_hold_without_fake_gil_pass():
    async def _test():
        selector = MagicMock()
        selector.select_active_documents.return_value = TickerDocumentSet(
            ticker="TEST_HOLD",
            annual_audited=ActiveDocument(
                doc_id=1,
                ticker="TEST_HOLD",
                doc_type="financial_statement",
                title="BCTC Kiem Toan 2025",
                published_date="2026-03-15",
                pdf_url="https://example.test/annual.pdf",
                role="ANNUAL_BACKBONE",
                fiscal_year=2025,
            ),
        )
        connector = MagicMock()
        connector.ingest_bctc_document = AsyncMock(return_value={
            "status": "BLOCKED",
            "error_code": "SAG_CLOSED",
            "error": "hold",
        })
        connector.get_gil_relationships = AsyncMock()
        repo = MagicMock()
        repo.should_skip_ocr.return_value = False
        r2 = MagicMock(is_configured=False)

        result = await BctcToSagPipeline(
            selector=selector,
            connector=connector,
            repo=repo,
            r2=r2,
        ).process_ticker(
            "TEST_HOLD",
            mock_markdowns={"ANNUAL_BACKBONE": "# BCTC"},
            _single_document=True,
        )

        assert result["status"] == "SAG_CLOSED"
        assert result["gil_flag"] == "PENDING_SAG_CLOSED"
        assert result["db_updated"] is False
        connector.get_gil_relationships.assert_not_awaited()

    asyncio.run(_test())


def test_pipeline_hold_stops_before_selector_or_cached_gil_use():
    async def _test():
        selector = MagicMock()
        connector = SAGConnector(api_base="http://sag.test")
        connector.sag_analysis_hold = True

        result = await BctcToSagPipeline(
            selector=selector,
            connector=connector,
        ).process_ticker("FPT")

        assert result["status"] == "SAG_CLOSED"
        selector.select_active_documents.assert_not_called()

    asyncio.run(_test())


def test_thesis_does_not_read_sag_hold_or_mark_it_as_data_insufficient():
    previous = sag_connector.sag_analysis_hold
    sag_connector.sag_analysis_hold = True
    try:
        eligible, payload, reason = ThesisEngine().build_structured_thesis_output(
            "FPT",
            {
                "ticker": "FPT",
                "css": 90,
                "conviction": "A",
                "f4_earnings": 80,
                "f5_flow": 80,
                "f3_momentum": 80,
            },
            {"forensic_assessment": ForensicAssessment.closed("FPT").to_dict()},
        )
        assert eligible is True
        assert payload["ticker"] == "FPT"
        assert "SAG_CLOSED" not in reason
        assert "DATA_INSUFFICIENT" not in reason
    finally:
        sag_connector.sag_analysis_hold = previous
