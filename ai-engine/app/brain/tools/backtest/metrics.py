"""Shared backtest metrics — delegates to new quant modules where possible.

Provides:
- calc_bars_per_year (VN-specific)
- win_rate_and_stats, by_symbol_stats, by_exit_reason_stats (trade-level)
- calc_metrics (wraps new app.backtest.metrics + app.quant.risk.risk_model)
- estimate_garch_volatility (unique, kept here)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from backtest.models import TradeRecord
from app.backtest.metrics import compute_sharpe, compute_sortino, compute_max_drawdown, compute_calmar_ratio, compute_hit_rate, compute_profit_factor, compute_deflated_sharpe, compute_alpha_beta
from app.quant.risk.risk_model import compute_var, compute_cvar

_TRADING_DAYS = {"vietfin": 252, "dnse": 252}
_BARS_PER_DAY = {
    "1D": {"vietfin": 1, "dnse": 1},
}


def calc_bars_per_year(interval: str = "1D", source: str = "vietfin") -> int:
    trading_days = _TRADING_DAYS.get(source, 252)
    bars_per_day = _BARS_PER_DAY.get(interval, {}).get(source, 1)
    return trading_days * bars_per_day


def win_rate_and_stats(trades: List[TradeRecord]) -> Dict[str, float]:
    if not trades:
        return {
            "win_rate": 0.0,
            "profit_loss_ratio": 0.0,
            "max_consecutive_loss": 0,
            "avg_holding_bars": 0.0,
            "profit_factor": 0.0,
        }
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl < 0]
    win_rate = len(wins) / len(trades)
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = abs(float(np.mean(losses))) if losses else 1e-10
    profit_loss_ratio = avg_win / avg_loss if avg_loss > 1e-10 else 0.0
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 1e-10
    profit_factor = gross_profit / gross_loss if gross_loss > 1e-10 else 0.0
    max_consec = 0
    cur_consec = 0
    for t in trades:
        if t.pnl < 0:
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 0
    hold_bars = [t.holding_bars for t in trades if t.holding_bars > 0]
    avg_holding = float(np.mean(hold_bars)) if hold_bars else 0.0
    return {
        "win_rate": win_rate,
        "profit_loss_ratio": round(profit_loss_ratio, 4),
        "max_consecutive_loss": max_consec,
        "avg_holding_bars": round(avg_holding, 1),
        "profit_factor": round(profit_factor, 4),
    }


def by_symbol_stats(trades: List[TradeRecord]) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, list] = {}
    for t in trades:
        groups.setdefault(t.symbol, []).append(t)
    result = {}
    for sym, sym_trades in groups.items():
        pnls = [t.pnl for t in sym_trades]
        wins = [p for p in pnls if p > 0]
        result[sym] = {
            "count": len(sym_trades),
            "win_rate": round(len(wins) / len(sym_trades), 4) if sym_trades else 0.0,
            "total_pnl": round(sum(pnls), 2),
            "avg_pnl": round(float(np.mean(pnls)), 2) if pnls else 0.0,
        }
    return result


def by_exit_reason_stats(trades: List[TradeRecord]) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, list] = {}
    for t in trades:
        groups.setdefault(t.exit_reason, []).append(t)
    result = {}
    for reason, reason_trades in groups.items():
        pnls = [t.pnl for t in reason_trades]
        result[reason] = {
            "count": len(reason_trades),
            "total_pnl": round(sum(pnls), 2),
        }
    return result


def estimate_garch_volatility(returns: pd.Series, spy: int = 252) -> float:
    import scipy.optimize as opt
    n = len(returns)
    if n < 10:
        return float(returns.std() * np.sqrt(spy))
    r = returns.values - returns.mean()
    var_init = np.var(r)
    if var_init < 1e-12:
        return 0.0

    def garch_nll(params):
        omega, alpha, beta = params
        sigma2 = np.zeros(n)
        sigma2[0] = var_init
        for t in range(1, n):
            sigma2[t] = omega + alpha * (r[t-1] ** 2) + beta * sigma2[t-1]
        if np.any(sigma2 <= 0):
            return 1e10
        return 0.5 * np.sum(np.log(sigma2) + (r ** 2) / sigma2)

    bounds = ((1e-8, 1.0), (0.0, 1.0), (0.0, 1.0))

    def constraint(params):
        return 0.999 - (params[1] + params[2])

    x0 = np.array([var_init * 0.05, 0.05, 0.90])
    try:
        res = opt.minimize(
            garch_nll, x0, bounds=bounds,
            constraints={"type": "ineq", "fun": constraint},
            method="SLSQP", options={"maxiter": 100},
        )
        if res.success:
            omega, alpha, beta = res.x
            sigma2 = np.zeros(n)
            sigma2[0] = var_init
            for t in range(1, n):
                sigma2[t] = omega + alpha * (r[t-1] ** 2) + beta * sigma2[t-1]
            mean_vol = np.mean(np.sqrt(sigma2))
            return float(mean_vol * np.sqrt(spy))
    except Exception:
        pass
    return float(returns.std() * np.sqrt(spy))


def calc_metrics(
    equity_curve: pd.Series,
    trades: List[TradeRecord],
    initial_cash: float,
    bars_per_year: Optional[int] = 252,
    bench_ret: Optional[pd.Series] = None,
) -> Dict[str, Any]:
    """Full set of performance metrics using new quant modules."""
    if len(equity_curve) == 0:
        return _empty_metrics(initial_cash)

    n = len(equity_curve)
    if bars_per_year is None:
        first, last = equity_curve.index[0], equity_curve.index[-1]
        calendar_days = (last - first).days
        years = calendar_days / 365.25 if calendar_days > 0 else 1.0
        bpy = int(n / years) if years > 0 else 252
    else:
        bpy = bars_per_year

    port_ret = equity_curve.pct_change().fillna(0.0)
    total_ret = float(equity_curve.iloc[-1] / initial_cash - 1)
    ann_ret = float((1 + total_ret) ** (bpy / max(n, 1)) - 1)

    sharpe = compute_sharpe(port_ret, bpy)
    sortino = compute_sortino(port_ret, bpy)
    max_dd = compute_max_drawdown(equity_curve)
    calmar = compute_calmar_ratio(port_ret, equity_curve, bpy)
    hit_rate = compute_hit_rate(port_ret)
    profit_factor = compute_profit_factor(port_ret)
    dsr = compute_deflated_sharpe(sharpe, n, n_trials=1000)

    var_95 = compute_var(port_ret, 0.05)
    cvar_95 = compute_cvar(port_ret, 0.05)
    garch_vol = estimate_garch_volatility(port_ret, bpy)

    trade_stats = win_rate_and_stats(trades)
    by_symbol = by_symbol_stats(trades)
    by_exit = by_exit_reason_stats(trades)

    bench_return = 0.0
    excess = 0.0
    ir = 0.0
    alpha = 0.0
    beta = 0.0
    if bench_ret is not None and len(bench_ret) > 0:
        bench_return = float((1 + bench_ret).prod() - 1)
        excess = total_ret - bench_return
        aligned = pd.concat([port_ret, bench_ret], axis=1).dropna()
        if len(aligned) > 5:
            alpha, beta = compute_alpha_beta(aligned.iloc[:, 0], aligned.iloc[:, 1])
            active_ret = aligned.iloc[:, 0] - aligned.iloc[:, 1]
            active_std = float(active_ret.std())
            ir = float(active_ret.mean() / (active_std + 1e-10) * np.sqrt(bpy))

    return {
        "final_value": float(equity_curve.iloc[-1]),
        "total_return": total_ret,
        "annual_return": ann_ret,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "calmar": round(calmar, 4),
        "sortino": round(sortino, 4),
        "deflated_sharpe": round(dsr, 4),
        "win_rate": trade_stats["win_rate"],
        "profit_loss_ratio": trade_stats["profit_loss_ratio"],
        "profit_factor": trade_stats["profit_factor"],
        "max_consecutive_loss": trade_stats["max_consecutive_loss"],
        "avg_holding_days": trade_stats["avg_holding_bars"],
        "trade_count": len(trades),
        "benchmark_return": round(bench_return, 6),
        "excess_return": round(excess, 6),
        "information_ratio": round(ir, 4),
        "alpha": round(alpha, 6),
        "beta": round(beta, 4),
        "var_95": round(var_95, 6),
        "cvar_95": round(cvar_95, 6),
        "garch_vol": round(garch_vol, 6),
    }


def _empty_metrics(initial_cash: float) -> Dict[str, Any]:
    return {
        "final_value": initial_cash,
        "total_return": 0,
        "annual_return": 0,
        "max_drawdown": 0,
        "sharpe": 0,
        "calmar": 0,
        "sortino": 0,
        "deflated_sharpe": 0,
        "win_rate": 0,
        "profit_loss_ratio": 0,
        "profit_factor": 0,
        "max_consecutive_loss": 0,
        "avg_holding_days": 0,
        "trade_count": 0,
        "benchmark_return": 0,
        "excess_return": 0,
        "information_ratio": 0,
        "alpha": 0,
        "beta": 0,
        "var_95": 0,
        "cvar_95": 0,
        "garch_vol": 0,
    }
