"""Time Series Forecast Tool — ARIMA/SARIMA forecasting.

Agent-facing interface around :mod:`app.services.time_series_forecast`.
"""

from __future__ import annotations

import json
import logging

from app.brain.agents.core.tools import BaseTool

logger = logging.getLogger(__name__)


class ForecastTool(BaseTool):
    """Forecast stock prices using ARIMA/SARIMA time series models.

    Auto-selects optimal (p,d,q) parameters and generates out-of-sample
    forecasts with confidence intervals.

    Use this for short-term price trend forecasting.
    """

    name = "forecast"
    description = (
        "Forecast stock price direction using ARIMA time series model. "
        "Auto-selects optimal parameters. Returns forecast with confidence "
        "intervals and directional signal (UP/DOWN/NEUTRAL). "
        "Use this for short-term trend prediction."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Ticker symbol",
            },
            "horizon": {
                "type": "integer",
                "description": "Forecast horizon in days (default: 20)",
            },
            "seasonal": {
                "type": "boolean",
                "description": "Use SARIMA with weekly seasonality (default: false)",
            },
        },
        "required": ["symbol"],
    }
    repeatable = True

    def execute(self, **kwargs: str) -> str:
        from app.services.time_series_forecast import forecast_arima

        symbol = kwargs.get("symbol", "")
        horizon = int(kwargs.get("horizon", 20))
        seasonal = kwargs.get("seasonal", "false").lower() == "true"

        try:
            result = forecast_arima(symbol, forecast_horizon=horizon, seasonal=seasonal)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception("forecast failed for %s", symbol)
            return json.dumps({"symbol": symbol, "error": str(e)}, ensure_ascii=False)
