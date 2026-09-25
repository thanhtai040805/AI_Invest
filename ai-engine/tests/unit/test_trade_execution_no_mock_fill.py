import asyncio
from datetime import datetime, timezone

from app.core.registry import AgentRegistry
from app.domain.agents.trade_execution import TradeExecutionAgent
from app.domain.repositories.market_data_repository import MarketDataRepository
from app.domain.rules.failsafe import failsafe_engine


def test_emergency_sell_without_live_depth_is_not_filled(monkeypatch):
    class Repository:
        executed = False

        def get_account_state(self):
            return {"total_nav": 1_000_000_000}

        def execute_order_transaction(self, **kwargs):
            self.executed = True
            return {"order_id": "unexpected"}

        def record_slippage(self, **kwargs):
            raise AssertionError("unfilled orders must not record slippage")

    async def approve_governance(cls, agent_name, event):
        return {
            "status": "SUCCESS",
            "result": {"data": {"verdict": "APPROVE", "governance_token": "test"}},
        }

    monkeypatch.setattr(AgentRegistry, "dispatch", classmethod(approve_governance))
    repo = Repository()
    agent = TradeExecutionAgent(repository=repo)
    agent.eae_engine.determine_market_phase = lambda _now: "CONTINUOUS"
    failsafe_engine.reset()

    result = asyncio.run(agent.process({
        "order_instruction": {
            "ticker": "PVT",
            "side": "SELL",
            "approved_shares": 100,
            "price": 25_000,
            "urgency": "HIGH",
            "bypass_portfolio_agent": True,
        },
        "orderbook": {
            "symbol": "PVT",
            "bids": [],
            "asks": [],
            "marketState": "continuous_morning",
            "lastUpdate": datetime.now(timezone.utc).isoformat(),
        },
    }))

    assert result["data"]["status"] == "BLOCKED_NO_EXECUTABLE_DEPTH"
    assert result["data"]["shares"] == 0
    assert not repo.executed


def test_missing_decision_price_does_not_query_rest_or_daily_fallback(monkeypatch):
    async def approve_governance(cls, agent_name, event):
        return {
            "status": "SUCCESS",
            "result": {"data": {"verdict": "APPROVE", "governance_token": "test"}},
        }

    monkeypatch.setattr(AgentRegistry, "dispatch", classmethod(approve_governance))

    price_lookups = []

    def forbidden_price_lookup(*_args, **_kwargs):
        price_lookups.append(True)
        return None

    monkeypatch.setattr(MarketDataRepository, "get_realtime_or_latest_price", forbidden_price_lookup)
    monkeypatch.setattr(MarketDataRepository, "get_market_data_daily", forbidden_price_lookup)
    agent = TradeExecutionAgent(repository=object())
    result = asyncio.run(agent.process({
        "order_instruction": {"ticker": "PVT", "side": "BUY", "approved_shares": 100},
    }))

    assert result["data"]["status"] == "REJECTED_MISSING_PRICE"
    assert result["data"]["shares"] == 0
    assert not price_lookups
