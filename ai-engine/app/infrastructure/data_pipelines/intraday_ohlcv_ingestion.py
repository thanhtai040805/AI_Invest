"""Persist the last raw-trade candle available by the 09:45 strategy cutoff."""

from datetime import date, datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

from psycopg2.extras import execute_values

from app.infrastructure.data_pipelines.ohlc_validation import is_valid_ohlc
from app.infrastructure.database.pg_pool import get_conn
from app.infrastructure.external_api.dnse.intraday_tool import get_intraday_tool

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
RAW_SOURCE = "DNSE_TRADES_RAW"
LOCF_SOURCE = "LOCF_INTRADAY"
FILL_SOURCE = "FORWARD_FILL"


def persist_morning_intraday_bars(target_date: date) -> dict[str, int]:
    """Upsert the 09:45 reference candle without replacing other session bars."""
    start = datetime.combine(target_date, dt_time(9, 0), VN_TZ)
    cutoff = datetime.combine(target_date, dt_time(9, 45), VN_TZ)
    end = cutoff + timedelta(minutes=1)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT symbol FROM stocks WHERE exchange = 'HOSE' ORDER BY symbol")
        symbols = [str(row[0]).upper() for row in cur.fetchall()]
        if not symbols:
            raise RuntimeError("Cannot persist intraday OHLCV: HOSE stock universe is empty")
        cur.execute(
            """SELECT DISTINCT ON (symbol) symbol, close FROM (
                   SELECT ticker AS symbol, date, close_unadj AS close, 1 AS priority
                   FROM market_data_daily
                   WHERE ticker = ANY(%s) AND date < %s AND close_unadj > 0
                   UNION ALL
                   SELECT symbol, time AS date, close, 0 AS priority
                   FROM ohlcv_unadjusted
                   WHERE symbol = ANY(%s) AND time < %s AND close > 0
               ) raw_closes ORDER BY symbol, date DESC, priority DESC""",
            (symbols, target_date, symbols, target_date),
        )
        previous_closes = {str(ticker).upper(): float(close) for ticker, close in cur.fetchall()}

    tool = get_intraday_tool()
    rows = []
    raw_count = locf_count = fill_count = 0
    unfilled_symbols = []
    for symbol in symbols:
        bars = tool.fetch_raw_trade_bars(
            symbol,
            from_ts=int(start.timestamp()),
            to_ts=int(end.timestamp()),
            latest_bar_only=True,
            end_exclusive=True,
        )
        if not bars:
            close = previous_closes.get(symbol)
            if close is None:
                unfilled_symbols.append(symbol)
                continue
            price = round(close / 1000 if close >= 1000 else close, 3)
            rows.append((cutoff, symbol, price, price, price, price, 0, FILL_SOURCE))
            fill_count += 1
            continue
        bar = bars[-1]
        stamp = datetime.fromisoformat(str(bar["time"]).replace("Z", "+00:00"))
        stamp = stamp.replace(tzinfo=VN_TZ) if stamp.tzinfo is None else stamp.astimezone(VN_TZ)
        prices = [float(bar.get(key) or 0) for key in ("open", "high", "low", "close")]
        volume = int(bar.get("volume") or 0)
        if not start <= stamp <= cutoff or not is_valid_ohlc(*prices) or volume <= 0:
            raise ValueError(f"Invalid raw trade candle for {symbol} at {stamp}; Intraday rows were not replaced")
        if stamp == cutoff:
            rows.append((
                cutoff,
                symbol,
                *(round(price / 1000 if price >= 1000 else price, 3) for price in prices),
                volume,
                RAW_SOURCE,
            ))
            raw_count += 1
        else:
            price = round(prices[3] / 1000 if prices[3] >= 1000 else prices[3], 3)
            rows.append((cutoff, symbol, price, price, price, price, 0, LOCF_SOURCE))
            locf_count += 1
    if not rows:
        raise RuntimeError(f"DNSE returned no valid HOSE 1-minute candles for {target_date} before 09:45")

    with get_conn() as conn, conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO ohlcv_intraday_1m (time, symbol, open, high, low, close, volume, data_source)
            VALUES %s
            ON CONFLICT (time, symbol) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                data_source = EXCLUDED.data_source,
                fetched_at = CURRENT_TIMESTAMP
            """,
            rows,
            page_size=1000,
        )

    return {
        "universe_symbols": len(symbols),
        "raw_0945": raw_count,
        "morning_locf": locf_count,
        "previous_close_fill": fill_count,
        "unfilled_no_price_source": unfilled_symbols,
        "bars_written": len(rows),
    }
