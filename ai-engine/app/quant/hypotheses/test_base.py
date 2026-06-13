"""Base class for hypothesis testing — long-only VN equity simulation with T+2."""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import date as DateType, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import psycopg2
import psycopg2.extras

from app.services.pg_pool import DB_URL

logger = logging.getLogger(__name__)


@dataclass
class HypothesisResult:
    hypothesis_id: str = ""
    title: str = ""
    thesis: str = ""
    trade_dates: List[str] = field(default_factory=list)
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0
    avg_holding_days: float = 0.0
    calmar_ratio: float = 0.0
    vs_benchmark_return_pct: float = 0.0

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items()}


class HypothesisTester(ABC):
    def __init__(self, symbols: List[str], start_date: str, end_date: str):
        self.symbols = symbols
        self.start_date = DateType.fromisoformat(start_date)
        self.end_date = DateType.fromisoformat(end_date)
        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            self._conn = psycopg2.connect(DB_URL)
        return self._conn

    @abstractmethod
    def compute_signals(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return dict[symbol, list of signal dicts with: entry_date, exit_date, direction, confidence]"""

    def get_prices(self, symbol: str, start: DateType, end: DateType) -> List[tuple]:
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT time, adj_close, close
                FROM ohlcv
                WHERE symbol = %s AND time >= %s AND time <= %s
                ORDER BY time
            """, (symbol, start, end))
            rows = cur.fetchall()
        result = []
        for r in rows:
            price = float(r[1]) if r[1] is not None else float(r[2])
            result.append((r[0], price))
        return result

    def get_all_trading_days(self) -> List[DateType]:
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT time FROM ohlcv
                WHERE time >= %s AND time <= %s
                ORDER BY time
            """, (self.start_date, self.end_date))
            return [r[0] for r in cur.fetchall()]

    def simulate(self) -> HypothesisResult:
        signals = self.compute_signals()
        all_days = self.get_all_trading_days()
        day_set = set(all_days)

        daily_returns = []
        positions: Dict[str, Dict[str, Any]] = {}
        equity = 100_000_000.0
        cash = equity
        trade_records = []

        for i, today in enumerate(all_days):
            today_returns = []
            total_exposure = 0.0

            opened_today = []

            for sym, sym_signals in signals.items():
                for sig in sym_signals:
                    entry = sig["entry_date"]
                    if isinstance(entry, str):
                        entry = DateType.fromisoformat(entry)
                    if entry == today:
                        prices = self.get_prices(sym, today, today)
                        if not prices:
                            continue
                        entry_price = prices[0][1]
                        pos_size = equity * 0.08 / entry_price
                        pos_size = int(pos_size / 100) * 100
                        if pos_size >= 100 and pos_size * entry_price <= cash:
                            opened_today.append({
                                "symbol": sym,
                                "entry_date": today,
                                "entry_price": entry_price,
                                "size": pos_size,
                                "invested": pos_size * entry_price,
                            })

            for o in opened_today:
                cash -= o["invested"]
                positions[o["symbol"]] = o

            for sym in list(positions.keys()):
                pos = positions[sym]
                prices = self.get_prices(sym, today, today)
                if not prices:
                    continue
                current_price = prices[0][1]
                ret = current_price / pos["entry_price"] - 1
                today_returns.append((sym, ret))

                sigs = signals.get(sym, [])
                should_exit = False
                for sig in sigs:
                    exit_d = sig.get("exit_date")
                    if exit_d:
                        if isinstance(exit_d, str):
                            exit_d = DateType.fromisoformat(exit_d)
                        if exit_d and today >= exit_d:
                            should_exit = True

                holding_days = (today - pos["entry_date"]).days
                if holding_days >= 2 and should_exit:
                    exit_proceeds = current_price * pos["size"]
                    pnl = exit_proceeds - pos["invested"]
                    ret_pct = pnl / pos["invested"] * 100
                    trade_records.append({
                        "symbol": sym,
                        "entry_date": str(pos["entry_date"]),
                        "exit_date": str(today),
                        "holding_days": holding_days,
                        "return_pct": ret_pct,
                        "pnl": pnl,
                    })
                    cash += exit_proceeds
                    del positions[sym]

            if today_returns:
                avg_daily_ret = np.mean([r[1] for r in today_returns])
            else:
                avg_daily_ret = 0.0

            total_equity = cash + sum(
                self._get_price(positions[sym]["symbol"], today) * positions[sym]["size"]
                for sym in list(positions.keys())
            )

            if equity > 0:
                daily_ret = (total_equity - equity) / equity
            else:
                daily_ret = 0.0
            daily_returns.append(daily_ret)
            equity = total_equity

        result = self._compute_metrics(daily_returns, all_days, trade_records)
        self.conn.close()
        return result

    def _get_price(self, symbol: str, day: DateType) -> float:
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT adj_close, close FROM ohlcv
                WHERE symbol = %s AND time = %s
            """, (symbol, day))
            row = cur.fetchone()
        if row:
            return float(row[0]) if row[0] is not None else float(row[1])
        return 0.0

    def _compute_metrics(self, daily_returns: List[float], all_days: List[DateType],
                         trades: List[dict]) -> HypothesisResult:
        r = np.array(daily_returns)
        total_ret = float((1 + r).prod() - 1) * 100
        n_days = len(r)
        years = n_days / 252 if n_days > 0 else 1
        ann_ret = float(((1 + total_ret / 100) ** (1 / years) - 1) * 100) if years > 0 else 0.0

        excess = r - 0.0475 / 252
        sharpe = float(np.mean(excess) / (np.std(r, ddof=1) + 1e-10) * np.sqrt(252)) if np.std(r) > 0 else 0.0

        peak = np.maximum.accumulate(1 + r)
        dd = (1 + r) / peak - 1
        max_dd = float(np.min(dd)) * 100

        calmar = ann_ret / abs(max_dd) if abs(max_dd) > 0 else 0.0

        total_trades = len(trades)
        winning = [t for t in trades if t["return_pct"] > 0]
        losing = [t for t in trades if t["return_pct"] <= 0]
        win_rate = len(winning) / total_trades if total_trades > 0 else 0.0
        avg_win = np.mean([t["return_pct"] for t in winning]) if winning else 0.0
        avg_loss = abs(np.mean([t["return_pct"] for t in losing])) if losing else 0.0
        profit_factor = (sum(t["pnl"] for t in winning) / abs(sum(t["pnl"] for t in losing))
                         if losing and sum(t["pnl"] for t in losing) != 0 else 0.0)
        avg_hold = np.mean([t["holding_days"] for t in trades]) if trades else 0.0

        vnindex_ret = self._get_vnindex_return()
        vs_benchmark = total_ret - vnindex_ret

        return HypothesisResult(
            total_return_pct=round(total_ret, 2),
            annualized_return_pct=round(ann_ret, 2),
            sharpe_ratio=round(sharpe, 3),
            max_drawdown_pct=round(max_dd, 2),
            win_rate=round(win_rate, 3),
            total_trades=total_trades,
            winning_trades=len(winning),
            losing_trades=len(losing),
            avg_win_pct=round(avg_win, 2),
            avg_loss_pct=round(avg_loss, 2),
            profit_factor=round(profit_factor, 3),
            avg_holding_days=round(avg_hold, 1),
            calmar_ratio=round(calmar, 3),
            vs_benchmark_return_pct=round(vs_benchmark, 2),
        )

    def _get_vnindex_return(self) -> float:
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT time, close FROM ohlcv
                WHERE symbol = 'VNINDEX' AND time >= %s AND time <= %s
                ORDER BY time
            """, (self.start_date, self.end_date))
            rows = cur.fetchall()
        if len(rows) >= 2:
            return (float(rows[-1][1]) / float(rows[0][1]) - 1) * 100
        return 0.0
