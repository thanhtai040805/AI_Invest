"""Big-Picture Technical Structure & Multi-Session Money Flow Analyzer (IOS v5.3).

Pure domain logic (zero SQL/HTTP/SDK) implementing Top-Down Technical Analysis:
1. Trend & Price Pattern (MA10/MA20/MA50/MA200, Higher-High/Higher-Low, Base vs Extended Climax).
2. Real Support & Resistance Zones (20d/60d Swing Lows, Dynamic Trend Support, Overhead Supply).
3. Multi-Session Volume & Money Flow (Up/Down Vol Ratio, Chaikin Money Flow 20d, OBV Slope,
   Stock-Specific Distribution Days, Foreign Net Flow).
4. Confirmed Daily / Near-Close Session Candle Evaluation (never reacts to isolated 1m wicks).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence
import numpy as np


@dataclass
class TechnicalStructureSnapshot:
    close: float
    ma10: float
    ma20: float
    ma50: float
    ma200: float
    ma20_slope_pct: float
    ma50_slope_pct: float
    ma20_volume: float
    swing_low_20d: float
    swing_low_60d: float
    recent_higher_low: Optional[float]
    resistance_20d: float
    resistance_60d: float
    distance_to_resistance_pct: float
    extension_vs_ma20_pct: float
    higher_high_higher_low: bool
    price_structure: str  # ACCUMULATION_BASE | BREAKOUT_UPTREND | EXTENDED_CLIMAX | DISTRIBUTION_TOP | DOWNTREND_BREAKDOWN | NEUTRAL
    up_down_vol_ratio_10d: float
    cmf_20d: float
    obv_slope_10d: float
    stock_dist_days_10d: int
    foreign_net_10d: float
    money_flow_state: str  # ACCUMULATING | NEUTRAL | DISTRIBUTING
    rs_20d: float = 0.0
    big_picture_score: float = 50.0

    def compute_dynamic_support(self, entry_price: float, peak_pnl_pct: float = 0.0) -> tuple[float, str]:
        """Compute structural support that rises with confirmed trend structure on winning waves.

        Replaces mechanical trailing % giveback:
        - Base / early entry (peak < +10%): structural base support (20d swing low, anchored near MA50/base).
        - Confirmed Wave 1 winner (+10% <= peak < +20%): raise support to rising MA20 / recent Higher-Low.
        - Extended Wave 2 winner (peak >= +20%): raise support toward MA10/MA20 rising trendline.
        """
        base = self.swing_low_20d if self.swing_low_20d > 0 else (self.ma50 or entry_price * 0.93)
        if peak_pnl_pct >= 0.20:
            candidates = [x for x in (base, self.ma20 * 0.995 if self.ma20 > 0 else 0.0,
                                      self.ma10 * 0.99 if self.ma10 > 0 else 0.0,
                                      self.recent_higher_low or 0.0) if x > 0]
            return round(max(candidates), 2), "DYNAMIC_TREND_SUPPORT_MA10_MA20"
        if peak_pnl_pct >= 0.10:
            candidates = [x for x in (base, self.ma20 * 0.995 if self.ma20 > 0 else 0.0,
                                      self.recent_higher_low or 0.0) if x > 0]
            # Never set trailing support below breakeven once a stock has rallied >= +10%
            # unless MA20 and HL are still below entry (in which case protect at least entry * 0.99).
            support = max(candidates) if candidates else base
            if entry_price > 0 and support < entry_price * 0.99:
                support = max(support, entry_price * 0.99)
            return round(support, 2), "DYNAMIC_TREND_SUPPORT_MA20_HL"
        return round(base, 2), "BASE_SWING_LOW_SUPPORT"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TechnicalStructureAnalyzer:
    """Computes Big-Picture multi-session technical context from Daily OHLCV bars."""

    @staticmethod
    def _normalize_price(val: float, scale: float) -> float:
        return float(val) * scale

    @classmethod
    def analyze_from_arrays(
        cls,
        opens: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        volumes: np.ndarray,
        foreign_nets: Optional[np.ndarray] = None,
        rs_20d: float = 0.0,
    ) -> TechnicalStructureSnapshot:
        n = len(closes)
        if n == 0:
            return TechnicalStructureSnapshot(
                close=0.0, ma10=0.0, ma20=0.0, ma50=0.0, ma200=0.0,
                ma20_slope_pct=0.0, ma50_slope_pct=0.0, ma20_volume=0.0,
                swing_low_20d=0.0, swing_low_60d=0.0, recent_higher_low=None,
                resistance_20d=0.0, resistance_60d=0.0, distance_to_resistance_pct=99.0,
                extension_vs_ma20_pct=0.0, higher_high_higher_low=False,
                price_structure="NEUTRAL", up_down_vol_ratio_10d=1.0, cmf_20d=0.0,
                obv_slope_10d=0.0, stock_dist_days_10d=0, foreign_net_10d=0.0,
                money_flow_state="NEUTRAL", rs_20d=rs_20d, big_picture_score=50.0,
            )

        last_close = float(closes[-1])
        ma10 = float(np.mean(closes[-min(10, n):]))
        ma20 = float(np.mean(closes[-min(20, n):]))
        ma50 = float(np.mean(closes[-min(50, n):]))
        ma200 = float(np.mean(closes[-min(200, n):]))
        ma20_vol = float(np.mean(volumes[-min(20, n):])) if len(volumes) > 0 else 0.0

        # MA slopes (over trailing 5 sessions)
        if n >= 25:
            ma20_prev = float(np.mean(closes[-25:-5]))
            ma20_slope = ((ma20 - ma20_prev) / ma20_prev * 100.0) if ma20_prev > 0 else 0.0
        else:
            ma20_slope = 0.0
        if n >= 55:
            ma50_prev = float(np.mean(closes[-55:-5]))
            ma50_slope = ((ma50 - ma50_prev) / ma50_prev * 100.0) if ma50_prev > 0 else 0.0
        else:
            ma50_slope = 0.0

        # Support & Resistance Zones
        w20_lows = lows[-min(20, n):]
        w60_lows = lows[-min(60, n):]
        w20_highs = highs[-min(20, n):]
        w60_highs = highs[-min(60, n):]

        swing_low_20d = float(np.min(w20_lows)) if len(w20_lows) else last_close * 0.93
        swing_low_60d = float(np.min(w60_lows)) if len(w60_lows) else swing_low_20d
        resistance_20d = float(np.max(w20_highs)) if len(w20_highs) else last_close
        resistance_60d = float(np.max(w60_highs)) if len(w60_highs) else resistance_20d

        # Detect Higher-High / Higher-Low & recent Higher-Low pivot over two 10-day halves
        recent_higher_low: Optional[float] = None
        hh_hl = False
        if n >= 20:
            prev_half_low = float(np.min(lows[-20:-10]))
            curr_half_low = float(np.min(lows[-10:]))
            prev_half_high = float(np.max(highs[-20:-10]))
            curr_half_high = float(np.max(highs[-10:]))
            hh_hl = (curr_half_low >= prev_half_low * 0.995) and (curr_half_high >= prev_half_high * 0.995)
            if curr_half_low > prev_half_low:
                recent_higher_low = curr_half_low

        # Distance to overhead resistance (nearest resistance strictly above or at current price)
        overhead_levels = [lvl for lvl in (resistance_20d, resistance_60d) if lvl >= last_close * 0.995]
        nearest_res = min(overhead_levels) if overhead_levels else resistance_60d
        dist_to_res_pct = ((nearest_res - last_close) / last_close * 100.0) if last_close > 0 else 99.0
        ext_vs_ma20_pct = ((last_close - ma20) / ma20 * 100.0) if ma20 > 0 else 0.0

        # Multi-session Volume & Money Flow
        # 1. Up-Volume vs Down-Volume (trailing 10 sessions)
        up_vol = 0.0
        down_vol = 0.0
        stock_dist_days = 0
        start_10 = max(1, n - 10)
        for i in range(start_10, n):
            prev_c = float(closes[i - 1])
            curr_c = float(closes[i])
            v = float(volumes[i])
            if prev_c <= 0:
                continue
            ret = (curr_c - prev_c) / prev_c
            if curr_c >= prev_c:
                up_vol += v
            else:
                down_vol += v
            # Stock-specific distribution day: down >= 1.0% on >= 1.2x daily MA20 volume
            if ret <= -0.01 and ma20_vol > 0 and v >= 1.2 * ma20_vol:
                stock_dist_days += 1

        if down_vol > 0:
            up_down_ratio = max(0.1, min(5.0, up_vol / down_vol))
        else:
            up_down_ratio = 3.0 if up_vol > 0 else 1.0

        # 2. Chaikin Money Flow (20 sessions): sum(MFV) / sum(Vol)
        cmf_len = min(20, n)
        c_slice = closes[-cmf_len:]
        h_slice = highs[-cmf_len:]
        l_slice = lows[-cmf_len:]
        v_slice = volumes[-cmf_len:]
        denom = h_slice - l_slice
        mfm = np.where(denom > 1e-9, ((c_slice - l_slice) - (h_slice - c_slice)) / np.where(denom > 1e-9, denom, 1.0), 0.0)
        vol_sum = float(np.sum(v_slice))
        cmf_20d = float(np.sum(mfm * v_slice) / vol_sum) if vol_sum > 0 else 0.0

        # 3. OBV 10-day slope normalized by MA20 volume
        obv_delta = 0.0
        for i in range(start_10, n):
            if closes[i] > closes[i - 1]:
                obv_delta += float(volumes[i])
            elif closes[i] < closes[i - 1]:
                obv_delta -= float(volumes[i])
        obv_slope_10d = (obv_delta / (ma20_vol * 10.0)) if ma20_vol > 0 else 0.0

        foreign_net_10d = float(np.sum(foreign_nets[-min(10, len(foreign_nets)):])) if foreign_nets is not None and len(foreign_nets) else 0.0

        # Money flow state classification
        if (cmf_20d >= 0.05 and up_down_ratio >= 1.15) or (up_down_ratio >= 1.35 and stock_dist_days == 0):
            mf_state = "ACCUMULATING"
        elif (cmf_20d <= -0.05 and up_down_ratio <= 0.90) or stock_dist_days >= 3 or (up_down_ratio <= 0.75 and obv_slope_10d < -0.1):
            mf_state = "DISTRIBUTING"
        else:
            mf_state = "NEUTRAL"

        # Price structure classification (Big Picture)
        range_20_pct = ((resistance_20d - swing_low_20d) / ma20 * 100.0) if ma20 > 0 else 15.0
        if last_close < ma20 and last_close < ma50 and not hh_hl and ma20_slope < 0:
            price_structure = "DOWNTREND_BREAKDOWN"
        elif ext_vs_ma20_pct >= 10.0 and (stock_dist_days >= 2 or mf_state == "DISTRIBUTING"):
            price_structure = "DISTRIBUTION_TOP"
        elif ext_vs_ma20_pct >= 12.0:
            price_structure = "EXTENDED_CLIMAX"
        elif last_close >= ma20 and last_close >= ma50 and (hh_hl or ma20_slope > 0.3):
            price_structure = "BREAKOUT_UPTREND"
        elif range_20_pct <= 14.0 and last_close >= swing_low_20d * 1.01 and mf_state != "DISTRIBUTING":
            price_structure = "ACCUMULATION_BASE"
        else:
            price_structure = "NEUTRAL"

        # Composite Big-Picture Score (0-100) usable on BUY side too
        score = 50.0
        if price_structure == "BREAKOUT_UPTREND":
            score += 18.0
        elif price_structure == "ACCUMULATION_BASE":
            score += 14.0
        elif price_structure == "EXTENDED_CLIMAX":
            score -= 8.0
        elif price_structure in ("DISTRIBUTION_TOP", "DOWNTREND_BREAKDOWN"):
            score -= 22.0

        if mf_state == "ACCUMULATING":
            score += 14.0
        elif mf_state == "DISTRIBUTING":
            score -= 16.0

        if hh_hl:
            score += 6.0
        if stock_dist_days >= 2:
            score -= 6.0 * min(3, stock_dist_days - 1)
        if 2.0 <= dist_to_res_pct <= 15.0:
            score += 5.0  # Clean headroom to resistance
        elif dist_to_res_pct < 1.5 and mf_state != "ACCUMULATING":
            score -= 6.0  # Buying straight into overhead resistance without accumulation

        big_picture_score = round(max(5.0, min(98.0, score)), 2)

        return TechnicalStructureSnapshot(
            close=round(last_close, 2),
            ma10=round(ma10, 2),
            ma20=round(ma20, 2),
            ma50=round(ma50, 2),
            ma200=round(ma200, 2),
            ma20_slope_pct=round(ma20_slope, 2),
            ma50_slope_pct=round(ma50_slope, 2),
            ma20_volume=round(ma20_vol, 2),
            swing_low_20d=round(swing_low_20d, 2),
            swing_low_60d=round(swing_low_60d, 2),
            recent_higher_low=round(recent_higher_low, 2) if recent_higher_low is not None else None,
            resistance_20d=round(resistance_20d, 2),
            resistance_60d=round(resistance_60d, 2),
            distance_to_resistance_pct=round(dist_to_res_pct, 2),
            extension_vs_ma20_pct=round(ext_vs_ma20_pct, 2),
            higher_high_higher_low=hh_hl,
            price_structure=price_structure,
            up_down_vol_ratio_10d=round(up_down_ratio, 3),
            cmf_20d=round(cmf_20d, 4),
            obv_slope_10d=round(obv_slope_10d, 3),
            stock_dist_days_10d=stock_dist_days,
            foreign_net_10d=round(foreign_net_10d, 2),
            money_flow_state=mf_state,
            rs_20d=round(float(rs_20d), 4),
            big_picture_score=big_picture_score,
        )

    @classmethod
    def analyze_ohlcv_bars(
        cls,
        ohlcv_bars: Sequence[Dict[str, Any]],
        rs_20d: float = 0.0,
    ) -> TechnicalStructureSnapshot:
        """Analyze a list of daily OHLCV dicts (ascending or descending by time/date)."""
        if not ohlcv_bars:
            return cls.analyze_from_arrays(
                np.array([]), np.array([]), np.array([]), np.array([]), np.array([]), rs_20d=rs_20d
            )

        bars = list(ohlcv_bars)
        # Ensure chronological ascending order
        if len(bars) > 1:
            t0 = str(bars[0].get("time") or bars[0].get("date") or "")
            t1 = str(bars[-1].get("time") or bars[-1].get("date") or "")
            if t0 and t1 and t0 > t1:
                bars.reverse()

        raw_closes = [float(b.get("close") or 0.0) for b in bars if float(b.get("close") or 0.0) > 0]
        if not raw_closes:
            return cls.analyze_from_arrays(
                np.array([]), np.array([]), np.array([]), np.array([]), np.array([]), rs_20d=rs_20d
            )
        # Series-level HOSE unit normalization (thousand VND -> VND)
        scale = 1000.0 if max(raw_closes) < 1000.0 else 1.0

        opens, highs, lows, closes, vols, fnets = [], [], [], [], [], []
        for b in bars:
            c = float(b.get("close") or 0.0)
            if c <= 0:
                continue
            o = float(b.get("open") or c)
            h = float(b.get("high") or max(o, c))
            l = float(b.get("low") or min(o, c))
            v = float(b.get("volume") or b.get("volume_continuous") or b.get("volume_total") or 0.0)
            fn = float(b.get("foreign_net_vol") or 0.0)
            opens.append(o * scale)
            highs.append(h * scale)
            lows.append(l * scale)
            closes.append(c * scale)
            vols.append(v)
            fnets.append(fn)

        return cls.analyze_from_arrays(
            opens=np.array(opens, dtype=float),
            highs=np.array(highs, dtype=float),
            lows=np.array(lows, dtype=float),
            closes=np.array(closes, dtype=float),
            volumes=np.array(vols, dtype=float),
            foreign_nets=np.array(fnets, dtype=float),
            rs_20d=rs_20d,
        )

    @staticmethod
    def evaluate_session_candle(
        candle: Optional[Dict[str, Any]],
        daily_ma20_volume: float,
    ) -> Dict[str, Any]:
        """Evaluate an aggregated full-session daily candle (09:15 -> near close >= 14:15)."""
        if not candle:
            return {
                "valid": False,
                "session_range_pct": 0.0,
                "close_location_pct": 0.5,
                "upper_wick_ratio": 0.0,
                "daily_vol_ratio": 0.0,
                "is_red": False,
                "is_weak_close": False,
                "is_hammer_recovery": False,
            }

        open_p = float(candle.get("open") or 0.0)
        high_p = float(candle.get("high") or 0.0)
        low_p = float(candle.get("low") or 0.0)
        close_p = float(candle.get("close") or 0.0)
        vol = float(candle.get("volume") or 0.0)
        if close_p <= 0 or high_p < low_p:
            return {
                "valid": False,
                "session_range_pct": 0.0,
                "close_location_pct": 0.5,
                "upper_wick_ratio": 0.0,
                "daily_vol_ratio": 0.0,
                "is_red": False,
                "is_weak_close": False,
                "is_hammer_recovery": False,
            }

        rng = high_p - low_p
        range_pct = (rng / close_p) if close_p > 0 else 0.0
        close_loc = ((close_p - low_p) / rng) if rng > 1e-9 else 0.5
        upper_wick = (high_p - max(open_p, close_p)) / rng if rng > 1e-9 else 0.0
        vol_ratio = (vol / daily_ma20_volume) if daily_ma20_volume > 0 else 1.0
        is_red = close_p < open_p
        is_weak_close = (close_loc <= 0.35 and (is_red or range_pct >= 0.015)) or (is_red and close_loc <= 0.40 and range_pct >= 0.025)
        is_hammer_recovery = (close_loc >= 0.60 and range_pct >= 0.015)

        return {
            "valid": True,
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": close_p,
            "volume": vol,
            "session_range_pct": round(range_pct, 4),
            "close_location_pct": round(close_loc, 4),
            "upper_wick_ratio": round(upper_wick, 4),
            "daily_vol_ratio": round(vol_ratio, 3),
            "is_red": is_red,
            "is_weak_close": is_weak_close,
            "is_hammer_recovery": is_hammer_recovery,
        }


technical_structure_analyzer = TechnicalStructureAnalyzer()
