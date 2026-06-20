"""Tests for HOSE transaction cost model."""
import math

import pytest

from app.domain.services.backtest.cost_model import (
    HOSE_COST_PARAMS,
    estimate_cost,
    get_hose_price_step,
    round_to_lot,
    snap_to_price_step,
)


class TestGetHosePriceStep:
    def test_under_10k(self):
        assert get_hose_price_step(9_000) == 10
        assert get_hose_price_step(10_000) == 10

    def test_under_50k(self):
        assert get_hose_price_step(15_000) == 50
        assert get_hose_price_step(50_000) == 50

    def test_under_100k(self):
        assert get_hose_price_step(55_000) == 100
        assert get_hose_price_step(100_000) == 100

    def test_under_200k(self):
        assert get_hose_price_step(150_000) == 500
        assert get_hose_price_step(200_000) == 500

    def test_over_200k(self):
        assert get_hose_price_step(250_000) == 1_000
        assert get_hose_price_step(1_000_000) == 1_000


class TestSnapToPriceStep:
    def test_buy_rounds_down(self):
        result = snap_to_price_step(15_730, "BUY")
        assert result == 15_700

    def test_sell_rounds_up(self):
        result = snap_to_price_step(15_730, "SELL")
        assert result == 15_750

    def test_at_exact_step(self):
        result = snap_to_price_step(15_000, "BUY")
        assert result == 15_000

    def test_high_price_buy(self):
        result = snap_to_price_step(210_000, "BUY")
        assert result == 210_000

    def test_high_price_sell(self):
        result = snap_to_price_step(210_100, "SELL")
        assert result == 211_000


class TestRoundToLot:
    def test_exact_lot(self):
        assert round_to_lot(100) == 100
        assert round_to_lot(500) == 500

    def test_rounds_down_to_lot(self):
        assert round_to_lot(150) == 100
        assert round_to_lot(199) == 100
        assert round_to_lot(101) == 100

    def test_below_one_lot(self):
        assert round_to_lot(50) == 0
        assert round_to_lot(99) == 0

    def test_custom_lot_size(self):
        assert round_to_lot(150, lot_size=50) == 150
        assert round_to_lot(160, lot_size=50) == 150


class TestEstimateCost:
    def test_buy_cost_breakdown(self):
        cost = estimate_cost("BUY", price=20_000, quantity=100)
        notional = 20_000 * 100
        expected_brokerage = max(notional * 0.001, 10_000)
        assert cost["brokerage"] == expected_brokerage
        assert cost["tax"] == 0
        assert cost["total_cost"] > 0

    def test_sell_includes_tax(self):
        cost = estimate_cost("SELL", price=20_000, quantity=100)
        assert cost["tax"] > 0
        expected_tax = 20_000 * 100 * 0.001
        assert cost["tax"] == expected_tax

    def test_buy_no_tax(self):
        cost = estimate_cost("BUY", price=20_000, quantity=100)
        assert cost["tax"] == 0

    def test_min_brokerage_fee(self):
        cost = estimate_cost("BUY", price=5_000, quantity=100)
        assert cost["brokerage"] == 10_000

    def test_round_trip_cost_pct(self):
        notional = 50_000 * 1000
        buy_cost = estimate_cost("BUY", price=50_000, quantity=1000, adv_20d=notional * 100)
        sell_cost = estimate_cost("SELL", price=50_000, quantity=1000, adv_20d=notional * 100)
        total_pct = buy_cost["total_cost_pct"] + sell_cost["total_cost_pct"]
        assert 0.002 <= total_pct <= 0.015, (
            f"Round-trip cost {total_pct:.4f} should be 0.2%-1.5%"
        )

    def test_adv_reduces_impact(self):
        low_adv = estimate_cost("BUY", price=50_000, quantity=100, adv_20d=1_000_000_000)
        high_adv = estimate_cost("BUY", price=50_000, quantity=100, adv_20d=100_000_000_000)
        assert low_adv["slippage"] >= high_adv["slippage"], (
            "Lower ADV should cause higher slippage"
        )

    def test_larger_order_higher_cost(self):
        small = estimate_cost("BUY", price=20_000, quantity=100)
        large = estimate_cost("BUY", price=20_000, quantity=1000)
        assert large["total_cost"] > small["total_cost"]

    def test_min_slippage_at_least_one_tick(self):
        cost = estimate_cost("BUY", price=20_000, quantity=100)
        price_step = get_hose_price_step(20_000)
        min_expected_slippage = price_step / 20_000
        assert cost["slippage"] / (20_000 * 100) >= min_expected_slippage - 1e-10

    def test_notional_zero(self):
        cost = estimate_cost("BUY", 0, 0)
        assert cost["total_cost"] == 0 or cost["total_cost"] == 10_000
