import pytest
from app.domain.services.quant.risk.live_safety import (
    SafetyConfig,
    SafetyCheckResult,
    LiveTraderSafety,
)


class TestLiveTraderSafety:
    def test_daily_loss_within_limit(self):
        safety = LiveTraderSafety()
        result = safety.check_daily_loss(-0.01)
        assert result.passed

    def test_daily_loss_exceeds_limit(self):
        safety = LiveTraderSafety(SafetyConfig(max_daily_loss_pct=0.02))
        result = safety.check_daily_loss(-0.03)
        assert not result.passed

    def test_circuit_breaker_normal(self):
        safety = LiveTraderSafety()
        result = safety.check_circuit_breaker(-0.03)
        assert result.passed

    def test_circuit_breaker_triggered(self):
        safety = LiveTraderSafety(SafetyConfig(circuit_breaker_drop_pct=0.04))
        result = safety.check_circuit_breaker(-0.05)
        assert not result.passed

    def test_position_limit_within(self):
        safety = LiveTraderSafety()
        result = safety.check_position_limit(1_000_000, 500_000, 100_000_000)
        assert result.passed

    def test_position_limit_just_under(self):
        safety = LiveTraderSafety()
        result = safety.check_position_limit(1_000_000, 1_000_000, 100_000_000)
        assert result.passed

    def test_position_limit_exceeded(self):
        safety = LiveTraderSafety()
        result = safety.check_position_limit(10_000_000, 15_000_000, 100_000_000)
        assert not result.passed

    def test_concentration_limit_within(self):
        safety = LiveTraderSafety()
        result = safety.check_concentration(0.08)
        assert result.passed

    def test_concentration_limit_exceeded(self):
        safety = LiveTraderSafety()
        result = safety.check_concentration(0.15)
        assert not result.passed

    def test_liquidity_within_limit(self):
        safety = LiveTraderSafety()
        result = safety.check_liquidity(1000, 100_000)
        assert result.passed

    def test_liquidity_exceeded(self):
        safety = LiveTraderSafety(SafetyConfig(max_adv_ratio=0.05))
        result = safety.check_liquidity(100_000, 100_000)
        assert not result.passed

    def test_broker_cash_sufficient(self):
        safety = LiveTraderSafety()
        result = safety.check_broker_cash(50_000_000, 30_000_000)
        assert result.passed

    def test_broker_cash_insufficient(self):
        safety = LiveTraderSafety()
        result = safety.check_broker_cash(20_000_000, 30_000_000)
        assert not result.passed

    def test_reset_daily(self):
        safety = LiveTraderSafety()
        safety.check_daily_loss(-0.05)
        safety.reset_daily()
        assert safety.daily_pnl == 0.0

    def test_pre_trade_all_pass(self):
        safety = LiveTraderSafety()
        checks = [
            SafetyCheckResult(True),
            SafetyCheckResult(True),
        ]
        result = safety.pre_trade_check(checks)
        assert result.passed

    def test_pre_trade_one_fails(self):
        safety = LiveTraderSafety()
        checks = [
            SafetyCheckResult(True),
            SafetyCheckResult(False, "Loss limit exceeded"),
            SafetyCheckResult(True),
        ]
        result = safety.pre_trade_check(checks)
        assert not result.passed
        assert "Loss limit exceeded" in result.reason
