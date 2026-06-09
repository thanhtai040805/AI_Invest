"""On-demand data pipeline — run manually when needed.

NO scheduled/auto execution to avoid wasting LLM tokens.
Run only when explicitly requested:

  1. backfill_ohlcv  — Daily OHLCV backfill
  2. compute_factors — Alpha factor computation
  3. run_risk_flags  — Risk flag scan for watched symbols
  4. train_ml_models — ML model training (no LLM tokens used)

Usage:
    python -m workflows.daily_pipeline
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add parent to path for module imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logger = logging.getLogger("workflows.daily_pipeline")

TZ_VN = timezone(timedelta(hours=7))


def task_backfill_ohlcv(symbols: List[str]) -> Dict[str, Any]:
    """Backfill daily OHLCV for specified symbols."""
    from app.services.ohlcv_backfill import run_backfill
    logger.info("Backfilling OHLCV for %d symbols", len(symbols))
    results = {}
    for sym in symbols:
        try:
            run_backfill(sym)
            results[sym] = "ok"
        except Exception as e:
            logger.error("Backfill failed for %s: %s", sym, e)
            results[sym] = f"error: {e}"
    return {"task": "backfill_ohlcv", "symbols": len(symbols), "results": results}


def task_compute_factors(universe: List[str]) -> Dict[str, Any]:
    """Compute VN-core factors for the universe."""
    from app.brain.quant.factors.vn_ic_tester import VN_FACTORS
    logger.info("VN-core factors: %d defined", len(VN_FACTORS))
    return {
        "task": "compute_factors",
        "n_vn_factors": len(VN_FACTORS),
    }


def task_run_risk_flags(symbols: List[str]) -> Dict[str, Any]:
    """Scan risk flags for watched symbols."""
    import asyncio
    from app.services.risk_flags import check_all_flags

    logger.info("Running risk flags for %d symbols", len(symbols))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        results = {}
        high_count = 0
        for sym in symbols:
            try:
                r = loop.run_until_complete(check_all_flags(sym))
                results[sym] = {
                    "totalFlags": r["totalFlags"],
                    "highCount": r["highCount"],
                }
                high_count += r["highCount"]
            except Exception as e:
                results[sym] = f"error: {e}"
        return {
            "task": "run_risk_flags",
            "symbols_scanned": len(symbols),
            "total_high_flags": high_count,
            "results": results,
        }
    finally:
        loop.close()


def task_train_ml_models(symbols: List[str]) -> Dict[str, Any]:
    """Train/update ML alpha prediction models."""
    from app.services.ml_alpha_predictor import train_model
    logger.info("Training ML models for %d symbols", len(symbols))
    results = {}
    for sym in symbols[:10]:  # Limit to 10 per run
        try:
            r = train_model(sym, model_type="xgboost")
            results[sym] = r.get("status", "error")
        except Exception as e:
            logger.error("ML train failed for %s: %s", sym, e)
            results[sym] = f"error: {e}"
    return {"task": "train_ml_models", "trained": len(results), "results": results}


def run_daily_pipeline(watched_symbols: Optional[List[str]] = None) -> Dict[str, Any]:
    """Run the full daily pipeline sequentially.

    Args:
        watched_symbols: List of symbols to process. Defaults to core VN30 symbols.

    Returns:
        Dict with per-task results.
    """
    if watched_symbols is None:
        watched_symbols = [
            "VCB", "HPG", "VNM", "VIC", "MSN", "BID", "CTG", "FPT",
            "MBB", "TCB", "ACB", "VIB", "VPB", "HDB", "STB", "SSI",
            "VHC", "PNJ", "MWG", "GAS", "PLX", "POW", "SAB", "BVH",
        ]

    results: Dict[str, Any] = {
        "pipeline_date": datetime.now(TZ_VN).strftime("%Y-%m-%d %H:%M:%S"),
        "watched_symbols": len(watched_symbols),
    }

    # Step 1: Backfill data
    logger.info("=== Step 1/4: OHLCV Backfill ===")
    results["backfill"] = task_backfill_ohlcv(watched_symbols)

    # Step 2: Compute factors
    logger.info("=== Step 2/4: Factor Computation ===")
    results["factors"] = task_compute_factors(watched_symbols)

    # Step 3: Risk flags
    logger.info("=== Step 3/4: Risk Flags ===")
    results["risk_flags"] = task_run_risk_flags(watched_symbols)

    # Step 4: ML training
    logger.info("=== Step 4/4: ML Training ===")
    results["ml_training"] = task_train_ml_models(watched_symbols)

    logger.info("Daily pipeline complete: %s", json.dumps(
        {k: v for k, v in results.items() if k != "pipeline_date"},
        ensure_ascii=False,
    ))
    return results


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    result = run_daily_pipeline()
    print(json.dumps(result, ensure_ascii=False, indent=2))
