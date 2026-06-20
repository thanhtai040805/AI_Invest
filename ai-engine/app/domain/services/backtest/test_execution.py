"""Tests for HOSE Execution Model (T+2, lock limit, fill price)."""
from datetime import date

import pytest

from app.domain.services.backtest.execution import (
    HOSEExecutionModel,
    count_trading_days,
    is_trading_day,
    next_trading_day,
    prev_trading_day,
)


class TestTradingCalendar:
    def test_weekday_is_trading(self):
        assert is_trading_day(date(2024, 3, 4))  # Monday

    def test_weekend_not_trading(self):
        assert not is_trading_day(date(2024, 3, 2))  # Saturday
        assert not is_trading_day(date(2024, 3, 3))  # Sunday

    def test_tet_holiday_not_trading(self):
        assert not is_trading_day(date(2024, 2, 9))  # Tet 2024 holiday

    def test_next_trading_day(self):
        friday = date(2024, 3, 1)
        nxt = next_trading_day(friday)
        assert nxt == date(2024, 3, 4)  # Monday

    def test_next_trading_day_after_holiday(self):
        before_tet = date(2024, 2, 7)  # Before Tet 2024
        nxt = next_trading_day(before_tet)
        assert nxt == date(2024, 2, 19)  # After Tet holiday week

    def test_prev_trading_day(self):
        monday = date(2024, 3, 4)
        prev = prev_trading_day(monday)
        assert prev == date(2024, 3, 1)  # Friday

    def test_count_trading_days_same_day(self):
        assert count_trading_days(date(2024, 3, 5), date(2024, 3, 5)) == 1

    def test_count_trading_days_span_weekend(self):
        count = count_trading_days(date(2024, 3, 1), date(2024, 3, 5))
        assert count == 3  # Fri, Mon, Tue


class TestHOSEExecutionModel:
    def test_can_sell_same_day_false(self):
        model = HOSEExecutionModel()
        buy = date(2024, 3, 4)  # Monday
        sell = date(2024, 3, 4)
        assert not model.can_sell("VNM", buy, sell)

    def test_can_sell_t_plus_1_false(self):
        model = HOSEExecutionModel()
        buy = date(2024, 3, 4)  # Monday
        sell = date(2024, 3, 5)  # Tuesday (T+1)
        assert not model.can_sell("VNM", buy, sell)

    def test_can_sell_t_plus_2_true(self):
        model = HOSEExecutionModel()
        buy = date(2024, 3, 4)  # Monday
        sell = date(2024, 3, 6)  # Wednesday (T+2)
        assert model.can_sell("VNM", buy, sell)

    def test_can_sell_t_plus_1_after_weekend_false(self):
        model = HOSEExecutionModel()
        buy = date(2024, 3, 1)  # Friday
        sell = date(2024, 3, 4)  # Monday (T+1 trading day)
        assert not model.can_sell("VNM", buy, sell)

    def test_can_sell_t_plus_2_cross_weekend(self):
        model = HOSEExecutionModel()
        buy = date(2024, 3, 1)  # Friday
        sell = date(2024, 3, 5)  # Tuesday (T+2 trading days)
        assert model.can_sell("VNM", buy, sell)

    def test_get_fill_price_atc(self):
        ref_func = lambda s, d: 20000.0
        model = HOSEExecutionModel(reference_price_func=ref_func)
        price = model.get_fill_price("VNM", date(2024, 3, 4), session="ATC", side="BUY")
        assert price is not None
        assert price > 20000

    def test_get_fill_price_none_without_ref(self):
        model = HOSEExecutionModel()
        price = model.get_fill_price("VNM", date(2024, 3, 4))
        assert price is None

    def test_handle_lock_limit_buy_normal(self):
        ref_func = lambda s, d: 20000.0
        model = HOSEExecutionModel(reference_price_func=ref_func)
        can_fill, ratio = model.handle_lock_limit("VNM", date(2024, 3, 4), "BUY")
        assert can_fill
        assert ratio == 1.0

    def test_handle_lock_limit_buy_near_ceiling(self):
        ref_func = lambda s, d: 21000.0  # ~5% up from 20000 ref
        model = HOSEExecutionModel(reference_price_func=ref_func)
        can_fill, ratio = model.handle_lock_limit("VNM", date(2024, 3, 4), "BUY")
        assert can_fill
        assert 0 < ratio <= 1.0

    def test_handle_lock_limit_sell_near_floor(self):
        ref_func = lambda s, d: 19000.0  # ~5% down from 20000 ref
        model = HOSEExecutionModel(reference_price_func=ref_func)
        can_fill, ratio = model.handle_lock_limit("VNM", date(2024, 3, 4), "SELL")
        assert can_fill
        assert 0 < ratio <= 1.0
