"""
Technical Indicators Pipeline — pre-compute 40+ indicators for all dates
Uses vectorized pandas (no pandas_ta dependency).
Full refresh or incremental per-symbol update.
"""
import json
import logging
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from app.services.pg_pool import DB_URL

logger = logging.getLogger(__name__)


def _calc_rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_full_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute 40+ technical indicators for EVERY row in the OHLCV DataFrame.

    Returns a DataFrame indexed by time with indicator columns.
    """
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    out = pd.DataFrame(index=df.index)

    # Moving Averages
    out["ma5"] = close.rolling(5).mean()
    out["ma10"] = close.rolling(10).mean()
    out["ma20"] = close.rolling(20).mean()
    out["ma50"] = close.rolling(50).mean()
    out["ma200"] = close.rolling(min(200, len(close))).mean()
    out["ema5"] = close.ewm(span=5, adjust=False).mean()
    out["ema12"] = close.ewm(span=12, adjust=False).mean()
    out["ema26"] = close.ewm(span=26, adjust=False).mean()
    out["ema200"] = close.ewm(span=min(200, len(close)), adjust=False).mean()

    # RSI
    out["rsi_7"] = _calc_rsi(close, 7)
    out["rsi_14"] = _calc_rsi(close, 14)
    out["rsi_21"] = _calc_rsi(close, 21)

    # MACD
    ema_12 = close.ewm(span=12, adjust=False).mean()
    ema_26 = close.ewm(span=26, adjust=False).mean()
    macd = ema_12 - ema_26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    out["macd"] = macd
    out["macd_signal"] = macd_signal
    out["macd_histogram"] = macd - macd_signal

    # Stochastic
    low_min = low.rolling(window=14).min()
    high_max = high.rolling(window=14).max()
    stoch_k = 100 * (close - low_min) / (high_max - low_min).replace(0, np.nan)
    stoch_d = stoch_k.rolling(window=3).mean()
    out["stoch_k"] = stoch_k
    out["stoch_d"] = stoch_d

    # ADX 14
    plus_dm = high.diff()
    minus_dm = low.diff()
    plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
    minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=14).mean()
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(window=14).mean() / atr.replace(0, np.nan))
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(window=14).mean() / atr.replace(0, np.nan))
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
    out["adx_14"] = dx.rolling(window=14).mean()
    out["plus_di"] = plus_di
    out["minus_di"] = minus_di
    out["atr_14"] = atr

    # MFI 14
    typical_price = (high + low + close) / 3
    raw_money_flow = typical_price * volume
    price_diff = typical_price.diff()
    pos_flow = pd.Series(np.where(price_diff > 0, raw_money_flow, 0.0), index=df.index).rolling(14).sum()
    neg_flow = pd.Series(np.where(price_diff < 0, raw_money_flow, 0.0), index=df.index).rolling(14).sum()
    mfr = pos_flow / neg_flow.replace(0, np.nan)
    out["mfi_14"] = 100 - (100 / (1 + mfr))

    # Bollinger Bands
    bb_middle = close.rolling(window=20).mean()
    bb_std = close.rolling(window=20).std()
    bb_upper = bb_middle + 2 * bb_std
    bb_lower = bb_middle - 2 * bb_std
    out["bb_upper"] = bb_upper
    out["bb_middle"] = bb_middle
    out["bb_lower"] = bb_lower
    out["bb_width"] = (bb_upper - bb_lower) / bb_middle.replace(0, np.nan)
    out["bb_pct"] = (close - bb_lower) / (bb_upper - bb_lower).replace(0, np.nan)

    # Volatility
    pct_change = close.pct_change()
    out["volatility_10d"] = pct_change.rolling(10).std() * np.sqrt(252) * 100
    out["volatility_20d"] = pct_change.rolling(20).std() * np.sqrt(252) * 100
    out["volatility_60d"] = pct_change.rolling(60).std() * np.sqrt(252) * 100
    out["volatility_252d"] = pct_change.rolling(min(252, len(pct_change))).std() * np.sqrt(252) * 100

    # Volume
    out["volume_ma5"] = volume.rolling(5).mean()
    out["volume_ma20"] = volume.rolling(20).mean()
    volume_ma20 = volume.rolling(20).mean()
    out["volume_ratio"] = volume / volume_ma20.replace(0, np.nan)

    # OBV
    obv = np.zeros(len(close))
    for i in range(1, len(close)):
        if close.iloc[i] > close.iloc[i - 1]:
            obv[i] = obv[i - 1] + volume.iloc[i]
        elif close.iloc[i] < close.iloc[i - 1]:
            obv[i] = obv[i - 1] - volume.iloc[i]
        else:
            obv[i] = obv[i - 1]
    out["obv"] = obv

    # Momentum (% returns)
    out["momentum_1d"] = pct_change * 100
    out["momentum_5d"] = (close / close.shift(5) - 1) * 100
    out["momentum_1m"] = (close / close.shift(20) - 1) * 100
    out["momentum_3m"] = (close / close.shift(60) - 1) * 100
    out["momentum_6m"] = (close / close.shift(120) - 1) * 100
    out["momentum_1y"] = (close / close.shift(252) - 1) * 100

    return out


def compute_for_symbol(cur, symbol: str) -> int:
    """Compute technical indicators for one symbol, upsert into table. Returns row count."""
    cur.execute(
        """SELECT time, open, high, low, close, volume
           FROM ohlcv WHERE symbol = %s ORDER BY time ASC""",
        (symbol,),
    )
    rows = cur.fetchall()
    if len(rows) < 20:
        return 0

    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    df = df.set_index("time").astype(float)

    ind_df = compute_full_indicators(df)
    # Filter to rows with at least ma5 (first 5 rows have NaN)
    ind_df = ind_df[ind_df["ma5"].notna()]

    # Convert to list of (symbol, date, jsonb) for upsert
    insert_rows = []
    for dt, row in ind_df.iterrows():
        # Clean NaN/Inf → None
        clean = {}
        for k, v in row.items():
            try:
                if pd.isna(v) or np.isinf(v):
                    clean[k] = None
                else:
                    clean[k] = float(v)
            except (TypeError, ValueError):
                clean[k] = float(v) if v is not None else None
        insert_rows.append((symbol, dt.date(), json.dumps(clean)))

    if not insert_rows:
        return 0

    psycopg2.extras.execute_values(
        cur,
        """INSERT INTO technical_indicators (symbol, calc_date, indicators)
           VALUES %s
           ON CONFLICT (symbol, calc_date)
           DO UPDATE SET indicators = EXCLUDED.indicators,
                         updated_at = NOW()""",
        insert_rows,
        page_size=500,
    )
    return len(insert_rows)


def refresh_all() -> dict:
    """Full refresh for all HOSE stocks."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol"
        )
        symbols = [r[0] for r in cur.fetchall()]
        logger.info("Refreshing technical indicators for %d symbols", len(symbols))

        total_rows = 0
        for idx, sym in enumerate(symbols):
            count = compute_for_symbol(cur, sym)
            total_rows += count
            conn.commit()
            if idx > 0 and idx % 50 == 0:
                logger.info("  Progress: %d/%d symbols, %d rows", idx, len(symbols), total_rows)

        logger.info(
            "Technical indicators done: %d rows for %d symbols", total_rows, len(symbols)
        )
        return {"rows": total_rows, "symbols": len(symbols)}
    finally:
        cur.close()
        conn.close()


def refresh_incremental(symbols: Optional[list[str]] = None) -> dict:
    """Incremental: for symbols with OHLCV newer than latest technical_indicators."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        if symbols is None:
            cur.execute(
                """SELECT DISTINCT o.symbol
                   FROM ohlcv o
                   WHERE o.symbol IN (SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX'))
                   AND o.time::date > COALESCE(
                       (SELECT MAX(calc_date) FROM technical_indicators ti WHERE ti.symbol = o.symbol),
                       '2000-01-01'::date
                   )"""
            )
            symbols = [r[0] for r in cur.fetchall()]

        if not symbols:
            logger.info("No symbols need incremental technical indicator update")
            return {"rows": 0, "symbols": 0}

        logger.info("Incremental technical indicators for %d symbols", len(symbols))
        total_rows = 0
        for idx, sym in enumerate(symbols):
            count = compute_for_symbol(cur, sym)
            total_rows += count
            conn.commit()

        logger.info("Incremental done: %d rows for %d symbols", total_rows, len(symbols))
        return {"rows": total_rows, "symbols": len(symbols)}
    finally:
        cur.close()
        conn.close()
