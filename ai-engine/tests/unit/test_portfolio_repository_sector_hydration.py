from datetime import datetime, timedelta

from app.domain.repositories.portfolio_repository import PortfolioRepository


class PortfolioStorageStub:
    def __init__(self):
        self.position_query = ""

    def fetch_all(self, query, params=None):
        if "FROM positions p" in query:
            self.position_query = query
            return [("FPT", 1000, 500000.0, datetime.now() - timedelta(days=7), 600000.0, "Technology", 620000.0)]
        return [(400_000_000.0,)]


def test_open_positions_include_sector_for_allocation_and_risk_checks():
    storage = PortfolioStorageStub()
    repository = PortfolioRepository(storage=storage)

    positions = repository.get_open_positions(user_id="account-1")

    assert "LEFT JOIN stocks s ON s.symbol = p.symbol" in storage.position_query
    assert "COALESCE(NULLIF(BTRIM(s.sector), ''), NULLIF(BTRIM(s.industry), ''), 'Unknown')" in storage.position_query
    assert positions[0]["ticker"] == "FPT"
    assert positions[0]["sector"] == "Technology"
    assert positions[0]["market_value"] == 600_000_000.0
    assert positions[0]["peak_price"] == 620_000.0


def test_position_peak_update_is_account_scoped_and_atomic():
    class PeakStorageStub:
        def __init__(self):
            self.query = ""
            self.params = None

        def fetch_all(self, query, params=None):
            self.query = query
            self.params = params
            return [(25_550.0,)]

    storage = PeakStorageStub()
    repository = PortfolioRepository(storage=storage)

    peak = repository.update_position_peak_price("pvp", 22_700.0, user_id="account-7")

    assert peak == 25_550.0
    assert "GREATEST(COALESCE(peak_price, avg_price), avg_price, %s)" in storage.query
    assert "WHERE user_id = %s AND symbol = %s AND quantity > 0" in storage.query
    assert storage.params == (22_700.0, "account-7", "PVP")
