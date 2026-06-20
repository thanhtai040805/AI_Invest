"""H002: Foreign net buy signal — Foreign net buy >= 3 ngày liên tiếp = signal tăng 5d.

Hypothesis: When foreign investors buy VN stocks for 3+ consecutive days
with total net > 30B VND, it signals institutional smart money flowing in
and prices rise over the next 5 trading days.
"""
from __future__ import annotations

import logging
from datetime import date as DateType, timedelta
from typing import Any, Dict, List

import psycopg2
import psycopg2.extras

from app.infrastructure.database.pg_pool import DB_URL
from tests.unit.quant.test_base import HypothesisTester, HypothesisResult

logger = logging.getLogger(__name__)

MIN_STREAK_DAYS = 3
MIN_NET_VALUE = 30e9
HOLDING_DAYS = 5


class ForeignFlowHypothesisTester(HypothesisTester):
    def compute_signals(self) -> Dict[str, List[Dict[str, Any]]]:
        conn = psycopg2.connect(DB_URL)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT symbol, trade_date, net_value
                    FROM foreign_flow
                    WHERE trade_date >= %s AND trade_date <= %s
                    ORDER BY symbol, trade_date
                """, (self.start_date, self.end_date))
                rows = cur.fetchall()
        finally:
            conn.close()

        flow_by_symbol: Dict[str, List[dict]] = {}
        for r in rows:
            flow_by_symbol.setdefault(r["symbol"], []).append({
                "date": r["trade_date"],
                "net_value": float(r["net_value"] or 0),
            })

        signals: Dict[str, List[Dict[str, Any]]] = {}

        for sym, flow_data in flow_by_symbol.items():
            if sym not in self.symbols:
                continue
            sym_signals = []
            streak_dates = []
            streak_value = 0.0

            for i, day in enumerate(flow_data):
                nv = day["net_value"]

                if nv > 0:
                    streak_dates.append(day["date"])
                    streak_value += nv
                else:
                    if len(streak_dates) >= MIN_STREAK_DAYS and streak_value >= MIN_NET_VALUE:
                        entry_date = streak_dates[-1]
                        exit_date = entry_date + timedelta(days=HOLDING_DAYS * 1)
                        exit_calendar = self._get_trading_day_after(entry_date, HOLDING_DAYS)
                        if exit_calendar:
                            sym_signals.append({
                                "entry_date": entry_date,
                                "exit_date": exit_calendar,
                                "direction": "BUY",
                                "confidence": 0.7,
                                "metadata": {
                                    "streak_days": len(streak_dates),
                                    "total_net_value": streak_value,
                                    "start_date": str(streak_dates[0]),
                                },
                            })
                    streak_dates = []
                    streak_value = 0.0

            if len(streak_dates) >= MIN_STREAK_DAYS and streak_value >= MIN_NET_VALUE:
                entry_date = streak_dates[-1]
                exit_calendar = self._get_trading_day_after(entry_date, HOLDING_DAYS)
                if exit_calendar:
                    sym_signals.append({
                        "entry_date": entry_date,
                        "exit_date": exit_calendar,
                        "direction": "BUY",
                        "confidence": 0.7,
                        "metadata": {
                            "streak_days": len(streak_dates),
                            "total_net_value": streak_value,
                            "start_date": str(streak_dates[0]),
                        },
                    })

            if sym_signals:
                signals[sym] = sym_signals

        return signals

    def _get_trading_day_after(self, from_date: DateType, n_days: int) -> DateType | None:
        conn = psycopg2.connect(DB_URL)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT time FROM ohlcv
                    WHERE time > %s
                    ORDER BY time
                    LIMIT %s
                """, (from_date, n_days + 2))
                days = [r[0] for r in cur.fetchall()]
        finally:
            conn.close()

        if len(days) >= n_days:
            return days[n_days - 1]
        return None


def run_foreign_flow_hypothesis() -> HypothesisResult:
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT DISTINCT symbol FROM foreign_flow
                WHERE trade_date >= '2020-01-01'
                ORDER BY symbol
            """)
            all_symbols = [r["symbol"] for r in cur.fetchall()]
    finally:
        conn.close()

    logger.info("Foreign flow hypothesis: testing %d symbols", len(all_symbols))
    tester = ForeignFlowHypothesisTester(
        symbols=all_symbols,
        start_date="2020-01-01",
        end_date="2026-05-30",
    )
    result = tester.simulate()
    result.hypothesis_id = "H002"
    result.title = "Foreign net buy >= 3 ngày liên tiếp = signal tăng 5d"
    result.thesis = "Foreign institutional net buying for 3+ consecutive days predicts short-term price increase"
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    result = run_foreign_flow_hypothesis()
    for k, v in result.to_dict().items():
        print(f"  {k}: {v}")
