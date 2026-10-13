from datetime import datetime, timedelta

from app.domain.repositories.portfolio_repository import PortfolioRepository


class PortfolioStorageStub:
    def __init__(self):
        self.position_query = ""

    def fetch_all(self, query, params=None):
        if "FROM positions p" in query:
            self.position_query = query
            return [("FPT", 1000, 500000.0, datetime.now() - timedelta(days=7), 600000.0, "Technology")]
        return [(400_000_000.0,)]


def test_open_positions_include_sector_for_allocation_and_risk_checks():
    storage = PortfolioStorageStub()
    repository = PortfolioRepository(storage=storage)

    positions = repository.get_open_positions(user_id="account-1")

    assert "LEFT JOIN stocks s ON s.symbol = p.symbol" in storage.position_query
    assert "COALESCE(NULLIF(s.sector, ''), s.industry, 'Unknown')" in storage.position_query
    assert positions[0]["ticker"] == "FPT"
    assert positions[0]["sector"] == "Technology"
    assert positions[0]["market_value"] == 600_000_000.0
