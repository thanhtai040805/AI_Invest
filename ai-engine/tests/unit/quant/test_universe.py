"""Tests for survivorship-free universe."""
from datetime import date

import pytest

from app.domain.services.quant.data.universe import (
    get_universe_at_date,
    get_universe_history_summary,
)


class TestGetUniverseAtDate:
    def test_2020_universe_contains_delisted_symbols(self):
        universe_2020 = get_universe_at_date(date(2020, 6, 1))
        delisted = {"FTM", "ITA", "HVG", "ROS", "TTF"}
        present = delisted & set(universe_2020)
        assert len(present) >= 3, (
            f"2020 universe should contain many now-delisted symbols, "
            f"only found {present}"
        )

    def test_2024_universe_excludes_delisted(self):
        universe_2024 = get_universe_at_date(date(2024, 12, 31))
        delisted_by_2024 = {"FTM", "ITA", "HVG", "ROS", "TTF"}
        still_present = delisted_by_2024 & set(universe_2024)
        assert len(still_present) < len(delisted_by_2024), (
            f"2024 universe should exclude delisted symbols, "
            f"but found {still_present}"
        )

    def test_universe_differs_by_year(self):
        u2020 = set(get_universe_at_date(date(2020, 6, 1)))
        u2024 = set(get_universe_at_date(date(2024, 12, 31)))
        assert u2020 != u2024, "2020 universe should differ from 2024 universe"

    def test_2020_larger_than_2024(self):
        u2020 = len(get_universe_at_date(date(2020, 6, 1)))
        u2024 = len(get_universe_at_date(date(2024, 12, 31)))
        assert u2020 >= u2024, (
            f"2020 ({u2020}) should have >= symbols than 2024 ({u2024}) "
            f"due to delistings"
        )

    def test_future_date_includes_all(self):
        universe = get_universe_at_date(date(2030, 1, 1))
        summary = get_universe_history_summary()
        active = summary["active"]
        assert len(universe) == active, (
            f"Future universe ({len(universe)}) should include all "
            f"active symbols ({active})"
        )

    def test_contains_major_hose_symbols(self):
        universe = get_universe_at_date(date(2024, 6, 1))
        major = {"VIC", "VNM", "VCB", "TCB", "HPG", "FPT"}
        missing = major - set(universe)
        assert not missing, f"Major HOSE symbols missing: {missing}"

    def test_summary_has_correct_counts(self):
        summary = get_universe_history_summary()
        assert summary["total"] > 0
        assert summary["active"] > summary["delisted"]
        assert len(summary["delisted_symbols"]) == summary["delisted"]


class TestNoSurvivorshipBias:
    def test_delisted_symbols_present_in_historical_data(self):
        universe_2018 = get_universe_at_date(date(2018, 6, 1))
        for sym in ["FTM", "HVG", "ROS"]:
            assert sym in universe_2018, (
                f"Symbol {sym} (now delisted) should be in 2018 universe"
            )

    def test_backtest_can_include_delisted(self):
        sample = {"FTM": date(2023, 12, 19), "HVG": date(2023, 8, 29)}
        for sym, test_date in sample.items():
            before = get_universe_at_date(test_date)
            assert sym in set(before), f"{sym} should be in universe before delisting"

    def test_delisted_excluded_after_date(self):
        assert "FTM" not in set(get_universe_at_date(date(2024, 1, 1)))
        assert "HVG" not in set(get_universe_at_date(date(2023, 9, 1)))
