"""Vietnam equity backtest engine — delegates to new quant modules.

Market rules:
  - T+2: can sell shares on T+2 via HOSEExecutionModel
  - No short selling for retail investors
  - Board-based price limits: HOSE ±7%, HNX ±10%, UPCoM ±15%
  - Minimum lot: 100 shares via round_to_lot
  - Cost via estimate_cost (brokerage + tax + slippage)
"""

from __future__ import annotations

import pandas as pd

from backtest.engines.base import BaseEngine
from app.backtest.cost_model import estimate_cost, round_to_lot, snap_to_price_step
from app.backtest.execution import HOSEExecutionModel

VN_TIMEZONE = "Asia/Ho_Chi_Minh"

_VN_BOARDS = {
    "HOSE": {"price_limit": 0.07},
    "HNX":  {"price_limit": 0.10},
    "UPC":  {"price_limit": 0.15},
}

_VN_SYMBOL_BOARD: dict[str, str] = {
    "VCB": "HOSE", "HPG": "HOSE", "VNM": "HOSE", "VIC": "HOSE",
    "MSN": "HOSE", "BID": "HOSE", "CTG": "HOSE", "FPT": "HOSE",
    "MBB": "HOSE", "TCB": "HOSE", "ACB": "HOSE", "VIB": "HOSE",
    "VPB": "HOSE", "HDB": "HOSE", "STB": "HOSE", "SSI": "HOSE",
    "VHC": "HOSE", "PNJ": "HOSE", "MWG": "HOSE", "GAS": "HOSE",
    "PLX": "HOSE", "POW": "HOSE", "SAB": "HOSE", "BVH": "HOSE",
    "VRE": "HOSE", "KDH": "HOSE", "NVL": "HOSE", "DXG": "HOSE",
    "DIG": "HOSE", "GEX": "HOSE", "HSG": "HOSE", "NKG": "HOSE",
    "LPB": "HOSE", "OCB": "HOSE", "MSB": "HOSE", "EIB": "HOSE",
    "TPB": "HOSE", "SHI": "HOSE", "DPM": "HOSE", "DCM": "HOSE",
    "PVS": "HNX", "SHB": "HNX", "SHS": "HNX", "MBS": "HNX",
    "TNG": "HNX", "VIF": "HNX", "NVB": "HNX", "VCS": "HNX",
    "PVI": "HNX", "PVC": "HNX", "PVB": "HNX", "HUT": "HNX",
    "CEO": "HNX", "IDJ": "HNX", "VGS": "HNX", "LHC": "HNX",
    "BVS": "HNX", "ART": "HNX",
    "BSR": "UPC", "MCH": "UPC", "VEA": "UPC", "VID": "UPC",
    "ACV": "UPC", "OIL": "UPC", "VTP": "UPC", "CVN": "UPC",
    "BAB": "UPC", "ABI": "UPC", "DPP": "UPC",
}


def _detect_vn_board(symbol: str) -> str:
    parts = symbol.upper().split(".")
    if len(parts) == 2 and parts[1] in _VN_BOARDS:
        return parts[1]
    code = parts[0] if len(parts) == 1 else parts[0]
    return _VN_SYMBOL_BOARD.get(code, "HOSE")


def _vn_price_limit(symbol: str) -> float:
    return _VN_BOARDS[_detect_vn_board(symbol)]["price_limit"]


def _vn_ceiling_price(symbol: str, ref_price: float) -> float:
    return ref_price * (1 + _vn_price_limit(symbol))


def _vn_floor_price(symbol: str, ref_price: float) -> float:
    return ref_price * (1 - _vn_price_limit(symbol))


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
    """Vietnam equity market engine — delegates to new quant modules."""

    def __init__(self, config: dict):
        config = {**config, "leverage": 1.0}
        super().__init__(config)
        self.brokerage_rate: float = config.get("brokerage_rate", 0.0015)
        self.sell_tax: float = config.get("sell_tax", 0.001)
        self.slippage_rate: float = config.get("slippage", 0.001)
        self._execution = HOSEExecutionModel(reference_price_func=None)

    def can_execute(self, symbol: str, direction: int, bar: pd.Series) -> bool:
        if direction == -1:
            return False
        if direction == 0:
            pos = self.positions.get(symbol)
            if pos is not None:
                bar_date = _bar_date(bar)
                if bar_date is not None and hasattr(pos.entry_time, "date"):
                    return self._execution.can_sell(symbol, pos.entry_time.date(), bar_date)
        return True

    def round_size(self, raw_size: float, price: float = 0) -> float:
        return round_to_lot(raw_size)

    def calc_commission(self, size: float, price: float, _direction: int, is_open: bool) -> float:
        side = "SELL" if not is_open else "BUY"
        cost = estimate_cost(side, price, size, brokerage_rate=self.brokerage_rate, sell_tax=self.sell_tax)
        return cost["total_cost"]

    def apply_slippage(self, price: float, direction: int) -> float:
        return snap_to_price_step(price * (1 + direction * self.slippage_rate), direction)


def _bar_date(bar: pd.Series):
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
