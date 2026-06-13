"""Disclosures Tool — retrieves risk flags for Vietnamese stocks.

Agent-facing interface around :mod:`app.services.risk_flags_v2`.
Uses pre-computed flags from batch ETL — no per-symbol API calls.
"""
from __future__ import annotations

import json
import logging

from app.brain.agents.core.tools import BaseTool
from app.brain.risk.queries import get_active_flags, get_hard_blocked

logger = logging.getLogger(__name__)


class DisclosuresTool(BaseTool):
    """Retrieve risk flags and regulatory disclosures for a Vietnamese stock.

    Reads from the pre-computed `risk_flags` table (batch ETL).
    Includes: CANH_BAO_TC, CHAM_BAO_TC, FLOOR_TRAP, SHARP_DROP,
    KHOI_LUONG_BAT_THUONG, FOREIGN_FLOW_ANOMALY, INSIDER_SELLING_ANOMALY,
    GOVERNANCE_SHOCK, M-Score, F-Score.

    Use this to check red flags before making investment decisions.
    """

    name = "disclosures"
    description = (
        "Retrieve risk flags and regulatory disclosures for a Vietnamese stock symbol. "
        "Returns active flags from the daily pre-computed risk engine. "
        "HARD flags (CANH_BAO_TC, CHAM_BAO_TC) indicate critical issues. "
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
