"""Runs Router - Serve backtest run data (equity curve, metrics, Pine Script)"""

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["Runs"])

RUNS_DIR = Path(__file__).resolve().parents[1] / "runs"


def _load_csv(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="Run not found")

    artifacts = run_dir / "artifacts"
    equity_csv = artifacts / "equity.csv"
    metrics_csv = artifacts / "metrics.csv"
    trades_csv = artifacts / "trades.csv"
    summary_json = artifacts / "summary.json"

    equity_curve: Optional[List[Dict[str, Any]]] = None
    if equity_csv.exists():
        raw = _load_csv(equity_csv)
        if raw:
            equity_curve = [
                {"time": r.get("time", r.get("date", "")), "equity": float(r.get("equity", r.get("value", 0)))}
                for r in raw
            ]

    metrics: Optional[Dict[str, float]] = None
    if metrics_csv.exists():
        raw = _load_csv(metrics_csv)
        if raw:
            metrics = {k: float(v) for k, v in raw[0].items() if v}

    trades: Optional[List[Dict[str, Any]]] = None
    if trades_csv.exists():
        raw = _load_csv(trades_csv)
        if raw:
            trades = raw

    summary = _load_json(summary_json)

    result: Dict[str, Any] = {}
    if equity_curve:
        result["equity_curve"] = equity_curve
    if metrics:
        result["metrics"] = metrics
    if trades:
        result["trades"] = trades
    if summary:
        result["summary"] = summary

    return result


@router.get("/runs/{run_id}/pine")
async def get_run_pine(run_id: str):
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

    content = pine_path.read_text(encoding="utf-8")
    return {"exists": True, "content": content}
