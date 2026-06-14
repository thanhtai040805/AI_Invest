"""Risk Flags Tool — CRS 7-layer risk assessment for Vietnamese stocks.

Queries the risk_assessments table for pre-computed CRS scores.
No per-symbol API calls — all layers are computed during daily ETL.
"""
from __future__ import annotations

import json
import logging

from app.brain.agents.core.tools import BaseTool
from app.brain.risk.queries import get_active_flags, get_hard_blocked, get_soft_flag_count

logger = logging.getLogger(__name__)


class RiskFlagsTool(BaseTool):
    """Query CRS 7-layer risk assessment for a given stock symbol.

    Reads from the `risk_assessments` table, populated daily by the batch ETL.
    7 layers: quant, fundamental, market, macro, global, regulatory, behavioral.
    Returns hard_blocked status, soft flag count, and active flags.
    """

    name = "risk_flags"
    description = (
        "Query CRS 7-layer risk assessment for a Vietnamese stock symbol. "
        "Flags are computed daily from structured data, OHLCV, news, and macro. "
        "Includes hard_blocked status (blocks BUY), soft flag count, "
        "and all active CRS layer flags with descriptions."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Vietnamese ticker symbol (e.g., VCB, HPG, FPT).",
            },
            "include_resolved": {
                "type": "boolean",
                "description": "Also show recently resolved (inactive) flags.",
                "default": False,
            },
        },
        "required": ["symbol"],
    }
    repeatable = True

    def execute(self, **kwargs: str) -> str:
        symbol = kwargs.get("symbol", "").strip().upper()
        include_resolved = kwargs.get("include_resolved", False)

        try:
            flags = get_active_flags(symbol)
            hard_blocked = get_hard_blocked(symbol)
            soft_count = get_soft_flag_count(symbol)

            result = {
                "symbol": symbol,
                "hard_blocked": hard_blocked,
                "total_active_flags": len(flags),
                "soft_flag_count": soft_count,
                "risk_level": "HARD" if hard_blocked else ("HIGH" if soft_count >= 3 else "MEDIUM" if soft_count >= 1 else "LOW"),
                "flags": flags,
                "summary": self._build_summary(symbol, hard_blocked, flags),
            }

            return json.dumps(result, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.exception("risk_flags failed for %s", symbol)
            return json.dumps({
                "symbol": symbol,
                "error": str(e),
                "total_active_flags": 0,
                "hard_blocked": False,
                "flags": [],
                "summary": f"Risk check failed: {e}",
            }, ensure_ascii=False)

    @staticmethod
    def _build_summary(symbol: str, hard_blocked: bool, flags: list) -> str:
        parts = []
        if hard_blocked:
            parts.append("HARD BLOCK by CRS")
        if flags:
            parts.append(f"{len(flags)} active flags")
        if not parts:
            return f"No active risk flags for {symbol}."
        return f"{' | '.join(parts)}"
