"""Runs Router — Serve backtest run data (equity curve, metrics, Pine Script, code, list)."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["Runs"])

RUNS_DIR = Path(__file__).resolve().parents[1] / "runs"


def _load_csv(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


# ── Single-run detail ──


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    """Fetch full details for a run — equity curve, metrics, trades, artifacts, state."""
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="Run not found")

    artifacts = run_dir / "artifacts"

    equity_curve: Optional[List[Dict[str, Any]]] = None
    equity_csv = artifacts / "equity.csv"
    if equity_csv.exists():
        raw = _load_csv(equity_csv)
        if raw:
            equity_curve = [
                {"time": r.get("time", r.get("date", "")), "equity": float(r.get("equity", r.get("value", 0)))}
                for r in raw
            ]

    metrics: Optional[Dict[str, float]] = None
    metrics_csv = artifacts / "metrics.csv"
    if metrics_csv.exists():
        raw = _load_csv(metrics_csv)
        if raw:
            metrics = {k: float(v) for k, v in raw[0].items() if v}

    trades: Optional[List[Dict[str, Any]]] = None
    trades_csv = artifacts / "trades.csv"
    if trades_csv.exists():
        raw = _load_csv(trades_csv)
        if raw:
            trades = raw

    summary = _load_json(artifacts / "summary.json")
    state_data = _load_json(run_dir / "state.json")

    status = "unknown"
    reason: Optional[str] = None
    if state_data:
        status = str(state_data.get("status") or "unknown").lower()
        reason = state_data.get("reason")

    price_series: Optional[Dict[str, List[Dict[str, Any]]]] = None
    if artifacts.exists():
        ohlcv_files = sorted(artifacts.glob("ohlcv_*.csv"))
        if ohlcv_files:
            price_series = {}
            for f in ohlcv_files:
                match = re.search(r"ohlcv_(.+)\.csv$", f.name)
                symbol = match.group(1).upper() if match else "unknown"
                raw = _load_csv(f)
                if raw:
                    price_series[symbol] = [
                        {
                            "time": r.get("date", r.get("time", "")),
                            "open": float(r.get("open", 0)),
                            "high": float(r.get("high", 0)),
                            "low": float(r.get("low", 0)),
                            "close": float(r.get("close", 0)),
                            "volume": float(r.get("volume", 0)),
                        }
                        for r in raw
                    ]

    artifact_list: List[Dict[str, Any]] = []
    if artifacts.exists():
        for f in artifacts.iterdir():
            if f.is_file():
                artifact_list.append({
                    "name": f.name,
                    "path": str(f),
                    "type": f.suffix.lstrip("."),
                    "size": f.stat().st_size,
                    "exists": True,
                })

    result: Dict[str, Any] = {
        "run_id": run_id,
        "status": status,
        "reason": reason,
    }
    if equity_curve:
        result["equity_curve"] = equity_curve
    if price_series:
        result["price_series"] = price_series
    if metrics:
        result["metrics"] = metrics
    if trades:
        result["trades"] = trades
    if summary:
        result["summary"] = summary
    if artifact_list:
        result["artifacts"] = artifact_list
    return result


# ── Pine Script ──


@router.get("/runs/{run_id}/pine")
async def get_run_pine(run_id: str):
    """Return Pine Script file for a run."""
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        return {"exists": False, "content": None}

    pine_path = run_dir / "pine_script.txt"
    if not pine_path.exists():
        pine_path = run_dir / "artifacts" / "strategy.pine"
    if not pine_path.exists():
        artifacts = run_dir / "artifacts"
        pines = list(artifacts.glob("*.pine")) if artifacts.exists() else []
        pine_path = pines[0] if pines else None

    if not pine_path or not pine_path.exists():
        return {"exists": False, "content": None}
    return {"exists": True, "content": pine_path.read_text(encoding="utf-8")}


# ── Strategy code ──


@router.get("/runs/{run_id}/code")
async def get_run_code(run_id: str):
    """Return strategy source files (e.g. signal_engine.py) for a run."""
    code_dir = RUNS_DIR / run_id / "code"
    if not code_dir.exists():
        return {}
    result: Dict[str, str] = {}
    for name in ["signal_engine.py", "strategy.py"]:
        p = code_dir / name
        if p.exists():
            result[name] = p.read_text(encoding="utf-8")
    return result


# ── List runs ──


@router.get("/runs")
async def list_runs(limit: int = 20):
    """List recent runs with summary fields."""
    limit = min(max(1, limit), 100)
    if not RUNS_DIR.exists():
        return []

    dirs = sorted(
        [d for d in RUNS_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")],
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    )

    results: List[Dict[str, Any]] = []
    for d in dirs[:limit]:
        run_id = d.name

        # status
        state_data = _load_json(d / "state.json")
        status = str(state_data.get("status") or "unknown").lower() if state_data else "unknown"
        if status == "unknown" and (d / "artifacts" / "equity.csv").exists():
            status = "success"

        # timestamp
        mtime = dt.fromtimestamp(d.stat().st_mtime)
        created_at = mtime.strftime("%Y-%m-%d %H:%M:%S")

        # prompt
        prompt: Optional[str] = None
        for fname, key in [("req.json", "prompt"), ("planner_output.json", "user_goal")]:
            data = _load_json(d / fname)
            if data:
                prompt = data.get(key) or data.get("goal")
                if prompt:
                    break
        if not prompt:
            pf = d / "user_prompt.txt"
            if pf.exists():
                prompt = pf.read_text(encoding="utf-8").strip()

        # metrics
        total_return: Optional[float] = None
        sharpe: Optional[float] = None
        mf = d / "artifacts" / "metrics.csv"
        if mf.exists():
            try:
                rows = _load_csv(mf)
                if rows:
                    total_return = float(rows[0].get("total_return", 0) or 0)
                    sharpe = float(rows[0].get("sharpe", 0) or 0)
            except (ValueError, TypeError):
                pass

        results.append({
            "run_id": run_id,
            "status": status,
            "created_at": created_at,
            "prompt": prompt or "Manual Analysis",
            "total_return": total_return,
            "sharpe": sharpe,
        })

    return results
