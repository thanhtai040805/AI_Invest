import pytest
from datetime import date, timedelta
from app.domain.services.backtest.engine import (
    HOSEBacktestEngine,
    Fill,
    Portfolio,
    Position,
    BacktestReport,
    round_to_lot,
)


class TestFill:
    def test_fill_creation(self):
        f = Fill("VNM", 80_000, 100, "BUY", date(2024, 1, 2))
        assert f.symbol == "VNM"
        assert f.price == 80_000


class TestPortfolio:
    def test_initial_state(self):
        p = Portfolio(initial_capital=1_000_000)
        assert p.cash == 1_000_000
        assert p.total_equity == 1_000_000

    def test_apply_buy(self):
        p = Portfolio(1_000_000)
        fill = Fill("VNM", 80_000, 100, "BUY", date(2024, 1, 2))
        cost = {"total_cost": 5000, "brokerage": 4000, "tax": 0, "slippage": 1000}
        p.apply_fill(fill, cost)
        assert "VNM" in p.positions
        assert p.positions["VNM"].quantity == 100
        assert p.cash == 1_000_000 - 80_000 * 100 - 5000

    def test_apply_sell(self):
        p = Portfolio(1_000_000)
        p.positions["VNM"] = Position("VNM", 100, 80_000, date(2024, 1, 2))
        p.cash = 200_000
        fill = Fill("VNM", 85_000, 50, "SELL", date(2024, 1, 9))
        cost = {"total_cost": 3000, "brokerage": 2000, "tax": 850, "slippage": 150}
        p.apply_fill(fill, cost)
        assert p.positions["VNM"].quantity == 50
        assert p.cash == 200_000 + 85_000 * 50 - 3000

    def test_sell_full_removes_position(self):
        p = Portfolio(1_000_000)
        p.positions["VNM"] = Position("VNM", 100, 80_000, date(2024, 1, 2))
        p.cash = 200_000
        fill = Fill("VNM", 85_000, 100, "SELL", date(2024, 1, 9))
        cost = {"total_cost": 5000, "brokerage": 3000, "tax": 850, "slippage": 1150}
        p.apply_fill(fill, cost)
        assert "VNM" not in p.positions


class FakeStrategy:
    def __init__(self, target_weight=0.05):
        self.target_weight = target_weight

    def generate_signals(self, features, current_date):
        return {"VNM": 1.0, "VIC": -0.5, "HPG": 0.8}

    def optimize(self, signals):
        return {k: v * self.target_weight for k, v in signals.items()}


def fake_universe(dt):
    return ["VNM", "VIC", "HPG", "MSN", "FPT"]


def fake_features(universe, dt):
    return {s: {"zscore": 0.5} for s in universe}


def fake_price(sym, dt):
    prices = {"VNM": 80_000, "VIC": 45_000, "HPG": 28_000, "MSN": 75_000, "FPT": 110_000}
    if sym in prices:
        return prices[sym]
    return None


class TestHOSEBacktestEngine:
    def test_engine_creation(self):
        engine = HOSEBacktestEngine(fake_universe, fake_features, fake_price)
        assert engine.execution is not None

    def test_engine_run_short_period(self):
        engine = HOSEBacktestEngine(fake_universe, fake_features, fake_price)
        strat = FakeStrategy(target_weight=0.05)
        report = engine.run(strat, date(2024, 1, 2), date(2024, 1, 10), initial_capital=1_000_000_000)
        assert isinstance(report, BacktestReport)

    def test_engine_run_returns_equity_curve(self):
        engine = HOSEBacktestEngine(fake_universe, fake_features, fake_price)
        strat = FakeStrategy(target_weight=0.02)
        report = engine.run(strat, date(2024, 1, 2), date(2024, 1, 12), initial_capital=1_000_000_000)
        assert hasattr(report, "gross_cagr")
        assert hasattr(report, "total_costs")
