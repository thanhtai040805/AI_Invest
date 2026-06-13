"""Paper Trading Service — auto-trade signals into simulated portfolio.

Derived flow:
  1. Read today's BUY/SELL signals from `signals` table
  2. Apply position sizing from `trading_rules.py`
  3. Record decisions in `paper_trades` table
  4. Evaluate open positions daily (T+2/T+5)
  5. Generate daily summary report

All amounts in VND.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras

from app.services.pg_pool import DB_URL
from app.services.trading_rules import (
    TradingRulesConfig,
    calc_position_size,
    check_all_rules,
    PortfolioState,
    PositionInfo,
)
from app.eval.signal_tracker import SignalTracker

logger = logging.getLogger(__name__)

INITIAL_CAPITAL = 100_000_000  # 100M VND
MAX_POSITIONS = 5
PAPER_MODE = True

CREATE_PAPER_TRADES_SQL = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id              SERIAL PRIMARY KEY,
    ticker          TEXT NOT NULL,
    action          TEXT NOT NULL,
    price           DOUBLE PRECISION NOT NULL,
    date            TIMESTAMP NOT NULL DEFAULT NOW(),
    confidence      DOUBLE PRECISION DEFAULT 0.0,
    thesis          TEXT,
    pnl             DOUBLE PRECISION,
    status          TEXT DEFAULT 'OPEN',
    resolve_price   DOUBLE PRECISION,
    resolved_at     TIMESTAMP,
    quantity        INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT NOW()
);
"""


class PaperTradingService:
    def __init__(self, capital: float = INITIAL_CAPITAL):
        self.capital = capital
        self.cash = capital
        self._ensure_table()
        self.rules_config = TradingRulesConfig(
            stop_loss_fixed_pct=-0.10,
            max_position_pct=0.20,
            max_concentration_pct=0.20,
            max_drawdown_pct=-0.25,
            min_cash_pct=0.05,
        )
        self.tracker = SignalTracker()

    def _ensure_table(self):
        conn = psycopg2.connect(DB_URL)
        try:
            with conn.cursor() as cur:
                cur.execute(CREATE_PAPER_TRADES_SQL)
            conn.commit()
        finally:
            conn.close()

    def process_today_signals(self, trade_date: date) -> Dict[str, Any]:
        logger.info("Paper trading: processing signals for %s", trade_date)

        signals = self._get_today_signals(trade_date)
        positions = self._get_open_positions()
        portfolio_value = self._calc_portfolio_value(positions, trade_date)

        results = {"buys": 0, "sells": 0, "holds": 0, "errors": 0, "trades": []}

        for sig in signals:
            symbol = sig["symbol"]
            direction = sig["signal"]
            rank = sig.get("composite_rank", 50)
            hard = sig.get("hard_flags", 0)
            soft = sig.get("soft_flags", 0)
            sector = sig.get("sector_group", "UNKNOWN")

            price = self._get_price(symbol, trade_date)
            if price is None or price <= 0:
                results["errors"] += 1
                continue

            if direction in ("BUY", "BUY_WEAK") and hard == 0:
                if len(positions) >= MAX_POSITIONS:
                    continue
                qty, method = calc_position_size(
                    symbol, price, portfolio_value,
                    max_pct_per_position=self.rules_config.max_position_pct,
                )
                if qty < 100:
                    continue
                cost = qty * price
                if cost > self.cash:
                    continue

                trade = self._record_trade(symbol, "BUY", price, qty, rank, trade_date)
                self.cash -= cost
                positions.append({
                    "symbol": symbol,
                    "quantity": qty,
                    "entry_price": price,
                    "entry_date": trade_date,
                    "sector": sector,
                })
                results["buys"] += 1
                results["trades"].append(trade)

            elif direction in ("SELL", "SELL_WEAK"):
                pos = next((p for p in positions if p["symbol"] == symbol), None)
                if pos:
                    qty = pos["quantity"]
                    proceeds = qty * price * (1 - 0.0015 - 0.001)
                    self.cash += proceeds
                    pnl = proceeds - (qty * pos["entry_price"])
                    trade = self._record_trade(symbol, "SELL", price, qty, rank, trade_date, pnl)
                    positions = [p for p in positions if p["symbol"] != symbol]
                    results["sells"] += 1
                    results["trades"].append(trade)

        self._update_portfolio_value(positions, trade_date, results)
        results["cash"] = round(self.cash)
        results["position_count"] = len(positions)
        results["total_value"] = round(self.cash + sum(
            self._get_price(p["symbol"], trade_date) * p["quantity"]
            for p in positions if self._get_price(p["symbol"], trade_date)
        ))

        self._log_daily_summary(trade_date, results)
        return results

    def evaluate_open_positions(self, trade_date: date) -> List[Dict[str, Any]]:
        conn = psycopg2.connect(DB_URL)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM paper_trades
                    WHERE status = 'OPEN' AND date <= %s
                """, (trade_date,))
                open_trades = cur.fetchall()
        finally:
            conn.close()

        results = []
        for t in open_trades:
            price = self._get_price(t["ticker"], trade_date)
            if price is None:
                continue
            holding_days = (trade_date - t["date"].date()).days if hasattr(t["date"], "date") else 0

            if holding_days >= 2:
                pnl_pct = (price - t["price"]) / t["price"] * 100
                conn = psycopg2.connect(DB_URL)
                try:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE paper_trades
                            SET status = 'CLOSED', pnl = %s, resolve_price = %s, resolved_at = %s
                            WHERE id = %s
                        """, (
                            (price - t["price"]) * 100,
                            price,
                            datetime.combine(trade_date, datetime.min.time()),
                            t["id"],
                        ))
                    conn.commit()
                finally:
                    conn.close()
                results.append({
                    "id": t["id"],
                    "symbol": t["ticker"],
                    "pnl_pct": round(pnl_pct, 2),
                    "resolved_price": price,
                })

        return results

    def generate_daily_report(self, trade_date: date) -> str:
        stats = self.tracker.get_stats(days=30)
        signals_today = self._get_today_signals(trade_date)
        positions = self._get_open_positions()
        portfolio_value = self._calc_portfolio_value(positions, trade_date)

        lines = [
            f"# Paper Trading Report — {trade_date.isoformat()}",
            "",
            "## Portfolio Summary",
            f"- Total Value: {portfolio_value:,.0f} VND",
            f"- Cash: {self.cash:,.0f} VND",
            f"- Positions: {len(positions)}/{MAX_POSITIONS}",
            f"- Return: {((portfolio_value / INITIAL_CAPITAL) - 1) * 100:+.2f}%",
            "",
            "## Signal Performance (30d)",
            f"- Win Rate: {stats.get('win_rate', 0) * 100:.1f}%",
            f"- Total Signals: {stats.get('total', 0)}",
            f"- Avg Return: {(stats.get('avg_return') or 0):+.2f}%",
            "",
            "## Today's Signals",
            f"- BUY: {sum(1 for s in signals_today if s['signal'] in ('BUY', 'BUY_WEAK'))}",
            f"- SELL: {sum(1 for s in signals_today if s['signal'] in ('SELL', 'SELL_WEAK'))}",
            f"- BUY_WEAK/SELL_WEAK: {sum(1 for s in signals_today if 'WEAK' in s['signal'])}",
            "",
            "## Open Positions",
        ]

        for p in positions:
            price = self._get_price(p["symbol"], trade_date)
            if price:
                pnl = (price - p["entry_price"]) / p["entry_price"] * 100
                lines.append(f"- {p['symbol']}: entry={p['entry_price']:,.0f}, current={price:,.0f}, PnL={pnl:+.2f}%")

        lines.append("")
        return "\n".join(lines)

    def _get_today_signals(self, trade_date: date) -> List[Dict[str, Any]]:
        conn = psycopg2.connect(DB_URL)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Load factor signals
                cur.execute("""
                    SELECT symbol, signal, composite_rank, hard_flags, soft_flags, sector_group
                    FROM signals
                    WHERE signal_date = %s
                """, (trade_date,))
                factor_rows = {r["symbol"]: dict(r) for r in cur.fetchall()}

                # Load AI signals and prefer them over factor signals for the same symbol
                cur.execute("""
                    SELECT symbol, signal, rating, confidence, thesis
                    FROM ai_signals
                    WHERE signal_date = %s
                """, (trade_date,))
                ai_rows = {r["symbol"]: dict(r) for r in cur.fetchall()}

                # Merge, preferring AI signals
                merged = []
                symbols = set(list(factor_rows.keys()) + list(ai_rows.keys()))
                for sym in symbols:
                    if sym in ai_rows:
                        row = ai_rows[sym]
                        # Normalize ai schema to the expected one
                        merged.append({
                            "symbol": sym,
                            "signal": row.get("signal"),
                            "composite_rank": None,
                            "hard_flags": 0,
                            "soft_flags": 0,
                            "sector_group": None,
                            "ai": True,
                            "rating": row.get("rating"),
                            "confidence": row.get("confidence", 0.8),
                            "thesis": row.get("thesis"),
                        })
                    else:
                        merged.append(factor_rows[sym])

                # Sort: AI signals first (by confidence), then factor by composite_rank
                merged.sort(key=lambda r: (0 if r.get("ai") else 1, -(r.get("confidence") or r.get("composite_rank") or 0)))
                return merged
        finally:
            conn.close()

    def _get_open_positions(self) -> List[Dict[str, Any]]:
        conn = psycopg2.connect(DB_URL)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT ticker as symbol, price as entry_price, date::date as entry_date
                    FROM paper_trades
                    WHERE status = 'OPEN'
                """)
                return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def _get_price(self, symbol: str, trade_date: date) -> Optional[float]:
        conn = psycopg2.connect(DB_URL)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT adj_close, close FROM ohlcv
                    WHERE symbol = %s AND time = %s
                """, (symbol, trade_date))
                row = cur.fetchone()
            if row:
                return float(row[0]) if row[0] is not None else float(row[1])
            return None
        finally:
            conn.close()

    def _calc_portfolio_value(self, positions: List[dict], trade_date: date) -> float:
        total = self.cash
        for p in positions:
            price = self._get_price(p["symbol"], trade_date)
            if price:
                total += price * p.get("quantity", 100)
        return total

    def _record_trade(self, symbol: str, action: str, price: float,
                      quantity: int, rank: float, trade_date: date,
                      pnl: Optional[float] = None) -> Dict[str, Any]:
        conn = psycopg2.connect(DB_URL)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO paper_trades
                        (ticker, action, price, date, confidence, pnl, status, thesis)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    symbol, action, price,
                    datetime.combine(trade_date, datetime.min.time()),
                    rank / 100.0,
                    pnl,
                    "OPEN" if action == "BUY" else "CLOSED",
                    f"Auto-traded from signal (rank={rank:.0f})"
                ))
                trade_id = cur.fetchone()[0]
            conn.commit()
            return {"id": trade_id, "symbol": symbol, "action": action, "price": price, "quantity": quantity}
        finally:
            conn.close()

    def _update_portfolio_value(self, positions: List[dict], trade_date: date, results: dict):
        total = self.cash
        for p in positions:
            price = self._get_price(p["symbol"], trade_date)
            if price:
                total += price * p["quantity"]
        results["portfolio_value"] = round(total)

    def _log_daily_summary(self, trade_date: date, results: dict):
        logger.info(
            "Paper trading %s: %d buys, %d sells, cash=%s, positions=%d",
            trade_date, results["buys"], results["sells"],
            f"{results.get('cash', 0):,.0f}",
            results.get("position_count", 0),
        )


def run_paper_trading(trade_date_str: Optional[str] = None) -> Dict[str, Any]:
    d = date.fromisoformat(trade_date_str) if trade_date_str else date.today()
    service = PaperTradingService()
    result = service.process_today_signals(d)
    evaluations = service.evaluate_open_positions(d)
    result["evaluations"] = evaluations
    report = service.generate_daily_report(d)
    report_path = f"~/.vibe-trading/reports/paper_trading_{d.isoformat()}.md"
    import os
    os.makedirs(os.path.expanduser("~/.vibe-trading/reports"), exist_ok=True)
    with open(os.path.expanduser(report_path), "w", encoding="utf-8") as f:
        f.write(report)
    result["report_path"] = report_path
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    result = run_paper_trading()
    print(json.dumps(result, indent=2, ensure_ascii=False))
