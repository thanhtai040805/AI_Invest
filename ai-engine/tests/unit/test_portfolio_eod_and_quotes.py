import asyncio
import time
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.infrastructure.external_api.dnse.stream_hub import DnseStreamHub
from app.infrastructure.external_api.market_data_service import quote_with_freshness
from app.infrastructure.workers import eod_learning_daemon as eod_module
from app.domain.repositories.portfolio_repository import PortfolioRepository
from app.infrastructure.database import pg_pool
from app.domain.agents.position_monitoring import PositionMonitoringAgent


def test_inconsistent_security_reference_does_not_create_false_daily_loss():
    hub = object.__new__(DnseStreamHub)
    hub._stock_metadata = {"SSI": {"refPrice": 36050, "floor": 33600, "ceiling": 38500}}
    trade = SimpleNamespace(symbol="SSI", price=20.15, quantity=100, totalVolumeTraded=100)
    result = hub._map_trade(trade)
    assert result["price"] == 20150
    assert result["changePercent"] is None
    assert result["prevClose"] is None
    assert result["floor"] == 0
    assert result["referenceStatus"] == "inconsistent"
    hub._stock_metadata["SSI"] = {"refPrice": 20000, "floor": 18600, "ceiling": 21400}
    result = hub._map_trade(trade)
    assert result["changePercent"] == pytest.approx(0.75)


def test_quote_freshness_uses_receive_time_even_after_redis_expiry():
    now = time.time()
    assert not quote_with_freshness({"receivedAt": now, "price": 20000})["stale"]
    assert not quote_with_freshness({"receivedAt": now * 1000, "price": 20000})["stale"]
    assert quote_with_freshness({"receivedAt": now - 60, "price": 20000, "stale": False})["stale"]
    assert quote_with_freshness({"price": 20000})["stale"]
    assert quote_with_freshness({"receivedAt": now, "stale": True})["stale"]


def test_stop_alert_never_closes_a_paper_lot_without_a_fill():
    statements = []
    agent = object.__new__(PositionMonitoringAgent)
    agent.repository = SimpleNamespace(storage=SimpleNamespace(execute=lambda sql, params: statements.append((sql, params))))
    agent._persist_stop_loss_events([{
        "ticker": "FPT", "triggered_price": 131000, "current_pnl_pct": 0.77,
        "quantity": 100, "dispatch_status": "PENDING_SHADOW",
    }])
    assert len(statements) == 1
    assert "INSERT INTO stop_loss_events" in statements[0][0]
    assert not any("paper_trades" in sql for sql, _ in statements)


@pytest.mark.parametrize("missing_main,failed_main", [(False, False), (True, False), (False, True)])
def test_eod_marks_each_fund_and_retries_missing_prices_independently(monkeypatch, missing_main, failed_main):
    monkeypatch.setenv("MULTI_AGENT_ACCOUNT_ID", "main")
    monkeypatch.setenv("STANDALONE_ML_ACCOUNT_ID", "ml")
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 30, 18, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    monkeypatch.setattr(eod_module, "datetime", Clock)
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params): self.sql, self.params = sql, params
        def fetchone(self):
            if self.sql.startswith("SELECT 1"): return (1,)
            return (int(missing_main and self.params[0] == "main"),)
    @contextmanager
    def connection():
        yield SimpleNamespace(cursor=Cursor)
    monkeypatch.setattr(pg_pool, "get_conn", connection)
    marks = []
    def mark(self, *, user_id, mark_as_of):
        if failed_main and user_id == "main": raise RuntimeError("mark failed")
        marks.append((user_id, mark_as_of.isoformat()))
    monkeypatch.setattr(PortfolioRepository, "record_replay_mark", mark)
    runs = []
    async def run(**kwargs):
        runs.append(kwargs)
        return {"status": "SUCCESS"}
    daemon = eod_module.EODLearningDaemon(runner=SimpleNamespace(run=run))
    daemon.session_mgr = SimpleNamespace(is_trading_day=lambda now: True)
    asyncio.run(daemon._check_and_trigger())
    assert ("ml", "2026-09-30") in marks
    if missing_main or failed_main:
        assert daemon._last_run_date is None
        assert runs == []
    else:
        assert ("main", "2026-09-30") in marks
        assert daemon._last_run_date == "2026-09-30"
        assert len(runs) == 1
