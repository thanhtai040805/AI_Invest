"""Disclosures Tool — CRS 7-layer risk assessment for Vietnamese stocks.

Queries the risk_assessments table for pre-computed CRS scores.
No per-symbol API calls — all layers are computed during daily ETL.
"""
from __future__ import annotations

import json
import logging

from app.brain.agents.core.tools import BaseTool
from app.brain.risk.queries import get_active_flags, get_hard_blocked

logger = logging.getLogger(__name__)


class DisclosuresTool(BaseTool):
    """Retrieve CRS 7-layer risk flags for a Vietnamese stock.

    Reads from the `risk_assessments` table (batch ETL).
    7 layers: quant, fundamental, market, macro, global, regulatory, behavioral.
    Returns hard_blocked status and active flags with descriptions.
    """

    name = "disclosures"
    description = (
        "Retrieve CRS 7-layer risk flags for a Vietnamese stock symbol. "
        "Returns active flags from the daily pre-computed CRS risk engine. "
        "hard_blocked=true indicates critical risk that blocks trading. "
        "Use this to check red flags before making investment decisions."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Vietnamese ticker symbol (e.g., VCB, HPG, FPT).",
            },
        },
        "required": ["symbol"],
    }
    repeatable = True

    def execute(self, **kwargs: str) -> str:
        symbol = kwargs.get("symbol", "").strip().upper()
        try:
            flags = get_active_flags(symbol)
            hard_blocked = get_hard_blocked(symbol)

            return json.dumps({
                "symbol": symbol,
                "hard_blocked": hard_blocked,
                "total_flags": len(flags),
                "flags": flags,
            }, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.exception("disclosures failed for %s", symbol)
            return json.dumps({
                "symbol": symbol,
                "error": str(e),
                "flags": [],
                "summary": f"Disclosures check failed: {e}",
            }, ensure_ascii=False)
