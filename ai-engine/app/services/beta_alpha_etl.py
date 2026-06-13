"""Beta/Alpha ETL — compute real market-relative risk metrics using VNINDEX.

Methodology:
  - Market proxy: VNINDEX daily returns (from macro_indicators DB table)
  - Beta (1y/3y): Cov(R_i, R_m) / Var(R_m), 252 trading days/year
  - Alpha (1y): R_i - [R_f + Beta * (R_m - R_f)], annualized
  - Risk-free rate: SBV refinancing rate from macro_indicators (latest)
  - R_squared: goodness-of-fit for the market model

Architecture:
  - Reads: macro_indicators (VNINDEX returns), ohlcv (stock prices)
  - Writes: technical_indicators.indicators JSONB (beta_1y, beta_3y, alpha_1y, r_squared_1y)
  - Performance: vectorized batch computation, single DB pass per symbol
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg2

from app.services.pg_pool import DB_URL

logger = logging.getLogger(__name__)

LOOKBACK_1Y = 252
LOOKBACK_3Y = 756
MIN_OBS = 60


def _load_vnindex_returns(cur, end_date: date, lookback: int) -> pd.Series:
    """Load VNINDEX daily returns from VietFin API (full history, not DB).

    macro_indicators only stores daily snapshots (sparse).
    VietFin provides complete daily OHLCV history -> proper alignment.

    Returns Series indexed by date with decimal returns.
    """
    from vietfin import vf

    start = end_date - timedelta(days=lookback + 120)
    try:
        r = vf.index.price.historical(
            symbol="vnindex",
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            interval="1d",
            provider="dnse",
        )
        hist = r.to_df()
        if hist is None or hist.empty or "close" not in hist.columns:
            return pd.Series(dtype=float)

        df = hist[["close"]].copy()
        df["ret"] = df["close"].astype(float).pct_change()
        df = df.dropna()
        s = pd.Series(df["ret"].values, index=pd.to_datetime(df.index), dtype=float)
        s.index = s.index.date
        return s
    except Exception as e:
        logger.warning("VietFin VNINDEX fetch failed: %s", e)
        return pd.Series(dtype=float)


def _load_stock_returns(cur, symbol: str, end_date: date, lookback: int) -> pd.Series:
    """Load stock daily returns from ohlcv.

    Returns Series indexed by date.
    """
    start = end_date - timedelta(days=lookback + 60)
    cur.execute(
        """SELECT time::date as dt, close FROM ohlcv
           WHERE symbol = %s AND time::date >= %s AND time::date <= %s
           ORDER BY time""",
        (symbol, start, end_date),
    )
    rows = cur.fetchall()
    if len(rows) < MIN_OBS:
        return pd.Series(dtype=float)

    df = pd.DataFrame(rows, columns=["dt", "close"]).set_index("dt").astype(float)
    df["ret"] = df["close"].pct_change()
    return df["ret"].dropna()


def _load_risk_free_rate(cur) -> float:
    """Load latest SBV refinancing rate from macro_indicators.

    DB stores rate as percentage (e.g. 4.5 = 4.5%). Returns decimal.
    """
    cur.execute(
        """SELECT value FROM macro_indicators
           WHERE indicator_name = 'refinancing_rate'
           ORDER BY indicator_date DESC LIMIT 1"""
    )
    row = cur.fetchone()
    if row and row[0] is not None:
        return float(row[0]) / 100.0
    return 0.045  # fallback 4.5%


def compute_beta_alpha(
    stock_returns: pd.Series,
    market_returns: pd.Series,
    risk_free_rate: float = 0.045,
) -> dict:
    """Compute Beta, Alpha, R-squared for a stock vs market.

    Args:
        stock_returns: daily returns Series (index=date)
        market_returns: VNINDEX daily returns Series (index=date)
        risk_free_rate: annual risk-free rate (decimal)

    Returns:
        dict with beta_1y, beta_3y, alpha_1y, r_squared_1y, n_obs
    """
    result = {}

    for label, lookback in [("1y", LOOKBACK_1Y), ("3y", LOOKBACK_3Y)]:
        cutoff = stock_returns.index[-1] - timedelta(days=lookback)
        s = stock_returns[stock_returns.index >= cutoff]
        m = market_returns[market_returns.index >= cutoff]

        common = s.index.intersection(m.index)
        if len(common) < MIN_OBS:
            result[f"beta_{label}"] = None
            result[f"alpha_{label}"] = None
            result[f"r_squared_{label}"] = None
            result[f"n_obs_{label}"] = 0
            continue

        s_aligned = s[common].values
        m_aligned = m[common].values

        cov = np.cov(s_aligned, m_aligned)
        var_m = np.var(m_aligned, ddof=1)

        if var_m < 1e-12:
            beta = 0.0
        else:
            beta = cov[0, 1] / var_m

        # Annualized metrics
        ann_mkt_return = float(np.mean(m_aligned)) * 252
        ann_stock_return = float(np.mean(s_aligned)) * 252
        alpha = ann_stock_return - (risk_free_rate + beta * (ann_mkt_return - risk_free_rate))

        # R-squared
        if cov[0, 0] > 0 and cov[1, 1] > 0:
            r_sq = (cov[0, 1] ** 2) / (cov[0, 0] * cov[1, 1])
        else:
            r_sq = 0.0

        result[f"beta_{label}"] = round(float(beta), 4)
        result[f"alpha_{label}"] = round(float(alpha), 4)
        result[f"r_squared_{label}"] = round(float(r_sq), 4)
        result[f"n_obs_{label}"] = len(common)

    return result


def refresh_beta_alpha(symbols: list[str] | None = None) -> dict:
    """Compute Beta/Alpha for all (or given) symbols, upsert to technical_indicators.

    Args:
        symbols: explicit list (if None, all HOSE stocks)

    Returns:
        dict with status, symbols_computed, errors
    """
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        if symbols is None:
            cur.execute(
                "SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol"
            )
            symbols = [r[0] for r in cur.fetchall()]

        if not symbols:
            return {"status": "skipped", "reason": "no_symbols"}

        # Find latest calc_date
        cur.execute("SELECT MAX(calc_date) FROM technical_indicators")
        max_date = cur.fetchone()[0]
        if max_date is None:
            return {"status": "skipped", "reason": "no_indicators_data"}
        calc_date = max_date

        # Load VNINDEX returns once
        vnindex_1y = _load_vnindex_returns(cur, calc_date, LOOKBACK_1Y)
        vnindex_3y = _load_vnindex_returns(cur, calc_date, LOOKBACK_3Y)
        risk_free = _load_risk_free_rate(cur)

        if len(vnindex_1y) < MIN_OBS:
            logger.warning("Insufficient VNINDEX data for beta computation (%d obs)", len(vnindex_1y))
            return {"status": "failed", "reason": "insufficient_vnindex_data"}

        logger.info(
            "Beta/Alpha for %d symbols at %s (VNINDEX: 1y=%d, 3y=%d obs, Rf=%.2f%%)",
            len(symbols), calc_date, len(vnindex_1y), len(vnindex_3y), risk_free * 100,
        )

        computed = 0
        errors = 0

        for sym in symbols:
            try:
                # Check if row exists at calc_date
                cur.execute(
                    "SELECT 1 FROM technical_indicators WHERE symbol = %s AND calc_date = %s",
                    (sym, calc_date),
                )
                if cur.fetchone() is None:
                    continue

                stock_ret_1y = _load_stock_returns(cur, sym, calc_date, LOOKBACK_1Y)
                if len(stock_ret_1y) < MIN_OBS:
                    continue

                ba = compute_beta_alpha(stock_ret_1y, vnindex_1y, risk_free)

                # Load existing indicators and merge
                cur.execute(
                    "SELECT indicators FROM technical_indicators WHERE symbol = %s AND calc_date = %s",
                    (sym, calc_date),
                )
                row = cur.fetchone()
                if row is None:
                    continue
                indicators = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                for k, v in ba.items():
                    if v is not None:
                        indicators[k] = v

                cur.execute(
                    "UPDATE technical_indicators SET indicators = %s, updated_at = NOW() WHERE symbol = %s AND calc_date = %s",
                    (json.dumps(indicators), sym, calc_date),
                )
                computed += 1
                conn.commit()

            except Exception as e:
                logger.warning("Beta/Alpha failed for %s: %s", sym, e)
                errors += 1
                conn.rollback()

        logger.info("Beta/Alpha done: %d computed, %d errors", computed, errors)
        return {"status": "success", "symbols_computed": computed, "errors": errors}
    finally:
        cur.close()
        conn.close()
