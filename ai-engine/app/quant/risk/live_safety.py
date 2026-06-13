"""Live Trading Safety — Circuit Breakers & Pre-Trade Checks.

Features:
- Daily loss limit (hard stop)
- Circuit breaker on VNIndex drop
- Position limit check
- Concentration limit check
- Order-to-ADV ratio check
- Broker balance sanity check
"""
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class SafetyConfig:
    max_daily_loss_pct: float = 0.03
    max_portfolio_value_pct: float = 0.02
    circuit_breaker_drop_pct: float = 0.05
    min_adv_ratio: float = 0.05
    max_adv_ratio: float = 0.10
    max_leverage: float = 1.0
    max_orders_per_day: int = 50


@dataclass
class SafetyCheckResult:
    passed: bool
    reason: str = ""


class LiveTraderSafety:
    """Pre-trade safety checks for live trading."""

    def __init__(self, config: SafetyConfig | None = None):
        self.config = config or SafetyConfig()
        self.daily_pnl: float = 0.0
        self.order_count: int = 0

    def reset_daily(self) -> None:
        self.daily_pnl = 0.0
        self.order_count = 0

    def check_daily_loss(self, current_pnl: float) -> SafetyCheckResult:
        self.daily_pnl += current_pnl
        if self.daily_pnl < -self.config.max_daily_loss_pct:
            return SafetyCheckResult(False, f"Daily loss {self.daily_pnl:.2%} exceeds max {self.config.max_daily_loss_pct:.2%}")
        return SafetyCheckResult(True)

    def check_circuit_breaker(
        self, index_change_pct: float
    ) -> SafetyCheckResult:
        if index_change_pct < -self.config.circuit_breaker_drop_pct:
            return SafetyCheckResult(
                False,
                f"VNIndex dropped {index_change_pct:.2%}, circuit breaker triggered",
            )
        return SafetyCheckResult(True)

    def check_position_limit(
        self, current_value: float, new_order_value: float, equity: float
    ) -> SafetyCheckResult:
        new_total = current_value + new_order_value
        max_allowed = equity * self.config.max_portfolio_value_pct
        if new_total > max_allowed:
            return SafetyCheckResult(
                False,
                f"Position ${new_total:.0f} exceeds max ${max_allowed:.0f} ({self.config.max_portfolio_value_pct:.0%} of equity)",
            )
        return SafetyCheckResult(True)

    def check_concentration(
        self, symbol_weight: float, weight_limit: Optional[float] = None
    ) -> SafetyCheckResult:
        limit = weight_limit or 0.10
        if symbol_weight > limit:
            return SafetyCheckResult(
                False,
                f"Concentration {symbol_weight:.2%} exceeds limit {limit:.2%}",
            )
        return SafetyCheckResult(True)

    def check_liquidity(
        self, order_shares: int, avg_daily_volume: int
    ) -> SafetyCheckResult:
        if avg_daily_volume <= 0:
            return SafetyCheckResult(False, "Zero ADV")
        ratio = order_shares / avg_daily_volume
        if ratio > self.config.max_adv_ratio:
            return SafetyCheckResult(
                False,
                f"Order is {ratio:.1%} of ADV, max allowed is {self.config.max_adv_ratio:.1%}",
            )
        return SafetyCheckResult(True)

    def check_broker_cash(
        self, available_cash: float, required_cash: float
    ) -> SafetyCheckResult:
        if available_cash < required_cash:
            return SafetyCheckResult(
                False,
                f"Cash ${available_cash:.0f} < required ${required_cash:.0f}",
            )
        return SafetyCheckResult(True)

    def pre_trade_check(
        self,
        checks: list[SafetyCheckResult],
    ) -> SafetyCheckResult:
        failures = [c for c in checks if not c.passed]
        if failures:
            return SafetyCheckResult(False, "; ".join(c.reason for c in failures))
        return SafetyCheckResult(True)
