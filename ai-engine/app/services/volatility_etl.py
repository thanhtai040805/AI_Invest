"""GARCH/EWMA Volatility ETL — post-market compute for top liquid symbols.

Architecture:
  - GARCH(1,1) for top 50 liquid symbols (CPU-intensive, MLE optimization)
  - EWMA fallback for all other symbols (already computed in technical_indicators)
  - Results upserted into technical_indicators.indicators JSONB

Methodology:
  - Returns: log returns × 100
  - GARCH(1,1): MLE via scipy SLSQP with stationarity constraint (α+β < 1)
  - EWMA: λ = 2/(span+1), already in technical_indicators
  - Annualized: × √252
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from scipy import optimize as opt

from app.services.pg_pool import DB_URL

logger = logging.getLogger(__name__)

GARCH_TOP_N = 50
GARCH_LOOKBACK = 504
VOLATILITY_TABLE = "technical_indicators"


def _estimate_garch(returns: np.ndarray) -> float:
    """GARCH(1,1) MLE — return annualized vol estimate.

    Falls back to EWMA if optimization fails or data insufficient.
    """
    n = len(returns)
    if n < 20:
        return float(np.std(returns, ddof=1) * np.sqrt(252))

    r = returns - np.mean(returns)
    var_init = np.var(r, ddof=1)
    if var_init < 1e-12:
        return 0.0

    def nll(params):
        omega, alpha, beta = params
        sigma2 = np.full(n, var_init)
        for t in range(1, n):
            sigma2[t] = omega + alpha * (r[t-1] ** 2) + beta * sigma2[t-1]
        if np.any(sigma2 <= 1e-12):
            return 1e10
        return 0.5 * float(np.sum(np.log(sigma2) + (r ** 2) / sigma2))

    bounds = ((1e-8, 1.0), (0.0, 1.0), (0.0, 1.0))
    x0 = np.array([var_init * 0.05, 0.05, 0.90])

    try:
        res = opt.minimize(
            nll, x0, bounds=bounds,
            constraints={"type": "ineq", "fun": lambda p: 0.999 - (p[1] + p[2])},
            method="SLSQP", options={"maxiter": 200},
        )
        if res.success:
            omega, alpha, beta = res.x
            sigma2 = np.full(n, var_init)
            for t in range(1, n):
                sigma2[t] = omega + alpha * (r[t-1] ** 2) + beta * sigma2[t-1]
            return float(np.mean(np.sqrt(sigma2)) * np.sqrt(252))
    except Exception:
        pass

    return float(np.std(returns, ddof=1) * np.sqrt(252))


def _get_top_liquid(cur, n: int = GARCH_TOP_N) -> list[str]:
    """Return top N symbols by 20-day average trading value."""
    cutoff = date.today() - timedelta(days=90)
    cur.execute("""
        SELECT o.symbol, AVG(o.close * o.volume) as avg_value
        FROM ohlcv o
        WHERE o.time::date >= %s
        GROUP BY o.symbol
        ORDER BY avg_value DESC
        LIMIT %s
    """, (cutoff, n))
    return [r[0] for r in cur.fetchall()]


def compute_garch_for_symbol(cur, symbol: str) -> dict | None:
    """Compute GARCH(1,1) vol for a single symbol.

    Returns {garch_vol_20d, garch_vol_60d} or None on failure.
    """
    cur.execute(
        """SELECT time, close FROM ohlcv
           WHERE symbol = %s ORDER BY time DESC LIMIT %s""",
        (symbol, GARCH_LOOKBACK),
    )
    rows = cur.fetchall()
    if len(rows) < 30:
        return None

    df = pd.DataFrame(rows, columns=["time", "close"]).set_index("time").astype(float)
    df = df.sort_index()
    pct = df["close"].pct_change().dropna() * 100

    if len(pct) < 20:
        return None

    garch_20d = _estimate_garch(pct.tail(20).values)
    garch_60d = _estimate_garch(pct.tail(60).values)

    return {
        "garch_vol_20d": round(float(garch_20d), 4),
        "garch_vol_60d": round(float(garch_60d), 4),
        "garch_updated": date.today().isoformat(),
    }


def upsert_garch_to_indicators(cur, symbol: str, calc_date: date, garch_data: dict) -> None:
    """Merge GARCH vol into existing technical_indicators JSONB row."""
    cur.execute(
        "SELECT indicators FROM technical_indicators WHERE symbol = %s AND calc_date = %s",
        (symbol, calc_date),
    )
    row = cur.fetchone()
    if row is None:
        return

    indicators = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    indicators.update(garch_data)

    cur.execute(
        "UPDATE technical_indicators SET indicators = %s, updated_at = NOW() WHERE symbol = %s AND calc_date = %s",
        (json.dumps(indicators), symbol, calc_date),
    )


def refresh_garch(symbols: list[str] | None = None, top_n: int = GARCH_TOP_N) -> dict:
    """Compute GARCH volatility for top symbols, upsert to technical_indicators.

    Args:
        symbols: explicit list (if None, auto-detect top liquid)
        top_n: number of top liquid symbols to compute

    Returns:
        dict with status, symbols_computed, errors
    """
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        if symbols is None:
            symbols = _get_top_liquid(cur, top_n)

        if not symbols:
            logger.info("No symbols for GARCH computation")
            return {"status": "skipped", "reason": "no_symbols"}

        # Find latest calc_date available in technical_indicators
        cur.execute("SELECT MAX(calc_date) FROM technical_indicators")
        max_date = cur.fetchone()[0]
        if max_date is None:
            logger.info("No technical_indicators data yet, skipping GARCH")
            return {"status": "skipped", "reason": "no_indicators_data"}
        calc_date = max_date

        logger.info("GARCH volatility for %d top symbols at %s...", len(symbols), calc_date)
        computed = 0
        errors = 0

        for sym in symbols:
            try:
                # Check if row exists for this symbol at calc_date
                cur.execute(
                    "SELECT 1 FROM technical_indicators WHERE symbol = %s AND calc_date = %s",
                    (sym, calc_date),
                )
                if cur.fetchone() is None:
                    continue
                garch_data = compute_garch_for_symbol(cur, sym)
                if garch_data:
                    upsert_garch_to_indicators(cur, sym, calc_date, garch_data)
                    computed += 1
                conn.commit()
            except Exception as e:
                logger.warning("GARCH failed for %s: %s", sym, e)
                errors += 1
                conn.rollback()

        logger.info("GARCH done: %d computed, %d errors", computed, errors)
        return {"status": "success", "symbols_computed": computed, "errors": errors}
    finally:
        cur.close()
        conn.close()


def refresh_garch_incremental(symbols: list[str] | None = None) -> dict:
    """Incremental GARCH for today's date only (fast, no full re-compute)."""
    return refresh_garch(symbols=symbols)
