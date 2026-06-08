"""Time series forecasting — ARIMA/SARIMA for VN stock price prediction.

Uses auto ARIMA to find optimal (p,d,q) parameters and generates
out-of-sample forecasts with confidence intervals.
"""

from __future__ import annotations

import logging
import warnings
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore", category=UserWarning, module="pmdarima")

TZ_VN = timezone(timedelta(hours=7))


def _fetch_returns(symbol: str, days: int = 365) -> Optional[pd.Series]:
    """Fetch daily close prices and compute log returns."""
    import asyncio
    from app.services.market_data_service import market_data_svc

    end = datetime.now(TZ_VN)
    start = end - timedelta(days=days)

    ohlcv = asyncio.run(
        market_data_svc.get_ohlcv(
            symbol, interval="1D",
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
        )
    )
    bars = ohlcv.get("data", [])
    if len(bars) < 60:
        return None

    df = pd.DataFrame(bars)
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time").sort_index()
    close = pd.to_numeric(df["close"], errors="coerce")
    returns = np.log(close / close.shift(1)).dropna()
    return returns


def forecast_arima(
    symbol: str,
    forecast_horizon: int = 20,
    max_p: int = 5,
    max_d: int = 2,
    max_q: int = 5,
    seasonal: bool = False,
) -> Dict[str, Any]:
    """Fit auto ARIMA and generate forecast.

    Args:
        symbol: Ticker symbol.
        forecast_horizon: Days to forecast ahead.
        max_p: Max AR order.
        max_d: Max differencing.
        max_q: Max MA order.
        seasonal: Whether to fit SARIMA.

    Returns:
        Dict with forecast, confidence intervals, model info.
    """
    try:
        from pmdarima import auto_arima
    except ImportError:
        return {"symbol": symbol, "error": "pmdarima not installed. Run: pip install pmdarima"}

    returns = _fetch_returns(symbol)
    if returns is None or len(returns) < 60:
        return {"symbol": symbol, "error": "Insufficient data (need >= 60 days)"}

    try:
        model = auto_arima(
            returns.values,
            start_p=1, max_p=max_p,
            start_d=1, max_d=max_d,
            start_q=1, max_q=max_q,
            seasonal=seasonal,
            m=5 if seasonal else 1,
            trace=False,
            error_action="ignore",
            suppress_warnings=True,
            stepwise=True,
            information_criterion="aic",
        )

        forecast_result = model.predict(n_periods=forecast_horizon, return_conf_int=True)
        pred_values = forecast_result[0]
        conf_int = forecast_result[1] if len(forecast_result) > 1 else None

        last_date = returns.index[-1]
        forecast_dates = pd.date_range(
            start=last_date + timedelta(days=1),
            periods=forecast_horizon,
            freq="B",  # business days
        )

        forecast_points = [
            {
                "date": str(d.date()),
                "predictedReturn": round(float(p), 6),
                "lowerCi": round(float(conf_int[i][0]), 6) if conf_int is not None else None,
                "upperCi": round(float(conf_int[i][1]), 6) if conf_int is not None else None,
            }
            for i, (d, p) in enumerate(zip(forecast_dates, pred_values))
        ]

        # Direction signal
        avg_forecast = np.mean(pred_values)
        direction = "UP" if avg_forecast > 0 else ("DOWN" if avg_forecast < 0 else "NEUTRAL")

        # Summary stats
        last_price_result = _fetch_price(symbol)
        if last_price_result:
            last_price = last_price_result
            forecast_prices = [last_price * np.exp(np.sum(pred_values[:i+1])) for i in range(len(pred_values))]
        else:
            forecast_prices = []

        return {
            "symbol": symbol,
            "model": str(model),
            "order": {
                "p": model.order[0] if hasattr(model, "order") else None,
                "d": model.order[1] if hasattr(model, "order") else None,
                "q": model.order[2] if hasattr(model, "order") else None,
            },
            "aic": round(model.aic(), 2) if hasattr(model, "aic") else None,
            "forecast_horizon": forecast_horizon,
            "direction": direction,
            "avg_predicted_return": round(float(avg_forecast * 100), 4),
            "forecast": forecast_points,
            "forecast_prices": [round(float(p), 2) for p in forecast_prices] if forecast_prices else [],
        }

    except Exception as e:
        logger.exception("ARIMA forecast failed for %s", symbol)
        return {"symbol": symbol, "error": str(e)}


def _fetch_price(symbol: str) -> Optional[float]:
    """Fetch latest close price."""
    import asyncio
    from app.services.market_data_service import market_data_svc

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        quote = loop.run_until_complete(market_data_svc.get_quote(symbol))
        loop.close()
        return float(quote.get("price", 0) or quote.get("close", 0))
    except Exception:
        return None
