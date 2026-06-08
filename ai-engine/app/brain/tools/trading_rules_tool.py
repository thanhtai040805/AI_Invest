"""Trading Rules Tool — configurable stop-loss, take-profit, position sizing,
and portfolio constraint checking for Vietnamese equities.

Agent-facing interface around :mod:`app.services.trading_rules`.
"""

from __future__ import annotations

import json
import logging

from app.brain.agents.core.tools import BaseTool

logger = logging.getLogger(__name__)


class TradingRulesTool(BaseTool):
    """Apply trading rules (stop-loss, take-profit, position sizing, constraints).

    Use this tool to:
      - Check if any open position hits stop-loss or take-profit
      - Calculate suggested position size before opening a new trade
      - Check portfolio-level constraints (concentration, drawdown, min cash)
      - Run rebalancing analysis against target weights

    Provide current portfolio state as JSON.
    """

    name = "trading_rules"
    description = (
        "Apply trading rules: stop-loss / take-profit checks, "
        "position sizing calculation, portfolio constraint validation, "
        "and rebalancing analysis. Provide current portfolio data as JSON. "
        "Returns action signals (SELL/HOLD/BUY) with priority ordering."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["check_all", "position_size"],
                "description": "'check_all' to run all rules against a portfolio, 'position_size' to calculate size for a new trade.",
            },
            "portfolio_json": {
                "type": "string",
                "description": "JSON string of portfolio state. Required for 'check_all'. Format: {\"totalValue\":..., \"cash\":..., \"peakValue\":..., \"positions\": [{\"symbol\":\"HPG\",\"entryPrice\":28000,\"currentPrice\":27500,\"quantity\":1000,\"pnlPct\":-0.0178,\"daysHeld\":15,\"entryValue\":...,\"currentValue\":...}]}",
            },
            "symbol": {
                "type": "string",
                "description": "Ticker symbol. Required for 'position_size'.",
            },
            "price": {
                "type": "number",
                "description": "Current price. Required for 'position_size'.",
            },
            "portfolio_value": {
                "type": "number",
                "description": "Total portfolio value in VND. Required for 'position_size'.",
            },
            "atr": {
                "type": "number",
                "description": "Optional ATR value for volatility-adjusted sizing.",
            },
            "peak_prices_json": {
                "type": "string",
                "description": "Optional JSON dict of symbol -> highest price since entry for trailing stops.",
            },
        },
        "required": ["action"],
    }
    repeatable = True

    def execute(self, **kwargs: str) -> str:
        from app.services.trading_rules import (
            TradingRulesConfig,
            PortfolioState,
            PositionInfo,
            check_all_rules,
            calc_suggested_position_size,
        )

        try:
            action = kwargs.get("action", "check_all")

            if action == "position_size":
                symbol = kwargs.get("symbol", "")
                price = float(kwargs.get("price", 0))
                pv = float(kwargs.get("portfolio_value", 0))
                atr = float(kwargs["atr"]) if kwargs.get("atr") else None
                result = calc_suggested_position_size(symbol, price, pv, atr=atr)
                return json.dumps(result, ensure_ascii=False, indent=2)

            if action == "check_all":
                portfolio_raw = json.loads(kwargs.get("portfolio_json", "{}"))
                positions = [
                    PositionInfo(
                        symbol=p["symbol"],
                        entry_price=float(p["entryPrice"]),
                        current_price=float(p["currentPrice"]),
                        quantity=int(p["quantity"]),
                        pnl_pct=float(p.get("pnlPct", 0)),
                        days_held=int(p.get("daysHeld", 0)),
                        entry_value=float(p.get("entryValue", 0)),
                        current_value=float(p.get("currentValue", 0)),
                    )
                    for p in portfolio_raw.get("positions", [])
                ]
                portfolio = PortfolioState(
                    total_value=float(portfolio_raw.get("totalValue", 0)),
                    cash=float(portfolio_raw.get("cash", 0)),
                    positions=positions,
                    peak_value=float(portfolio_raw.get("peakValue", 0)),
                )
                peak_prices = json.loads(kwargs.get("peak_prices_json", "{}")) if kwargs.get("peak_prices_json") else None

                cfg = TradingRulesConfig()
                result = check_all_rules(
                    portfolio,
                    peak_prices=peak_prices,
                    config=cfg,
                )
                return json.dumps(result, ensure_ascii=False, indent=2)

            return json.dumps({"error": f"Unknown action: {action}"})

        except Exception as e:
            logger.exception("trading_rules failed")
            return json.dumps({"error": str(e)}, ensure_ascii=False)
