"""Engine 3: Position Sizing Engine (Quarter Kelly & Regime Scaling)

Chức năng:
- Tính toán quy mô phân bổ lý thuyết sơ bộ (preliminary_target) dựa trên công thức Quarter Kelly:
    f* = 0.25 * (p - (1 - p) / R)
- Co giãn theo trạng thái thị trường (Market Regime Scaling):
    - Bull Trending / Bull Market: 1.0x
    - Bull Choppy / Range Bound: 0.75x
    - Bear Bounce: 0.50x
    - Bear Trending / Bear Market: 0.25x
- Ràng buộc trần sơ bộ theo Hiến pháp: Không vượt quá 15% NAV.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class KellySizingResult:
    full_kelly: float
    quarter_kelly_raw: float
    regime_multiplier: float
    regime_scaled_target: float
    preliminary_target: float
    preliminary_value_vnd: float


class KellySizingEngine:
    def __init__(self, fraction: float = 0.25, max_single_stock_pct: float = 0.15):
        self.fraction = fraction
        self.max_single_stock_pct = max_single_stock_pct

    def calculate_sizing(
        self,
        prob_win: float,
        payoff_ratio: float,
        regime_str: str,
        total_nav: float,
    ) -> KellySizingResult:
        if payoff_ratio <= 0 or prob_win <= 0:
            return KellySizingResult(
                full_kelly=0.0,
                quarter_kelly_raw=0.0,
                regime_multiplier=1.0,
                regime_scaled_target=0.0,
                preliminary_target=0.0,
                preliminary_value_vnd=0.0,
            )

        full_kelly = prob_win - ((1.0 - prob_win) / payoff_ratio)
        if full_kelly <= 0:
            return KellySizingResult(
                full_kelly=round(full_kelly, 4),
                quarter_kelly_raw=0.0,
                regime_multiplier=1.0,
                regime_scaled_target=0.0,
                preliminary_target=0.0,
                preliminary_value_vnd=0.0,
            )

        quarter_kelly = full_kelly * self.fraction

        # 1. Regime Multiplier
        regime_upper = str(regime_str).upper()
        if "BEAR_TRENDING" in regime_upper or "BEAR_MARKET" in regime_upper:
            multiplier = 0.25
        elif "BEAR_BOUNCE" in regime_upper or "RANGE_BOUND" in regime_upper:
            multiplier = 0.50
        elif "CHOPPY" in regime_upper:
            multiplier = 0.75
        else:
            multiplier = 1.0

        scaled_target = quarter_kelly * multiplier
        preliminary_target = min(scaled_target, self.max_single_stock_pct)
        preliminary_value = round(preliminary_target * total_nav, 2)

        return KellySizingResult(
            full_kelly=round(full_kelly, 4),
            quarter_kelly_raw=round(quarter_kelly, 4),
            regime_multiplier=multiplier,
            regime_scaled_target=round(scaled_target, 4),
            preliminary_target=round(preliminary_target, 4),
            preliminary_value_vnd=preliminary_value,
        )
