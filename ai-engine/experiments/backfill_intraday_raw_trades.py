"""Upsert stored 09:45 inputs with raw candles and explicit price fallbacks."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from psycopg2.extras import execute_values
from dotenv import load_dotenv

AI_ENGINE_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(AI_ENGINE_ROOT / ".env")
sys.path.insert(0, str(AI_ENGINE_ROOT))

from app.infrastructure.data_pipelines.ohlc_validation import is_valid_ohlc
from app.infrastructure.database.pg_pool import get_conn
from app.infrastructure.external_api.dnse.intraday_tool import get_intraday_tool

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
RAW_SOURCE = "DNSE_TRADES_RAW"
LOCF_SOURCE = "LOCF_INTRADAY"
FILL_SOURCE = "FORWARD_FILL"


def backfill_day(day: date) -> dict[str, int]:
    start = datetime.combine(day, time(9, 0), VN_TZ)
    cutoff = datetime.combine(day, time(9, 45), VN_TZ)
    end = cutoff + timedelta(minutes=1)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT time, symbol, volume, data_source
               FROM ohlcv_intraday_1m WHERE time >= %s AND time < %s
               ORDER BY symbol, time""",
            (datetime.combine(day, time.min, VN_TZ), datetime.combine(day + timedelta(days=1), time.min, VN_TZ)),
        )
        old_rows = cur.fetchall()
        cur.execute("SELECT DISTINCT symbol FROM stocks WHERE exchange = 'HOSE' ORDER BY symbol")
        symbols = [str(row[0]).upper() for row in cur.fetchall()]
        if not symbols:
            raise RuntimeError("Cannot replace Intraday: HOSE stock universe is empty")
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
            (symbols, day, symbols, day),
        )
        previous_closes = {str(ticker).upper(): float(close) for ticker, close in cur.fetchall()}

    cutoff_rows = [row for row in old_rows if row[0].astimezone(VN_TZ) == cutoff]
    cutoff_symbols = {str(row[1]).upper() for row in cutoff_rows}
    if (
        set(symbols).issubset(cutoff_symbols)
        and all(row[3] in {RAW_SOURCE, LOCF_SOURCE, FILL_SOURCE} for row in cutoff_rows)
    ):
        return {"skipped": 1, "symbols": len(cutoff_symbols), "bars": len(cutoff_rows)}

    old_dnse_volumes = {}
    for stamp, symbol, volume, source in old_rows:
        if source == "DNSE" and int(volume) > 0:
            minute = stamp.astimezone(VN_TZ).replace(second=0, microsecond=0)
            old_dnse_volumes[(str(symbol).upper(), minute)] = int(volume)

    tool = get_intraday_tool()
    rows = []
    raw_count = locf_count = fill_count = volume_mismatch_count = 0
    unfilled_symbols = []
    for symbol in symbols:
        bars = tool.fetch_raw_trade_bars(
            symbol,
            int(start.timestamp()),
            int(end.timestamp()),
            min_request_interval=0.15,
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
        stamp = datetime.fromisoformat(bar["time"]).astimezone(VN_TZ)
        prices = [float(bar[key]) for key in ("open", "high", "low", "close")]
        volume = int(bar["volume"])
        if not start <= stamp <= cutoff or not is_valid_ohlc(*prices) or volume <= 0:
            raise ValueError(f"Invalid raw trade candle: {symbol} {stamp}")
        old_volume = old_dnse_volumes.get((symbol, stamp))
        if old_volume is not None and volume != old_volume:
            volume_mismatch_count += 1
            print(
                f"WARNING DNSE OHLC/raw-trade volume mismatch {symbol} {stamp}: "
                f"ohlc={old_volume}, raw_trades={volume}; keeping raw-trade volume",
                flush=True,
            )
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
        raise RuntimeError(f"DNSE returned no raw trade candles for {day}; existing data was kept")

    with get_conn() as conn, conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO ohlcv_intraday_1m
               (time, symbol, open, high, low, close, volume, data_source)
               VALUES %s
               ON CONFLICT (time, symbol) DO UPDATE SET
                   open = EXCLUDED.open,
                   high = EXCLUDED.high,
                   low = EXCLUDED.low,
                   close = EXCLUDED.close,
                   volume = EXCLUDED.volume,
                   data_source = EXCLUDED.data_source,
                   fetched_at = CURRENT_TIMESTAMP""",
            rows,
            page_size=1000,
        )

    return {
        "skipped": 0,
        "symbols_checked": len(symbols),
        "raw_0945": raw_count,
        "morning_locf": locf_count,
        "previous_close_fill": fill_count,
        "unfilled_no_price_source": unfilled_symbols,
        "ohlc_volume_mismatches": volume_mismatch_count,
        "bars_written": len(rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    args = parser.parse_args()

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT date FROM market_data_daily_calculation ORDER BY date")
        days = [
            row[0] for row in cur.fetchall()
            if (args.start_date is None or row[0] >= args.start_date)
            and (args.end_date is None or row[0] <= args.end_date)
        ]
    if args.start_date is None and args.end_date is None:
        days = days[-52:]
    elif len(days) > 52:
        raise ValueError(f"Intraday backfill is limited to 52 market sessions; requested {len(days)}")

    for index, day in enumerate(days, 1):
        result = backfill_day(day)
        print(f"[{index}/{len(days)}] {day}: {result}", flush=True)


if __name__ == "__main__":
    main()
