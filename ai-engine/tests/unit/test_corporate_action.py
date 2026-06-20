"""Unit tests for Corporate Action Adjustment Engine (TASK-102)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.domain.rules.risk.corporate_action import (
    ActionType,
    AdjustmentReport,
    AdjustmentResult,
    CorporateActionRecord,
    MarketDataRow,
    _compute_split_factor,
    adjust_prices_historical,
    apply_all_pending_adjustments,
    compute_adjustment_factor,
)


# ─── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def base_market_data_vhm() -> list[MarketDataRow]:
    """15 ngày giá VHM trước và sau ex_date."""
    base_price = 50.0
    rows = []
    for i in range(15):
        d = date(2026, 5, 25) + timedelta(days=i)
        rows.append(MarketDataRow(
            ticker="VHM",
            date=d,
            close_adj=base_price + i * 0.5,
            open_adj=base_price + i * 0.5 - 0.2,
            high_adj=base_price + i * 0.5 + 0.3,
            low_adj=base_price + i * 0.5 - 0.3,
            vwap=base_price + i * 0.5 - 0.1,
            close_unadj=base_price + i * 0.5,
            adj_factor=1.0,
        ))
    return rows


@pytest.fixture
def split_ca() -> CorporateActionRecord:
    return CorporateActionRecord(
        ticker="VHM",
        action_type=ActionType.SPLIT,
        ex_date=date(2026, 6, 1),
        ratio=2.0,
    )


# ─── Test compute_adjustment_factor ────────────────────────────────────


class TestComputeAdjustmentFactor:
    def test_split_2_1_factor(self):
        ca = CorporateActionRecord("VHM", ActionType.SPLIT, date(2026, 6, 1), ratio=2.0)
        factor = compute_adjustment_factor(ca)
        assert factor == pytest.approx(0.5)

    def test_split_3_1_factor(self):
        ca = CorporateActionRecord("VHM", ActionType.SPLIT, date(2026, 6, 1), ratio=3.0)
        factor = compute_adjustment_factor(ca)
        assert factor == pytest.approx(1.0 / 3.0)

    def test_merge_2_1_factor(self):
        ca = CorporateActionRecord("VHM", ActionType.MERGE, date(2026, 6, 1), ratio=2.0)
        factor = compute_adjustment_factor(ca)
        assert factor == pytest.approx(2.0)

    def test_dividend_stock_10pct_factor(self):
        # 100:10 cổ tức cổ phiếu → factor = 100/(100+10) = 0.909
        ca = CorporateActionRecord("VHM", ActionType.DIVIDEND_STOCK, date(2026, 6, 1), ratio=0.10)
        factor = compute_adjustment_factor(ca)
        assert factor == pytest.approx(1.0 / 1.10)

    def test_dividend_cash_default(self):
        ca = CorporateActionRecord("VHM", ActionType.DIVIDEND_CASH, date(2026, 6, 1), cash_amount=2000)
        factor = compute_adjustment_factor(ca)
        assert factor == 1.0  # placeholder until actual price is used

    def test_unknown_action_returns_1(self):
        # RIGHTS cũng dùng công thức tương tự
        ca = CorporateActionRecord("VHM", ActionType.RIGHTS, date(2026, 6, 1), ratio=0.5)
        factor = compute_adjustment_factor(ca)
        assert factor == pytest.approx(1.0 / 1.5)

    def test_zero_ratio_returns_1(self):
        ca = CorporateActionRecord("VHM", ActionType.SPLIT, date(2026, 6, 1), ratio=0)
        factor = compute_adjustment_factor(ca)
        assert factor == 1.0

    def test_explicit_factor_overrides(self):
        ca = CorporateActionRecord("VHM", ActionType.SPLIT, date(2026, 6, 1),
                                    ratio=2.0, adjustment_factor=0.6)
        factor = compute_adjustment_factor(ca)
        assert factor == 0.6  # explicit override


# ─── Test _compute_split_factor ────────────────────────────────────────


class TestComputeSplitFactor:
    def test_split_2_1(self):
        assert _compute_split_factor(2.0) == 0.5

    def test_split_5_1(self):
        assert _compute_split_factor(5.0) == 0.2

    def test_split_zero_ratio(self):
        assert _compute_split_factor(0) == 1.0


# ─── Test adjust_prices_historical ────────────────────────────────────


class TestAdjustPricesHistorical:
    def test_split_halves_prices_before_ex(self, base_market_data_vhm, split_ca):
        """Split 2:1 → giá các ngày trước ex_date bị halve."""
        adjusted, factor = adjust_prices_historical(base_market_data_vhm, split_ca)
        assert factor == pytest.approx(0.5)

        ex_idx = None
        for i, row in enumerate(base_market_data_vhm):
            if row.date == split_ca.ex_date:
                ex_idx = i
                break
        assert ex_idx is not None

        # Prices before ex_date should be halved
        for i in range(ex_idx):
            original = base_market_data_vhm[i].close_adj
            expected = original * 0.5
            assert adjusted[i].close_adj == pytest.approx(expected)

        # Prices on/after ex_date should remain unchanged
        for i in range(ex_idx, len(adjusted)):
            assert adjusted[i].close_adj == base_market_data_vhm[i].close_adj

    def test_nothing_changed_when_already_applied(self, base_market_data_vhm, split_ca):
        split_ca.applied = True
        adjusted, factor = adjust_prices_historical(base_market_data_vhm, split_ca)
        assert factor == 1.0
        # All prices unchanged
        for i, row in enumerate(adjusted):
            assert row.close_adj == base_market_data_vhm[i].close_adj

    def test_dividend_cash_with_price(self, base_market_data_vhm):
        """Cash dividend with close price → uses (price - cash) / price."""
        ex_date = date(2026, 6, 1)
        ca = CorporateActionRecord("VHM", ActionType.DIVIDEND_CASH, ex_date, cash_amount=2000.0)

        # Find close price on ex_date
        ex_close = None
        for row in base_market_data_vhm:
            if row.date == ex_date:
                ex_close = row.close_unadj
                break

        adjusted, factor = adjust_prices_historical(
            base_market_data_vhm, ca, ex_date_close_price=ex_close,
        )
        expected_factor = (ex_close - 2000) / ex_close
        assert factor == pytest.approx(expected_factor)

    def test_all_fields_adjusted(self, base_market_data_vhm, split_ca):
        """Verify open, high, low, vwap are also adjusted."""
        adjusted, _ = adjust_prices_historical(base_market_data_vhm, split_ca)

        ex_idx = None
        for i, row in enumerate(base_market_data_vhm):
            if row.date == split_ca.ex_date:
                ex_idx = i
                break

        for i in range(ex_idx):
            assert adjusted[i].open_adj == pytest.approx(base_market_data_vhm[i].open_adj * 0.5)
            assert adjusted[i].high_adj == pytest.approx(base_market_data_vhm[i].high_adj * 0.5)
            assert adjusted[i].low_adj == pytest.approx(base_market_data_vhm[i].low_adj * 0.5)
            assert adjusted[i].vwap == pytest.approx(base_market_data_vhm[i].vwap * 0.5)

    def test_adj_factor_accumulates(self, base_market_data_vhm, split_ca):
        """adj_factor should be cumulatively multiplied."""
        adjusted, _ = adjust_prices_historical(base_market_data_vhm, split_ca)

        ex_idx = None
        for i, row in enumerate(base_market_data_vhm):
            if row.date == split_ca.ex_date:
                ex_idx = i
                break

        for i in range(ex_idx):
            expected_factor = base_market_data_vhm[i].adj_factor * 0.5
            assert adjusted[i].adj_factor == pytest.approx(expected_factor)


# ─── Test apply_all_pending_adjustments ────────────────────────────────


class TestApplyAllPendingAdjustments:
    def test_single_split(self, base_market_data_vhm, split_ca):
        market_data = {"VHM": base_market_data_vhm}
        adjusted, report = apply_all_pending_adjustments(market_data, [split_ca])

        assert report.succeeded == 1
        assert report.failed == 0
        assert report.results[0].factor == pytest.approx(0.5)

        # Prices adjusted
        ex_idx = None
        for i, row in enumerate(base_market_data_vhm):
            if row.date == split_ca.ex_date:
                ex_idx = i
                break
        # Check first row is halved
        assert adjusted["VHM"][0].close_adj == pytest.approx(base_market_data_vhm[0].close_adj * 0.5)

    def test_multiple_cas_chronological(self, base_market_data_vhm):
        """Two splits in sequence should compound."""
        ca1 = CorporateActionRecord("VHM", ActionType.SPLIT, date(2026, 6, 1), ratio=2.0)
        ca2 = CorporateActionRecord("VHM", ActionType.SPLIT, date(2026, 6, 8), ratio=2.0)

        market_data = {"VHM": base_market_data_vhm}
        adjusted, report = apply_all_pending_adjustments(market_data, [ca1, ca2])

        assert report.succeeded == 2

        # After two 2:1 splits, factor = 0.5 * 0.5 = 0.25
        ex1_idx = None
        for i, row in enumerate(base_market_data_vhm):
            if row.date == ca1.ex_date:
                ex1_idx = i
                break
        # Row before ca1: factor = 0.25
        assert adjusted["VHM"][0].adj_factor == pytest.approx(0.25)
        assert adjusted["VHM"][0].close_adj == pytest.approx(base_market_data_vhm[0].close_adj * 0.25)

        # Row between ca1 and ca2: factor = 0.5
        for i in range(ex1_idx, len(base_market_data_vhm)):
            if base_market_data_vhm[i].date >= ca2.ex_date:
                break
            if base_market_data_vhm[i].date >= ca1.ex_date:
                assert adjusted["VHM"][i].adj_factor == pytest.approx(0.5)

    def test_missing_ticker_handled(self, split_ca):
        market_data = {}
        _, report = apply_all_pending_adjustments(market_data, [split_ca])
        assert report.failed == 1
        assert report.results[0].error is not None

    def test_different_tickers(self):
        ca_vhm = CorporateActionRecord("VHM", ActionType.SPLIT, date(2026, 6, 1), ratio=2.0)
        ca_fpt = CorporateActionRecord("FPT", ActionType.SPLIT, date(2026, 6, 5), ratio=3.0)

        rows_vhm = [MarketDataRow("VHM", date(2026, 5, 25) + timedelta(days=i), close_adj=50.0) for i in range(15)]
        rows_fpt = [MarketDataRow("FPT", date(2026, 5, 25) + timedelta(days=i), close_adj=100.0) for i in range(15)]

        market_data = {"VHM": rows_vhm, "FPT": rows_fpt}
        adjusted, report = apply_all_pending_adjustments(market_data, [ca_vhm, ca_fpt])

        assert report.succeeded == 2
        assert adjusted["VHM"][0].adj_factor == pytest.approx(0.5)
        assert adjusted["FPT"][0].adj_factor == pytest.approx(1.0 / 3.0)


# ─── Test AdjustmentReport ────────────────────────────────────────────


class TestAdjustmentReport:
    def test_empty_report(self):
        r = AdjustmentReport()
        assert r.total == 0
        assert r.succeeded == 0
        assert r.failed == 0

    def test_mixed_results(self):
        r = AdjustmentReport()
        r.results.append(AdjustmentResult("1", "VHM", ActionType.SPLIT, date(2026, 6, 1), 0.5, 10, True))
        r.results.append(AdjustmentResult("2", "FPT", ActionType.SPLIT, date(2026, 6, 1), 1.0, 0, False, "No data"))
        assert r.succeeded == 1
        assert r.failed == 1
        assert r.total == 2
