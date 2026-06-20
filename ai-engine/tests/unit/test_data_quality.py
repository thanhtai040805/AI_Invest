"""Unit tests for Data Quality Check Engine (TASK-101)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.domain.rules.risk.data_quality import (
    CheckSeverity,
    CheckStatus,
    DataQualityCheck,
    DataQualityReport,
    check_ohlcv_completeness,
    check_price_limit,
    check_volume_non_negative,
    check_volume_separation,
    check_financial_freshness,
    check_corporate_action_applied,
    check_announcement_date_exists,
    check_point_in_time_integrity,
    run_all_checks,
)


# ─── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def tickers() -> list[str]:
    return ["VHM", "FPT", "MSN", "VNM", "HPG"]


@pytest.fixture
def today() -> date:
    return date(2026, 6, 15)


@pytest.fixture
def base_market_data(today: date, tickers: list[str]) -> list[dict]:
    """5 ticker với data ngày target_date, giá hợp lệ, volume dương."""
    rows = []
    for i, t in enumerate(tickers):
        base_price = 100.0 + i * 10
        rows.append({
            "ticker": t,
            "date": today,
            "open_adj": base_price,
            "high_adj": base_price * 1.02,
            "low_adj": base_price * 0.98,
            "close_adj": base_price,
            "prev_close_adj": base_price,
            "volume_continuous": 1_000_000,
            "volume_atc": 200_000,
            "volume_ato": 50_000,
            "volume_total": 1_250_000,
            "foreign_buy_vol": 100_000,
            "foreign_sell_vol": 50_000,
        })
    return rows


@pytest.fixture
def base_financials(tickers: list[str], today: date) -> list[dict]:
    """5 ticker với BCTC trong 60 ngày gần nhất."""
    rows = []
    for t in tickers:
        rows.append({
            "ticker": t,
            "fiscal_year": 2026,
            "fiscal_quarter": 1,
            "announcement_date": today - timedelta(days=30),
            "revenue": 1_000_000_000_000,
            "net_income": 100_000_000_000,
        })
    return rows


# ─── Test DataQualityReport ───────────────────────────────────────────


class TestDataQualityReport:
    def test_empty_report_returns_skip(self):
        report = DataQualityReport()
        assert report.overall == "SKIP"

    def test_all_pass_returns_pass(self):
        report = DataQualityReport()
        report.add_check(DataQualityCheck(
            check_id="T1", name="Test", severity=CheckSeverity.CRITICAL,
            status=CheckStatus.PASS,
        ))
        assert report.overall == "PASS"

    def test_critical_fail_returns_fail(self):
        report = DataQualityReport()
        report.add_check(DataQualityCheck(
            check_id="T1", name="Test", severity=CheckSeverity.CRITICAL,
            status=CheckStatus.FAIL, reason="Something wrong",
        ))
        assert report.overall == "FAIL"

    def test_warning_fail_does_not_cause_overall_fail(self):
        report = DataQualityReport()
        report.add_check(DataQualityCheck(
            check_id="T1", name="Test", severity=CheckSeverity.WARNING,
            status=CheckStatus.FAIL, reason="Minor issue",
        ))
        # Now returns WARNING instead of PASS for improved clarity
        assert report.overall == "WARNING"

    def test_summary_counts(self):
        report = DataQualityReport()
        report.add_check(DataQualityCheck("C1", "C1", CheckSeverity.CRITICAL, CheckStatus.PASS))
        report.add_check(DataQualityCheck("C2", "C2", CheckSeverity.CRITICAL, CheckStatus.FAIL, "bad"))
        report.add_check(DataQualityCheck("C3", "C3", CheckSeverity.WARNING, CheckStatus.SKIP))
        s = report.summary
        assert s["total"] == 3
        assert s["passed"] == 1
        assert s["failed"] == 1
        assert s["skipped"] == 1
        assert s["overall"] == "FAIL"


# ─── Test CHECK-01 ────────────────────────────────────────────────────


class TestCheckOhlcvCompleteness:
    def test_all_tickers_present(self, tickers, base_market_data, today):
        result = check_ohlcv_completeness(tickers, base_market_data, today)
        assert result.passed

    def test_missing_ticker_detected(self, tickers, base_market_data, today):
        # Remove VHM from data
        filtered = [r for r in base_market_data if r["ticker"] != "VHM"]
        result = check_ohlcv_completeness(tickers, filtered, today)
        assert not result.passed
        assert "VHM" in result.reason

    def test_empty_ticker_list(self, base_market_data, today):
        result = check_ohlcv_completeness([], base_market_data, today)
        assert not result.passed
        assert "Empty ticker" in result.reason

    def test_wrong_date_ignored(self, tickers, base_market_data, today):
        """Data for a different date should not count."""
        wrong_date = today - timedelta(days=1)
        result = check_ohlcv_completeness(tickers, base_market_data, wrong_date)
        assert not result.passed
        assert result.details.get("missing_tickers") is not None

    def test_multiple_missing_tickers(self, tickers, base_market_data, today):
        filtered = [r for r in base_market_data if r["ticker"] not in ("VHM", "FPT", "MSN")]
        result = check_ohlcv_completeness(tickers, filtered, today)
        assert not result.passed
        assert "VHM" in result.reason and "FPT" in result.reason


# ─── Test CHECK-02 ────────────────────────────────────────────────────


class TestCheckPriceLimit:
    def test_all_within_limit(self, base_market_data, today):
        result = check_price_limit(base_market_data, today)
        assert result.passed

    def test_excessive_change_detected(self, base_market_data, today):
        base_market_data[0]["close_adj"] = base_market_data[0]["prev_close_adj"] * 1.10
        result = check_price_limit(base_market_data, today)
        assert not result.passed
        assert "VHM" in result.details["violations"][0]

    def test_zero_prev_close_skipped(self, base_market_data, today):
        base_market_data[0]["prev_close_adj"] = 0
        base_market_data[0]["close_adj"] = 100
        result = check_price_limit(base_market_data, today)
        assert result.passed  # Should skip, not fail

    def test_custom_max_change(self, base_market_data, today):
        base_market_data[0]["close_adj"] = base_market_data[0]["prev_close_adj"] * 1.03
        result = check_price_limit(base_market_data, today, max_change_pct=2.0)
        assert not result.passed


# ─── Test CHECK-03 ────────────────────────────────────────────────────


class TestCheckVolumeNonNegative:
    def test_all_positive(self, base_market_data, today):
        result = check_volume_non_negative(base_market_data, today)
        assert result.passed

    def test_negative_volume_detected(self, base_market_data, today):
        base_market_data[0]["volume_continuous"] = -100
        result = check_volume_non_negative(base_market_data, today)
        assert not result.passed
        assert "VHM" in result.details["negative_fields"][0]

    def test_negative_foreign_volume(self, base_market_data, today):
        base_market_data[1]["foreign_buy_vol"] = -500
        result = check_volume_non_negative(base_market_data, today)
        assert not result.passed

    def test_none_volume_skipped(self, base_market_data, today):
        base_market_data[0]["volume_continuous"] = None
        result = check_volume_non_negative(base_market_data, today)
        assert result.passed  # None is not negative


# ─── Test CHECK-04 ────────────────────────────────────────────────────


class TestCheckVolumeSeparation:
    def test_exact_match(self, base_market_data, today):
        result = check_volume_separation(base_market_data, today)
        assert result.passed

    def test_mismatch_detected(self, base_market_data, today):
        base_market_data[0]["volume_total"] = 999_999_999  # Way off
        result = check_volume_separation(base_market_data, today)
        assert not result.passed

    def test_no_total_volume_skipped(self, base_market_data, today):
        for row in base_market_data:
            row.pop("volume_total", None)
        result = check_volume_separation(base_market_data, today)
        assert result.passed

    def test_zero_volumes_ok(self, base_market_data, today):
        for row in base_market_data:
            row["volume_continuous"] = 0
            row["volume_atc"] = 0
            row["volume_ato"] = 0
            row["volume_total"] = 0
        result = check_volume_separation(base_market_data, today)
        assert result.passed


# ─── Test CHECK-05 ────────────────────────────────────────────────────


class TestCheckFinancialFreshness:
    def test_recent_financials_pass(self, base_financials, today):
        result = check_financial_freshness(base_financials, reference_date=today)
        assert result.passed

    def test_stale_financials_fail(self, base_financials, today):
        for row in base_financials:
            row["announcement_date"] = today - timedelta(days=200)
        result = check_financial_freshness(base_financials, reference_date=today)
        assert not result.passed
        # Updated to check for actual message content
        assert "Stale" in result.reason

    def test_missing_ann_date_flagged(self, base_financials, today):
        base_financials[0]["announcement_date"] = None
        result = check_financial_freshness(base_financials, reference_date=today)
        assert not result.passed

    def test_empty_financials_pass(self, today):
        result = check_financial_freshness([], reference_date=today)
        assert result.passed


# ─── Test CHECK-06 ────────────────────────────────────────────────────


class TestCheckCorporateActionApplied:
    def test_all_applied(self):
        actions = [
            {"ticker": "VHM", "action_type": "DIVIDEND_CASH", "ex_date": "2026-01-15", "applied": True},
            {"ticker": "FPT", "action_type": "SPLIT", "ex_date": "2026-03-01", "applied": True},
        ]
        result = check_corporate_action_applied(actions)
        assert result.passed

    def test_unapplied_detected(self):
        actions = [
            {"ticker": "VHM", "action_type": "DIVIDEND_CASH", "ex_date": "2026-01-15", "applied": True},
            {"ticker": "FPT", "action_type": "SPLIT", "ex_date": "2026-03-01", "applied": False},
        ]
        result = check_corporate_action_applied(actions)
        assert not result.passed
        assert "FPT" in result.reason

    def test_empty_list_pass(self):
        result = check_corporate_action_applied([])
        assert result.passed

    def test_missing_applied_field(self):
        actions = [
            {"ticker": "VHM", "action_type": "DIVIDEND_CASH", "ex_date": "2026-01-15"},
        ]
        result = check_corporate_action_applied(actions)
        assert not result.passed


# ─── Test CHECK-07 ────────────────────────────────────────────────────


class TestCheckAnnouncementDateExists:
    def test_all_have_dates(self, base_financials):
        result = check_announcement_date_exists(base_financials)
        assert result.passed

    def test_excessive_nulls_detected(self, base_financials):
        for row in base_financials[:3]:
            row["announcement_date"] = None
        result = check_announcement_date_exists(base_financials)
        assert not result.passed
        assert "3/5" in result.reason or "60.00%" in result.reason

    def test_empty_list_skips(self):
        result = check_announcement_date_exists([])
        assert result.status == CheckStatus.SKIP

    def test_below_threshold_passes(self, base_financials):
        # 1 out of 5 = 20% — below default 5% threshold? No! 
        # Default max_null_pct = 5%, so 1/5 = 20% should fail.
        # Let's test with a higher threshold explicitly.
        base_financials[0]["announcement_date"] = None
        result = check_announcement_date_exists(base_financials, max_null_pct=25.0)
        assert result.passed  # 20% < 25%


# ─── Test CHECK-08 ────────────────────────────────────────────────────


class TestCheckPointInTimeIntegrity:
    def test_no_future_data(self, base_market_data, today):
        result = check_point_in_time_integrity(base_market_data, today)
        assert result.passed

    def test_future_data_detected(self, base_market_data, today):
        base_market_data[0]["date"] = today + timedelta(days=1)
        result = check_point_in_time_integrity(base_market_data, today)
        assert not result.passed
        assert "Future" in result.reason

    def test_future_for_one_ticker(self, base_market_data, today):
        base_market_data[2]["date"] = today + timedelta(days=5)
        result = check_point_in_time_integrity(base_market_data, today)
        assert not result.passed

    def test_empty_data_pass(self, today):
        result = check_point_in_time_integrity([], today)
        assert result.passed


# ─── Test run_all_checks ──────────────────────────────────────────────


class TestRunAllChecks:
    def test_full_pass_scenario(self, tickers, base_market_data, base_financials, today):
        report = run_all_checks(
            tickers=tickers,
            market_data=base_market_data,
            financials=base_financials,
            corp_actions=[{"ticker": "VHM", "action_type": "SPLIT", "ex_date": "2026-01-01", "applied": True}],
            target_date=today,
        )
        assert report.overall == "PASS"
        assert all(c.status != CheckStatus.FAIL for c in report.checks)

    def test_missing_data_causes_fail(self, tickers, today):
        """Only provide tickers and empty data — multiple checks should fail."""
        report = run_all_checks(tickers=tickers, target_date=today)
        assert report.overall == "FAIL"
        # CHECK-01 should fail (no market data)
        assert report.checks[0].status == CheckStatus.FAIL

    def test_report_contains_8_checks(self, tickers, base_market_data, base_financials, today):
        report = run_all_checks(
            tickers=tickers,
            market_data=base_market_data,
            financials=base_financials,
            target_date=today,
        )
        assert len(report.checks) == 8
        check_ids = [c.check_id for c in report.checks]
        expected = [f"CHECK-{i:02d}" for i in range(1, 9)]
        assert check_ids == expected

    def test_partial_financials_still_runs(self, tickers, base_market_data, today):
        """Should not crash when financials data is incomplete."""
        report = run_all_checks(
            tickers=tickers,
            market_data=base_market_data,
            financials=[],  # Empty but not None
            target_date=today,
        )
        # CHECK-05 and CHECK-07 should handle empty gracefully
        assert report.overall == "PASS"  # No critical fail from empty financials
        # CHECK-07 with empty list should be SKIP
        assert report.checks[6].status == CheckStatus.SKIP
