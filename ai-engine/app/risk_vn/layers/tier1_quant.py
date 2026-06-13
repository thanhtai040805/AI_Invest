import logging
import math

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_quant_risk(
    symbols: list[str],
    ohlcv_data: dict[str, pd.DataFrame],
    tech_data: dict[str, dict],
) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for sym in symbols:
        flags: list[str] = []
        risk_score = 0.0
        detail = {}

        df = ohlcv_data.get(sym)
        if df is not None and len(df) >= 20:
            close = df["adj_close"] if "adj_close" in df.columns else df["close"]
            ret = close.pct_change().dropna().values
            if len(ret) >= 20:
                ret_60 = ret[-60:] if len(ret) >= 60 else ret
                var_95 = float(np.percentile(ret_60, 5))
                cvar_95 = float(ret_60[ret_60 <= var_95].mean()) if (ret_60 <= var_95).any() else 0.0
                detail["cvar_95"] = round(abs(cvar_95) * 100, 2)
                if abs(cvar_95) > 0.04:
                    risk_score += 0.30
                    flags.append("CVAR_HIGH")
                elif abs(cvar_95) > 0.03:
                    risk_score += 0.20
                    flags.append("CVAR_MEDIUM")
                elif abs(cvar_95) > 0.02:
                    risk_score += 0.10

                vol_60 = float(np.std(ret_60))
                detail["volatility_60d"] = round(vol_60 * 100, 2)
                if vol_60 > 0.035:
                    risk_score += 0.25
                    flags.append("VOLATILITY_HIGH")
                elif vol_60 > 0.025:
                    risk_score += 0.15
                    flags.append("VOLATILITY_MEDIUM")

                mdd = _max_drawdown(close.values)
                detail["max_drawdown_20d"] = round(mdd * 100, 2)
                if mdd > 0.15:
                    risk_score += 0.25
                    flags.append("MOMENTUM_CRASH")
                elif mdd > 0.10:
                    risk_score += 0.15

        tech = tech_data.get(sym, {})
        amihud = tech.get("amihud_illiquidity")
        if amihud is not None:
            detail["amihud"] = round(float(amihud), 6)
            if float(amihud) > 0.01:
                risk_score += 0.20
                flags.append("LIQUIDITY_RISK")
            elif float(amihud) > 0.005:
                risk_score += 0.10

        risk_score = min(risk_score, 1.0)
        results[sym] = {"risk_score": round(risk_score, 3), "flags": flags, "detail": detail}
    return results


def _max_drawdown(prices: np.ndarray) -> float:
    peak = np.maximum.accumulate(prices)
    drawdown = (peak - prices) / peak
    return float(np.max(drawdown)) if len(drawdown) > 0 else 0.0
