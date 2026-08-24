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

logger = logging.getLogger("app.application.use_cases.daily_pipeline")

TZ_VN = timezone(timedelta(hours=7))


def task_backfill_ohlcv(symbols: List[str]) -> Dict[str, Any]:
    """Backfill daily OHLCV for specified symbols."""
    from app.infrastructure.data_pipelines.ohlcv_backfill import run_backfill
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
    from app.domain.services.quant.vn_ic_tester import VN_FACTORS
    logger.info("VN-core factors: %d defined", len(VN_FACTORS))
    return {
        "task": "compute_factors",
        "n_vn_factors": len(VN_FACTORS),
    }





def task_train_ml_models(symbols: List[str]) -> Dict[str, Any]:
    """Train/update cross-sectional ML alpha prediction models and HMM market regime model."""
    from app.domain.services.ml.ml_alpha_predictor import train_panel_model
    from app.domain.rules.market.hmm_classifier import hmm_classifier
    
    results = {}
    
    logger.info("Training HMM market regime model...")
    try:
        hmm_success = hmm_classifier.train_hmm_model()
        results["hmm_model"] = "trained" if hmm_success else "failed"
    except Exception as e:
        logger.error("HMM training failed: %s", e)
        results["hmm_model"] = f"error: {e}"

    logger.info("Training cross-sectional ML panel model for %d symbols", len(symbols))
    try:
        r = train_panel_model(symbols, model_type="xgboost")
        if "error" in r:
            logger.error("ML panel train failed: %s", r["error"])
            results["panel_xgboost"] = f"error: {r['error']}"
        else:
            results["panel_xgboost"] = r.get("status", "trained")
            logger.info("Panel model trained successfully on %d samples", r.get("training_samples", 0))
    except Exception as e:
        logger.error("ML train failed: %s", e)
        results["panel_xgboost"] = f"error: {e}"
        
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
    logger.info("=== Step 1/3: OHLCV Backfill ===")
    results["backfill"] = task_backfill_ohlcv(watched_symbols)

    # Step 2: Compute factors
    logger.info("=== Step 2/3: Factor Computation ===")
    results["factors"] = task_compute_factors(watched_symbols)

    # Step 3: ML training
    logger.info("=== Step 3/3: ML Training ===")
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
