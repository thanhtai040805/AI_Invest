"""Poll persisted Shadow limit orders and dispatch only executable entries."""

import asyncio
import logging

import app.domain.agents  # Register the existing trade execution agent.
from app.core.registry import AgentRegistry
from app.domain.repositories.portfolio_repository import PortfolioRepository
from app.domain.rules.execution.shadow_fill import shadow_fill
from app.infrastructure.external_api.dnse.market_session import MarketSessionManager, MarketState
from app.infrastructure.external_api.market_data_service import market_data_svc

logger = logging.getLogger("ai_engine.daemon.shadow_orders")


class ShadowOrderDaemon:
    def __init__(self, interval_seconds: int = 1):
        self.interval = interval_seconds
        self.session = MarketSessionManager()
        self.repository = PortfolioRepository()
        self._running = False
        self._last_expiry_check = 0.0
        self._has_pending_orders = False

    async def run_single_tick(self) -> int:
        loop = asyncio.get_running_loop()
        if loop.time() - self._last_expiry_check >= 60:
            expired = self.repository.expire_pending_shadow_orders()
            self._last_expiry_check = loop.time()
            if expired:
                logger.info("Expired %s pending Shadow orders", expired)
        if self.session.get_market_state() not in (
            MarketState.CONTINUOUS_MORNING, MarketState.CONTINUOUS_AFTERNOON,
        ):
            self._has_pending_orders = False
            return 0

        filled = 0
        orders = self.repository.get_pending_shadow_orders()
        self._has_pending_orders = bool(orders)
        for order in orders:
            try:
                book = await market_data_svc.get_order_book(order["ticker"])
                fill_price = shadow_fill(book, order["side"], order["shares"], order["limit_price"])

                if order["order_type"] == "SHADOW_ML_LIMIT":
                    self.repository.execute_order_transaction(
                        ticker=order["ticker"], action="BUY", shares=order["shares"],
                        executed_price=fill_price, target_price=order["limit_price"],
                        execution_mode="SHADOW_PAPER", user_id=order["user_id"],
                        status="FILLED", pending_order_id=order["order_id"],
                    )
                    filled += 1
                    continue

                result = await AgentRegistry.dispatch("trade_execution", {
                    "order_instruction": {
                        "ticker": order["ticker"],
                        "side": order["side"],
                        "approved_shares": order["shares"],
                        "price": order["limit_price"],
                        "max_price": order["limit_price"],
                        "user_id": order["user_id"],
                        "pending_order_id": order["order_id"],
                    },
                    "orderbook": book,
                    "adtv20": 2_500_000,
                })
                data = result.get("result", {}).get("data", {}) if result.get("status") == "SUCCESS" else {}
                if data.get("status") == "EXECUTED":
                    filled += 1
                    logger.info("Shadow order %s filled for %s", order["order_id"], order["ticker"])
                elif data.get("status") and data["status"] not in (
                    "PENDING_SHADOW", "BLOCKED_NO_EXECUTABLE_DEPTH",
                ):
                    self.repository.cancel_pending_shadow_order(order["order_id"])
                    logger.warning("Shadow order %s cancelled: %s", order["order_id"], data["status"])
            except ValueError:
                continue
            except Exception:
                logger.exception("Shadow order scan failed for %s", order["order_id"])
        return filled

    async def start(self) -> None:
        self._running = True
        while self._running:
            try:
                await self.run_single_tick()
            except Exception:
                logger.exception("Shadow order scan failed")
            await asyncio.sleep(self.interval if self._has_pending_orders else max(5, self.interval))

    def stop(self) -> None:
        self._running = False


daemon = ShadowOrderDaemon()
