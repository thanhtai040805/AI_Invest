"""Parameter Optimizer Tool — grid search for backtest strategy parameters.

Agent-facing interface around :mod:`app.services.param_optimizer`.
"""

from __future__ import annotations

import json
import logging

from app.brain.agents.core.tools import BaseTool

logger = logging.getLogger(__name__)


class ParamOptimizerTool(BaseTool):
    """Run grid search to find optimal strategy parameters.

    Tests multiple parameter combinations (e.g., SMA fast=5,10,20,
    slow=20,30,50) and returns the best combination by Sharpe,
    total return, or other metrics.

    Supports strategies: sma_cross, rsi, bollinger.
    """

    name = "param_optimizer"
    description = (
        "Run grid search optimization for backtest strategy parameters. "
        "Tries all parameter combinations, runs backtests, and returns "
        "the best configuration by Sharpe (default) or other metrics. "
        "Also provides sensitivity analysis per parameter."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Ticker symbol",
            },
            "start_date": {
                "type": "string",
                "description": "Start date (YYYY-MM-DD)",
            },
            "end_date": {
                "type": "string",
                "description": "End date (YYYY-MM-DD)",
            },
            "strategy_type": {
                "type": "string",
                "enum": ["sma_cross", "rsi", "bollinger"],
                "description": "Strategy type",
            },
            "param_grid_json": {
                "type": "string",
                "description": "JSON dict of param -> list of values. Example: {\"fast\": [5,10,20], \"slow\": [20,30,50]}",
            },
            "metric": {
                "type": "string",
                "description": "Metric to optimize: sharpe, total_return, sortino, calmar. Default: sharpe",
            },
        },
        "required": ["symbol", "start_date", "end_date", "strategy_type", "param_grid_json"],
    }
    repeatable = True

    def execute(self, **kwargs: str) -> str:
        from app.services.param_optimizer import grid_search

        symbol = kwargs.get("symbol", "")
        start = kwargs.get("start_date", "")
        end = kwargs.get("end_date", "")
        st = kwargs.get("strategy_type", "sma_cross")
        metric = kwargs.get("metric", "sharpe")

        try:
            param_grid = json.loads(kwargs.get("param_grid_json", "{}"))
            result = grid_search(symbol, start, end, st, param_grid, metric=metric)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception("param_optimizer failed")
            return json.dumps({"error": str(e)}, ensure_ascii=False)
