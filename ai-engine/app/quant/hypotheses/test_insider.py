"""H003: Insider buy signal — CEO/CFO/Chairman mua > 1% outstanding = signal tăng 20d.

Hypothesis: When top executives (CEO, CFO, Chairman) buy > 1% of outstanding
shares, it signals insider confidence and the stock tends to outperform
over the next 20 trading days.
"""
from __future__ import annotations

import logging
from datetime import date as DateType, timedelta
from typing import Any, Dict, List

import psycopg2
import psycopg2.extras

from app.services.pg_pool import DB_URL
from app.quant.hypotheses.test_base import HypothesisTester, HypothesisResult

logger = logging.getLogger(__name__)

KEY_ROLES = ["chủ tịch hđqt", "tổng giám đốc", "ceo", "cfo", "chairman"]
MIN_OWNERSHIP_PCT = 0.5
HOLDING_DAYS = 20


class InsiderHypothesisTester(HypothesisTester):
    def compute_signals(self) -> Dict[str, List[Dict[str, Any]]]:
        conn = psycopg2.connect(DB_URL)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT symbol, trade_date, trade_type, quantity,
                           ownership_pct, trader_name, trader_position,
                           before_volume, after_volume
                    FROM insider_trades
                    WHERE trade_type = 'BUY'
                      AND LOWER(trader_position) IN %s
                      AND trade_date >= %s AND trade_date <= %s
                    ORDER BY trade_date
                """, (tuple(KEY_ROLES), self.start_date, self.end_date))
                rows = cur.fetchall()
        finally:
            conn.close()

        trades_by_symbol: Dict[str, List[dict]] = {}
        for r in rows:
            trades_by_symbol.setdefault(r["symbol"], []).append({
                "date": r["trade_date"],
                "quantity": int(r["quantity"] or 0),
                "ownership_pct": float(r["ownership_pct"] or 0),
                "trader_position": r["trader_position"],
                "before_volume": int(r["before_volume"] or 0),
                "after_volume": int(r["after_volume"] or 0),
            })

        signals: Dict[str, List[Dict[str, Any]]] = {}

        for sym, insider_trades in trades_by_symbol.items():
            if sym not in self.symbols:
                continue
            sym_signals = []

            for t in insider_trades:
                if t["ownership_pct"] < MIN_OWNERSHIP_PCT:
                    continue

                entry_date = t["date"]
                exit_date = self._get_trading_day_after(entry_date, HOLDING_DAYS)
                if exit_date is None:
                    continue
                if entry_date < self.start_date or entry_date > self.end_date:
                    continue

                sym_signals.append({
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "direction": "BUY",
                    "confidence": 0.75,
                    "metadata": {
                        "trader_position": t["trader_position"],
                        "ownership_pct_after": t["ownership_pct"],
                        "quantity": t["quantity"],
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
                """, (from_date, n_days + 5))
                days = [r[0] for r in cur.fetchall()]
        finally:
            conn.close()

        if len(days) >= n_days:
            return days[n_days - 1]
        return None


def run_insider_hypothesis() -> HypothesisResult:
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT DISTINCT symbol FROM insider_trades
                WHERE trade_type = 'BUY' AND LOWER(trader_position) IN %s
                ORDER BY symbol
            """, (tuple(KEY_ROLES),))
            all_symbols = [r["symbol"] for r in cur.fetchall()]
    finally:
        conn.close()

    logger.info("Insider hypothesis: testing %d symbols", len(all_symbols))
    tester = InsiderHypothesisTester(
        symbols=all_symbols,
        start_date="2020-01-01",
        end_date="2026-05-30",
    )
    result = tester.simulate()
    result.hypothesis_id = "H003"
    result.title = "CEO/CFO/Chairman mua > 0.5% outstanding = signal tăng 20d"
    result.thesis = "Top executives buying significant stakes signals insider confidence and future outperformance"
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    result = run_insider_hypothesis()
    for k, v in result.to_dict().items():
        print(f"  {k}: {v}")
