"""Trading Rules Engine — configurable stop-loss, take-profit, position sizing,
portfolio constraints, and rebalancing logic for Vietnamese equities.

All rules are stateless: given market state + positions + config, return decisions.
Callers (backtest engine, live agent, signal engine) apply the decisions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class PositionInfo:
    symbol: str
    entry_price: float
    current_price: float
    quantity: int
    pnl_pct: float          # (current - entry) / entry
    days_held: int
    entry_value: float      # entry_price * quantity
    current_value: float    # current_price * quantity


@dataclass
class PortfolioState:
    total_value: float
    cash: float
    positions: List[PositionInfo] = field(default_factory=list)
    peak_value: float = 0.0
    daily_return: float = 0.0


@dataclass
class RuleResult:
    action: str              # "HOLD" | "SELL" | "BUY" | "REBALANCE"
    symbol: str
    reason: str
    rule_name: str
    quantity: Optional[int] = None
    priority: int = 0


# ---------------------------------------------------------------------------
# Stop-loss rules
# ---------------------------------------------------------------------------

def check_stop_loss(
    pos: PositionInfo,
    fixed_pct: float = -0.10,
    trailing_pct: float = -0.08,
    peak_price: Optional[float] = None,
    max_hold_days: int = 999,
) -> Optional[RuleResult]:
    """Check all stop-loss variants. Returns first triggered rule, or None."""
    # Fixed % stop-loss
    if pos.pnl_pct <= fixed_pct:
        return RuleResult(
            action="SELL",
            symbol=pos.symbol,
            rule_name="stop_loss_fixed",
            reason=f"Fixed stop-loss triggered: PnL {pos.pnl_pct*100:.1f}% <= {fixed_pct*100:.1f}%",
            priority=100,
        )

    # Trailing stop-loss
    if peak_price is not None and peak_price > pos.entry_price:
        drawdown_from_peak = (pos.current_price - peak_price) / peak_price
        if drawdown_from_peak <= trailing_pct:
            return RuleResult(
                action="SELL",
                symbol=pos.symbol,
                rule_name="stop_loss_trailing",
                reason=f"Trailing stop triggered: {drawdown_from_peak*100:.1f}% from peak {peak_price:,.0f}",
                priority=90,
            )

    # Time-based stop-loss
    if pos.days_held >= max_hold_days:
        return RuleResult(
            action="SELL",
            symbol=pos.symbol,
            rule_name="stop_loss_time",
            reason=f"Max hold days reached: {pos.days_held} >= {max_hold_days}",
            priority=80,
        )

    return None


# ---------------------------------------------------------------------------
# Take-profit rules
# ---------------------------------------------------------------------------

def check_take_profit(
    pos: PositionInfo,
    fixed_pct: float = 0.20,
    trailing_pct: Optional[float] = None,
    peak_price: Optional[float] = None,
) -> Optional[RuleResult]:
    """Check take-profit rules."""
    # Fixed take-profit
    if pos.pnl_pct >= fixed_pct:
        return RuleResult(
            action="SELL",
            symbol=pos.symbol,
            rule_name="take_profit_fixed",
            reason=f"Take-profit triggered: PnL {pos.pnl_pct*100:.1f}% >= {fixed_pct*100:.1f}%",
            priority=70,
        )

    return None


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------

from app.domain.rules.position_sizing import volatility_targeted_size

def calc_position_size(
    symbol: str,
    price: float,
    portfolio_value: float,
    max_pct_per_position: float = 0.10,
    atr: Optional[float] = None,
    risk_per_trade_pct: float = 0.02,
    adv_20d_volume: Optional[float] = None,
    max_adv_pct: float = 0.05,
) -> Tuple[int, str]:
    """Calculate position size using volatility-adjusted or fixed-fraction method,
    with an optional cap based on Average Daily Volume (ADV).

    Returns:
        (quantity, method_description)
    """
    return volatility_targeted_size(
        symbol=symbol,
        price=price,
        portfolio_value=portfolio_value,
        target_vol_pct=risk_per_trade_pct,
        atr=atr,
        adv_20d_volume=adv_20d_volume,
        max_adv_pct=max_adv_pct,
        max_pct_per_position=max_pct_per_position
    )


# ---------------------------------------------------------------------------
# Portfolio-level constraints
# ---------------------------------------------------------------------------

def check_portfolio_constraints(
    portfolio: PortfolioState,
    max_concentration_pct: float = 0.20,
    max_sector_pct: float = 0.30,
    max_drawdown_pct: float = -0.25,
    min_cash_pct: float = 0.05,
    sector_map: Optional[Dict[str, str]] = None,
) -> List[RuleResult]:
    """Check portfolio-level constraints. Returns list of triggered rules."""
    results: List[RuleResult] = []

    # Max drawdown check
    if portfolio.peak_value > 0:
        dd = (portfolio.total_value - portfolio.peak_value) / portfolio.peak_value
        if dd <= max_drawdown_pct:
            # Force close all positions
            for pos in portfolio.positions:
                results.append(RuleResult(
                    action="SELL",
                    symbol=pos.symbol,
                    rule_name="portfolio_max_drawdown",
                    reason=f"Portfolio drawdown {dd*100:.1f}% <= {max_drawdown_pct*100:.1f}%, closing {pos.symbol}",
                    priority=200,
                ))

    # Min cash constraint
    cash_pct = portfolio.cash / portfolio.total_value if portfolio.total_value > 0 else 1.0
    if cash_pct < min_cash_pct:
        # Need to raise cash — sell worst performer
        if portfolio.positions:
            worst = min(portfolio.positions, key=lambda p: p.pnl_pct)
            results.append(RuleResult(
                action="SELL",
                symbol=worst.symbol,
                rule_name="portfolio_min_cash",
                reason=f"Cash {cash_pct*100:.1f}% < min {min_cash_pct*100:.1f}%, selling worst performer",
                priority=150,
            ))

    # Concentration check
    for pos in portfolio.positions:
        pos_pct = pos.current_value / portfolio.total_value if portfolio.total_value > 0 else 0
        if pos_pct > max_concentration_pct:
            results.append(RuleResult(
                action="SELL",
                symbol=pos.symbol,
                rule_name="portfolio_concentration",
                reason=f"Position {pos_pct*100:.1f}% > max {max_concentration_pct*100:.1f}%",
                quantity=int((pos.current_value - portfolio.total_value * max_concentration_pct) / pos.current_price / 100) * 100,
                priority=120,
            ))

    return results


# ---------------------------------------------------------------------------
# Rebalancing
# ---------------------------------------------------------------------------

def check_rebalance(
    portfolio: PortfolioState,
    target_weights: Dict[str, float],
    rebalance_threshold: float = 0.05,
    max_trades: int = 5,
) -> List[RuleResult]:
    """Check if rebalancing is needed based on drift from target weights.

    Args:
        portfolio: Current portfolio state.
        target_weights: symbol -> target fraction of portfolio.
        rebalance_threshold: Max allowed deviation before rebalancing.
        max_trades: Max trades to suggest.

    Returns:
        List of rebalance actions.
    """
    results: List[RuleResult] = []
    deviations: List[Tuple[str, float, float]] = []

    for symbol, target in target_weights.items():
        if target <= 0:
            continue
        pos = next((p for p in portfolio.positions if p.symbol == symbol), None)
        current_weight = (pos.current_value / portfolio.total_value) if pos else 0.0
        deviation = current_weight - target
        if abs(deviation) > rebalance_threshold:
            deviations.append((symbol, deviation, target))

    if not deviations:
        return results

    # Sort by absolute deviation descending
    deviations.sort(key=lambda x: abs(x[1]), reverse=True)

    for symbol, deviation, target in deviations[:max_trades]:
        action = "SELL" if deviation > 0 else "BUY"
        results.append(RuleResult(
            action=action,
            symbol=symbol,
            rule_name="rebalance_drift",
            reason=f"Weight drift: current {((target + deviation) if action == 'SELL' else target)*100:.1f}% vs target {target*100:.1f}%",
            priority=50,
        ))

    return results


# ---------------------------------------------------------------------------
# Master check
# ---------------------------------------------------------------------------

@dataclass
class TradingRulesConfig:
    """Central configuration for all trading rules."""
    # Stop-loss
    stop_loss_fixed_pct: float = -0.10
    stop_loss_trailing_pct: float = -0.08
    max_hold_days: int = 999

    # Take-profit
    take_profit_fixed_pct: float = 0.20

    # Position sizing
    max_position_pct: float = 0.10
    risk_per_trade_pct: float = 0.02

    # Portfolio constraints
    max_concentration_pct: float = 0.20
    max_drawdown_pct: float = -0.25
    min_cash_pct: float = 0.05

    # Rebalancing
    rebalance_threshold: float = 0.05
    rebalance_frequency_days: int = 30


def check_all_rules(
    portfolio: PortfolioState,
    peak_prices: Optional[Dict[str, float]] = None,
    atr_values: Optional[Dict[str, float]] = None,
    target_weights: Optional[Dict[str, float]] = None,
    sector_map: Optional[Dict[str, str]] = None,
    config: Optional[TradingRulesConfig] = None,
) -> Dict[str, Any]:
    """Run all trading rules against current portfolio state.

    Args:
        portfolio: Current portfolio state.
        peak_prices: symbol -> highest price since entry.
        atr_values: symbol -> ATR value for volatility-adjusted sizing.
        target_weights: symbol -> target allocation for rebalancing.
        sector_map: symbol -> sector name for sector constraints.
        config: Rule configuration. Uses defaults if None.

    Returns:
        Dict with keys:
          - signals: list of RuleResult dicts
          - totalSignals: int
          - sellSignals: int
          - buySignals: int
          - hasAlerts: bool
          - portfolioHealth: str ("GOOD" | "WARN" | "CRITICAL")
    """
    cfg = config or TradingRulesConfig()
    signals: List[RuleResult] = []
    peak_prices = peak_prices or {}
    atr_values = atr_values or {}

    # Per-position checks
    for pos in portfolio.positions:
        peak = peak_prices.get(pos.symbol, pos.entry_price)

        # Stop-loss
        sl = check_stop_loss(
            pos,
            fixed_pct=cfg.stop_loss_fixed_pct,
            trailing_pct=cfg.stop_loss_trailing_pct,
            peak_price=peak,
            max_hold_days=cfg.max_hold_days,
        )
        if sl:
            signals.append(sl)
            continue

        # Take-profit
        tp = check_take_profit(
            pos,
            fixed_pct=cfg.take_profit_fixed_pct,
            peak_price=peak,
        )
        if tp:
            signals.append(tp)
            continue

    # Portfolio-level checks
    portfolio_signals = check_portfolio_constraints(
        portfolio,
        max_concentration_pct=cfg.max_concentration_pct,
        max_drawdown_pct=cfg.max_drawdown_pct,
        min_cash_pct=cfg.min_cash_pct,
        sector_map=sector_map,
    )
    signals.extend(portfolio_signals)

    # Rebalancing
    if target_weights:
        rebalance_signals = check_rebalance(
            portfolio,
            target_weights,
            rebalance_threshold=cfg.rebalance_threshold,
        )
        signals.extend(rebalance_signals)

    # Sort by priority descending
    signals.sort(key=lambda s: s.priority, reverse=True)

    sell_count = sum(1 for s in signals if s.action == "SELL")
    buy_count = sum(1 for s in signals if s.action == "BUY")
    has_critical = any(s.priority >= 100 for s in signals)

    if has_critical:
        health = "CRITICAL"
    elif sell_count > 0:
        health = "WARN"
    else:
        health = "GOOD"

    return {
        "signals": [
            {
                "action": s.action,
                "symbol": s.symbol,
                "ruleName": s.rule_name,
                "reason": s.reason,
                "quantity": s.quantity,
                "priority": s.priority,
            }
            for s in signals
        ],
        "totalSignals": len(signals),
        "sellSignals": sell_count,
        "buySignals": buy_count,
        "hasAlerts": len(signals) > 0,
        "portfolioHealth": health,
    }


def calc_suggested_position_size(
    symbol: str,
    price: float,
    portfolio_value: float,
    atr: Optional[float] = None,
    config: Optional[TradingRulesConfig] = None,
) -> Dict[str, Any]:
    """Calculate suggested position size without opening a position.

    Useful for pre-trade analysis.
    """
    cfg = config or TradingRulesConfig()
    quantity, method = calc_position_size(
        symbol, price, portfolio_value,
        max_pct_per_position=cfg.max_position_pct,
        atr=atr,
        risk_per_trade_pct=cfg.risk_per_trade_pct,
    )
    cost = quantity * price
    pct_of_portfolio = cost / portfolio_value if portfolio_value > 0 else 0

    return {
        "symbol": symbol,
        "price": price,
        "suggestedQuantity": quantity,
        "estimatedCost": cost,
        "percentOfPortfolio": round(pct_of_portfolio * 100, 2),
        "method": method,
        "portfolioValue": portfolio_value,
    }
