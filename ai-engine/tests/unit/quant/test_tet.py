"""H001: Mua trước Tết — Stocks tăng 2-3 tuần trước Tết Nguyên Đán.

Hypothesis: VN stocks tend to rise 2-3 weeks before Tet (Lunar New Year)
due to retail investor buying and liquidity injection before the holiday.
"""
from __future__ import annotations

import logging
from datetime import date as DateType
from typing import Any, Dict, List

from app.infrastructure.vendors.vn.calendar import VNCalendar
from tests.unit.quant.test_base import HypothesisTester, HypothesisResult

logger = logging.getLogger(__name__)

TET_WINDOW_DAYS_BEFORE = 15
HOLDING_TRADING_DAYS = 10


class TetHypothesisTester(HypothesisTester):
    def __init__(self, symbols: List[str], start_date: str = "2020-01-01", end_date: str = "2026-06-05"):
        super().__init__(symbols, start_date, end_date)
        self.cal = VNCalendar()

    def compute_signals(self) -> Dict[str, List[Dict[str, Any]]]:
        signals: Dict[str, List[Dict[str, Any]]] = {}

        years = range(self.start_date.year, self.end_date.year + 1)
        tet_windows = []

        for year in years:
            tet = self.cal.get_tet_date(year)
            if tet is None:
                continue
            trading_before = self.cal.get_tet_trading_days_before(year, TET_WINDOW_DAYS_BEFORE)
            if not trading_before:
                continue
            entry_date = trading_before[0]
            exit_trading_days = []
            current = tet
            while len(exit_trading_days) < HOLDING_TRADING_DAYS:
                if current.weekday() < 5:
                    yr_holidays = self.cal.VN_HOLIDAYS.get(current.year, [])
                    if current not in yr_holidays:
                        exit_trading_days.append(current)
                current += __import__("datetime").timedelta(days=1)
            exit_date = exit_trading_days[-1] if exit_trading_days else tet

            if entry_date >= self.start_date and entry_date <= self.end_date:
                tet_windows.append((entry_date, exit_date, tet))

        for sym in self.symbols:
            sym_signals = []
            for entry, exit_d, tet_d in tet_windows:
                sym_signals.append({
                    "entry_date": entry,
                    "exit_date": exit_d,
                    "direction": "BUY",
                    "confidence": 0.65,
                    "metadata": {"tet_date": str(tet_d)},
                })
            if sym_signals:
                signals[sym] = sym_signals

        return signals


def run_tet_hypothesis() -> HypothesisResult:
    conn = __import__("psycopg2").connect(__import__("app.infrastructure.database.pg_pool", fromlist=["DB_URL"]).DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT o.symbol, AVG(o.volume::float) as avg_vol
                FROM ohlcv o
                WHERE o.time >= '2020-01-01'
                GROUP BY o.symbol
                HAVING AVG(o.volume::float) > 1000000
                ORDER BY avg_vol DESC
                LIMIT 30
            """)
            top_symbols = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

    logger.info("Tet hypothesis: testing %d liquid symbols", len(top_symbols))
    tester = TetHypothesisTester(
        symbols=top_symbols,
        start_date="2020-01-01",
        end_date="2026-05-30",
    )
    result = tester.simulate()
    result.hypothesis_id = "H001"
    result.title = "Mua trước Tết — Stocks tăng 2-3 tuần trước Tết Nguyên Đán"
    result.thesis = "VN stocks rise 2-3 weeks before Tet due to retail buying and liquidity injection"
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    result = run_tet_hypothesis()
    for k, v in result.to_dict().items():
        print(f"  {k}: {v}")
