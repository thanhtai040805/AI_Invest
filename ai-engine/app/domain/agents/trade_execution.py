"""AGENT-08: Trade Execution Agent (IOS v5.1)

Gom nhóm và điều phối các engines thực tế:
- ExecutionAdaptationEngine (app/domain/rules/execution/eae.py)
- PortfolioRepository (app/domain/repositories/portfolio_repository.py) kết nối PostgreSQL
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional
from app.core.base_agent import BaseAgent
from app.domain.rules.execution.eae import ExecutionAdaptationEngine
from app.domain.repositories.portfolio_repository import PortfolioRepository

logger = logging.getLogger(__name__)


class TradeExecutionAgent(BaseAgent):
    def __init__(self, repository: Optional[PortfolioRepository] = None):
        super().__init__(
            agent_name="trade_execution",
            state_tables=["order_executions", "slippage_records"],
            log_table="log_trade_execution",
            enabled=True,
        )
        self.eae_engine = ExecutionAdaptationEngine()
        self.repository = repository or PortfolioRepository()

    async def process(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Thực thi lệnh bằng ExecutionAdaptationEngine và tự động cập nhật CSDL PostgreSQL."""
        decision = event_data.get("order_instruction", {})
        ticker = decision.get("ticker") or event_data.get("ticker")
        if not ticker:
            raise ValueError("[TradeExecutionAgent] Thiếu mã cổ phiếu (ticker) trong lệnh thực thi.")
        ticker = str(ticker).upper().strip()

        action = decision.get("action", "BUY")
        shares = int(decision.get("target_shares", decision.get("shares", 0)))
        if shares <= 0:
            logger.info(f"[TradeExecutionAgent] Số lượng cổ phiếu = 0 cho {ticker}. Bỏ qua thực thi.")
            return {
                "data": {
                    "order_id": str(uuid.uuid4()),
                    "ticker": ticker,
                    "action": action,
                    "shares": 0,
                    "executed_price": 0.0,
                    "target_price": 0.0,
                    "slippage_bps": 0.0,
                    "slice_count": 0,
                    "execution_mode": "NONE",
                    "status": "SKIPPED_ZERO_SHARES",
                    "remaining_cash": self.repository.get_account_state().get("cash_balance", 0.0),
                },
                "trace": {"reason": "ZERO_SHARES"},
            }

        target_price = float(decision.get("target_price", 0.0))
        if target_price <= 0:
            try:
                from app.domain.repositories.market_data_repository import MarketDataRepository
                m_repo = MarketDataRepository()
                latest_m = m_repo.get_market_data_daily(ticker, limit=1)
                if latest_m and "close" in latest_m[0]:
                    target_price = float(latest_m[0]["close"])
            except Exception:
                target_price = 100000.0

        adtv20 = float(event_data.get("adtv20", 2500000.0))

        # 1. Gọi EAE thực tế để chia nhỏ slices
        slices = self.eae_engine.slice_order(
            ticker=ticker,
            side=action,
            total_quantity=shares,
            adtv20=adtv20,
            urgency="NORMAL",
        )

        executed_price = target_price * 1.0012 if action == "BUY" else target_price * 0.9988
        slippage_bps = abs(executed_price - target_price) / target_price * 10000.0

        # 2. Ghi nhận giao dịch vào CSDL qua PortfolioRepository (Atomic Transaction)
        tx_result = self.repository.execute_order_transaction(
            ticker=ticker,
            action=action,
            shares=shares,
            executed_price=executed_price,
            target_price=target_price,
            slippage_bps=slippage_bps,
            execution_mode="VWAP_SLICED",
        )

        execution_report = {
            "order_id": tx_result["order_id"],
            "ticker": ticker,
            "action": action,
            "shares": shares,
            "executed_price": round(executed_price, 2),
            "target_price": target_price,
            "slippage_bps": round(slippage_bps, 2),
            "slice_count": len(slices),
            "execution_mode": "VWAP_SLICED",
            "status": "EXECUTED",
            "remaining_cash": tx_result.get("remaining_cash"),
        }

        trace = {
            "eae_engine": self.eae_engine.__class__.__name__,
            "slices": [
                {"slice": i+1, "quantity": s.quantity, "type": s.price_type}
                for i, s in enumerate(slices)
            ],
            "db_synced": True,
        }

        return {"data": execution_report, "trace": trace}
