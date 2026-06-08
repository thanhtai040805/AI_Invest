"""Seasonality Analysis Tool — VN market calendar effects.

Agent-facing interface around :mod:`app.services.seasonality`.
"""

from __future__ import annotations

import json
import logging

from app.brain.agents.core.tools import BaseTool

logger = logging.getLogger(__name__)


class SeasonalityTool(BaseTool):
    """Analyze VN market seasonality patterns.

    Returns day-of-week effects, month-of-year effects, turn-of-month
    effects, and overall market win rate.

    Use this to understand historical seasonal patterns
    before making timing decisions.
    """

    name = "seasonality"
    description = (
        "Analyze VN market seasonality: day-of-week effects, "
        "month-of-year effects, turn-of-month patterns, "
        "and overall win rate. Based on VNINDEX historical data. "
        "Use this for timing and calendar-aware decisions."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Index symbol (default: VNINDEX)",
            },
        },
        "required": [],
    }
    repeatable = True

    def execute(self, **kwargs: str) -> str:
        from app.services.seasonality import analyze_all

        symbol = kwargs.get("symbol", "VNINDEX")
        try:
            result = analyze_all(symbol)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception("seasonality failed")
            return json.dumps({"error": str(e)}, ensure_ascii=False)
