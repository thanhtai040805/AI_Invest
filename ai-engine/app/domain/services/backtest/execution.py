"""HOSE Execution Model — T+2 settlement, price limits, lock trần/sàn.

Cash account HOSE:
- Mua khớp ngày T → cổ phiếu về tài khoản T+2 (chiều)
- Không bán được phần vừa mua trong T, T+1
- Bán ngày T → tiền về T+2
- Holding period tối thiểu thực tế: 2 phiên giao dịch

KHÔNG phải T+2 calendar days — phải tính T+2 TRADING days
(bỏ qua weekend, nghỉ lễ VN)
"""
import logging
from datetime import date, timedelta
from typing import Optional
from app.core.utils.market_utils import get_hose_price_step

logger = logging.getLogger(__name__)

SETTLEMENT_LAG = 2  # trading days

HOSE_PRICE_LIMITS = {
    "normal": 0.07,
    "ipo_first_day": 0.20,
    "after_long_suspension": 0.20,
    "gdkhq_adjustment": None,
}

NEAR_CEILING = 0.069
NEAR_FLOOR = -0.069

VN_HOLIDAYS_BY_YEAR: dict[int, list[tuple[date, date]]] = {
    2024: [(date(2024, 2, 8), date(2024, 2, 16)), (date(2024, 4, 29), date(2024, 4, 30)), (date(2024, 5, 1), date(2024, 5, 1)), (date(2024, 9, 2), date(2024, 9, 2)), (date(2024, 12, 31), date(2024, 12, 31))],
    2025: [(date(2025, 1, 27), date(2025, 2, 2)), (date(2025, 4, 30), date(2025, 5, 1)), (date(2025, 9, 1), date(2025, 9, 2))],
    2026: [(date(2026, 2, 16), date(2026, 2, 23)), (date(2026, 4, 30), date(2026, 5, 1)), (date(2026, 9, 2), date(2026, 9, 3))],
}


def is_trading_day(d: date) -> bool:
    """Check if d is a HOSE trading day (weekday, not holiday)."""
    if d.weekday() >= 5:
        return False
    year = d.year
    if year in VN_HOLIDAYS_BY_YEAR:
        for start, end in VN_HOLIDAYS_BY_YEAR[year]:
            if start <= d <= end:
                return False
    return True


def next_trading_day(d: date) -> date:
    """Return the next trading day after d."""
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def prev_trading_day(d: date) -> date:
    """Return the previous trading day before d."""
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def count_trading_days(start: date, end: date) -> int:
    """Đếm số ngày giao dịch trong khoảng [start, end]."""
    count = 0
    current = start
    while current <= end:
        if is_trading_day(current):
            count += 1
        current += timedelta(days=1)
    return count


class HOSEExecutionModel:
    """Execution model for HOSE cash account trading.

    Implements T+2 settlement, lock limit handling, and fill price estimation.
    """

    def __init__(self, reference_price_func=None):
        """
        Args:
            reference_price_func: callable(symbol, date) -> float
                Return the reference price for a symbol on a given date.
        """
        self.reference_price_func = reference_price_func

    def can_sell(self, symbol: str, buy_date: date, sell_date: date) -> bool:
        """Check if a position can be sold on sell_date given buy_date.

        Cash account: minimum 2 trading days holding period.
        Buy on T → sell earliest on T+2 (second trading day after T).
        """
        trading_days_held = count_trading_days(buy_date, sell_date) - 1
        return trading_days_held >= SETTLEMENT_LAG

    def get_fill_price(
        self,
        symbol: str,
        date: date,
        session: str = "ATC",
        side: str = "BUY",
    ) -> Optional[float]:
        """Estimate fill price for a given session.

        Args:
            symbol: ticker
            date: trading date
            session: "ATO" | "ATC" | "VWAP" | "CONTINUOUS"
            side: "BUY" | "SELL"

        Returns:
            Estimated fill price, or None if cannot determine.
        """
        if self.reference_price_func is None:
            return None

        ref_price = self.reference_price_func(symbol, date)
        if ref_price is None or ref_price == 0:
            return None

        if session == "ATC":
            slippage = get_hose_price_step(ref_price) * (1.5 if side == "BUY" else 1.0)
            return ref_price + (slippage if side == "BUY" else -slippage)
        elif session == "ATO":
            limit = ref_price * (1 + HOSE_PRICE_LIMITS["normal"] * 0.5)
            return min(ref_price * 1.02, limit) if side == "BUY" else max(ref_price * 0.98, ref_price * 0.97)
        elif session in ("VWAP", "CONTINUOUS"):
            spread = get_hose_price_step(ref_price)
            return ref_price + (spread if side == "BUY" else -spread)
        else:
            return ref_price

    def handle_lock_limit(
        self,
        symbol: str,
        date: date,
        side: str,
    ) -> tuple[bool, float]:
        """Check if an order can fill given lock limit conditions.

        Returns:
            (can_fill, actual_qty_ratio):
            - can_fill: False if completely blocked
            - actual_qty_ratio: 0.0-1.0 fill ratio
        """
        if self.reference_price_func is None:
            return True, 1.0

        ref_price = self.reference_price_func(symbol, date)
        if ref_price is None or ref_price == 0:
            return False, 0.0

        ceiling = ref_price * (1 + HOSE_PRICE_LIMITS["normal"])
        floor = ref_price * (1 - HOSE_PRICE_LIMITS["normal"])

        if side == "BUY":
            near_ceiling = ref_price * (1 + NEAR_CEILING)
            if ref_price >= near_ceiling:
                fill_ratio = max(0.0, 1.0 - (ref_price - near_ceiling) / (ceiling - near_ceiling))
                return fill_ratio > 0, max(fill_ratio, 0.2)
            return True, 1.0
        else:
            near_floor = ref_price * (1 + NEAR_FLOOR)
            if ref_price <= near_floor:
                fill_ratio = max(0.0, 1.0 - (near_floor - ref_price) / (near_floor - floor))
                return fill_ratio > 0, max(fill_ratio, 0.2)
            return True, 1.0
