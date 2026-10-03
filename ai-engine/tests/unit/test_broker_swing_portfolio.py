from dataclasses import replace

import pandas as pd
import pytest

from experiments.broker_swing_portfolio import BrokerSwingPolicy, simulate_broker_swing


def _market(tickers=("HPG",), sessions=12):
    dates = pd.bdate_range("2026-01-02", periods=sessions)
    bars = pd.DataFrame([
        {"date": day, "ticker": ticker, "open": 100.0, "close": 100.0, "volume_continuous": 10_000.0}
        for day in dates for ticker in ("VNINDEX", *tickers)
    ])
    return dates, bars


def _entries(dates, signals=((0, "HPG", .03, 3.0),)):
    return pd.DataFrame([
        {"decision_date": dates[index], "ticker": ticker, "expected_net_return": edge,
         "expected_holding_sessions": holding, "p_profit": .4, "downside_q10": -.05,
         "market_risk_probability": 0.0, "market_opportunity_score": .1,
         "expert_id": "reversal", "adtv20_shares": 10_000.0}
        for index, ticker, edge, holding in signals
    ])


def _states(dates, signals=((0, "HPG", 2, -.01),)):
    return pd.DataFrame([
        {"entry_decision_date": dates[entry], "ticker": ticker, "state_date": dates[entry + holding],
         "holding_sessions": holding, "continuation_net_return": continuation}
        for entry, ticker, holding, continuation in signals
    ])


def _policy(**changes):
    policy = BrokerSwingPolicy(initial_cash=100_000.0, price_scale=1.0, friction_bps=0.0,
                               brokerage_fee_rate=0.0, sell_tax_rate=0.0, minimum_fee=0.0,
                               min_notional=1.0, lot_size=1, risk_budget_per_position=.005,
                               cash_buffer=0.0)
    return replace(policy, **changes)


def test_close_state_executes_next_close_at_earliest_sellable_session():
    dates, bars = _market()
    bars.loc[bars["date"].eq(dates[2]) & bars["ticker"].eq("HPG"), "close"] = 150.0
    bars.loc[bars["date"].eq(dates[3]) & bars["ticker"].eq("HPG"), "close"] = 110.0

    trades, nav, metrics = simulate_broker_swing(_entries(dates), _states(dates), bars, _policy())

    trade = trades.iloc[0]
    assert trade["entry_date"] == dates[1]
    assert trade["exit_date"] == dates[3]
    assert trade["exit_price_vnd"] == 110.0
    assert trade["actual_holding_sessions"] == 3
    assert trade["planned_holding_sessions"] == 7
    assert trade["exit_reason"] == "LEARNED_EXIT"
    assert trade["p_profit"] == .4  # Positive EV need not win most trades.
    assert nav.loc[nav["date"].eq(dates[3]), "unsettled_receivable"].iloc[0] == 11_000.0
    assert nav.loc[nav["date"].eq(dates[4]), "cash"].iloc[0] == 90_000.0
    assert nav.loc[nav["date"].eq(dates[5]), "cash"].iloc[0] == 101_000.0
    assert metrics["sessions_to_first_positive_realized_pnl"] == 3
    assert metrics["capital_days_vnd"] == 30_000.0
    assert metrics["net_profit_per_capital_day"] == pytest.approx(1000 / 30_000)


def test_static_control_keeps_same_entries_and_capital_until_forced_close():
    dates, bars = _market()
    adaptive, _, _ = simulate_broker_swing(_entries(dates), _states(dates), bars, _policy())
    static, _, _ = simulate_broker_swing(_entries(dates), _states(dates), bars, _policy(), "static")

    assert adaptive.iloc[0]["entry_date"] == static.iloc[0]["entry_date"]
    assert adaptive.iloc[0]["entry_cash_cost"] == static.iloc[0]["entry_cash_cost"]
    assert static.iloc[0]["exit_date"] == dates[7]
    assert static.iloc[0]["actual_holding_sessions"] == 7


def test_invalid_state_cannot_sell_early_or_reuse_previous_entry_signal():
    dates, bars = _market(sessions=16)
    states = _states(dates, [(0, "HPG", 1, -.01), (0, "HPG", 2, -.01)])
    entries = _entries(dates, [(0, "HPG", .03, 3.0), (4, "HPG", .03, 3.0)])

    trades, _, metrics = simulate_broker_swing(entries, states, bars, _policy())

    assert trades["exit_date"].tolist() == [dates[3], dates[11]]
    assert metrics["coverage"]["exit_state_rejections"] == {"INVALID_EXIT_STATE": 1}


def test_missing_next_open_is_cancelled_without_later_fill():
    dates, bars = _market()
    bars.loc[bars["date"].eq(dates[1]) & bars["ticker"].eq("HPG"), "open"] = float("nan")

    trades, _, metrics = simulate_broker_swing(_entries(dates), _states(dates), bars, _policy())

    assert trades.empty and not metrics["open_positions"]
    assert metrics["coverage"]["entry_rejections"] == {"NO_EXECUTABLE_NEXT_OPEN": 1}


def test_untradable_forced_exit_defers_past_seven_sessions_without_invented_fill():
    dates, bars = _market()
    bars.loc[bars["date"].eq(dates[7]) & bars["ticker"].eq("HPG"), "volume_continuous"] = 0.0
    bars.loc[bars["date"].eq(dates[8]) & bars["ticker"].eq("HPG"), "close"] = float("nan")

    trades, _, metrics = simulate_broker_swing(_entries(dates), pd.DataFrame(), bars, _policy())

    assert trades.iloc[0]["planned_exit_date"] == dates[7]
    assert trades.iloc[0]["exit_date"] == dates[9]
    assert trades.iloc[0]["actual_holding_sessions"] == 9
    assert trades.iloc[0]["deferred_exit_sessions"] == 2
    assert metrics["trades_exceeding_planned_horizon"] == 1
    assert metrics["coverage"]["deferred_exit_sessions"] == {"NO_EXECUTABLE_CLOSE": 2}
    assert metrics["capital_days_vnd"] == 90_000.0


def test_learned_exit_deferral_records_requested_date_and_waits_for_valid_close():
    dates, bars = _market()
    bars.loc[bars["date"].eq(dates[3]) & bars["ticker"].eq("HPG"), "volume_continuous"] = 0

    trades, _, metrics = simulate_broker_swing(_entries(dates), _states(dates), bars, _policy())

    trade = trades.iloc[0]
    assert trade["scheduled_exit_date"] == dates[3]
    assert trade["planned_exit_date"] == dates[7]
    assert trade["exit_date"] == dates[4]
    assert trade["deferred_exit_sessions"] == 1
    assert metrics["closed_trades_with_deferred_exit"] == 1


def test_unobserved_final_liquidation_remains_open_and_timing_is_missing():
    dates, bars = _market(sessions=8)
    bars.loc[bars["date"].eq(dates[7]) & bars["ticker"].eq("HPG"), "close"] = float("nan")

    trades, _, metrics = simulate_broker_swing(_entries(dates), pd.DataFrame(), bars, _policy())

    assert trades.empty
    assert len(metrics["open_positions"]) == 1
    assert metrics["first_positive_realized_pnl_date"] is None
    assert metrics["realized_pnl_vnd_at_20_sessions"] is None
    assert metrics["net_profit_per_capital_day"] == 0.0


def test_fees_friction_tax_and_cash_reconcile_exactly():
    dates, bars = _market()
    policy = _policy(friction_bps=20, brokerage_fee_rate=.001, sell_tax_rate=.001, minimum_fee=10)

    trades, nav, metrics = simulate_broker_swing(_entries(dates), _states(dates), bars, policy)

    trade = trades.iloc[0]
    assert trade["entry_price_vnd"] == pytest.approx(100.1)
    assert trade["shares"] == 99
    assert trade["buy_fee"] == 10
    assert trade["exit_price_vnd"] == pytest.approx(99.9)
    assert trade["sell_fee"] == 10
    assert trade["sell_tax"] == pytest.approx(9.8901)
    assert trade["net_pnl_vnd"] == pytest.approx(-49.6901)
    assert metrics["final_nav"] == pytest.approx(policy.initial_cash + trade["net_pnl_vnd"])
    assert metrics["final_settled_cash"] == pytest.approx(metrics["final_nav"])
    assert (nav["cash"] >= 0).all()


def test_ten_thousand_vnd_minimum_fee_applies_on_both_sides():
    dates, bars = _market()
    policy = _policy(initial_cash=1_000_000, liquidity_participation=.1,
                     brokerage_fee_rate=.001, sell_tax_rate=.001, minimum_fee=10_000)

    trades, _, metrics = simulate_broker_swing(_entries(dates), _states(dates), bars, policy)

    trade = trades.iloc[0]
    assert trade["buy_notional"] == 100_000
    assert trade["buy_fee"] == trade["sell_fee"] == 10_000
    assert trade["sell_tax"] == 100
    assert trade["net_pnl_vnd"] == -20_100
    assert metrics["final_settled_cash"] == 979_900


def test_one_hundred_bps_stress_reduces_pnl_using_same_execution_convention():
    dates, bars = _market()
    base, _, _ = simulate_broker_swing(_entries(dates), _states(dates), bars, _policy(friction_bps=20))
    stress, _, _ = simulate_broker_swing(_entries(dates), _states(dates), bars, _policy(friction_bps=100))

    assert base.iloc[0]["entry_date"] == stress.iloc[0]["entry_date"]
    assert base.iloc[0]["exit_date"] == stress.iloc[0]["exit_date"]
    assert stress.iloc[0]["net_pnl_vnd"] < base.iloc[0]["net_pnl_vnd"]


def test_same_close_gain_and_loss_are_aggregated_before_first_profit():
    dates, bars = _market(tickers=("WIN", "LOSS"), sessions=20)
    bars.loc[bars["date"].eq(dates[3]) & bars["ticker"].eq("WIN"), "close"] = 110.0
    bars.loc[bars["date"].eq(dates[3]) & bars["ticker"].eq("LOSS"), "close"] = 80.0
    entries = _entries(dates, [(0, "WIN", .04, 3), (0, "LOSS", .03, 3)])
    states = _states(dates, [(0, "WIN", 2, -.01), (0, "LOSS", 2, -.01)])

    trades, _, metrics = simulate_broker_swing(entries, states, bars, _policy())

    assert trades["net_pnl_vnd"].tolist() == [1000.0, -2000.0]
    assert metrics["first_positive_realized_pnl_date"] is None
    assert metrics["realized_pnl_vnd_at_20_sessions"] == -1000
    assert metrics["profitable_realized_session_fraction"] == 0.0


def test_empty_entry_period_still_reports_every_cash_nav_session():
    dates, bars = _market(sessions=20)
    entries = _entries(dates).iloc[:0]

    trades, nav, metrics = simulate_broker_swing(entries, pd.DataFrame(), bars, _policy())

    assert trades.empty
    assert nav["date"].tolist() == list(dates)
    assert nav["total_nav"].eq(100_000).all()
    assert metrics["coverage"]["simulated_sessions"] == 20
    assert metrics["realized_pnl_vnd_at_20_sessions"] == 0.0
    assert metrics["sessions_to_first_positive_realized_pnl"] is None
    assert metrics["capital_days_vnd"] == 0.0
    assert metrics["net_profit_per_capital_day"] is None


def test_delayed_candidate_profit_clock_starts_at_period_beginning():
    dates, bars = _market(sessions=30)
    entries = _entries(dates, [(18, "HPG", .03, 3)])
    states = _states(dates, [(18, "HPG", 2, -.01)])
    bars.loc[bars["date"].eq(dates[21]) & bars["ticker"].eq("HPG"), "close"] = 110

    trades, nav, metrics = simulate_broker_swing(entries, states, bars, _policy())

    assert len(trades) == 1
    assert len(nav) == 30
    assert metrics["first_positive_realized_pnl_date"] == dates[21].date().isoformat()
    assert metrics["sessions_to_first_positive_realized_pnl"] == 21
    assert metrics["realized_pnl_vnd_at_20_sessions"] == 0.0
    assert metrics["profitable_realized_session_fraction"] == pytest.approx(9 / 30)


def test_sale_receivable_is_unavailable_at_settlement_day_open():
    dates, bars = _market(tickers=("A", "B", "C"))
    policy = _policy(initial_cash=10_000, max_single_weight=1, risk_budget_per_position=1,
                     liquidity_participation=1)
    entries = _entries(dates, [(0, "A", .03, 3), (4, "B", .03, 3), (5, "C", .03, 3)])

    trades, _, metrics = simulate_broker_swing(entries, _states(dates, [(0, "A", 2, -.01)]), bars, policy)

    assert trades["ticker"].tolist() == ["A"]
    assert metrics["open_positions"][0]["ticker"] == "C"
    assert metrics["coverage"]["entry_rejections"] == {"INSUFFICIENT_CAPITAL_OR_LIQUIDITY": 1}


def test_market_risk_continuously_reduces_size_and_edge_per_day_orders_entries():
    dates, bars = _market(tickers=("FAST", "SLOW"))
    entries = _entries(dates, [(0, "SLOW", .05, 7), (0, "FAST", .03, 3)])
    ordered_trades, _, ordered = simulate_broker_swing(entries, pd.DataFrame(), bars, _policy(max_positions=1))
    assert ordered_trades["ticker"].tolist() == ["FAST"]
    assert ordered["per_expert"][0]["closed_trades"] == 1
    assert ordered["coverage"]["entry_rejections"] == {"POSITION_CAP": 1}
    dates, bars = _market()
    low, _, _ = simulate_broker_swing(_entries(dates), _states(dates), bars, _policy())
    risky = _entries(dates)
    risky["market_risk_probability"] = 1.0
    high, _, _ = simulate_broker_swing(risky, _states(dates), bars, _policy())
    assert low.iloc[0]["shares"] == 100
    assert high.iloc[0]["shares"] == 25


def test_entry_sizing_uses_prior_close_nav_and_decision_time_liquidity():
    dates, bars = _market(tickers=("A", "B"))
    bars.loc[bars["date"].eq(dates[2]) & bars["ticker"].eq("A"), "close"] = 1000
    entries = _entries(dates, [(0, "A", .03, 3), (1, "B", .03, 3)])
    uncapped, _, _ = simulate_broker_swing(entries, pd.DataFrame(), bars, _policy(liquidity_participation=.1))
    entries.loc[entries["ticker"].eq("B"), "adtv20_shares"] = 700

    trades, _, _ = simulate_broker_swing(entries, pd.DataFrame(), bars, _policy(liquidity_participation=.1))

    # B's risk budget uses A's preceding 100 close, never its later 1000 close.
    # Decision-time ADTV further caps the order at 10% of 700 shares.
    assert uncapped.loc[uncapped["ticker"].eq("B"), "shares"].iloc[0] == 100
    assert trades.loc[trades["ticker"].eq("B"), "shares"].iloc[0] == 70


@pytest.mark.parametrize("holding", [2.9, 7.1, float("inf")])
def test_invalid_expected_holding_sessions_rejected(holding):
    dates, bars = _market()
    entries = _entries(dates, [(0, "HPG", .03, holding)])
    trades, _, metrics = simulate_broker_swing(entries, pd.DataFrame(), bars, _policy())
    assert trades.empty
    assert metrics["coverage"]["entry_rejections"] == {"INVALID_ENTRY_FORECAST": 1}


def test_action_conflict_and_same_day_state_timestamp_are_rejected():
    dates, bars = _market()
    states = _states(dates)
    states["action"] = "HOLD"
    _, _, conflict = simulate_broker_swing(_entries(dates), states, bars, _policy())
    states["action"] = "EXIT"
    states["state_date"] = dates[1]
    _, _, mistimed = simulate_broker_swing(_entries(dates), states, bars, _policy())
    assert conflict["coverage"]["exit_state_rejections"] == {"INVALID_EXIT_STATE": 1}
    assert mistimed["coverage"]["exit_state_rejections"] == {"INVALID_EXIT_STATE": 1}
