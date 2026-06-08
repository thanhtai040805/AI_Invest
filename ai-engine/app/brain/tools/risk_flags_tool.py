"""Risk Flags Tool — detects warning signals for Vietnamese stocks.

Agent-facing interface around :mod:`app.services.risk_flags_v2`.
Queries the DB for pre-computed 10 risk flags (batch ETL).
No per-symbol API calls — all flags are pre-computed during daily ETL.
"""
from __future__ import annotations

import json
import logging

from app.brain.agents.core.tools import BaseTool
from app.services.risk_flags_v2 import get_active_flags, get_hard_blocked, get_soft_flag_count

logger = logging.getLogger(__name__)

HARD_FLAGS_LABEL = {"CANH_BAO_TC", "CHAM_BAO_TC"}


class RiskFlagsTool(BaseTool):
    """Query pre-computed risk flags for a given stock symbol.

    Reads from the `risk_flags` table, populated daily by the batch ETL.
    10 flags total: 2 HARD (CANH_BAO_TC, CHAM_BAO_TC → block BUY)
    and 8 SOFT (add risk but don't block automatically).

    Use this BEFORE making a buy decision, or to understand why
    a symbol might have elevated risk.
    """

    name = "risk_flags"
    description = (
        "Query pre-computed risk flags for a Vietnamese stock symbol. "
        "Flags are computed daily from structured data (financial statements, "
        "OHLCV, foreign flow, insider trades, news). "
        "2 HARD flags (CANH_BAO_TC, CHAM_BAO_TC) block BUY. "
        "8 SOFT flags add to risk assessment. "
        "Returns active flags with descriptions and sources."
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

            hard_flags = [f for f in flags if f["flag_type"] in HARD_FLAGS_LABEL]
            soft_flags = [f for f in flags if f["flag_type"] not in HARD_FLAGS_LABEL]

            result = {
                "symbol": symbol,
                "hard_blocked": hard_blocked,
                "total_active_flags": len(flags),
                "hard_flags": hard_flags,
                "soft_flags": soft_flags,
                "soft_flag_count": soft_count,
                "risk_level": "HARD" if hard_blocked else ("HIGH" if soft_count >= 3 else "MEDIUM" if soft_count >= 1 else "LOW"),
                "summary": self._build_summary(symbol, hard_blocked, hard_flags, soft_flags),
            }

            return json.dumps(result, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.exception("risk_flags failed for %s", symbol)
            return json.dumps({
                "symbol": symbol,
                "error": str(e),
                "total_active_flags": 0,
                "hard_blocked": False,
                "hard_flags": [],
                "soft_flags": [],
                "summary": f"Risk check failed: {e}",
            }, ensure_ascii=False)

    @staticmethod
    def _build_summary(symbol: str, hard_blocked: bool, hard_flags: list, soft_flags: list) -> str:
        parts = []
        if hard_blocked:
            descs = [f["description"] for f in hard_flags]
            parts.append(f"HARD BLOCK: {'; '.join(descs)}")
        if soft_flags:
            parts.append(f"{len(soft_flags)} soft flags")
        if not parts:
            return f"No active risk flags for {symbol}."
        return f"{' | '.join(parts)}"
