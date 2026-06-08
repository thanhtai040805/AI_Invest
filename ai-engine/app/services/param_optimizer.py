"""Parameter Optimizer — grid search and sensitivity analysis for backtest strategies.

Optimizes strategy parameters (SMA periods, RSI thresholds, stop-loss levels)
by running multiple backtests and comparing performance metrics.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ParamGrid:
    """Defines a parameter search space."""
    name: str
    values: List[Any]


@dataclass
class StrategyTemplate:
    """Strategy config template with parameter placeholders."""
    type: str
    param_template: Dict[str, Any]  # e.g., {"fast": "{{fast}}", "slow": "{{slow}}"}


@dataclass
class OptimizationResult:
    params: Dict[str, Any]
    metrics: Dict[str, float]
    sort_key: float = 0.0


def _run_single_backtest(
    symbol: str,
    start_date: str,
    end_date: str,
    source: str,
    strategy_type: str,
    params: Dict[str, Any],
    run_id_prefix: str,
) -> Optional[Dict[str, Any]]:
    """Run a single backtest with given parameters and return metrics."""
    try:
        import asyncio
        from app.routers.backtest import BacktestRequest, run_backtest_route
        from fastapi import Request as FastAPIRequest

        # Use a simple synchronous approach
        request = BacktestRequest(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            strategy_config={"type": strategy_type, "params": params},
            source=source,
        )

        # Create a mock scope for FastAPI
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(run_backtest_route(request))
        finally:
            loop.close()

        return result
    except Exception as e:
        logger.debug("Grid search single run failed: %s", e)
        return None


def _extract_metric(result: Dict[str, Any], metric: str) -> float:
    """Extract a single metric from backtest result."""
    metrics = result.get("metrics", {})
    if isinstance(metrics, dict):
        val = metrics.get(metric, metrics.get(metric.lower(), 0))
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def grid_search(
    symbol: str,
    start_date: str,
    end_date: str,
    strategy_type: str,
    param_grid: Dict[str, List[Any]],
    metric: str = "sharpe",
    maximize: bool = True,
    source: str = "dnse",
    max_combinations: int = 50,
) -> Dict[str, Any]:
    """Run grid search over strategy parameters.

    Args:
        symbol: Ticker symbol.
        start_date: Backtest start (YYYY-MM-DD).
        end_date: Backtest end (YYYY-MM-DD).
        strategy_type: Strategy type (sma_cross, rsi, bollinger).
        param_grid: Dict of param_name -> list of values to try.
        metric: Metric to optimize (sharpe, total_return, sortino, calmar).
        maximize: True to maximize, False to minimize.
        source: Data source.
        max_combinations: Max param combinations to try.

    Returns:
        Dict with best_params, best_metric, all_results, sensitivity.
    """
    keys = list(param_grid.keys())
    value_lists = [param_grid[k] for k in keys]
    combinations = list(itertools.product(*value_lists))

    if len(combinations) > max_combinations:
        # Uniformly sample if too many
        indices = np.linspace(0, len(combinations) - 1, max_combinations, dtype=int)
        combinations = [combinations[i] for i in indices]

    results: List[OptimizationResult] = []

    for combo in combinations:
        params = dict(zip(keys, combo))
        result = _run_single_backtest(
            symbol, start_date, end_date, source,
            strategy_type, params,
            f"gs_{symbol}",
        )
        if result is None:
            continue

        metric_val = _extract_metric(result, metric)

        results.append(OptimizationResult(
            params=params,
            metrics=result.get("metrics", {}),
            sort_key=metric_val,
        ))

    if not results:
        return {
            "symbol": symbol,
            "strategy": strategy_type,
            "error": "No successful backtest runs",
        }

    # Sort by metric
    results.sort(key=lambda r: r.sort_key, reverse=maximize)
    best = results[0]

    # Sensitivity analysis: vary one param at a time around best
    sensitivity: Dict[str, List[Dict[str, Any]]] = {}
    for i, key in enumerate(keys):
        sensitivity[key] = []
        for val in param_grid[key]:
            test_params = dict(best.params)
            test_params[key] = val
            # Find matching result
            matching = [r for r in results if r.params.get(key) == val]
            if matching:
                avg_metric = np.mean([m.sort_key for m in matching])
                count = len(matching)
            else:
                avg_metric = best.sort_key
                count = 0
            sensitivity[key].append({
                "value": val,
                "avg_metric": round(float(avg_metric), 4),
                "count": count,
            })

    return {
        "symbol": symbol,
        "strategy": strategy_type,
        "metric": metric,
        "combinations_tried": len(results),
        "best_params": best.params,
        "best_metric": round(float(best.sort_key), 4),
        "best_metrics": {k: round(float(v), 4) for k, v in best.metrics.items()},
        "all_results": [
            {
                "params": r.params,
                "metric": round(float(r.sort_key), 4),
            }
            for r in results[:20]
        ],
        "sensitivity": sensitivity,
    }
