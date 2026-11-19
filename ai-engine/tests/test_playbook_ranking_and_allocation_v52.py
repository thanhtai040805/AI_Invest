"""Comprehensive Test Suite for Phase 1 (5 Ranking Fixes) & Phase 2 (AUM/Regime Elastic Corridor Allocation)."""

from __future__ import annotations

import asyncio
from datetime import date
import pandas as pd
import pytest

from app.core.registry import AgentRegistry
from app.domain.pipeline.daily_pipeline_orchestrator import DailyInvestmentPipeline, ExecutionMode
from app.domain.repositories.intelligence_repository import IntelligenceRepository
from app.domain.rules.optimizer import PortfolioOptimizer
from app.domain.rules.portfolio import (
    AUMTier,
    DynamicAllocationEngine,
    InvestmentPlaybook,
    KellySizingEngine,
    PortfolioConstructionEngine,
    RebalancingEngine,
    resolve_aum_tier,
    resolve_portfolio_policy,
)
from app.domain.rules.scoring import CSSScoringEngine
from app.domain.rules.thesis_engine import ThesisEngine
from app.domain.agents.portfolio_risk import PortfolioRiskAgent


def test_allocation_policy_regimes_and_aum_tiers():
    # 1. Crisis -> 0% new buys
    crisis = resolve_portfolio_policy("BEAR_MARKET", total_nav=1_000_000_000.0, session_context="Crisis")
    assert crisis.playbook == InvestmentPlaybook.CRISIS_LOCKDOWN
    assert crisis.max_equity_exposure == 0.0
    assert crisis.min_cash_target == 1.0
    assert crisis.max_positions == 0

    # 2. Unstable / sideways / mild Bear -> Tactical Swing 10-20% NAV, 1-3 stocks, 6% floor
    for reg in ("RANGE_BOUND", "CHOPPY", "BEAR_MARKET"):
        p = resolve_portfolio_policy(reg, total_nav=1_000_000_000.0, session_context="Normal")
        assert p.playbook == InvestmentPlaybook.TACTICAL_SWING
        assert p.aum_tier == AUMTier.NAV_LE_1B
        assert p.max_equity_exposure == 0.20
        assert p.min_cash_target == 0.80
        assert p.max_positions == 3
        assert p.min_entry_weight == 0.06
        assert p.max_single_stock_weight == 0.10
        assert p.max_a_plus_stock_weight == 0.12
        assert p.timeline_months == 1

    # 3. Bull Market AUM Tiers
    bull_1b = resolve_portfolio_policy("BULL_MARKET", total_nav=1_000_000_000.0)
    assert bull_1b.playbook == InvestmentPlaybook.MARKET_LEADER
    assert bull_1b.aum_tier == AUMTier.NAV_LE_1B
    assert bull_1b.max_equity_exposure == 1.00
    assert bull_1b.min_cash_target == 0.00
    assert bull_1b.min_positions == 3
    assert bull_1b.max_positions == 5
    assert bull_1b.min_entry_weight == 0.10
    assert bull_1b.max_single_stock_weight == 0.25
    assert bull_1b.max_a_plus_stock_weight == 0.30
    assert bull_1b.allow_zero_cash_when_healthy is True

    bull_3b = resolve_portfolio_policy("BULL_MARKET", total_nav=3_000_000_000.0)
    assert bull_3b.aum_tier == AUMTier.NAV_1B_TO_5B
    assert bull_3b.max_positions == 7
    assert bull_3b.max_single_stock_weight == 0.20

    bull_10b = resolve_portfolio_policy("BULL_MARKET", total_nav=10_000_000_000.0)
    assert bull_10b.aum_tier == AUMTier.NAV_GT_5B
    assert bull_10b.max_positions == 12
    assert bull_10b.max_single_stock_weight == 0.15


def test_gap3_f6_technical_not_overwritten_by_volatility_score():
    class FakeStorage:
        def fetch_all(self, query, params):
            return [(
                "FPT",
                date(2026, 8, 12),
                62.0,   # value
                84.0,   # quality
                78.0,   # momentum_3m
                75.0,   # earnings
                70.0,   # flow
                31.5,   # volatility_score (low-vol score)
                72.0,   # composite_score
                85.0,   # percentile
                {"f6_technical": 88.5, "conviction": "A"},
            )]

    repo = IntelligenceRepository(storage=FakeStorage())
    res = repo.get_factor_score("FPT", score_date=date(2026, 8, 12))
    assert res is not None
    assert res["f6_technical"] == 88.5
    assert res["low_vol_score"] == 31.5
    assert res["f6_is_vol_fallback"] is False


def test_gap4_playbook_scoring_and_rl_weight_clamping():
    engine = CSSScoringEngine()
    base = {"f1_value": 0.15, "f2_quality": 0.15, "f3_momentum": 0.30, "f4_earnings": 0.15, "f5_flow": 0.15, "f6_technical": 0.10}
    extreme_rl = {"f1_value": 0.80, "f2_quality": 0.01, "f3_momentum": 0.01, "f4_earnings": 0.01, "f5_flow": 0.01, "f6_technical": 0.16}
    clamped = engine.clamp_rl_weights(base, extreme_rl, max_relative_deviation=0.20)
    assert abs(sum(clamped.values()) - 1.0) < 1e-3
    # Momentum cannot collapse to 0.01; stays near its base band
    assert clamped["f3_momentum"] > 0.24

    # Market Leader rejects negative RS / below MA20
    laggard = engine.calculate_playbook_score(
        factors={"f1_value": 85, "f2_quality": 70, "f3_momentum": 40, "f4_earnings": 60, "f5_flow": 45, "f6_technical": 40},
        leadership={"rs_20d": -0.04, "rs_score": 25.0, "sector_leadership_score": 30.0, "trend_template_score": 35.0, "close_above_ma20": False},
        playbook="MARKET_LEADER",
    )
    assert "LEADER_RS20D_NONPOSITIVE" in laggard["disqualifiers"]
    assert "LEADER_BELOW_MA20" in laggard["disqualifiers"]

    # Tactical Swing rewards VSA Spring + Quality + Value and rejects Bearish Upthrust
    rebound = engine.calculate_playbook_score(
        factors={"f1_value": 76, "f2_quality": 78, "f3_momentum": 35, "f4_earnings": 60, "f5_flow": 62, "f6_technical": 55},
        leadership={"rs_20d": 0.02, "rs_score": 68.0, "vsa_swing_score": 84.0, "has_spring": True, "has_bearish_upthrust": False},
        playbook="TACTICAL_SWING",
        universe_group="A",
    )
    assert rebound["disqualifiers"] == []
    assert rebound["playbook_score"] > 75.0

    upthrust_trap = engine.calculate_playbook_score(
        factors={"f1_value": 76, "f2_quality": 78, "f3_momentum": 60, "f4_earnings": 60, "f5_flow": 62, "f6_technical": 55},
        leadership={"rs_20d": 0.02, "rs_score": 68.0, "vsa_swing_score": 35.0, "has_bearish_upthrust": True},
        playbook="TACTICAL_SWING",
    )
    assert "VSA_BEARISH_UPTHRUST" in upthrust_trap["disqualifiers"]


def test_gap5_signal3_tactical_rebound_vs_crisis():
    te = ThesisEngine()
    factors = {"f1_value": 72.0, "f2_quality": 74.0, "f3_momentum": 35.0, "f4_earnings": 64.0, "f5_flow": 62.0, "f6_technical": 55.0}

    # Crisis strictly fails Signal 3
    passed_crisis, sigs_crisis, _ = te.evaluate_independent_signals(
        factors=factors, css_score=68.0, quality_score=74.0,
        regime_label="BEAR_MARKET", playbook="TACTICAL_SWING",
        leadership_metrics={"has_spring": True, "vsa_swing_score": 78.0},
        session_context="Crisis",
    )
    assert passed_crisis is False
    assert sigs_crisis["signal_3_macro_hmm"].startswith("FAIL")

    # Mild Bear under TACTICAL_SWING passes Signal 3 via VSA absorption + quality + value
    passed_rebound, sigs_rebound, count_rebound = te.evaluate_independent_signals(
        factors=factors, css_score=68.0, quality_score=74.0,
        regime_label="BEAR_MARKET", playbook="TACTICAL_SWING",
        leadership_metrics={"has_spring": True, "vsa_swing_score": 78.0, "has_bearish_upthrust": False},
        session_context="Normal",
    )
    assert passed_rebound is True
    assert count_rebound == 3
    assert "TACTICAL_REBOUND_ABSORPTION" in sigs_rebound["signal_3_macro_hmm"]

    # Weak stock with no absorption or RS leadership still fails in Bear
    passed_weak, _, _ = te.evaluate_independent_signals(
        factors={"f1_value": 45.0, "f2_quality": 45.0, "f3_momentum": 35.0, "f4_earnings": 64.0, "f5_flow": 62.0, "f6_technical": 40.0},
        css_score=62.0, quality_score=45.0,
        regime_label="BEAR_MARKET", playbook="TACTICAL_SWING",
        leadership_metrics={"rs_20d": -0.05, "vsa_swing_score": 40.0},
        session_context="Normal",
    )
    assert passed_weak is False


def test_gap1_gap2_finalist_ranker_selects_best_and_filters_correlation():
    opt = PortfolioOptimizer(min_positions=1, max_positions=3)
    corr = pd.DataFrame(
        {
            "AAA": {"AAA": 1.0, "BBB": 0.85, "CCC": 0.25, "DDD": 0.20},
            "BBB": {"AAA": 0.85, "BBB": 1.0, "CCC": 0.30, "DDD": 0.25},
            "CCC": {"AAA": 0.25, "BBB": 0.30, "CCC": 1.0, "DDD": 0.15},
            "DDD": {"AAA": 0.20, "BBB": 0.25, "CCC": 0.15, "DDD": 1.0},
        }
    )
    finalists = [
        {"ticker": "DDD", "sector": "Retail", "conviction": "B", "css": 62.0, "playbook_score": 63.0, "cts_score": 45.0, "current_price": 20000, "base_case_target_price": 23500, "sector_leadership_score": 55.0, "counter_verdict": "CONDITIONAL"},
        {"ticker": "AAA", "sector": "Tech", "conviction": "A+", "css": 86.0, "playbook_score": 88.0, "cts_score": 15.0, "current_price": 100000, "base_case_target_price": 132000, "sector_leadership_score": 92.0, "counter_verdict": "PROCEED"},
        {"ticker": "BBB", "sector": "Industrial", "conviction": "A", "css": 80.0, "playbook_score": 82.0, "cts_score": 20.0, "current_price": 40000, "base_case_target_price": 50000, "sector_leadership_score": 80.0, "counter_verdict": "PROCEED"},
        {"ticker": "CCC", "sector": "Bank", "conviction": "A", "css": 76.0, "playbook_score": 78.0, "cts_score": 22.0, "current_price": 30000, "base_case_target_price": 37500, "sector_leadership_score": 75.0, "counter_verdict": "PROCEED"},
    ]
    selected, board = opt.rank_and_select_finalists(
        finalists=finalists,
        existing_positions=[],
        total_nav=1_000_000_000.0,
        max_positions=2,
        max_sector_weight=0.50,
        min_slot_weight=0.06,
        corr_matrix=corr,
        correlation_limit=0.65,
    )
    # AAA wins #1; BBB is #2 by score but skipped due to 0.85 correlation with AAA; CCC gets #2 slot!
    assert [s["ticker"] for s in selected] == ["AAA", "CCC"]
    bbb_row = next(r for r in board if r["ticker"] == "BBB")
    assert bbb_row["selected"] is False
    assert "HIGH_CORRELATION_WITH_AAA" in bbb_row["selection_reason"]


def test_phase2_corridor_sizing_floor_and_max_positions():
    policy_swing = resolve_portfolio_policy("RANGE_BOUND", total_nav=1_000_000_000.0)
    kelly = KellySizingEngine()
    # Even with conservative Range Bound Tier B prior (p=0.47, b=1.45), no 1.3% dust sizing!
    k_res = kelly.calculate_sizing(0.47, 1.45, "RANGE_BOUND", 1_000_000_000.0, policy=policy_swing, conviction="B", signal_strength=68.0)
    assert 0.06 <= k_res.preliminary_target <= 0.10

    # DynamicAllocationEngine caps total equity at 20% and rejects < 6% residual dust
    dyn = DynamicAllocationEngine()
    # Already 17% invested across 2 stocks -> only 3% remaining < 6% floor -> 0%
    d_dust = dyn.evaluate_allocation(
        portfolio_target=0.09,
        ticker="CCC",
        regime_str="RANGE_BOUND",
        total_nav=1_000_000_000.0,
        cash_balance=830_000_000.0,
        existing_positions=[
            {"ticker": "AAA", "shares": 1000, "market_value": 90_000_000.0},
            {"ticker": "BBB", "shares": 1000, "market_value": 80_000_000.0},
        ],
        policy=policy_swing,
    )
    assert d_dust.adjusted_target == 0.0

    # Already 3 stocks (even if small total weight) -> max_positions=3 blocks 4th stock!
    d_full_slots = dyn.evaluate_allocation(
        portfolio_target=0.08,
        ticker="DDD",
        regime_str="RANGE_BOUND",
        total_nav=1_000_000_000.0,
        cash_balance=880_000_000.0,
        existing_positions=[
            {"ticker": "AAA", "shares": 1000, "market_value": 40_000_000.0},
            {"ticker": "BBB", "shares": 1000, "market_value": 40_000_000.0},
            {"ticker": "CCC", "shares": 1000, "market_value": 40_000_000.0},
        ],
        policy=policy_swing,
    )
    assert d_full_slots.adjusted_target == 0.0

    # RebalancingEngine rejects liquidity-crushed 2.5% new entry (< 6% floor)
    reb = RebalancingEngine()
    r_dust = reb.evaluate_rebalance(
        ticker="VIX",
        current_weight=0.0,
        current_shares=0,
        available_shares=0,
        locked_t25_shares=0,
        portfolio_target=0.08,
        executable_target=0.025,
        executable_shares=1000,
        target_shares=3200,
        incremental_shares=1000,
        price=25_000.0,
        total_nav=1_000_000_000.0,
        min_entry_weight_pct=0.06,
    )
    assert r_dust.action == "HOLD"
    assert r_dust.sub_action == "HOLD_BELOW_MIN_ENTRY_FLOOR"
    assert r_dust.incremental_shares == 0
    assert r_dust.campaign_info is None


def test_phase2_risk_agent_blocks_subfloor_reduction_and_allows_bull_25pct():
    risk_agent = PortfolioRiskAgent()

    async def _run():
        # 1. Bull Market NAV <= 1B policy allows 25% leader order (with 0% cash target when healthy)
        bull_policy = resolve_portfolio_policy("BULL_MARKET", total_nav=1_000_000_000.0)
        res_bull = await risk_agent.process({
            "is_replay": True,
            "policy": bull_policy,
            "min_entry_weight": bull_policy.min_entry_weight,
            "portfolio": {
                "total_nav": 1_000_000_000.0,
                "peak_nav": 1_000_000_000.0,
                "cash_vnd": 1_000_000_000.0,
                "positions": {},
                "sector_exposure": {},
                "locked_t25_value": 0.0,
                "returns_series": [0.005, 0.003, -0.002, 0.004, 0.006] * 6,
            },
            "proposed_order": {
                "ticker": "FPT",
                "side": "BUY",
                "quantity": 2500,
                "price": 100_000.0,  # 250M = 25% NAV
                "stop_loss_price": 93_000.0,
                "sector": "Technology",
                "conviction": "A",
                "adtv20_shares": 3_000_000.0,
                "candle": {"open": 99_000.0, "high": 101_000.0, "low": 98_500.0, "close": 100_000.0, "volume": 2_000_000.0},
                "ma20_volume": 2_000_000.0,
            },
            "market_context": {"distribution_days": 1, "breadth_ma20_pct": 65.0, "vnindex_change_pct": 0.008},
            "observation_days_below_threshold": 3,
        })
        assert res_bull["data"]["risk_status"] == "PASS"
        assert res_bull["data"]["decision"]["approved_shares"] == 2500
        assert res_bull["data"]["decision"]["min_cash_target_pct"] == 0.0

        # 2. Tactical Swing: 6.5% proposed order reduced by 0.70x warning tape -> 4.5% < 6% floor -> BLOCK (0 shares!)
        swing_policy = resolve_portfolio_policy("RANGE_BOUND", total_nav=1_000_000_000.0)
        res_subfloor = await risk_agent.process({
            "is_replay": True,
            "policy": swing_policy,
            "min_entry_weight": swing_policy.min_entry_weight,
            "portfolio": {
                "total_nav": 1_000_000_000.0,
                "peak_nav": 1_000_000_000.0,
                "cash_vnd": 1_000_000_000.0,
                "positions": {},
                "sector_exposure": {},
                "locked_t25_value": 0.0,
                "returns_series": [0.002, -0.003, 0.001, -0.002] * 6,
            },
            "proposed_order": {
                "ticker": "HPG",
                "side": "BUY",
                "quantity": 2500,
                "price": 26_000.0,  # 65M = 6.5% NAV
                "stop_loss_price": 24_200.0,
                "sector": "Materials",
                "conviction": "B",
                "adtv20_shares": 20_000_000.0,
                "candle": {"open": 25_800.0, "high": 26_300.0, "low": 25_600.0, "close": 26_000.0, "volume": 15_000_000.0},
                "ma20_volume": 15_000_000.0,
            },
            # Drawdown ORANGE (0.50x) cuts 6.5% -> 3.25% < 6% floor -> BLOCK!
            "market_context": {"distribution_days": 4, "breadth_ma20_pct": 30.0, "vnindex_change_pct": -0.005},
            "observation_days_below_threshold": 2,
        })
        # 6.5% * 0.75 = 4.875% (< 6% - 1 lot = 5.74%) -> BLOCKED cleanly!
        assert res_subfloor["data"]["risk_status"] == "BLOCK"
        assert res_subfloor["data"]["decision"]["approved_shares"] == 0

    asyncio.run(_run())


def test_two_stage_tournament_ranks_best_over_adtv_and_tracks_inflight_budget(monkeypatch):
    """Verify orchestrator runs Stage-1 on all symbols, ranks by Final Score (NOT ADTV order),
    and reserves in-flight pending cash/positions so Range Bound never exceeds 20% total NAV."""
    from app.domain.pipeline import daily_pipeline_orchestrator as mod

    monkeypatch.setattr(
        mod.FactorService,
        "compute_universe_leadership_metrics",
        lambda self, tickers, as_of_date=None: (
            {
                "BIG_MEH": {"rs_20d": 0.01, "rs_score": 55.0, "sector_leadership_score": 50.0, "vsa_swing_score": 58.0},
                "MID_STAR": {"rs_20d": 0.08, "rs_score": 90.0, "sector_leadership_score": 88.0, "vsa_swing_score": 86.0, "has_spring": True},
                "MID_TWO": {"rs_20d": 0.05, "rs_score": 78.0, "sector_leadership_score": 76.0, "vsa_swing_score": 78.0, "has_no_supply": True},
            },
            pd.DataFrame(),
        ),
    )

    alloc_call_order = []

    async def fake_dispatch(cls, agent_name, event_data):
        if agent_name == "market_surveillance":
            return {"status": "SUCCESS", "result": {"data": {
                "current_regime": "RANGE_BOUND", "session_context": "Normal",
                "halted_tickers": [], "distribution_days": 2, "breadth_above_ma20_pct": 35.0,
                "breadth_above_ma50_pct": 32.0, "csad_score": 0.01, "adv_decl_ratio": 0.9,
            }}}
        if agent_name == "reinforcement_learning":
            return {"status": "SUCCESS", "result": {"data": {"policy_weights": {}, "kelly_matrix": {}, "cdc_triggered": False}}}
        if agent_name == "universe_discovery":
            return {"status": "SUCCESS", "result": {"data": {
                "eligible_count": 3,
                "discovery_list": [
                    {"ticker": "BIG_MEH", "universe_group": "A", "adtv20": 500e9, "adtv20_shares": 20e6, "portfolio_sector": "Banks"},
                    {"ticker": "MID_TWO", "universe_group": "A", "adtv20": 80e9, "adtv20_shares": 4e6, "portfolio_sector": "Retail"},
                    {"ticker": "MID_STAR", "universe_group": "A", "adtv20": 50e9, "adtv20_shares": 2e6, "portfolio_sector": "Tech"},
                ],
            }}}
        if agent_name == "equity_research":
            t = event_data["ticker"]
            scores = {"BIG_MEH": (62.0, 61.0, "B"), "MID_TWO": (75.0, 78.0, "A"), "MID_STAR": (85.0, 88.0, "A+")}
            css, pb, conv = scores[t]
            return {"status": "SUCCESS", "result": {"data": {
                "ticker": t, "sector": "General", "conviction": conv, "css": css,
                "playbook_score": pb, "rs_score": pb, "sector_leadership_score": pb,
                "current_price": 25_000.0, "eligible_for_thesis": True, "eligibility_reasons": [],
            }}}
        if agent_name == "investment_thesis":
            t = event_data["research_report"]["ticker"]
            return {"status": "SUCCESS", "result": {"data": {
                "thesis_id": f"TH_{t}", "ticker": t, "status": "PENDING_COUNTER_ANALYSIS",
                "thesis_body": {"price_target": {"base_case": 32_000.0}},
            }}}
        if agent_name == "counter_thesis":
            t = event_data["investment_thesis"]["ticker"]
            cts = {"BIG_MEH": 45.0, "MID_TWO": 22.0, "MID_STAR": 15.0}[t]
            return {"status": "SUCCESS", "result": {"data": {
                "ticker": t, "verdict": "PROCEED", "cts_score": cts, "block_reasons": [],
            }}}
        if agent_name == "strategy_cio":
            t = event_data["conflict"]["ticker"]
            cap = event_data["conflict"]["max_single_stock_cap"]
            return {"status": "SUCCESS", "result": {"data": {
                "ticker": t, "final_resolution": "PROCEED_WITH_PENALTY", "weight_cap": cap,
            }}}
        if agent_name == "portfolio_allocation":
            t = event_data["candidate"]["ticker"]
            alloc_call_order.append((t, len(event_data["positions"]), round(event_data["cash_balance"] / 1e9, 3)))
            # Simulate 10% allocation for #1, 9% for #2, and 0% (below 6% floor) when only 1% budget left for #3!
            used = sum(p.get("market_value", 0.0) for p in event_data["positions"]) / event_data["total_nav"]
            rem = max(0.0, 0.20 - used)
            desired = 0.10 if t == "MID_STAR" else (0.09 if t == "MID_TWO" else 0.07)
            actual = min(desired, rem)
            if actual < 0.06:
                return {"status": "SUCCESS", "result": {"data": {"action": "HOLD", "target_shares": 0, "allocated_weight_pct": 0.0, "rationale": "Below 6% floor"}}}
            shares = int(actual * event_data["total_nav"] / 25_000.0) // 100 * 100
            return {"status": "SUCCESS", "result": {"data": {
                "action": "BUY", "ticker": t, "target_shares": shares,
                "allocated_weight_pct": round(shares * 25_000.0 / event_data["total_nav"] * 100, 2),
                "target_weight_pct": round(shares * 25_000.0 / event_data["total_nav"], 4),
                "price": 25_000.0, "sector": event_data["candidate"]["sector"],
            }}}
        if agent_name == "portfolio_risk":
            sh = event_data["proposed_order"]["target_shares"]
            return {"status": "SUCCESS", "result": {"data": {
                "risk_status": "PASS", "decision_id": "R1",
                "decision": {"approved_shares": sh, "approved_weight_pct": round(sh * 25_000.0 / 1e9 * 100, 2), "rationale": "OK"},
            }}}
        if agent_name == "system_governance":
            return {"status": "SUCCESS", "result": {"data": {"system_status": "COMPLIANT", "audit_block_hash": "a" * 64}}}
        raise AssertionError(f"Unexpected agent {agent_name}")

    monkeypatch.setattr(AgentRegistry, "dispatch", classmethod(fake_dispatch))
    runner = DailyInvestmentPipeline(multi_agent_mode=ExecutionMode.DISABLED.value)
    res = asyncio.run(runner.run(
        target_date="2026-08-18",
        current_nav=1_000_000_000.0,
        max_candidates=3,
        replay_portfolio={
            "total_nav": 1_000_000_000.0, "peak_nav": 1_000_000_000.0,
            "cash_vnd": 1_000_000_000.0, "positions": [], "sector_exposure": {},
            "locked_t25_value": 0.0, "returns_series": [],
        },
    ))

    # MID_STAR (lowest ADTV but highest score) MUST be allocated #1, then MID_TWO #2, then BIG_MEH sees 19% used -> HOLD!
    assert [x[0] for x in alloc_call_order] == ["MID_STAR", "MID_TWO", "BIG_MEH"]
    assert alloc_call_order[1][1] == 1  # in-flight reservation updated working_positions!
    assert alloc_call_order[2][1] == 2
    assert [o["ticker"] for o in res["multi_agent_instructions"]] == ["MID_STAR", "MID_TWO"]
    total_w = sum(o["target_weight_pct"] for o in res["multi_agent_instructions"])
    assert 0.10 <= total_w <= 0.20


def test_big_picture_option_a_exit_engine_and_auto_sweep():
    """Verify Phương án A:
    - Zero intraday sells before 14:15 (even on 1m wicks or deep morning dips).
    - No rigid mechanical 35% trailing stop when winner holds above MA20.
    - Dry-volume shakeouts below support do NOT trigger panic exit.
    - Confirmed Distribution requires ALL 3 Big-Picture pillars & Auto-Sweeps residual < 4% NAV.
    - Confirmed Capital Stop respects strong Hammer recovery.
    """
    from app.domain.rules.stop_loss import StopLossEngine
    from app.domain.rules.technical_structure import TechnicalStructureAnalyzer

    engine = StopLossEngine()
    nav = 1_000_000_000.0

    # Build a 60-day uptrend OHLCV series ending in healthy breakout
    bars = []
    for i in range(60):
        base = 20_000.0 + i * 100.0
        bars.append({
            "date": f"2026-08-{i+1:02d}",
            "open": base - 100.0,
            "high": base + 200.0,
            "low": base - 200.0,
            "close": base + 100.0,
            "volume": 1_200_000.0 if i % 2 == 0 else 800_000.0,
        })
    snap = TechnicalStructureAnalyzer.analyze_ohlcv_bars(bars, rs_20d=0.06)
    assert snap.price_structure in ("BREAKOUT_UPTREND", "ACCUMULATION_BASE")
    assert snap.money_flow_state == "ACCUMULATING"

    # 1. Before 14:15 (is_near_close=False) -> ZERO sells even with huge 1m upper wick!
    intraday_res = engine.check_position(
        ticker="PVT",
        quantity=3400,
        entry_price=20_350.0,
        current_price=20_350.0,
        nav=nav,
        available_shares=3400,
        market_data={
            "is_near_close": False,
            "technical_structure": snap,
            "session_candle": {"open": 20_350, "high": 20_400, "low": 20_350, "close": 20_350, "volume": 60_900},
            "daily_ma20_volume": snap.ma20_volume,
        },
    )
    assert intraday_res is None

    # 2. At 14:15: 1-tick upper wick in healthy uptrend -> ZERO sells (no false FAST_EXIT!)
    eod_healthy = engine.check_position(
        ticker="PVT",
        quantity=3400,
        entry_price=20_350.0,
        current_price=snap.close,
        nav=nav,
        available_shares=3400,
        market_data={
            "is_near_close": True,
            "peak_price": snap.close * 1.04,
            "technical_structure": snap,
            "session_candle": {"open": snap.close, "high": snap.close + 50.0, "low": snap.close, "close": snap.close, "volume": snap.ma20_volume * 2.0},
            "daily_ma20_volume": snap.ma20_volume,
        },
    )
    assert eod_healthy is None

    # 3. Winner +10.8% pulling back to +6.5% STILL above MA20 -> ZERO mechanical 35% trailing exit!
    pvp_entry = 18_500.0
    pvp_peak = 20_500.0  # +10.8%
    pvp_now = 19_700.0   # +6.5% (gave back 40% of peak profit, old T2 would have sold!)
    pvp_struct = {
        "close": 19_700.0, "ma10": 19_500.0, "ma20": 19_000.0, "ma50": 18_000.0, "ma200": 17_000.0,
        "ma20_volume": 1_000_000.0, "swing_low_20d": 18_200.0, "swing_low_60d": 17_500.0,
        "recent_higher_low": 18_900.0, "resistance_20d": 20_500.0, "resistance_60d": 20_500.0,
        "distance_to_resistance_pct": 4.0, "extension_vs_ma20_pct": 3.7,
        "price_structure": "BREAKOUT_UPTREND", "up_down_vol_ratio_10d": 1.4, "cmf_20d": 0.10,
        "stock_dist_days_10d": 0, "money_flow_state": "ACCUMULATING",
    }
    assert engine.check_position(
        ticker="PVP", quantity=3500, entry_price=pvp_entry, current_price=pvp_now, nav=nav,
        available_shares=3500, market_data={"is_near_close": True, "peak_price": pvp_peak, "technical_structure": pvp_struct},
    ) is None

    # 4. Dry-volume shakeout 0.8% below swing low with strong hammer -> DO NOT SELL!
    shakeout_res = engine.check_position(
        ticker="VND", quantity=3900, entry_price=16_100.0, current_price=15_500.0, nav=nav,
        available_shares=3900,
        market_data={
            "is_near_close": True,
            "technical_structure": {
                **pvp_struct, "swing_low_20d": 15_600.0, "ma20": 15_800.0,
                "money_flow_state": "NEUTRAL", "stock_dist_days_10d": 0,
            },
            "session_candle": {"open": 15_400, "high": 15_600, "low": 15_100, "close": 15_500, "volume": 500_000},
            "daily_ma20_volume": 1_000_000,
        },
    )
    assert shakeout_res is None

    # 5. Confirmed Distribution on a 6.5% position -> Auto-Sweep 100% (no 3.25% / 100-share zombie!)
    dist_struct = {
        "close": 30_000.0, "ma10": 28_500.0, "ma20": 26_500.0, "ma50": 24_000.0, "ma200": 22_000.0,
        "ma20_volume": 1_000_000.0, "swing_low_20d": 25_000.0, "swing_low_60d": 23_000.0,
        "resistance_20d": 30_500.0, "resistance_60d": 30_500.0,
        "distance_to_resistance_pct": 1.6, "extension_vs_ma20_pct": 13.2,
        "price_structure": "DISTRIBUTION_TOP", "up_down_vol_ratio_10d": 0.78, "cmf_20d": -0.09,
        "stock_dist_days_10d": 2, "money_flow_state": "DISTRIBUTING",
    }
    sweep_res = engine.check_position(
        ticker="DCM", quantity=2100, entry_price=28_000.0, current_price=30_000.0, nav=nav,
        available_shares=2100,  # 6.3% NAV -> 50% would leave 3.15% < 4.0% -> Auto-Sweep 100%!
        market_data={
            "is_near_close": True,
            "technical_structure": dist_struct,
            "session_candle": {"open": 31_200, "high": 31_500, "low": 29_800, "close": 30_000, "volume": 1_900_000},
            "daily_ma20_volume": 1_000_000,
            "min_remaining_weight": 0.04,
        },
    )
    assert sweep_res is not None
    assert sweep_res.rule_level == "CONFIRMED_DISTRIBUTION"
    assert sweep_res.suggested_action == "SELL_ALL"
    assert sweep_res.quantity == 2100

    # 6. Phương án A Confirmed Capital Stop (-11% stock): Hammer recovery holds; weak close exits 100%
    hammer_hold = engine.check_position(
        ticker="ABC", quantity=2000, entry_price=30_000.0, current_price=26_700.0, nav=nav,
        available_shares=2000,
        market_data={
            "is_near_close": True,
            "session_candle": {"open": 25_500, "high": 27_000, "low": 25_000, "close": 26_700, "volume": 1_200_000},
            "daily_ma20_volume": 1_000_000,
        },
    )
    assert hammer_hold is None

    weak_exit = engine.check_position(
        ticker="ABC", quantity=2000, entry_price=30_000.0, current_price=26_700.0, nav=nav,
        available_shares=2000,
        market_data={
            "is_near_close": True,
            "session_candle": {"open": 28_000, "high": 28_100, "low": 26_600, "close": 26_700, "volume": 1_600_000},
            "daily_ma20_volume": 1_000_000,
        },
    )
    assert weak_exit is not None
    assert weak_exit.rule_level == "CONFIRMED_CAPITAL_STOP"
    assert weak_exit.quantity == 2000


def test_aum_hysteresis_and_tactical_sector_cap_and_breadth_rationale():
    """Verify 1.05B AUM hysteresis, 14% TACTICAL_SWING sector cap, and explicit Breadth BLOCK rationale."""
    assert resolve_aum_tier(1_020_000_000.0) == AUMTier.NAV_LE_1B
    assert resolve_aum_tier(1_100_000_000.0) == AUMTier.NAV_1B_TO_5B
    pol_swing = resolve_portfolio_policy("RANGE_BOUND", total_nav=1_000_000_000.0)
    assert pol_swing.max_sector_weight == 0.14

    async def _run():
        risk_agent = PortfolioRiskAgent()
        res = await risk_agent.process({
            "is_replay": True,
            "policy": pol_swing,
            "min_entry_weight": pol_swing.min_entry_weight,
            "portfolio": {"total_nav": 1e9, "peak_nav": 1e9, "positions": {}, "sector_exposure": {}, "locked_t25_value": 0.0},
            "proposed_order": {
                "ticker": "HAH", "side": "BUY", "quantity": 1400, "price": 48_000.0,
                "stop_loss_price": 44_600.0, "sector": "Transportation", "adtv20": 2_000_000.0,
                "candle": {"open": 47_500, "high": 48_200, "low": 47_400, "close": 48_000, "volume": 1_500_000},
                "ma20_volume": 1_400_000.0,
            },
            "market_context": {"distribution_days": 6, "breadth_ma20_pct": 28.0, "vnindex_change_pct": -0.01},
        })
        assert res["data"]["risk_status"] == "BLOCK"
        assert "MARKET BREADTH BLOCK" in res["data"]["decision"]["rationale"]

    asyncio.run(_run())
