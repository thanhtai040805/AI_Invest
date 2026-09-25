"""Fetch and normalize today's 09:45 OHLCV inputs from DNSE:
Áp dụng chuẩn 3 lớp:
1. Khớp đúng 09:45:00 -> giữ nguyên nến thực và volume từ DNSE.
2. Khớp trước 09:45:00 trong sáng nay -> lấy giá khớp gần nhất (LOCF_INTRADAY), volume = 0.
3. Không khớp lệnh cả sáng -> lấy giá đóng cửa phiên trước (FORWARD_FILL), volume = 0.
"""

import os
import sys
import logging
import time
from datetime import datetime, date
from zoneinfo import ZoneInfo
import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, r"d:\AIInvest\ai-engine")
from app.infrastructure.external_api.dnse.intraday_tool import DnseIntradayTool
from app.infrastructure.database.pg_pool import DB_URL

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def backfill_today():
    today = datetime.now(VN_TZ).date()
    target_time = datetime(today.year, today.month, today.day, 9, 45, tzinfo=VN_TZ)
    session_start = datetime(today.year, today.month, today.day, 9, 0, tzinfo=VN_TZ)
    session_end = datetime(today.year, today.month, today.day, 9, 46, tzinfo=VN_TZ)

    from_ts = int(session_start.timestamp())
    to_ts = int(session_end.timestamp())

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        # 1. Lấy danh sách 405 mã cổ phiếu trong vũ trụ
        cur.execute("SELECT DISTINCT symbol FROM ohlcv_intraday_1m ORDER BY symbol")
        symbols = [r[0] for r in cur.fetchall()]
        logger.info("Bat dau crawl du lieu 09:45 ngay %s cho %d ma...", today, len(symbols))

        # 2. Lấy sẵn giá đóng cửa phiên trước làm fallback
        cur.execute("""
            SELECT DISTINCT ON (ticker)
                ticker, COALESCE(close_unadj, close_adj)
            FROM market_data_daily
            WHERE date < %s AND COALESCE(close_unadj, close_adj) > 0
            ORDER BY ticker, date DESC
        """, (today,))
        prev_closes = {r[0]: float(r[1]) for r in cur.fetchall()}

        tool = DnseIntradayTool()
        insert_rows = []
        count_dnse = 0
        count_locf = 0
        count_ff = 0

        for idx, sym in enumerate(symbols):
            try:
                bars = tool.fetch(sym, resolution="1", from_ts=from_ts, to_ts=to_ts)
            except Exception as e:
                bars = None

            if bars:
                # Tìm nến đúng 09:45:00 (tức 02:45:00Z UTC)
                bar_0945 = next((b for b in bars if "02:45:00" in b.get("time", "")), None)
                if bar_0945:
                    insert_rows.append((
                        target_time,
                        sym,
                        float(bar_0945["open"]),
                        float(bar_0945["high"]),
                        float(bar_0945["low"]),
                        float(bar_0945["close"]),
                        int(bar_0945["volume"]),
                        "DNSE",
                    ))
                    count_dnse += 1
                else:
                    # Lấy nến gần nhất trước 09:45 trong sáng nay
                    latest_bar = bars[-1]
                    price = float(latest_bar["close"])
                    insert_rows.append((
                        target_time,
                        sym,
                        price,
                        price,
                        price,
                        price,
                        0,
                        "LOCF_INTRADAY",
                    ))
                    count_locf += 1
            else:
                # Cả sáng không có giao dịch -> Fallback previous close
                prev_price = prev_closes.get(sym)
                if prev_price:
                    insert_rows.append((
                        target_time,
                        sym,
                        prev_price,
                        prev_price,
                        prev_price,
                        prev_price,
                        0,
                        "FORWARD_FILL",
                    ))
                    count_ff += 1

            if (idx + 1) % 50 == 0 or (idx + 1) == len(symbols):
                logger.info("Tien do crawl: %d/%d ma...", idx + 1, len(symbols))

        logger.info(
            "Crawl hoan tat: %d ma (DNSE thuc: %d, LOCF lui lai: %d, Previous Close: %d)",
            len(insert_rows), count_dnse, count_locf, count_ff,
        )

        # 3. Ghi vao ohlcv_intraday_1m
        insert_query = """
            INSERT INTO ohlcv_intraday_1m (time, symbol, open, high, low, close, volume, data_source)
            VALUES %s
            ON CONFLICT ("time", "symbol") DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                data_source = EXCLUDED.data_source,
                fetched_at = CURRENT_TIMESTAMP
        """
        execute_values(cur, insert_query, insert_rows)
        conn.commit()
        logger.info("Da luu thanh cong %d ban ghi 09:45 cho ngay %s vao CSDL!", len(insert_rows), today)

    except Exception as exc:
        conn.rollback()
        logger.error("Loi khi crawl ngay hom nay: %s", exc, exc_info=True)
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    backfill_today()
