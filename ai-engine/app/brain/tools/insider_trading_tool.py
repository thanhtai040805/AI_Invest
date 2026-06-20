"""Insider Trading Tool — retrieves insider trading data for Vietnamese stocks.

Agent-facing interface around :mod:`app.infrastructure.data_pipelines.scraper_insider`.
"""

from __future__ import annotations

import json
import logging

from app.brain.agents.core.tools import BaseTool

logger = logging.getLogger(__name__)


class InsiderTradingTool(BaseTool):
    """Retrieve insider trading data for a Vietnamese stock symbol.

    Returns major shareholders, board of directors / officers,
    ownership breakdown (state, institutional, insider, retail),
    and recent insider-related news from CafeF.

    Use this when you need to understand who controls a company
    or detect unusual insider activity.
    """

    name = "insider_trading"
    description = (
        "Retrieve insider trading data for a Vietnamese stock symbol. "
        "Returns: major shareholders (>5% ownership), board of directors, "
        "ownership breakdown by investor type, and recent insider-related news. "
        "Use this to detect unusual insider activity or understand ownership structure."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Vietnamese ticker symbol (e.g., FPT, HPG, VNM).",
            },
        },
        "required": ["symbol"],
    }
    repeatable = True

    def execute(self, **kwargs: str) -> str:
        import asyncio

        symbol = kwargs["symbol"]
        try:
            from app.infrastructure.data_pipelines.scraper_insider import get_insider_data
            result = asyncio.run(get_insider_data(symbol))
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception("insider_trading failed for %s", symbol)
            return json.dumps({
                "symbol": symbol,
                "error": str(e),
                "shareholders": [],
                "officers": [],
                "ownership": [],
                "news": [],
                "summary": f"Insider data retrieval failed: {e}",
            }, ensure_ascii=False)
