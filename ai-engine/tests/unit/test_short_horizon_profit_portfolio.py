from dataclasses import replace

import pandas as pd
import pytest

from experiments.short_horizon_profit_portfolio import PortfolioPolicy, simulate_portfolio


def _market(tickers, sessions=20):
    dates = pd.bdate_range("2026-01-02", periods=sessions)
    bars = pd.DataFrame([
        {"date": day, "ticker": ticker, "open": 100.0, "close": 100.0, "volume_continuous": 10_000.0}
        for day in dates for ticker in ["VNINDEX", *tickers]
    ])
    return dates, bars


def _forecasts(dates, signals):
    return pd.DataFrame([
        {"decision_date": dates[session], "ticker": ticker, "p_profit": 0.7,
         "expected_net_return": expected, "downside_q10": -0.05,
         "adtv20_shares": 10_000.0, "horizon_sessions": horizon}
        for session, ticker, expected, horizon in signals
    ], columns=["decision_date", "ticker", "p_profit", "expected_net_return",
                "downside_q10", "adtv20_shares", "horizon_sessions"])


def _policy(**changes):
    return replace(PortfolioPolicy(), initial_cash=100_000.0, price_scale=1.0,
                   friction_bps=0.0, brokerage_fee_rate=0.0, sell_tax_rate=0.0,
                   minimum_fee=0.0, min_notional=1.0, lot_size=1,
                   risk_budget_per_position=0.1, cash_buffer=0.0, **changes)


def test_same_close_gain_offset_by_loss_is_not_early_profit():
    dates, bars = _market(["WIN", "LOSS"])
    bars.loc[bars["date"].eq(dates[3]) & bars["ticker"].eq("WIN"), "close"] = 110.0
    bars.loc[bars["date"].eq(dates[3]) & bars["ticker"].eq("LOSS"), "close"] = 80.0
    forecasts = _forecasts(dates, [(0, "WIN", 0.10, 3), (0, "LOSS", 0.09, 3)])

    trades, _, metrics = simulate_portfolio(forecasts, bars, _policy())

    assert trades["net_pnl_vnd"].tolist() == [1000.0, -2000.0]
    assert metrics["first_positive_realized_pnl_date"] is None
    assert metrics["sessions_to_first_positive_realized_pnl"] is None
    assert metrics["realized_pnl_vnd_at_20_sessions"] == -1000.0
    assert metrics["profitable_realized_session_fraction"] == 0.0


def test_realized_pnl_timing_counts_all_market_sessions_from_decision_close():
    dates, bars = _market(["LOSS", "GAIN"])
    bars.loc[bars["date"].eq(dates[3]) & bars["ticker"].eq("LOSS"), "close"] = 90.0
    bars.loc[bars["date"].eq(dates[7]) & bars["ticker"].eq("GAIN"), "close"] = 120.0
    forecasts = _forecasts(dates, [(0, "LOSS", 0.10, 3), (4, "GAIN", 0.10, 3)])

    trades, nav, metrics = simulate_portfolio(forecasts, bars, _policy())

    assert trades["net_pnl_vnd"].tolist() == [-1000.0, 1980.0]
    assert len(nav) == 20
    assert metrics["first_positive_realized_pnl_date"] == dates[7].date().isoformat()
    assert metrics["sessions_to_first_positive_realized_pnl"] == 7
    assert metrics["realized_pnl_vnd_at_20_sessions"] == 980.0
    assert metrics["profitable_realized_session_fraction"] == pytest.approx(13 / 20)
    assert metrics["mean_actual_holding_sessions"] == 3.0
    assert metrics["max_actual_holding_sessions"] == 3
    assert metrics["trades_exceeding_planned_horizon"] == 0


@pytest.mark.parametrize("sessions, expected_20_session_pnl", [(10, None), (20, 0.0)])
def test_no_trade_metrics_include_empty_sessions_and_short_sample(sessions, expected_20_session_pnl):
    dates, bars = _market(["HPG"], sessions=sessions)
    forecasts = _forecasts(dates, [(0, "HPG", -0.01, 3)])

    trades, nav, metrics = simulate_portfolio(forecasts, bars, _policy())

    assert trades.empty
    assert len(nav) == sessions
    assert metrics["first_positive_realized_pnl_date"] is None
    assert metrics["sessions_to_first_positive_realized_pnl"] is None
    assert metrics["realized_pnl_vnd_at_20_sessions"] == expected_20_session_pnl
    assert metrics["profitable_realized_session_fraction"] == 0.0
    assert metrics["mean_actual_holding_sessions"] is None
    assert metrics["max_actual_holding_sessions"] is None
    assert metrics["trades_exceeding_planned_horizon"] == 0


def test_empty_forecasts_have_no_simulated_profit_or_holding_timing():
    dates, bars = _market(["HPG"])

    trades, nav, metrics = simulate_portfolio(_forecasts(dates, []), bars, _policy())

    assert trades.empty and nav.empty
    assert metrics["first_positive_realized_pnl_date"] is None
    assert metrics["sessions_to_first_positive_realized_pnl"] is None
    assert metrics["realized_pnl_vnd_at_20_sessions"] is None
    assert metrics["profitable_realized_session_fraction"] == 0.0
    assert metrics["mean_actual_holding_sessions"] is None
    assert metrics["max_actual_holding_sessions"] is None
    assert metrics["trades_exceeding_planned_horizon"] == 0


@pytest.mark.parametrize("horizon, minimum, policy_horizon", [
    (3, 3, 3), (5, 3, 5), (5, 4, 5), (5, 5, 5), (5, 3, 3), (3, 3, 5),
])
def test_published_horizons_accept_consistent_minimum_holding_period(horizon, minimum, policy_horizon):
    dates, bars = _market(["HPG"], sessions=10)
    forecasts = _forecasts(dates, [(0, "HPG", 0.10, horizon)])

    trades, _, metrics = simulate_portfolio(
        forecasts, bars, _policy(holding_sessions=policy_horizon, minimum_holding_sessions=minimum)
    )

    assert len(trades) == 1
    assert trades.iloc[0]["exit_date"] == dates[horizon]
    assert metrics["mean_actual_holding_sessions"] == horizon


@pytest.mark.parametrize("horizon, minimum", [(4, 3), (20, 3), (3, 4), (5, 6)])
def test_policy_rejects_unpublished_or_inconsistent_horizons(horizon, minimum):
    with pytest.raises(ValueError, match="Planned holding horizons"):
        _policy(holding_sessions=horizon, minimum_holding_sessions=minimum).validate()


@pytest.mark.parametrize("horizon", [4, 20, 100])
def test_forecast_override_rejects_unpublished_horizons(horizon):
    dates, bars = _market(["HPG"], sessions=10)
    forecasts = _forecasts(dates, [(0, "HPG", 0.10, horizon)])

    trades, _, metrics = simulate_portfolio(forecasts, bars, _policy())

    assert trades.empty
    assert metrics["coverage"]["rejections"] == {"INVALID_FORECAST": 1}


def test_missing_executable_exit_is_deferred_and_actual_holding_is_reported():
    dates, bars = _market(["HPG"], sessions=10)
    planned_exit = bars["date"].eq(dates[3]) & bars["ticker"].eq("HPG")
    bars.loc[planned_exit, "volume_continuous"] = 0.0
    bars.loc[bars["date"].eq(dates[4]) & bars["ticker"].eq("HPG"), "close"] = float("nan")
    bars.loc[bars["date"].eq(dates[5]) & bars["ticker"].eq("HPG"), "close"] = 110.0
    forecasts = _forecasts(dates, [(0, "HPG", 0.10, 3)])

    trades, _, metrics = simulate_portfolio(forecasts, bars, _policy())

    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["planned_exit_date"] == dates[3]
    assert trade["exit_date"] == dates[5]
    assert trade["holding_sessions"] == 3
    assert trade["actual_holding_sessions"] == 5
    assert trade["net_pnl_vnd"] == 1000.0
    assert metrics["mean_actual_holding_sessions"] == 5.0
    assert metrics["max_actual_holding_sessions"] == 5
    assert metrics["trades_exceeding_planned_horizon"] == 1
    assert metrics["first_positive_realized_pnl_date"] == dates[5].date().isoformat()
    assert metrics["coverage"]["deferred_exit_sessions_by_reason"] == {
        "NO_VALID_EXECUTION_VOLUME": 1, "NO_VALID_CLOSE": 1,
    }
