"""Vietnam equity backtest engine — strictly HOSE market rules.

Market rules:
  - T+2: can sell shares on T+2 via HOSEExecutionModel
  - No short selling for retail investors
  - Board-based price limits: HOSE ±7%
  - Minimum lot: 100 shares via round_to_lot
  - Cost via estimate_cost (brokerage + tax + slippage)
"""

from __future__ import annotations

import pandas as pd

from backtest.engines.base import BaseEngine
from backtest.models import EquitySnapshot
from app.backtest.cost_model import estimate_cost, round_to_lot, snap_to_price_step
from app.backtest.execution import HOSEExecutionModel
from app.domain.rules.risk.risk_engine import MacroRiskEngine

VN_TIMEZONE = "Asia/Ho_Chi_Minh"

# Strictly HOSE (±7%)
HOSE_PRICE_LIMIT = 0.07

def is_vn_market_open(dt: pd.Timestamp | None = None) -> bool:
    if dt is None:
        dt = pd.Timestamp.now(tz=VN_TIMEZONE)
    elif dt.tz is None:
        dt = dt.tz_localize(VN_TIMEZONE)
    if dt.weekday() >= 5:
        return False
    t = dt.hour * 60 + dt.minute
    morning = (9 * 60, 11 * 60 + 30)
    afternoon = (13 * 60, 15 * 60)
    return (morning[0] <= t <= morning[1]) or (afternoon[0] <= t <= afternoon[1])


class VietnamEquityEngine(BaseEngine):
    """Vietnam equity market engine — strictly HOSE-only rules."""

    def __init__(self, config: dict):
        config = {**config, "leverage": 1.0}
        super().__init__(config)
        self.brokerage_rate: float = config.get("brokerage_rate", 0.0015)
        self.sell_tax: float = config.get("sell_tax", 0.001)
        self.slippage_rate: float = config.get("slippage", 0.001)
        self._execution = HOSEExecutionModel(reference_price_func=None)
        
        self.use_macro_risk = config.get("use_macro_risk", True)
        self.risk_engine = MacroRiskEngine() if self.use_macro_risk else None

    def _execute_bars(
        self,
        dates: pd.DatetimeIndex,
        data_map: dict[str, pd.DataFrame],
        close_df: pd.DataFrame,
        target_pos: pd.DataFrame,
        codes: list[str],
    ) -> None:
        """Bar-by-bar execution with Macro-Risk multiplier logic."""
        import logging
        logger = logging.getLogger(__name__)

        for i, ts in enumerate(dates):
            self._bar_idx = i
            
            # 1. Calculate Macro Risk Multiplier for this date
            multiplier = 1.0
            if self.risk_engine:
                try:
                    risk_data = self.risk_engine.calculate_risk_score(ts.date())
                    multiplier = risk_data["risk_multiplier"]
                    if multiplier < 1.0:
                        logger.info(f"Day {ts.date()}: Risk Score {risk_data['risk_score']} -> scaling positions by {multiplier}")
                except Exception as e:
                    logger.debug(f"Risk engine failed for {ts}: {e}")

            # a. Per-bar hooks
            for c in codes:
                if ts in data_map[c].index:
                    self.on_bar(c, data_map[c].loc[ts], ts)

            # b. Rebalance each symbol to target weight (SCALED by multiplier)
            equity = self._calc_equity(close_df, ts)
            for c in codes:
                try:
                    raw_w = float(target_pos.at[ts, c]) if ts in target_pos.index else 0.0
                    target_w = raw_w * multiplier
                    self._rebalance(c, target_w, data_map.get(c), ts, equity)
                except Exception as exc:
                    logger.warning("Rebalance failed for %s at %s: %s", c, ts, exc)

            # c. Record equity snapshot
            snap_equity = self._calc_equity(close_df, ts)
            total_unrealized = 0.0
            for p in self.positions.values():
                cp = self._safe_price(close_df, ts, p.symbol, p.entry_price)
                total_unrealized += self._calc_pnl(p.symbol, p.direction, p.size, p.entry_price, cp)
            self.equity_snapshots.append(EquitySnapshot(
                timestamp=ts,
                capital=self.capital,
                unrealized=total_unrealized,
                equity=snap_equity,
                positions=len(self.positions),
            ))

    def can_execute(self, symbol: str, direction: int, bar: pd.Series) -> bool:
        """Vietnam market rules: No shorting, T+2 settlement for sells."""
        if direction == -1: # No shorting
            return False
        if direction == 0: # Closing long position
            pos = self.positions.get(symbol)
            if pos is not None:
                bar_date = _bar_date(bar)
                if bar_date is not None and hasattr(pos.entry_time, "date"):
                    # Check if settlement cycle (T+2) is complete
                    return self._execution.can_sell(symbol, pos.entry_time.date(), bar_date)
        return True

    def round_size(self, raw_size: float, price: float = 0) -> float:
        """HOSE minimum lot is 100 shares."""
        return float(round_to_lot(raw_size))

    def calc_commission(self, size: float, price: float, _direction: int, is_open: bool) -> float:
        """Estimate trading costs including tax for sells."""
        side = "SELL" if not is_open else "BUY"
        cost = estimate_cost(side, price, int(size))
        return cost["total_cost"]

    def apply_slippage(self, price: float, direction: int) -> float:
        """Snap price to valid HOSE price steps after applying slippage."""
        # direction is 1 (buy) or -1 (sell)
        side = "BUY" if direction > 0 else "SELL"
        slipped_price = price * (1 + direction * self.slippage_rate)
        return snap_to_price_step(slipped_price, side=side)


def _bar_date(bar: pd.Series):
    """Extract date from bar series."""
    for col in ("trade_date", "date"):
        if col in bar.index:
            val = bar[col]
            if hasattr(val, "date"):
                return val.date()
            try:
                return pd.Timestamp(val).date()
            except Exception:
                pass
    if hasattr(bar, "name") and hasattr(bar.name, "date"):
        return bar.name.date()
    return None
