"""Composite Scoring Pipeline — IC-weighted Z-score → Risk Gate → Portfolio.

Reads individual factor percentiles from factor_scores.factor_details,
applies rolling 12-month IC weights via sector-neutral Z-score,
runs risk gate (ConfidenceScorer), and builds top-decile portfolio.

Usage:
    from app.brain.dataflows.vendors.vn.composite_pipeline import run_composite_pipeline
    run_composite_pipeline(score_date)
"""
import json
import logging
import math
from datetime import date, timedelta
from typing import Any, Optional

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from app.services.pg_pool import DB_URL
from app.brain.state.confidence_scorer import ConfidenceScorer
from app.brain.dataflows.vendors.vn.sector_groups import (
    classify_major as classify,
    FINANCIALS, REAL_ESTATE, OTHERS,
)

logger = logging.getLogger(__name__)

# ── 8 Core Factors with IC weights (from 3-year VN benchmark) ──────────
# IC is raw Spearman at 20d hold, sector-neutral.
# Factors excluded: FOREIGN_NET_5D (DEAD), EVEBITDA_INV (DEAD)
CORE_FACTORS = {
    "ROE_NORM": {
        "weight": 0.077, "direction": 1,
        "label": "Return on Equity (TTM normalized)",
    },
    "NM": {
        "weight": 0.051, "direction": 1,
        "label": "Net Margin (TTM)",
    },
    "GM": {
        "weight": 0.023, "direction": 1,
        "label": "Gross Margin (TTM)",
    },
    "YOY_REV": {
        "weight": 0.032, "direction": 1,
        "label": "YoY Revenue Growth",
    },
    "PIOTROSKI_F": {
        "weight": 0.026, "direction": 1,
        "label": "Piotroski F-Score (9-point)",
    },
    "VOL_20D_ORTHO": {
        "weight": 0.023, "direction": 1,
        "label": "Orthogonalized Volatility (low vol = high score)",
    },
    "HML_REAL": {
        "weight": 0.075, "direction": -1,
        "label": "Book-to-Market (flipped: growth premium in VN)",
    },
    "SIZE": {
        "weight": 0.040, "direction": -1,
        "label": "Market Cap (flipped: large cap premium in VN)",
    },
}

# Canonical field keys in factor_details JSONB (from factor_scores output)
FACTOR_FIELD_MAP = {
    "ROE_NORM": "roe_norm",
    "NM": "net_margin",
    "GM": "gross_margin",
    "YOY_REV": "yoy_revenue_growth",
    "PIOTROSKI_F": "piotroski_f_raw",
    "VOL_20D_ORTHO": "volatility_20d",  # raw volatility_20d (proxy for ortho)
    "HML_REAL": "hml_real_raw",
    "SIZE": "size_raw",
}

SECTOR_MAP = {
    "BANKS": "FINANCIALS",
    "FINANCIAL_SERVICES": "FINANCIALS",
    "REAL_ESTATE": "REAL_ESTATE",
    "CONSTRUCTION": "REAL_ESTATE",
}

SECTOR_GROUPS = ["FINANCIALS", "REAL_ESTATE", "OTHERS"]

MIN_STOCKS_PER_SECTOR = 5


def _get_sector_group(symbol: str, cur) -> str:
    """Map symbol to one of 3 legacy sector groups for robust Z-scoring."""
    sector = classify(None, symbol)
    if sector in ("BANKS", "FINANCIAL_SERVICES"):
        return "FINANCIALS"
    if sector in ("REAL_ESTATE", "CONSTRUCTION"):
        return "REAL_ESTATE"
    return "OTHERS"


def load_sector_map(symbols: list[str], cur) -> dict[str, str]:
    """Pre-load sector groups for all symbols."""
    return {sym: _get_sector_group(sym, cur) for sym in symbols}


def compute_sector_neutral_z(
    values: dict[str, float],
    sectors: dict[str, str],
) -> dict[str, float]:
    """Compute sector-neutral Z-score for a single factor.

    Z_i = (x_i - mean_sector) / std_sector
    For sectors with < MIN_STOCKS_PER_SECTOR stocks, use global mean/std.
    """
    df = pd.DataFrame({
        "symbol": list(values.keys()),
        "value": [values.get(s, float("nan")) for s in values],
        "sector": [sectors.get(s, "OTHERS") for s in values],
    })
    df = df.dropna(subset=["value"])

    global_mean = df["value"].mean()
    global_std = df["value"].std()
    if global_std == 0 or pd.isna(global_std):
        global_std = 1.0

    result = {}
    for sec in df["sector"].unique():
        mask = df["sector"] == sec
        sub = df[mask]
        if len(sub) >= MIN_STOCKS_PER_SECTOR:
            sec_mean = sub["value"].mean()
            sec_std = sub["value"].std()
            if sec_std == 0 or pd.isna(sec_std):
                sec_mean, sec_std = global_mean, global_std
        else:
            sec_mean, sec_std = global_mean, global_std
        for _, row in sub.iterrows():
            z = (row["value"] - sec_mean) / sec_std if sec_std > 0 else 0.0
            result[row["symbol"]] = z

    return result


def compute_composite_scores(
    score_date: date,
    factor_details: dict[str, dict],
    sectors: dict[str, str],
    ic_weights: Optional[dict[str, float]] = None,
) -> dict[str, dict[str, Any]]:
    """Compute IC-weighted composite scores for all symbols.

    Args:
        score_date: Evaluation date.
        factor_details: {symbol: {factor_key: percentile, ...}}.
        sectors: {symbol: sector_group}.
        ic_weights: Optional override of {factor_id: weight}.
                     Defaults to CORE_FACTORS static weights.

    Returns:
        {symbol: {composite: float, z_scores: dict, n_active: int}}
    """
    weights = ic_weights or {fid: meta["weight"] for fid, meta in CORE_FACTORS.items()}

    # 1. For each factor, compute sector-neutral Z per symbol
    z_by_factor: dict[str, dict[str, float]] = {}
    for fid, field_key in FACTOR_FIELD_MAP.items():
        raw_values = {}
        for sym, details in factor_details.items():
            v = details.get(field_key)
            if v is not None and isinstance(v, (int, float)) and not math.isnan(v):
                raw_values[sym] = float(v)
        if len(raw_values) < MIN_STOCKS_PER_SECTOR * 2:
            logger.warning("  Factor %s: only %d non-null values, skipping", fid, len(raw_values))
            continue
        z_by_factor[fid] = compute_sector_neutral_z(raw_values, sectors)

    # 2. Compute weighted composite
    results = {}
    all_symbols = set()
    for fid, z_map in z_by_factor.items():
        all_symbols.update(z_map.keys())

    for sym in all_symbols:
        z_vals = {}
        total_weight = 0.0
        weighted_sum = 0.0
        for fid, z_map in z_by_factor.items():
            z = z_map.get(sym)
            if z is not None and not math.isnan(z):
                w = abs(weights.get(fid, 0))
                direction = CORE_FACTORS.get(fid, {}).get("direction", 1)
                z_vals[fid] = z * direction  # apply direction flip
                weighted_sum += w * z_vals[fid]
                total_weight += w
        if total_weight > 0:
            composite = weighted_sum / total_weight
        else:
            composite = 0.0
        results[sym] = {
            "composite": composite,
            "z_scores": z_vals,
            "n_active": len(z_vals),
        }

    return results


def apply_risk_gate(
    composite_scores: dict[str, dict],
    risk_flags: dict[str, list[str]],
    crs_scores: Optional[dict[str, dict]] = None,
    technical_data: Optional[dict[str, bool]] = None,
    foreign_flow_data: Optional[dict[str, float]] = None,
) -> dict[str, dict]:
    """Run CRS-aware risk gate on all symbols.

    Uses CRS scores from risk_assessments as primary gate,
    falls back to binary risk_flags for hard-block override.
    Returns updated composite_scores with confidence, decision, flags.
    """
    scorer = ConfidenceScorer()
    results = {}
    for sym, data in composite_scores.items():
        comp = data["composite"]
        flags = risk_flags.get(sym, [])
        percentile = max(0, min(100, (comp + 3) / 6 * 100))

        crs = crs_scores.get(sym) if crs_scores else None

        if crs:
            score_result = scorer.score_crs(
                crs_result=crs,
                factor_percentile=percentile,
                technical_aligned=technical_data.get(sym, False) if technical_data else False,
            )
        else:
            score_result = scorer.score(
                factor_percentile=percentile,
                active_flags=flags,
                technical_aligned=technical_data.get(sym, False) if technical_data else False,
                foreign_flow_net=foreign_flow_data.get(sym) if foreign_flow_data else None,
            )

        data["confidence"] = score_result["confidence"]
        data["decision"] = score_result["decision"]
        data["rating"] = score_result["rating"]
        data["hard_flags"] = score_result.get("hard_flags", [])
        data["soft_flags"] = score_result.get("soft_flags", [])
        data["rationale"] = score_result.get("rationale", "")

        if score_result["confidence"] == 0:
            data["composite"] = -99.0
        else:
            data["composite"] *= score_result["confidence"]

        results[sym] = data

    return results


def build_portfolio(
    composite_scores: dict[str, dict],
    n_top: int = 15,
    max_weight: float = 0.05,
) -> list[dict]:
    """Top-decile score-weighted portfolio.

    Args:
        composite_scores: {symbol: {composite, confidence, ...}}.
        n_top: Number of top stocks to include.
        max_weight: Max single-stock weight (cap).

    Returns:
        List of {symbol, weight, composite, confidence, decision} sorted by weight desc.
    """
    # Filter blocked (composite = -99) and sort by composite desc
    active = {
        sym: data for sym, data in composite_scores.items()
        if data.get("composite", -99) > -90
    }
    sorted_syms = sorted(active.keys(), key=lambda s: active[s]["composite"], reverse=True)
    top = sorted_syms[:n_top]

    if not top:
        return []

    # Score-weighted
    scores = np.array([max(0, active[s]["composite"]) for s in top])
    total = scores.sum()
    if total <= 0:
        weights = np.ones(len(top)) / len(top)
    else:
        weights = scores / total

    # Cap at max_weight, redistribute residual
    capped = np.minimum(weights, max_weight)
    residual = 1.0 - capped.sum()
    under_cap = capped < max_weight
    if under_cap.any() and residual > 0:
        uncapped_total = (max_weight - capped[under_cap]).sum()
        if uncapped_total > 0:
            capped[under_cap] += residual * (max_weight - capped[under_cap]) / uncapped_total

    portfolio = []
    for i, sym in enumerate(top):
        portfolio.append({
            "symbol": sym,
            "weight": round(float(capped[i]), 4),
            "composite": round(float(active[sym]["composite"]), 4),
            "confidence": float(active[sym].get("confidence", 0)),
            "decision": active[sym].get("decision", "HOLD"),
        })

    portfolio.sort(key=lambda x: x["weight"], reverse=True)
    return portfolio


def load_factor_details(score_date: date, symbols: Optional[list[str]] = None, cur=None) -> dict[str, dict]:
    """Load factor_details JSONB from factor_scores table."""
    if cur is None:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        close_conn = True
    else:
        close_conn = False

    try:
        if symbols:
            cur.execute(
                """SELECT symbol, factor_details
                   FROM factor_scores
                   WHERE score_date = %s AND symbol = ANY(%s)""",
                (score_date, symbols),
            )
        else:
            cur.execute(
                """SELECT symbol, factor_details
                   FROM factor_scores WHERE score_date = %s""",
                (score_date,),
            )
        result = {}
        for sym, details in cur.fetchall():
            result[sym] = details if isinstance(details, dict) else (json.loads(details) if details else {})
        return result
    finally:
        if close_conn:
            cur.close()
            conn.close()


def load_risk_flags(score_date: date, cur) -> dict[str, list[str]]:
    """Load active risk flags for all symbols on given date (backward compat)."""
    cur.execute(
        """SELECT symbol, flag_type
           FROM risk_flags
           WHERE effective_date <= %s
             AND (lifted_date IS NULL OR lifted_date > %s)
             AND is_active = TRUE""",
        (score_date, score_date),
    )
    result: dict[str, list[str]] = {}
    for sym, flag in cur.fetchall():
        result.setdefault(sym, []).append(flag)
    return result


def load_crs_scores(score_date: date, cur) -> dict[str, dict]:
    """Load CRS assessment scores from risk_assessments table."""
    cur.execute(
        """SELECT symbol, crs_score, risk_level, hard_blocked,
                  soft_blocked, recommendation, hard_flags, soft_flags
           FROM risk_assessments
           WHERE assessment_date = %s""",
        (score_date,),
    )
    result: dict[str, dict] = {}
    for row in cur.fetchall():
        result[row[0]] = {
            "crs_score": row[1],
            "risk_level": row[2],
            "hard_blocked": row[3],
            "soft_blocked": row[4],
            "recommendation": row[5],
            "hard_flags": row[6] or [],
            "soft_flags": row[7] or [],
        }
    return result


def load_foreign_flow_5d(score_date: date, symbols: list[str], cur) -> dict[str, float]:
    """Load 5-day net foreign value (billion VND) for each symbol."""
    start = score_date - timedelta(days=10)
    cur.execute(
        """SELECT symbol, SUM(net_value) as net_5d
           FROM foreign_flow
           WHERE trade_date >= %s AND trade_date <= %s
             AND symbol = ANY(%s)
           GROUP BY symbol""",
        (start, score_date, symbols),
    )
    return {sym: float(net or 0) / 1e9 for sym, net in cur.fetchall()}


def _to_native(v):
    """Convert numpy types to native Python types for JSON/PostgreSQL."""
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, dict):
        return {k: _to_native(v) for k, v in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_native(x) for x in v]
    return v


def write_composite_scores(score_date: date, results: dict[str, dict], portfolio: list[dict], cur=None):
    """Write composite scores to factor_scores table."""
    if cur is None:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        close_conn = True
    else:
        close_conn = False

    try:
        rows = []
        for sym, data in results.items():
            comp = data.get("composite")
            if comp is not None and comp > -90:
                composite_val = float(comp)
                risk_json = json.dumps({
                    "confidence": _to_native(data.get("confidence")),
                    "decision": data.get("decision"),
                    "rating": data.get("rating"),
                    "hard_flags": data.get("hard_flags", []),
                    "soft_flags": data.get("soft_flags", []),
                    "z_scores": _to_native(data.get("z_scores", {})),
                    "rationale": data.get("rationale", ""),
                })
            else:
                composite_val = None
                risk_json = json.dumps({
                    "decision": "DO_NOT_TRADE",
                    "hard_flags": data.get("hard_flags", []),
                })

            rows.append((sym, score_date, composite_val, risk_json))

        if rows:
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO factor_scores (symbol, score_date, composite_score, factor_details)
                   VALUES %s
                   ON CONFLICT (symbol, score_date)
                   DO UPDATE SET
                       composite_score = EXCLUDED.composite_score,
                       factor_details = jsonb_set(
                           COALESCE(factor_scores.factor_details, '{}'::jsonb),
                           '{composite_pipeline}',
                           EXCLUDED.factor_details::jsonb
                       ),
                       updated_at = NOW()""",
                [(r[0], r[1], float(r[2]) if r[2] is not None else None, r[3]) for r in rows],
                page_size=500,
            )

        # Write portfolio weights to a new table
        if portfolio:
            pw_rows = [
                (score_date, p["symbol"],
                 float(p["weight"]), float(p["composite"]),
                 float(p["confidence"]), str(p["decision"]))
                for p in portfolio
            ]
            cur.execute("DROP TABLE IF EXISTS _portfolio_weights")
            cur.execute("""
                CREATE TEMP TABLE _portfolio_weights (
                    score_date DATE, symbol TEXT, weight FLOAT,
                    composite FLOAT, confidence FLOAT, decision TEXT
                )
            """)
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO _portfolio_weights VALUES %s",
                pw_rows,
                page_size=100,
            )
            cur.execute("""
                INSERT INTO portfolio_weights (score_date, symbol, weight, composite, confidence, decision)
                SELECT * FROM _portfolio_weights
                ON CONFLICT (score_date, symbol)
                DO UPDATE SET
                    weight = EXCLUDED.weight,
                    composite = EXCLUDED.composite,
                    confidence = EXCLUDED.confidence,
                    decision = EXCLUDED.decision,
                    updated_at = NOW()
            """)
            cur.execute("DROP TABLE IF EXISTS _portfolio_weights")

        if close_conn:
            conn.commit()

        logger.info("  Updated %d composite scores, %d portfolio weights",
                     len(rows), len(portfolio))

    finally:
        if close_conn:
            cur.close()
            conn.close()


def ensure_portfolio_weights_table():
    """Create portfolio_weights table if not exists."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_weights (
                score_date DATE NOT NULL,
                symbol TEXT NOT NULL,
                weight FLOAT,
                composite FLOAT,
                confidence FLOAT,
                decision TEXT,
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (score_date, symbol)
            )
        """)
        conn.commit()
    finally:
        cur.close()
        conn.close()


def run_composite_pipeline(
    score_date: date,
    symbols: Optional[list[str]] = None,
    n_top: int = 15,
    max_weight: float = 0.05,
) -> dict:
    """Run the full composite scoring pipeline for a given date.

    Args:
        score_date: Evaluation date.
        symbols: Optional subset of symbols. If None, load all HOSE.
        n_top: Portfolio size.
        max_weight: Max single-stock weight.

    Returns:
        Summary dict with counts.
    """
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        # 0. Ensure tables exist
        ensure_portfolio_weights_table()

        # 1. Load symbols
        if symbols is None:
            cur.execute(
                "SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol"
            )
            symbols = [r[0] for r in cur.fetchall()]

        logger.info("Composite pipeline for %s: %d symbols", score_date, len(symbols))

        # 2. Load factor details from factor_scores table
        factor_details = load_factor_details(score_date, symbols, cur)
        logger.info("  Loaded factor_details for %d symbols", len(factor_details))

        if not factor_details:
            logger.warning("  No factor data for %s, skipping", score_date)
            return {"status": "skipped", "reason": "no_factor_data"}

        # 3. Load sector map
        sectors = load_sector_map(symbols, cur)

        # 4. Compute IC-weighted composite scores
        scores = compute_composite_scores(score_date, factor_details, sectors)
        logger.info("  Computed composite scores for %d symbols", len(scores))

        # 5. Load CRS scores (primary) and risk flags (backup)
        crs_scores = load_crs_scores(score_date, cur)
        risk_flags = load_risk_flags(score_date, cur)
        logger.info("  Loaded CRS scores for %d symbols, risk flags for %d symbols",
                     len(crs_scores), len(risk_flags))

        # 6. Load foreign flow for risk gate
        foreign_flow_5d = load_foreign_flow_5d(score_date, symbols, cur)

        # 7. Apply risk gate (CRS-aware)
        scores = apply_risk_gate(
            scores, risk_flags,
            crs_scores=crs_scores,
            foreign_flow_data=foreign_flow_5d,
        )

        # 8. Build portfolio
        portfolio = build_portfolio(scores, n_top=n_top, max_weight=max_weight)
        logger.info("  Built portfolio with %d positions", len(portfolio))

        # 9. Write results
        write_composite_scores(score_date, scores, portfolio, cur)
        conn.commit()

        # 10. Summary
        n_blocked = sum(1 for v in scores.values() if v.get("composite", 0) <= -90)
        n_active = len(scores) - n_blocked

        return {
            "status": "success",
            "score_date": str(score_date),
            "total_symbols": len(symbols),
            "scored": len(scores),
            "blocked_by_risk": n_blocked,
            "active": n_active,
            "portfolio_positions": len(portfolio),
        }

    except Exception as e:
        logger.error("Composite pipeline failed: %s", e)
        conn.rollback()
        return {"status": "failed", "error": str(e)}
    finally:
        cur.close()
        conn.close()
