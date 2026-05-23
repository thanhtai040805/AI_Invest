"""Vietnam equity backtest engine.

Market rules:
  - T+2: can sell shares on T+2 (settlement cycle)
  - No short selling for retail investors
  - Board-based price limits: HOSE ±7%, HNX ±10%, UPCoM ±15%
  - Board-based tick sizes (price-step dependent on HOSE)
  - Minimum lot: 100 shares (odd lots can only be sold, not bought)
  - Brokerage fee: 0.15% bilateral
  - Sell tax: 0.1% sell-side only
  - Reference price: pre_close (HOSE/HNX) or VWAP (UPCoM)
"""

from __future__ import annotations

import pandas as pd

from backtest.engines.base import BaseEngine

VN_TIMEZONE = "Asia/Ho_Chi_Minh"

_VN_BOARDS = {
    "HOSE": {"price_limit": 0.07, "tick": None},   # tick depends on price
    "HNX":  {"price_limit": 0.10, "tick": 100},
    "UPC":  {"price_limit": 0.15, "tick": 100},
}

# Well-known VN symbols by board (for board detection)
_VN_SYMBOL_BOARD: dict[str, str] = {
    # HOSE
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
    # HNX
    "PVS": "HNX", "SHB": "HNX", "SHS": "HNX", "MBS": "HNX",
    "TNG": "HNX", "VIF": "HNX", "NVB": "HNX", "VCS": "HNX",
    "PVI": "HNX", "PVC": "HNX", "PVB": "HNX", "HUT": "HNX",
    "CEO": "HNX", "IDJ": "HNX", "VGS": "HNX", "LHC": "HNX",
    "BVS": "HNX", "ART": "HNX",
    # UPCoM
    "BSR": "UPC", "MCH": "UPC", "VEA": "UPC", "VID": "UPC",
    "ACV": "UPC", "OIL": "UPC", "VTP": "UPC", "CVN": "UPC",
    "BAB": "UPC", "ABI": "UPC", "DPP": "UPC",
}


def _detect_vn_board(symbol: str) -> str:
    """Detect VN board from qualified symbol (e.g. VCB.HOSE -> HOSE)."""
    parts = symbol.upper().split(".")
    if len(parts) == 2 and parts[1] in _VN_BOARDS:
        return parts[1]
    code = parts[0] if len(parts) == 1 else parts[0]
    return _VN_SYMBOL_BOARD.get(code, "HOSE")


def _vn_price_limit(symbol: str) -> float:
    """Return the daily price limit fraction for the board."""
    board = _detect_vn_board(symbol)
    return _VN_BOARDS[board]["price_limit"]


def _vn_tick_size(symbol: str, price: float) -> float:
    """Return the minimum price increment for a symbol at a given price."""
    board = _detect_vn_board(symbol)
    if board == "HOSE":
        if price < 10_000:
            return 10.0
        if price < 50_000:
            return 50.0
        return 100.0
    return _VN_BOARDS[board]["tick"]  # HNX/UPCoM: 100đ


def _vn_ceiling_price(symbol: str, ref_price: float) -> float:
    """Calculate ceiling price based on reference price and board limit."""
    limit = _vn_price_limit(symbol)
    return ref_price * (1 + limit)


def _vn_floor_price(symbol: str, ref_price: float) -> float:
    """Calculate floor price based on reference price and board limit."""
    limit = _vn_price_limit(symbol)
    return ref_price * (1 - limit)


def is_vn_market_open(dt: pd.Timestamp | None = None) -> bool:
    """Check if Vietnam stock market is currently open.

    VN market hours (GMT+7, no DST):
      - Morning session: 09:00 - 11:30 (ATO: 09:00-09:15)
      - Afternoon session: 13:00 - 15:00 (ATC: 14:30-14:45)
      - Negotiated & Derivatives: 13:00 - 15:00
    """
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
    """Vietnam equity market engine.

    Config keys:
      - brokerage_rate: default 0.0015 (0.15%)
      - sell_tax: default 0.001 (0.1%, sell-side only)
      - slippage: default 0.001
    """

    def __init__(self, config: dict):
        config = {**config, "leverage": 1.0}
        super().__init__(config)
        self.brokerage_rate: float = config.get("brokerage_rate", 0.0015)
        self.sell_tax: float = config.get("sell_tax", 0.001)
        self.slippage_rate: float = config.get("slippage", 0.001)

    def can_execute(self, symbol: str, direction: int, bar: pd.Series) -> bool:
        """VN equity execution rules.

        Args:
            symbol: Stock code (e.g. VCB.HOSE, PVS.HNX, BSR.UPC).
            direction: 1 (buy), -1 (short — always blocked), 0 (sell/close).
            bar: Current bar.

        Returns:
            True if the trade is allowed.
        """
        if direction == -1:
            return False

        if direction == 0:
            pos = self.positions.get(symbol)
            if pos is not None:
                bar_date = _bar_date(bar)
                entry_date = pos.entry_time.date() if hasattr(pos.entry_time, "date") else None
                if bar_date is not None and entry_date is not None:
                    days_held = (bar_date - entry_date).days
                    if days_held <= 1:
                        return False

        return True

    def round_size(self, raw_size: float, price: float) -> float:
        """Round down to 100-share lots."""
        return max(int(raw_size / 100) * 100, 0)

    def calc_commission(self, size: float, price: float, _direction: int, is_open: bool) -> float:
        """VN fee structure: brokerage (both sides) + sell tax.

        Args:
            size: Number of shares.
            price: Execution price.
            _direction: Unused.
            is_open: True for buy, False for sell.

        Returns:
            Total fee in VND.
        """
        notional = size * price
        comm = notional * self.brokerage_rate
        if not is_open:
            comm += notional * self.sell_tax
        return comm

    def apply_slippage(self, price: float, direction: int) -> float:
        """VN equity slippage."""
        return price * (1 + direction * self.slippage_rate)


def _bar_date(bar: pd.Series):
    """Extract date from bar, handling various column names."""
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
