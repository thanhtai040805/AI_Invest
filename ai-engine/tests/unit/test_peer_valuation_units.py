"""Missing fundamentals must not change VND into thousand VND or erase losses."""
from datetime import date

import pytest

from app.domain.repositories.financial_repository import FinancialRepository


def repository(data, close=35.0):
    class Storage:
        def fetch_all(self, query, params):
            if "SELECT sector" in query:
                return [("RETAIL_TRADE", "Retail")]
            if "SELECT close" in query:
                return [(close,)]
            if "WITH sector_universe" in query:
                return [(10, 10, 12., 10, 1.5, 10)]
            raise AssertionError(query)

    repo = FinancialRepository(Storage())
    repo.get_financial_statements = lambda *a, **k: [{
        "period_end": "2026-03-31", "published_date": None, "data": data,
    }]
    return repo


@pytest.mark.parametrize("close", [35., 1500.])
def test_missing_per_share_uses_explicit_thousand_vnd_market_close(close):
    result = repository({"pe": 10., "pb": 2.}, close).get_peer_valuation_inputs("TEST", date(2026,8,11))
    assert result["pe_price"] == close * 1000 / 10 * 12
    inputs = result["source"]["per_share_inputs"]
    assert inputs["eps"] == close * 1000 / 10
    assert inputs["bvps"] == close * 1000 / 2
    assert inputs["currency"] == "VND"
    assert inputs["eps_method"] == "IMPLIED_FROM_MARKET_CLOSE_AND_PE"


def test_reported_eps_bvps_keep_their_vnd_values():
    result = repository({"eps": 10677., "bvps": 42214., "pe": 10.}).get_peer_valuation_inputs("TEST", date(2026,8,11))
    assert result["pe_price"] == 128124.
    assert result["source"]["per_share_inputs"]["eps_method"] == "REPORTED"


@pytest.mark.parametrize("eps", [0., -100.])
def test_nonpositive_reported_eps_is_not_reconstructed_from_positive_pe(eps):
    result = repository({"eps": eps, "bvps": 10000., "pe": 10.}).get_peer_valuation_inputs("TEST", date(2026,8,11))
    assert "pe_price" not in result
    assert result["valuation_diagnostics"]["reason_code"] == "EPS_NONPOSITIVE_PB_UNSUPPORTED"
