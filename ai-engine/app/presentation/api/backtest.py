"""Backtest Router — wires Vibe-Trading backtest engine with DNSE/VietFin loaders."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["Backtest"])


class BacktestRequest(BaseModel):
    symbol: str = Field(..., description="Stock symbol (e.g., VCB)")
    start_date: str = Field(..., description="Start date (YYYY-MM-DD)")
    end_date: str = Field(..., description="End date (YYYY-MM-DD)")
    strategy_config: Dict[str, Any] = Field(..., description="Strategy configuration")
    source: str = Field(default="dnse", description="Data source: dnse, vietfin, auto")
    use_macro_risk: bool = Field(default=True, description="Enable Institutional Macro Risk Shield")


_SIGNAL_TEMPLATE = '''"""Signal engine for {symbol} — auto-generated from strategy config."""
import pandas as pd
import numpy as np


class SignalEngine:
    """Signal generation engine for backtest."""

    def __init__(self, config: dict | None = None):
        self.config = config or {{}}

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate signals: 1 (buy), -1 (sell), 0 (hold).

        Args:
            df: OHLCV DataFrame with columns open, high, low, close, volume.

        Returns:
            DataFrame with added 'signal' column.
        """
        df = df.copy()
{signal_body}
        return df
'''


def _render_signal_body(config: Dict[str, Any]) -> str:
    """Render the generate() body from strategy config."""
    strategy_type = config.get("type", "sma_cross")
    params = config.get("params", {})

    if strategy_type == "sma_cross":
        fast = params.get("fast", 10)
        slow = params.get("slow", 30)
        return (
            f'        df["sma_fast"] = df["close"].rolling({fast}).mean()\n'
            f'        df["sma_slow"] = df["close"].rolling({slow}).mean()\n'
            f'        df["signal"] = np.select(\n'
            f"            [\n"
            f'                df["sma_fast"] > df["sma_slow"],\n'
            f'                df["sma_fast"] < df["sma_slow"],\n'
            f"            ],\n"
            f"            [1.0, -1.0],\n"
            f'            default=0.0,\n'
            f"        )\n"
            f'        df.loc[df["signal"] == 1.0, "signal"] = 1.0\n'
            f'        df.loc[df["signal"] == -1.0, "signal"] = -1.0\n'
            f"        return df"
        )

    if strategy_type == "rsi":
        period = params.get("period", 14)
        oversold = params.get("oversold", 30)
        overbought = params.get("overbought", 70)
        return (
            f'        delta = df["close"].diff()\n'
            f'        gain = delta.where(delta > 0, 0.0).rolling({period}).mean()\n'
            f'        loss = (-delta.where(delta < 0, 0.0)).rolling({period}).mean()\n'
            f'        rs = gain / loss.replace(0, np.nan)\n'
            f'        df["rsi"] = 100 - (100 / (1 + rs))\n'
            f'        df["signal"] = np.select(\n'
            f"            [\n"
            f'                df["rsi"] < {oversold},\n'
            f'                df["rsi"] > {overbought},\n'
            f"            ],\n"
            f"            [1.0, -1.0],\n"
            f'            default=0.0,\n'
            f"        )\n"
            f"        return df"
        )

    if strategy_type == "bollinger":
        period = params.get("period", 20)
        std = params.get("std_dev", 2)
        return (
            f'        df["sma"] = df["close"].rolling({period}).mean()\n'
            f'        df["upper"] = df["sma"] + {std} * df["close"].rolling({period}).std()\n'
            f'        df["lower"] = df["sma"] - {std} * df["close"].rolling({period}).std()\n'
            f'        df["signal"] = np.select(\n'
            f"            [\n"
            f'                df["close"] < df["lower"],\n'
            f'                df["close"] > df["upper"],\n'
            f"            ],\n"
            f"            [1.0, -1.0],\n"
            f'            default=0.0,\n'
            f"        )\n"
            f"        return df"
        )

    # Default: buy signal every day
    return f'        df["signal"] = 1.0\n        return df'


@router.post("/run")
async def run_backtest_route(request: BacktestRequest):
    """Run backtest using Vibe-Trading engine with real data loader."""
    try:
        symbol = request.symbol.upper()
        run_id = f"vn_{symbol}_{request.start_date}_{request.end_date}"

        # Create a temp run directory with config.json + signal_engine.py
        runs_root = Path(os.getenv("RUNS_DIR", tempfile.gettempdir())) / "backtest_runs" / run_id
        runs_root.mkdir(parents=True, exist_ok=True)
        code_dir = runs_root / "code"
        code_dir.mkdir(exist_ok=True)

        config = {
            "source": request.source,
            "codes": [symbol],
            "start_date": request.start_date,
            "end_date": request.end_date,
            "interval": "1D",
            "use_macro_risk": request.use_macro_risk,
        }
        (runs_root / "config.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        signal_body = _render_signal_body(request.strategy_config)
        signal_source = _SIGNAL_TEMPLATE.format(
            symbol=symbol,
            signal_body=signal_body,
        )
        (code_dir / "signal_engine.py").write_text(signal_source, encoding="utf-8")

        # Run backtest in a thread (it's synchronous)
        from app.backtest.engine import run_backtest

        loop = asyncio.get_event_loop()
        result_json = await loop.run_in_executor(None, run_backtest, str(runs_root))
        result = json.loads(result_json)

        # Parse artifacts for structured response
        artifacts = result.get("artifacts", {})

        # Read metrics from metrics.json
        metrics: Dict[str, Any] = {}
        equity_points: List[Dict[str, Any]] = []
        trade_list: List[Dict[str, Any]] = []

        metrics_path = artifacts.get("metrics_json") or str(runs_root / "metrics.json")
        if os.path.exists(metrics_path):
            try:
                with open(metrics_path, encoding="utf-8") as f:
                    metrics = json.load(f)
            except Exception:
                pass

        # Read equity curve
        equity_path = artifacts.get("equity_csv") or str(runs_root / "equity.csv")
        if os.path.exists(equity_path):
            try:
                import pandas as pd
                eq = pd.read_csv(equity_path)
                equity_points = eq.to_dict(orient="records")
            except Exception:
                pass

        # Read trades
        trades_path = artifacts.get("trades_csv") or str(runs_root / "trades.csv")
        if os.path.exists(trades_path):
            try:
                import pandas as pd
                tr = pd.read_csv(trades_path)
                trade_list = tr.to_dict(orient="records")
            except Exception:
                pass

        return {
            "status": result.get("status", "error"),
            "run_id": run_id,
            "metrics": metrics,
            "equity_curve": equity_points[:500],
            "trades": trade_list[:200],
            "logs": (result.get("stdout", "") + "\n" + result.get("stderr", "")),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{run_id}")
async def get_backtest_status(run_id: str):
    runs_root = Path(os.getenv("RUNS_DIR", tempfile.gettempdir())) / "backtest_runs" / run_id
    if not runs_root.exists():
        return {"run_id": run_id, "status": "not_found"}
    return {
        "run_id": run_id,
        "status": "completed" if (runs_root / "metrics.json").exists() else "running",
    }


@router.get("/history")
async def get_backtest_history():
    runs_root = Path(os.getenv("RUNS_DIR", tempfile.gettempdir())) / "backtest_runs"
    if not runs_root.exists():
        return {"runs": []}
    try:
        runs = sorted(
            [d.name for d in runs_root.iterdir() if d.is_dir()],
            reverse=True,
        )[:20]
        return {"runs": runs}
    except Exception:
        return {"runs": []}


@router.get("/results/{run_id}")
async def get_backtest_results(run_id: str):
    runs_root = Path(os.getenv("RUNS_DIR", tempfile.gettempdir())) / "backtest_runs" / run_id
    if not runs_root.exists():
        return {"run_id": run_id, "status": "not_found"}

    metrics: Dict[str, Any] = {}
    equity_points: List[Dict[str, Any]] = []
    trade_list: List[Dict[str, Any]] = []

    metrics_path = runs_root / "metrics.json"
    if metrics_path.exists():
        try:
            with open(metrics_path, encoding="utf-8") as f:
                metrics = json.load(f)
        except Exception:
            pass

    equity_path = runs_root / "equity.csv"
    if equity_path.exists():
        try:
            import pandas as pd
            eq = pd.read_csv(equity_path)
            equity_points = eq.to_dict(orient="records")
        except Exception:
            pass

    trades_path = runs_root / "trades.csv"
    if trades_path.exists():
        try:
            import pandas as pd
            tr = pd.read_csv(trades_path)
            trade_list = tr.to_dict(orient="records")
        except Exception:
            pass

    return {
        "run_id": run_id,
        "status": "completed",
        "metrics": metrics,
        "equity_curve": equity_points[:500],
        "trades": trade_list[:200],
    }
