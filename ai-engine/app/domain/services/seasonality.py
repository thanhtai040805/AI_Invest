"""VN market seasonality analysis — month-of-year, day-of-week, holiday effects.

Analyzes historical returns for:
  - Day-of-week effect (e.g., Monday negative, Friday positive)
  - Month-of-year effect (e.g., Tết rally in January/February)
  - Holiday pre/post effects
  - Quarter-end effects
  - Turn of the month
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TZ_VN = timezone(timedelta(hours=7))


def _fetch_market_returns(days: int = 730) -> Optional[pd.DataFrame]:
    """Fetch VNINDEX daily returns for seasonality analysis."""
    import asyncio
    from app.infrastructure.external_api.market_data_service import market_data_svc

    # Use VNINDEX as market proxy
    end = datetime.now(TZ_VN)
    start = end - timedelta(days=days)

    ohlcv = asyncio.run(
        market_data_svc.get_ohlcv(
            "VNINDEX", interval="1D",
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
        )
    )
    bars = ohlcv.get("data", [])
    if len(bars) < 100:
        return None

    df = pd.DataFrame(bars)
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time").sort_index()
    df["return"] = pd.to_numeric(df["close"], errors="coerce").pct_change()
    df["return_pct"] = df["return"] * 100
    return df


def analyze_day_of_week(data: pd.DataFrame) -> List[Dict[str, Any]]:
    """Analyze average return by day of week."""
    dow_map = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday"}
    results = []
    for dow in range(5):
        mask = data.index.dayofweek == dow
        subset = data.loc[mask, "return_pct"].dropna()
        if len(subset) > 0:
            results.append({
                "day": dow_map[dow],
                "avg_return": round(float(subset.mean()), 4),
                "std_return": round(float(subset.std()), 4),
                "win_rate": round(float((subset > 0).sum() / len(subset) * 100), 2),
                "sample_size": len(subset),
            })
    return results


def analyze_month_of_year(data: pd.DataFrame) -> List[Dict[str, Any]]:
    """Analyze average return by month."""
    vn_months = [
        "", "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    results = []
    for month in range(1, 13):
        mask = data.index.month == month
        subset = data.loc[mask, "return_pct"].dropna()
        if len(subset) > 0:
            results.append({
                "month": vn_months[month],
                "month_num": month,
                "avg_return": round(float(subset.mean()), 4),
                "win_rate": round(float((subset > 0).sum() / len(subset) * 100), 2),
                "sample_size": len(subset),
            })
    return results


def analyze_turn_of_month(data: pd.DataFrame) -> Dict[str, float]:
    """Analyze returns around turn of month (last 2 days + first 3 days)."""
    # Last 2 trading days of month
    month_groups = data.groupby(data.index.to_period("M"))
    last_2d = []
    first_3d = []
    middle = []

    for _, group in month_groups:
        g = group.sort_index()
        if len(g) < 5:
            continue
        last = g.iloc[-2:]["return_pct"].dropna()
        first = g.iloc[:3]["return_pct"].dropna()
        mid = g.iloc[3:-2]["return_pct"].dropna()
        last_2d.extend(last.tolist())
        first_3d.extend(first.tolist())
        middle.extend(mid.tolist())

    return {
        "last_2_days_avg": round(float(np.mean(last_2d)), 4) if last_2d else 0,
        "first_3_days_avg": round(float(np.mean(first_3d)), 4) if first_3d else 0,
        "middle_days_avg": round(float(np.mean(middle)), 4) if middle else 0,
    }


def analyze_all(symbol: str = "VNINDEX") -> Dict[str, Any]:
    """Run full seasonality analysis."""
    data = _fetch_market_returns(days=730)
    if data is None:
        return {"symbol": symbol, "error": "Insufficient market data"}

    return {
        "symbol": symbol,
        "analysis_date": datetime.now(TZ_VN).strftime("%Y-%m-%d"),
        "data_range_days": (data.index[-1] - data.index[0]).days,
        "total_trading_days": len(data),
        "day_of_week": analyze_day_of_week(data),
        "month_of_year": analyze_month_of_year(data),
        "turn_of_month": analyze_turn_of_month(data),
        "overall_avg_return": round(float(data["return_pct"].mean()), 4),
        "overall_win_rate": round(float((data["return_pct"] > 0).sum() / len(data) * 100), 2),
    }
