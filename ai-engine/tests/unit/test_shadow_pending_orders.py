import asyncio
import json
from types import SimpleNamespace

import pytest

from app.domain.repositories.portfolio_repository import PortfolioRepository
from app.infrastructure.external_api.dnse.market_session import MarketState
from app.infrastructure.workers import shadow_order_daemon as shadow_module


class Storage:
    def __init__(self, pending_status="PENDING_SHADOW"):
        self.pending_status = pending_status
        self.executed = []
        self.rolled_back = False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetch_all(self, sql, params=None):
        if "SELECT user_id, symbol, side, quantity, status FROM orders" in sql:
            return [("account", "FPT", "BUY", 100, self.pending_status)]
        if "SELECT id, user_id, symbol, side, price, quantity, order_type, decision_context FROM orders" in sql:
            return [
                ("id1", "account", "FPT", "BUY", 25000, 100, "SHADOW_LIMIT", {"reason": "approved entry"}),
                ("id2", "ml-account", "HPG", "BUY", 30000, 200, "SHADOW_ML_LIMIT", {"reason": "ML signal"}),
            ]
        raise AssertionError(sql)

    def begin(self):
        pass

    def rollback(self):
        self.rolled_back = True

    def commit(self):
        raise AssertionError("must not commit a duplicate fill")


def test_shadow_order_is_persisted_without_touching_cash_or_positions():
    storage = Storage()
    context = {"reason": "approved entry", "rule_level": "MOS_PASS"}
    order_id = PortfolioRepository(storage).create_shadow_pending_order(
        "fpt", 100, 25000, "account", decision_context=context
    )

    sql, params = storage.executed[0]
    assert "INSERT INTO orders" in sql
    assert "PENDING_SHADOW" in sql
    assert params[1:7] == ("account", "FPT", "BUY", "SHADOW_LIMIT", 25000, 100)
    assert json.loads(params[-1]) == context
    assert order_id


def test_already_filled_shadow_order_cannot_debit_cash_twice():
    storage = Storage(pending_status="EXECUTED")
    repo = PortfolioRepository(storage)
    original_cash = repo._in_memory_account["cash_balance"]

    with pytest.raises(ValueError, match="no longer pending"):
        repo.execute_order_transaction(
            ticker="FPT", action="BUY", shares=100, executed_price=25000,
            user_id="account", pending_order_id="already-filled",
        )

    assert storage.rolled_back
    assert not any("cash_balance = cash_balance -" in sql for sql, _ in storage.executed)
    assert repo._in_memory_account["cash_balance"] == original_cash


def test_pending_scan_includes_both_shadow_channels():
    orders = PortfolioRepository(Storage()).get_pending_shadow_orders()

    assert [order["order_type"] for order in orders] == ["SHADOW_LIMIT", "SHADOW_ML_LIMIT"]
    assert orders[0]["decision_context"] == {"reason": "approved entry"}
    assert orders[1]["decision_context"] == {"reason": "ML signal"}


@pytest.mark.parametrize("order_type", ["SHADOW_LIMIT", "SHADOW_ML_LIMIT"])
def test_shadow_daemon_forwards_context_to_delayed_fill(monkeypatch, order_type):
    context = {"reason": "approved entry", "rule_level": "MOS_PASS"}
    captured = {}

    class Repository:
        def expire_pending_shadow_orders(self):
            return 0

        def get_pending_shadow_orders(self):
            return [{
                "order_id": "order-1", "user_id": "account", "ticker": "FPT",
                "side": "BUY", "limit_price": 25_000, "shares": 100,
                "order_type": order_type, "decision_context": context,
            }]

        def execute_order_transaction(self, **kwargs):
            captured["execution"] = kwargs

    class Registry:
        @classmethod
        async def dispatch(cls, agent_name, event):
            captured["dispatch"] = (agent_name, event)
            return {"status": "SUCCESS", "result": {"data": {"status": "EXECUTED"}}}

    async def order_book(_ticker):
        return {"bids": [], "asks": []}

    monkeypatch.setattr(shadow_module, "AgentRegistry", Registry)
    monkeypatch.setattr(shadow_module.market_data_svc, "get_order_book", order_book)
    monkeypatch.setattr(shadow_module, "shadow_fill", lambda *_args: 25_000)

    daemon = object.__new__(shadow_module.ShadowOrderDaemon)
    daemon.session = SimpleNamespace(get_market_state=lambda: MarketState.CONTINUOUS_MORNING)
    daemon.repository = Repository()

    async def run_tick():
        daemon._last_expiry_check = asyncio.get_running_loop().time()
        return await daemon.run_single_tick()

    assert asyncio.run(run_tick()) == 1
    if order_type == "SHADOW_ML_LIMIT":
        assert captured["execution"]["decision_context"] == context
    else:
        assert captured["dispatch"][0] == "trade_execution"
        assert captured["dispatch"][1]["order_instruction"]["decision_context"] == context
