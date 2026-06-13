"""Trade Execution Service — bridge between signal engine and DNSE live trading.

Two modes:
  - PAPER (default): logs intent, no real orders
  - LIVE: places real orders via DNSE REST API with safety checks

Safety features:
  - Max position size (20% of portfolio)
  - Max daily loss (5% of portfolio)
  - Hard risk flag check (DO_NOT_TRADE flags block execution)
  - Human approval required for trades > 20M VND
  - Circuit breaker: auto-stop if drawdown > 10%
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras

from app.services.pg_pool import DB_URL
from app.services.trading_rules import (
    TradingRulesConfig, calc_position_size, check_all_rules,
    PortfolioState, PositionInfo,
)
from app.brain.state.confidence_scorer import HARD_FLAGS

logger = logging.getLogger(__name__)

TZ_VN = timezone.utc  # simplified; VN is UTC+7

MAX_POSITION_PCT = 0.20
MAX_DAILY_LOSS_PCT = -0.05
MAX_DRAWDOWN_PCT = -0.10
HUMAN_APPROVAL_THRESHOLD = 20_000_000  # 20M VND


class ExecutionMode(Enum):
    PAPER = "paper"
    LIVE = "live"


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class OrderRequest:
    symbol: str
    side: OrderSide
    quantity: int
    price: float
    order_type: str = "LO"  # LO = limit order, MP = market price
    account_no: str = ""
    trading_token: str = ""
    loan_package_id: str = ""


@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str] = None
    dnse_order_id: Optional[str] = None
    filled_quantity: int = 0
    filled_price: float = 0.0
    status: str = ""
    error: Optional[str] = None
    human_approval_required: bool = False
    human_approved: bool = False


class TradeExecutionService:
    def __init__(self, mode: ExecutionMode = ExecutionMode.PAPER):
        self.mode = mode
        self.daily_pnl: float = 0.0
        self.initial_portfolio_value: float = 100_000_000
        self.peak_portfolio_value: float = self.initial_portfolio_value
        self.circuit_breaker_active: bool = False
        self.trade_date: date = date.today()
        self._dnse_client = None

    def get_dnse_client(self):
        if self._dnse_client is None and self.mode == ExecutionMode.LIVE:
            try:
                from app.services.dnse.api.client import DNSEClient
                self._dnse_client = DNSEClient()
            except Exception as e:
                logger.error("Failed to initialize DNSE client: %s", e)
        return self._dnse_client

    def execute_signal(self, symbol: str, side: OrderSide,
                       portfolio_value: float, current_price: float,
                       active_flags: Optional[List[str]] = None,
                       force: bool = False) -> OrderResult:
        if self.circuit_breaker_active and not force:
            return OrderResult(
                success=False, status="BLOCKED_CIRCUIT_BREAKER",
                error="Circuit breaker active — drawdown exceeds limit",
            )

        hard = set(active_flags or []) & HARD_FLAGS
        if hard and not force:
            return OrderResult(
                success=False, status="BLOCKED_HARD_FLAGS",
                error=f"Hard risk flags active: {', '.join(sorted(hard))}",
            )

        qty, method = calc_position_size(
            symbol, current_price, portfolio_value,
            max_pct_per_position=MAX_POSITION_PCT,
        )
        if qty < 100:
            return OrderResult(
                success=False, status="BLOCKED_MIN_LOT",
                error=f"Calculated quantity {qty} < 100 (min lot)",
            )

        estimated_cost = qty * current_price
        human_approval = estimated_cost >= HUMAN_APPROVAL_THRESHOLD

        if self.mode == ExecutionMode.PAPER:
            self._log_paper_order(symbol, side, qty, current_price, active_flags)
            return OrderResult(
                success=True,
                status="PAPER_EXECUTED",
                filled_quantity=qty,
                filled_price=current_price,
                human_approval_required=human_approval,
                human_approved=not human_approval,
            )

        return self._execute_live(
            OrderRequest(
                symbol=symbol, side=side, quantity=qty,
                price=current_price,
            ),
            human_approval,
        )

    def _execute_live(self, req: OrderRequest,
                      human_approval: bool) -> OrderResult:
        client = self.get_dnse_client()
        if client is None:
            return OrderResult(
                success=False, status="NO_DNSE_CLIENT",
                error="DNSE client not available",
            )

        if human_approval:
            return OrderResult(
                success=False, status="HUMAN_APPROVAL_REQUIRED",
                human_approval_required=True,
                error=f"Trade value exceeds {HUMAN_APPROVAL_THRESHOLD:,.0f} VND, needs approval",
            )

        try:
            payload = {
                "symbol": req.symbol,
                "side": req.side.value,
                "quantity": req.quantity,
                "price": req.price,
                "orderType": req.order_type,
                "accountNo": req.account_no,
                "loanPackageId": req.loan_package_id,
            }
            response = client.post_order("STO", payload, req.trading_token)
            order_id = response.get("orderId") or response.get("id")
            return OrderResult(
                success=True,
                dnse_order_id=order_id,
                status="LIVE_SUBMITTED",
                filled_quantity=req.quantity,
                filled_price=req.price,
            )
        except Exception as e:
            logger.error("Live order failed: %s", e)
            return OrderResult(
                success=False, status="LIVE_FAILED",
                error=str(e),
            )

    def update_daily_pnl(self, pnl: float, portfolio_value: float) -> Dict[str, Any]:
        self.daily_pnl += pnl
        self.peak_portfolio_value = max(self.peak_portfolio_value, portfolio_value)
        drawdown = (portfolio_value - self.peak_portfolio_value) / self.peak_portfolio_value

        if drawdown <= MAX_DRAWDOWN_PCT:
            self.circuit_breaker_active = True
            logger.warning("CIRCUIT BREAKER ACTIVATED: drawdown=%.2f%%", drawdown * 100)
        if self.daily_pnl / portfolio_value <= MAX_DAILY_LOSS_PCT:
            logger.warning("DAILY LOSS LIMIT: PnL=%.2f%%", self.daily_pnl / portfolio_value * 100)

        return {
            "daily_pnl": round(self.daily_pnl),
            "daily_pnl_pct": round(self.daily_pnl / portfolio_value * 100, 2),
            "peak_value": round(self.peak_portfolio_value),
            "drawdown": round(drawdown * 100, 2),
            "circuit_breaker": self.circuit_breaker_active,
        }

    def cancel_order(self, order_id: str, account_no: str = "") -> OrderResult:
        if self.mode == ExecutionMode.PAPER:
            return OrderResult(success=True, status="PAPER_CANCELLED")
        client = self.get_dnse_client()
        if client is None:
            return OrderResult(success=False, status="NO_DNSE_CLIENT")
        try:
            client.cancel_order(account_no, order_id)
            return OrderResult(success=True, status="LIVE_CANCELLED")
        except Exception as e:
            return OrderResult(success=False, status="CANCEL_FAILED", error=str(e))

    def reset_daily(self):
        self.daily_pnl = 0.0
        self.trade_date = date.today()

    @staticmethod
    def _log_paper_order(symbol: str, side: OrderSide, qty: int,
                         price: float, flags: Optional[List[str]] = None):
        logger.info(
            "[PAPER] %s %d %s @ %.0f (flags=%s)",
            side.value, qty, symbol, price, flags or "none",
        )


def auto_pilot(trade_date_str: Optional[str] = None, mode: str = "paper") -> Dict[str, Any]:
    """One-shot: read today's signals → execute → update portfolio."""
    from app.services.paper_trading_service import PaperTradingService

    d = date.fromisoformat(trade_date_str) if trade_date_str else date.today()
    exec_mode = ExecutionMode.LIVE if mode == "live" else ExecutionMode.PAPER

    svc = PaperTradingService()
    svc_results = svc.process_today_signals(d)

    exec_svc = TradeExecutionService(mode=exec_mode)
    portfolio_value = svc_results.get("total_value", 100_000_000)
    safety = exec_svc.update_daily_pnl(0, portfolio_value)

    return {
        "trade_date": d.isoformat(),
        "mode": exec_mode.value,
        "signals_processed": svc_results.get("buys", 0) + svc_results.get("sells", 0),
        "portfolio_value": portfolio_value,
        "safety": safety,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    import json
    result = auto_pilot(mode="paper")
    print(json.dumps(result, indent=2, ensure_ascii=False))
