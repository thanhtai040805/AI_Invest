import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.core.registry import AgentRegistry
from app.domain.pipeline.daily_pipeline_orchestrator import DailyInvestmentPipeline, ExecutionMode
from app.infrastructure.external_api.market_data_service import market_data_svc


@pytest.mark.parametrize("fail_refresh", [False, True])
def test_shadow_execution_refreshes_portfolio_before_next_candidate(monkeypatch, fail_refresh):
    class PortfolioStub:
        def __init__(self):
            self.cash = 1_000_000_000.0
            self.executions = 0
            self.positions = []
            self.positions_reads = 0

        def get_account_state(self, user_id=None):
            return {"cash_balance": self.cash, "total_nav": 1_000_000_000.0, "peak_nav": 1_000_000_000.0}

        def get_open_positions(self, user_id=None):
            self.positions_reads += 1
            if fail_refresh and self.executions and self.positions_reads > 1:
                raise RuntimeError("portfolio read failed")
            return list(self.positions)

    portfolio = PortfolioStub()
    allocation_payloads = []
    risk_payloads = []

    async def fake_dispatch(cls, agent_name, payload):
        if agent_name == "market_surveillance":
            data = {"current_regime": "RANGE_BOUND", "session_context": "Normal"}
        elif agent_name == "reinforcement_learning":
            data = {}
        elif agent_name == "universe_discovery":
            data = {
                "eligible_count": 2,
                "discovery_list": [
                    {"ticker": ticker, "universe_group": "A", "adtv20": 100_000_000, "adtv20_shares": 1_000_000}
                    for ticker in ("FPT", "VNM")
                ],
            }
        elif agent_name == "equity_research":
            data = {
                "ticker": payload["ticker"],
                "conviction": "A", "current_price": 100_000.0, "sector": "Technology",
                "eligible_for_thesis": True, "eligibility_reasons": [], "css": 80.0,
            }
        elif agent_name == "investment_thesis":
            data = {"status": "APPROVED", "thesis_id": f"thesis-{payload['research_report']['ticker']}"}
        elif agent_name == "counter_thesis":
            data = {"verdict": "PROCEED", "cts_score": 10.0}
        elif agent_name == "strategy_cio":
            data = {"final_resolution": "APPROVE_CONDITIONAL", "weight_cap": 0.10}
        elif agent_name == "portfolio_allocation":
            allocation_payloads.append(payload)
            ticker = payload["candidate"]["ticker"]
            data = {
                "action": "BUY", "target_shares": 100, "target_weight_pct": 0.01,
                "proposed_order": {"ticker": ticker, "sector": "Technology"},
            }
        elif agent_name == "portfolio_risk":
            risk_payloads.append(payload)
            data = {"risk_status": "PASS", "decision": {"approved_shares": 100}}
        elif agent_name == "trade_execution":
            instruction = payload["order_instruction"]
            ticker = instruction["ticker"]
            portfolio.executions += 1
            portfolio.cash -= 10_000_000.0
            portfolio.positions.append({
                "ticker": ticker, "shares": 100, "current_price": 100_000.0,
                "market_value": 10_000_000.0, "sector": "Technology", "locked_t25_shares": 0,
            })
            data = {"status": "EXECUTED", "shares": 100, "executed_price": 100_000.0, "execution_mode": "SHADOW_RUNNER"}
        elif agent_name == "position_monitoring":
            data = {"action": "HOLD"}
        elif agent_name == "system_governance":
            data = {"system_status": "COMPLIANT", "audit_block_hash": "a" * 64}
        else:
            raise AssertionError(f"Unexpected agent: {agent_name}")
        return {"status": "SUCCESS", "result": {"data": data}}

    async def fake_order_book(ticker):
        return {}

    monkeypatch.setattr(AgentRegistry, "dispatch", classmethod(fake_dispatch))
    monkeypatch.setattr(market_data_svc, "get_order_book", fake_order_book)
    runner = DailyInvestmentPipeline(
        multi_agent_mode=ExecutionMode.SHADOW_RUNNER.value,
        standalone_ml_mode=ExecutionMode.DISABLED.value,
    )
    runner.portfolio_repo = portfolio

    result = asyncio.run(runner.run(
        target_date=datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat(),
        candidate_tickers=["FPT", "VNM"],
        max_candidates=2,
        standalone_nav=500_000_000.0,
    ))

    if fail_refresh:
        assert result["status"] == "FAILED"
        assert result["trace"]["portfolio_state_refresh"]["status"] == "FAILED_AFTER_EXECUTION"
        assert len(allocation_payloads) == len(risk_payloads) == portfolio.executions == 1
    else:
        assert result["status"] == "SUCCESS"
        assert len(allocation_payloads) == len(risk_payloads) == portfolio.executions == 2
        assert allocation_payloads[1]["cash_balance"] == 990_000_000.0
        assert [p["ticker"] for p in allocation_payloads[1]["positions"]] == ["FPT"]
        assert risk_payloads[1]["portfolio"]["cash_vnd"] == 990_000_000.0
        assert [p["ticker"] for p in risk_payloads[1]["portfolio"]["positions"]] == ["FPT"]
