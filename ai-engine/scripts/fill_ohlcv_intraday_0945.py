"""Script fill các cây nến 09:45 bị khuyết trong bảng ohlcv_intraday_1m theo chiến lược 3 lớp LOCF:
1. Nếu có nến khớp đúng 09:45:00 -> giữ nguyên nến thực từ DNSE.
2. Nếu không có nến đúng 09:45:00:
   - Lùi lại tìm nến gần nhất trong cùng phiên sáng (09:00 -> 09:45) có khớp lệnh thực (data_source != 'FORWARD_FILL').
     -> Tạo nến 09:45 với open=high=low=close = giá Close gần nhất, volume=0, data_source='LOCF_INTRADAY'.
   - Nếu từ đầu phiên sáng không có giao dịch nào:
     -> Lấy giá đóng cửa ngày hôm trước (Previous Close / Tham chiếu), volume=0, data_source='FORWARD_FILL'.
"""

import os
import sys
import logging
from datetime import datetime, date
from zoneinfo import ZoneInfo
import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/aiinvest")
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def fill_0945_locf():
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        # 1. Lấy tất cả các ngày giao dịch có trong ohlcv_intraday_1m
        cur.execute("""
            SELECT DISTINCT (time AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS d
            FROM ohlcv_intraday_1m
            ORDER BY d ASC
        """)
        trading_dates = [row[0] for row in cur.fetchall()]
        logger.info("Tim thay %d ngay giao dich trong ohlcv_intraday_1m.", len(trading_dates))

        total_inserted = 0
        total_locf = 0
        total_prev_close = 0

        for t_date in trading_dates:
            target_time = datetime(t_date.year, t_date.month, t_date.day, 9, 45, tzinfo=VN_TZ)
            session_start = datetime(t_date.year, t_date.month, t_date.day, 9, 0, tzinfo=VN_TZ)

            # Lấy danh sách các mã thiếu nến tại đúng 09:45:00 của ngày t_date
            cur.execute("""
                WITH active_symbols AS (
                    SELECT DISTINCT symbol FROM ohlcv_intraday_1m
                )
                SELECT s.symbol 
                FROM active_symbols s
                LEFT JOIN ohlcv_intraday_1m m 
                  ON m.symbol = s.symbol 
                 AND m.time = %s
                WHERE m.symbol IS NULL
            """, (target_time,))

            missing_symbols = [r[0] for r in cur.fetchall()]
            if not missing_symbols:
                continue

            # Với mỗi mã thiếu nến 09:45, áp dụng chiến lược 2 lớp:
            # Lớp 2a: Tìm nến khớp gần nhất trong cùng phiên trước 09:45
            cur.execute("""
                SELECT DISTINCT ON (symbol)
                    symbol, close
                FROM ohlcv_intraday_1m
                WHERE symbol = ANY(%s)
                  AND time < %s
                  AND time >= %s
                  AND data_source NOT IN ('FORWARD_FILL', 'LOCF_INTRADAY')
                ORDER BY symbol, time DESC
            """, (missing_symbols, target_time, session_start))

            intraday_prices = {r[0]: float(r[1]) for r in cur.fetchall()}

            # Lớp 2b: Những mã vẫn chưa có giá (không có giao dịch sáng nay) -> lấy Previous Close
            remaining_symbols = [s for s in missing_symbols if s not in intraday_prices]
            prev_close_prices = {}
            if remaining_symbols:
                cur.execute("""
                    SELECT DISTINCT ON (ticker)
                        ticker, COALESCE(close_unadj, close_adj)
                    FROM market_data_daily
                    WHERE ticker = ANY(%s)
                      AND date < %s
                      AND COALESCE(close_unadj, close_adj) > 0
                    ORDER BY ticker, date DESC
                """, (remaining_symbols, t_date))
                prev_close_prices = {r[0]: float(r[1]) for r in cur.fetchall()}

            insert_rows = []
            for sym in missing_symbols:
                if sym in intraday_prices:
                    price = intraday_prices[sym]
                    src = "LOCF_INTRADAY"
                    total_locf += 1
                elif sym in prev_close_prices:
                    price = prev_close_prices[sym]
                    src = "FORWARD_FILL"
                    total_prev_close += 1
                else:
                    continue

                insert_rows.append((
                    target_time,
                    sym,
                    price,
                    price,
                    price,
                    price,
                    0,
                    src,
                ))

            if insert_rows:
                insert_query = """
                    INSERT INTO ohlcv_intraday_1m (time, symbol, open, high, low, close, volume, data_source)
                    VALUES %s
                    ON CONFLICT ("time", "symbol") DO NOTHING
                """
                execute_values(cur, insert_query, insert_rows)
                total_inserted += len(insert_rows)
                logger.info(
                    "[%s] Fill 09:45: %d ma (LOCF: %d, PrevClose: %d)",
                    t_date, len(insert_rows),
                    sum(1 for r in insert_rows if r[7] == 'LOCF_INTRADAY'),
                    sum(1 for r in insert_rows if r[7] == 'FORWARD_FILL'),
                )

        conn.commit()
        logger.info(
            "HOAN THANH FILL 09:45: Tong %d ban ghi (LOCF trong phien: %d, Previous Close: %d).",
            total_inserted, total_locf, total_prev_close,
        )

    except Exception as exc:
        conn.rollback()
        logger.error("Loi khi fill 09:45: %s", exc, exc_info=True)
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    fill_0945_locf()
