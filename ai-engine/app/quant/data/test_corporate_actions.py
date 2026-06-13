"""Tests for corporate actions and adjusted price continuity."""
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import pytest


def _compute_adj_factors(prices: list[tuple[date, float]], actions: list[tuple]) -> list[dict[str, Any]]:
    """
    Simulate adj_close computation matching existing adj_close.py logic.

    Dates are processed from newest to oldest, adjusting cumulative factor
    as corporate actions are encountered going backwards.

    On the action date itself, af = current factor (before adjusting for this
    action). For dates BEFORE the action, af = adjusted factor (after including
    this action). This means on the action date, adj_close = raw close.

    Args:
        prices: [(date, close), ...] sorted ASC (oldest first)
        actions: [(date, type, value, ratio), ...] sorted ASC

    Returns:
        List of {date, close_raw, adj_close, adj_factor}
    """
    price_desc = list(reversed(prices))
    acts = defaultdict(list)
    for d, t, v, r in actions:
        acts[d].append((t, float(v or 0), float(r or 0)))

    factor = 1.0
    results_desc = []

    for i, (d, c) in enumerate(price_desc):
        af = factor
        if d in acts:
            for at, v, rv in acts[d]:
                if at == "DIVIDEND" and v > 0:
                    if i + 1 < len(price_desc):
                        pc = price_desc[i + 1][1]
                        if pc > 0:
                            adj = (pc * 1000 - v) / (pc * 1000)
                            factor *= adj
                elif at == "SPLIT" and rv > 0:
                    factor *= 1.0 / rv
                elif at == "STOCK_DIVIDEND" and rv > 0:
                    factor *= 1.0 / (1.0 + rv)
        results_desc.append({
            "date": d,
            "close_raw": c,
            "adj_close": round(c * af, 2),
            "adj_factor": round(af, 6),
        })

    return list(reversed(results_desc))


def _factors_desc(results):
    """Return factors from newest to oldest — should be non-increasing."""
    return [r["adj_factor"] for r in reversed(results)]


class TestAdjCloseContinuity:
    """Kiểm tra adj_close consistency khi có corporate actions."""

    def test_adj_factor_non_increasing_going_backward(self):
        """From newest to oldest, factor should be non-increasing."""
        prices = [
            (date(2020, 3, 4), 19500.0),
            (date(2020, 3, 5), 1000.0),
            (date(2020, 3, 6), 990.0),
        ]
        actions = [
            (date(2020, 3, 5), "DIVIDEND", 19000.0, 0.0),
        ]
        results = _compute_adj_factors(prices, actions)
        factors_desc = _factors_desc(results)
        for i in range(1, len(factors_desc)):
            assert factors_desc[i] <= factors_desc[i - 1] + 1e-10, (
                f"Factor should be non-increasing backward: {factors_desc[i-1]} -> {factors_desc[i]}"
            )

    def test_adj_factor_decreases_on_split_backward(self):
        prices = [
            (date(2020, 2, 1), 49000.0),
            (date(2020, 2, 2), 24500.0),
            (date(2020, 2, 3), 24800.0),
        ]
        actions = [
            (date(2020, 2, 2), "SPLIT", 0.0, 2.0),
        ]
        results = _compute_adj_factors(prices, actions)
        factors_desc = _factors_desc(results)
        for i in range(1, len(factors_desc)):
            assert factors_desc[i] <= factors_desc[i - 1] + 1e-10

    def test_adj_factor_non_increasing_backward_multi_action(self):
        prices = [
            (date(2020, 1, 1) + timedelta(days=i), 10000.0 + i * 100) for i in range(50)
        ]
        actions = [
            (date(2020, 1, 10), "DIVIDEND", 500.0, 0.0),
            (date(2020, 1, 20), "STOCK_DIVIDEND", 0.0, 0.05),
            (date(2020, 2, 1), "SPLIT", 0.0, 1.5),
            (date(2020, 2, 10), "DIVIDEND", 300.0, 0.0),
        ]
        results = _compute_adj_factors(prices, actions)
        factors_desc = _factors_desc(results)
        for i in range(1, len(factors_desc)):
            assert factors_desc[i] <= factors_desc[i - 1] + 1e-10, (
                f"Backward factor non-increasing failed at index {i}: "
                f"{factors_desc[i-1]} -> {factors_desc[i]}"
            )

    def test_adj_factor_matches_expected_dividend_adjustment(self):
        pc = 20000.0
        dividend = 1000.0
        expected_adj = (pc * 1000 - dividend) / (pc * 1000)

        prices = [
            (date(2020, 1, 1), pc),
            (date(2020, 1, 2), 19000.0),
        ]
        actions = [
            (date(2020, 1, 2), "DIVIDEND", dividend, 0.0),
        ]
        results = _compute_adj_factors(prices, actions)

        pre_factor = results[0]["adj_factor"]
        assert abs(pre_factor - expected_adj) < 1e-6, (
            f"Pre-dividend adj_factor should be {expected_adj:.6f}, got {pre_factor:.6f}"
        )

    def test_adj_factor_matches_expected_split_adjustment(self):
        split_ratio = 2.0
        expected_adj = 1.0 / split_ratio

        prices = [
            (date(2020, 1, 1), 50000.0),
            (date(2020, 2, 1), 25000.0),
        ]
        actions = [
            (date(2020, 2, 1), "SPLIT", 0.0, split_ratio),
        ]
        results = _compute_adj_factors(prices, actions)

        pre_factor = results[0]["adj_factor"]
        assert abs(pre_factor - expected_adj) < 1e-6, (
            f"Pre-split adj_factor should be {expected_adj:.6f}, got {pre_factor:.6f}"
        )

    def test_adj_factor_matches_expected_stock_dividend(self):
        ratio = 0.1
        expected_adj = 1.0 / (1.0 + ratio)

        prices = [
            (date(2020, 1, 1), 30000.0),
            (date(2020, 2, 1), 27300.0),
        ]
        actions = [
            (date(2020, 2, 1), "STOCK_DIVIDEND", 0.0, ratio),
        ]
        results = _compute_adj_factors(prices, actions)

        pre_factor = results[0]["adj_factor"]
        assert abs(pre_factor - expected_adj) < 1e-6, (
            f"Pre-stock-dividend adj_factor should be {expected_adj:.6f}, got {pre_factor:.6f}"
        )

    def test_adj_close_at_action_date_is_raw_close(self):
        prices = [
            (date(2020, 1, 1), 20000.0),
            (date(2020, 1, 5), 19500.0),
            (date(2020, 1, 10), 10000.0),
        ]
        actions = [
            (date(2020, 1, 10), "DIVIDEND", 9000.0, 0.0),
        ]
        results = _compute_adj_factors(prices, actions)

        action_result = [r for r in results if r["date"] == date(2020, 1, 10)][0]
        assert action_result["adj_close"] == action_result["close_raw"], (
            "On action date, adj_close should equal raw close"
        )

    def test_pre_action_dates_have_adj_factor_below_one(self):
        """Pre-action dates should have adj_factor < 1.0 (adjusted down)."""
        prices = [
            (date(2020, 1, 1), 20000.0),
            (date(2020, 1, 2), 20500.0),
            (date(2020, 1, 10), 19500.0),
            (date(2020, 1, 11), 19600.0),
        ]
        actions = [
            (date(2020, 1, 10), "DIVIDEND", 500.0, 0.0),
        ]
        results = _compute_adj_factors(prices, actions)

        pre_action = [r for r in results if r["date"] < date(2020, 1, 10)]
        for r in pre_action:
            assert r["adj_factor"] < 1.0, (
                f"Pre-action dates should have adj_factor < 1.0"
            )

    def test_dividend_on_flat_price(self):
        prices = [
            (date(2020, 1, 1), 10000.0),
            (date(2020, 1, 2), 10000.0),
            (date(2020, 1, 3), 9000.0),
        ]
        actions = [
            (date(2020, 1, 3), "DIVIDEND", 1000.0, 0.0),
        ]
        results = _compute_adj_factors(prices, actions)

        pre_factor = results[0]["adj_factor"]
        expected = (10000 * 1000 - 1000) / (10000 * 1000)
        assert abs(pre_factor - expected) < 1e-6
