"""Replay scheduling checks without importing Agent startup or opening a database."""

import ast
import asyncio
from collections import Counter
from datetime import date, datetime, time, timedelta
from enum import Enum
import importlib.util
import logging
import math
import os
from pathlib import Path
import random
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]


def load_rule(path):
    name = "replay_precheck_" + Path(path).stem
    spec = importlib.util.spec_from_file_location(name, ROOT / "app/domain/rules" / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ReplayPrecheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Load pure rule files directly; package startup registers Agents and can write to LOCAL.
        cls.namespace = dict(globals(), logger=logging.getLogger(__name__))
        for path, names in (
            ("portfolio/construction_engine.py", ["PortfolioConstructionEngine"]),
            ("portfolio/dynamic_allocation_engine.py", ["DynamicAllocationEngine"]),
            ("portfolio/liquidity_engine.py", ["LiquidityEngine"]),
            ("risk/breadth_risk_engine.py", ["BreadthRiskEngine"]),
            ("risk/drawdown_recovery_protocol.py", ["DrawdownRecoveryProtocol", "DrawdownTier"]),
            ("risk/t25_exposure_manager.py", ["T25ExposureManager"]),
        ):
            module = load_rule(path)
            cls.namespace.update({name: getattr(module, name) for name in names})
        source = ast.parse((ROOT / "app/domain/pipeline/daily_pipeline_orchestrator.py").read_text(encoding="utf-8"))
        body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
        body.extend(node for node in source.body if isinstance(node, ast.ClassDef))
        module = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
        exec(compile(module, str(ROOT / "app/domain/pipeline/daily_pipeline_orchestrator.py"), "exec"), cls.namespace)

    def setUp(self):
        self.namespace["PortfolioRepository"] = SimpleNamespace
        self.runner = self.namespace["DailyInvestmentPipeline"]("DISABLED", "DISABLED")
        self.inputs = dict(
            ticker="FPT", current_price=50_000.0, sector="Technology", adtv20_shares=1_000_000.0,
            current_nav=1_000_000_000.0, cash_balance=1_000_000_000.0, existing_positions=[],
            regime="BULL_MARKET", peak_nav=1_000_000_000.0, locked_t25_value=0.0,
            distribution_days=1, breadth_ma20_pct=60.0,
        )

    def check(self, **changes):
        return self.runner._precheck_allocation_feasibility(**(self.inputs | changes))

    def test_original_risk_boundaries_and_missing_inputs(self):
        self.assertTrue(self.check()[0])
        self.assertTrue(self.check(current_nav=880_000_000.0)[0])  # 12% DD is not RED at HEAD.
        self.assertFalse(self.check(current_nav=850_000_000.0)[0])
        self.assertTrue(self.check(breadth_ma20_pct=20.0)[0])  # Low breadth alone does not BLOCK at HEAD.
        self.assertTrue(self.check(distribution_days=4)[0])
        self.assertFalse(self.check(distribution_days=5)[0])
        self.assertFalse(self.check(locked_t25_value=350_000_000.0)[0])
        self.assertTrue(self.check(cash_balance=0.0)[0])  # Agent may hydrate missing cash from storage.
        self.assertTrue(self.check(adtv20_shares=0.0)[0])
        self.assertTrue(self.check(current_price=float("nan"))[0])
        self.assertTrue(self.check(distribution_days=None, breadth_ma20_pct=None)[0])

    def test_cash_sector_and_lot_constraints_and_existing_positions(self):
        self.assertFalse(self.check(cash_balance=100_000_000.0)[0])
        self.assertFalse(self.check(adtv20_shares=99.0)[0])
        self.assertFalse(self.check(current_price=2_000_000.0)[0])
        sector_position = {"ticker": "ABC", "sector": "Technology", "market_value": 350_000_000.0}
        self.assertFalse(self.check(existing_positions=[sector_position])[0])
        held = {"ticker": "FPT", "shares": 1000, "available_shares": 0}
        self.assertEqual(self.check(existing_positions=[held], distribution_days=5), (True, "EXISTING_POSITION"))

    def test_optimistic_allocation_does_not_skip_a_smaller_executable_buy(self):
        rng = random.Random(20261024)
        for _ in range(300):
            price = rng.choice([10_000.0, 50_000.0, 500_000.0, 2_000_000.0])
            cash = rng.uniform(0.01, 1.0) * self.inputs["current_nav"]
            adtv = rng.choice([99.0, 400.0, 10_000.0, 1_000_000.0])
            regime = rng.choice(["BULL_MARKET", "RANGE_BOUND"])
            positions = [{"ticker": "ABC", "sector": "Technology", "market_value": rng.uniform(0, 0.5) * self.inputs["current_nav"]}]
            possible, _ = self.check(current_price=price, cash_balance=cash, adtv20_shares=adtv,
                                     regime=regime, existing_positions=positions)
            if possible:
                continue
            for target in [0.0, 0.025, 0.05, 0.08, 0.12, 0.15]:
                construction = self.runner._construction_engine.construct(
                    "FPT", target, "Technology", self.inputs["current_nav"], positions, target)
                dynamic = self.runner._dynamic_engine.evaluate_allocation(
                    construction.portfolio_target, "FPT", regime, self.inputs["current_nav"], cash, positions)
                liquidity = self.runner._liquidity_engine.evaluate_liquidity(
                    "FPT", dynamic.adjusted_target, price, self.inputs["current_nav"], 0, adtv)
                self.assertEqual(liquidity.incremental_shares, 0)

    def test_actual_pipeline_skips_llm_only_for_impossible_new_replay_buy(self):
        for historical, held, cash, expect_thesis in (
            (True, False, 100_000_000.0, False),
            (True, False, 1_000_000_000.0, True),
            (True, True, 100_000_000.0, True),
            (False, False, 100_000_000.0, True),
        ):
            calls = []

            async def dispatch(agent, event):
                calls.append(agent)
                data = {
                    "market_surveillance": {"current_regime": "BULL_MARKET", "distribution_days": 1, "breadth_above_ma20_pct": 60},
                    "reinforcement_learning": {},
                    "universe_discovery": {"eligible_count": 1, "discovery_list": [{"ticker": "FPT", "adtv20_shares": 1_000_000}]},
                    "equity_research": {"conviction": "A", "current_price": 50_000, "sector": "Technology", "css": 80,
                                        "eligible_for_thesis": True, "eligibility_reasons": []},
                    "investment_thesis": {"status": "WAIT_OR_SKIP"},
                    "system_governance": {"system_status": "COMPLIANT"},
                }[agent]
                return {"status": "SUCCESS", "result": {"data": data}}

            self.namespace["AgentRegistry"] = SimpleNamespace(dispatch=dispatch)
            self.namespace["standalone_ml_channel"] = SimpleNamespace(account_id="unused")
            portfolio = dict(total_nav=1_000_000_000.0, peak_nav=1_000_000_000.0,
                             cash_vnd=cash, positions=[{"ticker": "FPT"}] if held else [], locked_t25_value=0.0)
            self.runner._load_current_portfolio_state = lambda *args: portfolio
            self.runner._monitor_open_positions = AsyncMock(return_value=[])
            result = asyncio.run(self.runner.run(
                target_date="2020-01-02" if historical else (date.today() + timedelta(days=1)).isoformat(),
                replay_portfolio=portfolio, standalone_nav=0.0,
            ))
            self.assertEqual(result["status"], "SUCCESS")
            self.assertEqual("investment_thesis" in calls, expect_thesis)
            self.assertEqual(calls[:4], ["market_surveillance", "reinforcement_learning", "universe_discovery", "equity_research"])
            self.assertEqual(calls[-1], "system_governance")

    def test_replay_finishes_each_candidate_before_starting_the_next_like_prod(self):
        active_counter_calls = 0
        max_counter_calls = 0
        candidate_order = ["FPT", "HPG", "VCI"]
        chain_events = []

        async def dispatch(agent, event):
            nonlocal active_counter_calls, max_counter_calls
            if agent == "market_surveillance":
                data = {"current_regime": "BULL_MARKET", "distribution_days": 1, "breadth_above_ma20_pct": 60}
            elif agent == "reinforcement_learning":
                data = {}
            elif agent == "universe_discovery":
                data = {"eligible_count": 3, "discovery_list": [
                    {"ticker": "FPT", "adtv20": 1000, "adtv20_shares": 1_000_000},
                    {"ticker": "HPG", "adtv20": 900, "adtv20_shares": 1_000_000},
                    {"ticker": "VCI", "adtv20": 800, "adtv20_shares": 1_000_000},
                ]}
            elif agent == "equity_research":
                ticker = event["ticker"]
                chain_events.append((agent,ticker))
                return {"status": "SUCCESS", "result": {"data": {
                    "ticker": ticker, "conviction": "A", "current_price": 50_000,
                    "sector": "Industrials", "css": 80, "eligible_for_thesis": True,
                    "eligibility_reasons": [],
                }}}
            elif agent == "investment_thesis":
                ticker = event["research_report"]["ticker"]
                return {"status": "SUCCESS", "result": {"data": {
                    "ticker": ticker, "thesis_id": f"thesis-{ticker}", "status": "APPROVED_ACTIVE",
                }}}
            elif agent == "counter_thesis":
                active_counter_calls += 1
                max_counter_calls = max(max_counter_calls, active_counter_calls)
                await asyncio.sleep(0.02)
                active_counter_calls -= 1
                ticker = event["investment_thesis"]["ticker"]
                return {"status": "SUCCESS", "result": {"data": {
                    "ticker": ticker, "verdict": "PROCEED", "cts_score": 10, "block_reasons": [],
                }}}
            elif agent == "strategy_cio":
                chain_events.append((agent,event['conflict']['ticker']))
                data = {"final_resolution": "BLOCK", "weight_cap": 0, "rationale": "test stop"}
            elif agent == "system_governance":
                data = {"system_status": "COMPLIANT"}
            else:
                raise AssertionError(f"unexpected agent: {agent}")
            return {"status": "SUCCESS", "result": {"data": data}}

        self.namespace["AgentRegistry"] = SimpleNamespace(dispatch=dispatch)
        self.namespace["standalone_ml_channel"] = SimpleNamespace(account_id="unused")
        self.runner._monitor_open_positions = AsyncMock(return_value=[])
        self.runner._precheck_allocation_feasibility = lambda **_kwargs: (True, "FEASIBLE")
        portfolio = dict(total_nav=1_000_000_000.0, peak_nav=1_000_000_000.0,
                         cash_vnd=1_000_000_000.0, positions=[], locked_t25_value=0.0)
        self.runner._load_current_portfolio_state = lambda *args: portfolio

        result = asyncio.run(self.runner.run(
            target_date="2020-01-02", replay_portfolio=portfolio, standalone_nav=0.0,
            candidate_tickers=candidate_order, max_candidates=3,
        ))

        assert result["status"] == "SUCCESS"
        assert max_counter_calls == 1
        assert chain_events == [(agent,ticker) for ticker in candidate_order
                                for agent in ('equity_research','strategy_cio')]
        research_decisions = result["trace"]["phases"]["phase_4_equity_research_gate"]["candidate_decisions"]
        assert [item["ticker"] for item in research_decisions] == candidate_order


if __name__ == "__main__":
    unittest.main()
