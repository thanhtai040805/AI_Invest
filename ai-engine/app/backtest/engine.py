"""HOSE Backtest Engine — Event-Driven, Point-in-Time.

Nguyên tắc:
- PIT: tại ngày t, chỉ dùng dữ liệu available trước t
- T+2: chặn bán trước khi settle
- Lock trần/sàn: không fill hoặc fill một phần
- Chi phí đầy đủ: phí + thuế + slippage + impact
- Lot size: 100 cổ phiếu (HOSE)
"""
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from app.backtest.cost_model import estimate_cost, round_to_lot
from app.backtest.execution import HOSEExecutionModel, count_trading_days, is_trading_day
from app.domain.rules.risk.risk_engine import MacroRiskEngine

logger = logging.getLogger(__name__)


@dataclass
class Fill:
    symbol: str
    price: float
    quantity: int
    side: str
    date: date


@dataclass
class Position:
    symbol: str
    quantity: int = 0
    avg_cost: float = 0.0
    buy_date: Optional[date] = None


@dataclass
class Portfolio:
    initial_capital: float
    cash: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)
    equity_curve: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.cash = self.initial_capital

    @property
    def market_value(self) -> float:
        return sum(p.quantity * p.avg_cost for p in self.positions.values())

    @property
    def total_equity(self) -> float:
        return self.cash + self.market_value

    def apply_fill(self, fill: Fill, cost: dict) -> None:
        if fill.side == "BUY":
            pos = self.positions.setdefault(
                fill.symbol,
                Position(symbol=fill.symbol, buy_date=fill.date),
            )
            total_cost_before = pos.quantity * pos.avg_cost
            total_cost_new = fill.price * fill.quantity + cost["total_cost"]
            pos.quantity += fill.quantity
            pos.avg_cost = (total_cost_before + total_cost_new) / pos.quantity if pos.quantity > 0 else 0
            self.cash -= fill.price * fill.quantity + cost["total_cost"]
        else:
            pos = self.positions.get(fill.symbol)
            if pos:
                pos.quantity -= fill.quantity
                self.cash += fill.price * fill.quantity - cost["total_cost"]
                if pos.quantity <= 0:
                    del self.positions[fill.symbol]

    def mark_to_market(self, t: date, price_feed: Callable) -> None:
        mv = 0.0
        for sym, pos in self.positions.items():
            px = price_feed(sym, t)
            if px:
                mv += pos.quantity * px
        self.equity_curve.append({
            "date": t,
            "equity": self.cash + mv,
            "cash": self.cash,
            "market_value": mv,
        })


@dataclass
class BacktestReport:
    gross_cagr: float = 0.0
    net_cagr: float = 0.0
    gross_sharpe: float = 0.0
    net_sharpe: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown: float = 0.0
    hit_rate: float = 0.0
    profit_factor: float = 0.0
    total_brokerage: float = 0.0
    total_tax: float = 0.0
    total_slippage: float = 0.0
    total_costs: float = 0.0
    alpha_vs_vnindex: float = 0.0
    beta_vs_vnindex: float = 0.0
    information_ratio: float = 0.0
    baseline_name: str = "E1VFVN30"
    baseline_cagr: float = 0.0
    outperformance: float = 0.0


class HOSEBacktestEngine:
    """Event-driven backtester chuẩn cho HOSE."""

    def __init__(
        self,
        universe_func: Callable,
        feature_func: Callable,
        price_func: Callable,
        use_macro_risk: bool = True,
    ):
        self.universe_func = universe_func
        self.feature_func = feature_func
        self.price_func = price_func
        self.execution = HOSEExecutionModel(reference_price_func=price_func)
        self.risk_engine = MacroRiskEngine() if use_macro_risk else None

    def run(
        self,
        strategy: Any,
        start_date: date,
        end_date: date,
        initial_capital: float = 1_000_000_000,
        turnover_limit: float = 0.20,
    ) -> BacktestReport:
        portfolio = Portfolio(initial_capital)
        total_costs = {"brokerage": 0.0, "tax": 0.0, "slippage": 0.0}

        current = start_date
        while current <= end_date:
            if not is_trading_day(current):
                current += timedelta(days=1)
                continue

            # 1. Macro Risk Multiplier
            multiplier = 1.0
            if self.risk_engine:
                risk_data = self.risk_engine.calculate_risk_score(current)
                multiplier = risk_data["risk_multiplier"]
                if multiplier < 1.0:
                    logger.debug(f"Risk Score {risk_data['risk_score']} on {current} -> multiplier {multiplier}")

            universe = self.universe_func(current)
            features = self.feature_func(universe, current)
            signals = strategy.generate_signals(features, current)
            
            # 2. Get Raw Target Weights
            target_weights = strategy.optimize(signals)
            
            # 3. Apply Macro Risk Shield (Scaling down all positions)
            if multiplier < 1.0:
                target_weights = {k: v * multiplier for k, v in target_weights.items()}

            for sym in set(target_weights) | set(portfolio.positions):
                target_weight = target_weights.get(sym, 0.0)
                current_pos = portfolio.positions.get(sym)
                current_qty = current_pos.quantity if current_pos else 0
                target_qty = round_to_lot(
                    target_weight * portfolio.total_equity / (self.price_func(sym, current) or 1)
                )
                delta = target_qty - current_qty

                if delta > 0:
                    can_fill, ratio = self.execution.handle_lock_limit(
                        sym, current, "BUY"
                    )
                    if not can_fill:
                        continue
                    qty = round_to_lot(delta * ratio)
                    if qty == 0:
                        continue
                    price = self.execution.get_fill_price(sym, current, "ATC", "BUY")
                    if price is None:
                        continue
                    cost = estimate_cost("BUY", price, qty)
                    while qty > 0 and price * qty + cost["total_cost"] > portfolio.cash:
                        qty -= 100
                        cost = estimate_cost("BUY", price, qty) if qty > 0 else {"total_cost": 0.0, "brokerage": 0.0, "tax": 0.0, "slippage": 0.0}
                    if qty <= 0:
                        continue
                    fill = Fill(sym, price, qty, "BUY", current)
                    portfolio.apply_fill(fill, cost)
                    for k in total_costs:
                        total_costs[k] += cost[k]

                elif delta < 0:
                    sell_qty = abs(delta)
                    if current_pos and current_pos.buy_date:
                        if not self.execution.can_sell(sym, current_pos.buy_date, current):
                            continue
                    can_fill, ratio = self.execution.handle_lock_limit(
                        sym, current, "SELL"
                    )
                    if not can_fill:
                        continue
                    qty = round_to_lot(sell_qty * ratio)
                    if qty == 0:
                        continue
                    price = self.execution.get_fill_price(sym, current, "ATC", "SELL")
                    if price is None:
                        continue
                    cost = estimate_cost("SELL", price, qty)
                    fill = Fill(sym, price, qty, "SELL", current)
                    portfolio.apply_fill(fill, cost)
                    for k in total_costs:
                        total_costs[k] += cost[k]

            portfolio.mark_to_market(current, self.price_func)
            current += timedelta(days=1)

        report = BacktestReport()
        if len(portfolio.equity_curve) > 1:
            eq = pd.DataFrame(portfolio.equity_curve).set_index("date")
            gross_returns = eq["equity"].pct_change().dropna()
            n_years = (end_date - start_date).days / 365.25
            report.gross_cagr = (eq["equity"].iloc[-1] / initial_capital) ** (1 / n_years) - 1
            report.net_cagr = report.gross_cagr
            report.gross_sharpe = float(
                gross_returns.mean() / gross_returns.std() * np.sqrt(252)
                if gross_returns.std() > 0 else 0
            )
            report.net_sharpe = report.gross_sharpe
            roll_max = eq["equity"].cummax()
            dd = (eq["equity"] - roll_max) / roll_max
            report.max_drawdown = float(dd.min())
            report.total_brokerage = total_costs["brokerage"]
            report.total_tax = total_costs["tax"]
            report.total_slippage = total_costs["slippage"]
            report.total_costs = sum(total_costs.values())

        return report


def run_backtest(runs_root: str) -> str:
    """Run a long-only, next-session backtest from canonical daily market data."""
    import json
    from pathlib import Path

    from app.infrastructure.database.pg_pool import get_conn

    root = Path(runs_root)
    config_file = root / "config.json"
    config = {}
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    symbol = str((config.get("codes") or [""])[0]).upper()
    strategy = config.get("strategy_config") or {}
    strategy_type = strategy.get("type")
    if strategy_type not in {"sma_cross", "rsi", "bollinger"}:
        raise ValueError(f"Unsupported strategy: {strategy_type}")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT date, open_adj * 1000, high_adj * 1000, low_adj * 1000,
                          close_adj * 1000, volume_total
                   FROM market_data_daily
                   WHERE ticker = %s AND date BETWEEN %s AND %s
                   ORDER BY date""",
                (symbol, config["start_date"], config["end_date"]),
            )
            rows = cur.fetchall()
    if len(rows) < 2:
        raise ValueError(f"Insufficient market data for {symbol}")

    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    params = strategy.get("params") or {}
    if strategy_type == "sma_cross":
        fast, slow = int(params.get("fast", 10)), int(params.get("slow", 30))
        if not 1 <= fast < slow <= 500:
            raise ValueError("SMA periods must satisfy 1 <= fast < slow <= 500")
        desired = (df["close"].rolling(fast).mean() > df["close"].rolling(slow).mean()).astype(int)
    elif strategy_type == "rsi":
        period = int(params.get("period", 14))
        delta = df["close"].diff()
        rs = delta.clip(lower=0).rolling(period).mean() / (-delta.clip(upper=0)).rolling(period).mean().replace(0, np.nan)
        rsi = 100 - 100 / (1 + rs)
        desired = pd.Series(np.nan, index=df.index)
        desired[rsi < float(params.get("oversold", 30))] = 1
        desired[rsi > float(params.get("overbought", 70))] = 0
        desired = desired.ffill().fillna(0).astype(int)
    else:
        period, width = int(params.get("period", 20)), float(params.get("std_dev", 2))
        mid = df["close"].rolling(period).mean()
        std = df["close"].rolling(period).std()
        desired = pd.Series(np.nan, index=df.index)
        desired[df["close"] < mid - width * std] = 1
        desired[df["close"] > mid + width * std] = 0
        desired = desired.ffill().fillna(0).astype(int)

    initial_capital = float(config.get("initial_capital", 1_000_000_000))
    cash, quantity, buy_date = initial_capital, 0, None
    equity_rows, trades = [], []
    total_cost = 0.0
    for i, row in df.iterrows():
        target = int(desired.iloc[i - 1]) if i > 0 else 0  # signal t executes at t+1 open
        price = float(row["open"] or row["close"])
        trade_date = row["date"]
        if target == 1 and quantity == 0:
            qty = round_to_lot(cash / (price * 1.002))
            if qty > 0:
                cost = estimate_cost("BUY", price, qty)
                while qty > 0 and price * qty + cost["total_cost"] > cash:
                    qty -= 100
                    cost = estimate_cost("BUY", price, qty) if qty else {"total_cost": 0.0}
                if qty:
                    cash -= price * qty + cost["total_cost"]
                    quantity, buy_date = qty, trade_date
                    total_cost += cost["total_cost"]
                    trades.append({"date": trade_date, "side": "BUY", "price": price, "quantity": qty})
        elif target == 0 and quantity > 0 and buy_date and count_trading_days(buy_date, trade_date) - 1 >= 2:
            cost = estimate_cost("SELL", price, quantity)
            cash += price * quantity - cost["total_cost"]
            total_cost += cost["total_cost"]
            trades.append({"date": trade_date, "side": "SELL", "price": price, "quantity": quantity})
            quantity, buy_date = 0, None
        equity_rows.append({"date": trade_date, "equity": cash + quantity * float(row["close"]), "cash": cash, "market_value": quantity * float(row["close"])})

    eq = pd.DataFrame(equity_rows)
    returns = eq["equity"].pct_change().dropna()
    years = max((pd.Timestamp(eq["date"].iloc[-1]) - pd.Timestamp(eq["date"].iloc[0])).days / 365.25, 1 / 365.25)
    roll_max = eq["equity"].cummax()
    max_drawdown = float(((eq["equity"] - roll_max) / roll_max).min())
    metrics = {
        "cagr": float((eq["equity"].iloc[-1] / initial_capital) ** (1 / years) - 1),
        "sharpe_ratio": float(returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0.0,
        "max_drawdown": max_drawdown,
        "total_trades": len(trades),
        "total_costs": total_cost,
        "ending_equity": float(eq["equity"].iloc[-1]),
    }
    (root / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    equity_file = root / "equity.csv"
    eq.to_csv(equity_file, index=False)
    trades_file = root / "trades.csv"
    pd.DataFrame(trades, columns=["date", "side", "price", "quantity"]).to_csv(trades_file, index=False)

    artifacts = {
        "metrics_json": str(root / "metrics.json"),
        "equity_csv": str(equity_file),
        "trades_csv": str(trades_file),
    }

    return json.dumps({
        "status": "success",
        "run_id": root.name,
        "artifacts": artifacts,
        "metrics": metrics,
    })
