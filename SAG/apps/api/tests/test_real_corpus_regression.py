from __future__ import annotations

from pathlib import Path

from sag_api.extraction.parsers.governance_table_parser import GovernanceTableParser
from sag_api.extraction.parsers.statement_table_parser import StatementTableParser
from sag_api.services.financial_v2_service import _infer_period_end


ROOT = Path(__file__).resolve().parents[4]
CORPUS = ROOT / "artifacts" / "cleaned_ocr_audit"
TICKERS = ("VCB", "VIC", "MWG", "FPT", "HPG")
ROLES = ("ANNUAL_BACKBONE", "LATEST_QUARTER", "GOVERNANCE_REPORT")


def test_real_corpus_has_safe_evidence_and_periods() -> None:
    for ticker in TICKERS:
        for role in ROLES:
            markdown = (CORPUS / f"{ticker}_{role}.md").read_text(encoding="utf-8")
            inferred = _infer_period_end(markdown, role)
            assert inferred is not None, (ticker, role)
            assert inferred.year <= 2026, (ticker, role, inferred)

            if role == "GOVERNANCE_REPORT":
                frames = GovernanceTableParser(ticker).parse_governance_tables(markdown)
                assert all(0.0 <= frame.ownership_pct <= 100.0 for frame in frames)
                continue

            facts = StatementTableParser(markdown, ticker).parse_statement_facts()
            lines = markdown.splitlines()
            assets = [
                fact["value_numeric"]
                for fact in facts
                if fact["semantic_key"] == "total_assets" and fact["value_numeric"] > 0
            ]
            for fact in facts:
                evidence = fact["evidence"]
                assert 1 <= evidence["line_start"] <= len(lines)
                assert evidence["line_start"] <= evidence["line_end"] <= len(lines)
                assert fact["value_numeric"] is not None
                if fact["semantic_key"] == "guarantee_balance" and assets:
                    assert fact["value_numeric"] <= max(assets) * 10
                    assert "thu phi" not in fact["raw_label"].casefold()
