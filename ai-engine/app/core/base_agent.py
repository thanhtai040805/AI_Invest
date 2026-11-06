"""Base Agent Framework (IOS v5.1 Plug-and-Play & Event-Driven Architecture)

Mọi Agent trong hệ thống kế thừa từ BaseAgent sẽ tự động có:
1. Định danh Semantic độc lập (không phụ thuộc thứ tự số).
2. Tự động kết nối đúng bảng dữ liệu nghiệp vụ và bảng log riêng biệt.
3. Hỗ trợ 3 phương thức giao tiếp:
   - .run_event(): Thực thi pipeline trực tiếp & tự động ghi log vào bảng log riêng.
   - .publish_event(): Bắn sự kiện lên RabbitMQ Topic Exchange (aiinvest.events).
   - .subscribe_topics(): Lắng nghe các topics chỉ định từ RabbitMQ Queue.
   - .as_tool(): Cung cấp tool/function cho Chatbot hoặc FastMCP truy vấn O(1).
"""

from __future__ import annotations

import abc
import json
import logging
from datetime import datetime, timezone, date, time as day_time
from zoneinfo import ZoneInfo
from typing import Any, Callable, Coroutine, Dict, List, Optional

from app.application.ports.event_bus import EventMessage
from app.adapters.rabbitmq_event_bus import event_bus

logger = logging.getLogger(__name__)


def _resolve_target_date(event_data: Dict[str, Any], output_data: Dict[str, Any], is_replay: bool) -> date:
    """Trích xuất target_date từ event_data / output_data, ưu tiên ngày mô phỏng."""
    raw = (
        event_data.get("target_date")
        or event_data.get("date")
        or output_data.get("target_date")
        or output_data.get("date")
    )
    if isinstance(raw, datetime):
        return (
            raw.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date()
            if raw.tzinfo
            else raw.date()
        )
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw.split("T")[0])
        except Exception:
            if is_replay:
                raise ValueError("Replay audit requires a valid target_date")
    if is_replay:
        raise ValueError("Replay audit requires target_date")
    return datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()


def _resolve_created_at(target_dt: date, is_replay: bool) -> datetime:
    """Nếu replay → created_at = target_date 09:45 VN, nếu live → now()."""
    if is_replay:
        return datetime.combine(target_dt, day_time(9, 45), tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    return datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))


class BaseAgent(abc.ABC):
    def __init__(
        self,
        agent_name: str,
        state_tables: List[str],
        log_table: str,
        enabled: bool = True,
    ) -> None:
        self.agent_name = agent_name
        self.state_tables = state_tables
        self.log_table = log_table
        self.enabled = enabled
        self.queue_name = f"queue.agent.{agent_name}"

    @abc.abstractmethod
    async def process(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Triển khai logic nghiệp vụ cốt lõi của Agent."""
        pass

    async def run_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Thực thi nghiệp vụ và tự động ghi log vào bảng log riêng biệt của Agent."""
        if not self.enabled:
            logger.info(f"Agent {self.agent_name} is disabled. Skipping execution.")
            return {"status": "SKIPPED", "agent": self.agent_name, "reason": "DISABLED"}

        start_time = datetime.now(timezone.utc)
        logger.info(f"[{self.agent_name}] Bắt đầu thực thi sự kiện...")

        try:
            event_data["_from_run_event"] = True
            result = await self.process(event_data)
            
            # Ghi log độc lập vào bảng log riêng
            await self._log_audit_trace(
                event_data=event_data,
                computation_trace=result.get("trace", {}),
                output_data=result.get("data", result),
                status="SUCCESS",
            )
            
            return {
                "status": "SUCCESS",
                "agent": self.agent_name,
                "timestamp": start_time.isoformat(),
                "result": result,
            }
        except Exception as e:
            logger.error(f"[{self.agent_name}] Lỗi thực thi: {e}", exc_info=True)
            await self._log_audit_trace(
                event_data=event_data,
                computation_trace={"error_details": str(e)},
                output_data={"error": str(e)},
                status="FAILED",
            )
            raise e

    async def publish_event(
        self,
        topic: str,
        payload: Dict[str, Any],
        correlation_id: Optional[str] = None,
    ) -> EventMessage:
        """Bắn sự kiện lên RabbitMQ Topic Exchange."""
        return await event_bus.publish(
            topic=topic,
            payload=payload,
            source_agent=self.agent_name,
            correlation_id=correlation_id,
        )

    async def subscribe_topics(
        self,
        topics: List[str],
        custom_handler: Optional[Callable[[EventMessage], Coroutine[Any, Any, None]]] = None,
    ) -> None:
        """Đăng ký lắng nghe các topics từ RabbitMQ."""
        async def _default_handler(msg: EventMessage):
            logger.info(f"[{self.agent_name}] Nhận event từ Topic '{msg.topic}'...")
            await self.run_event(msg.payload)

        handler = custom_handler or _default_handler
        await event_bus.subscribe(
            queue_name=self.queue_name,
            routing_keys=topics,
            handler=handler,
        )

    async def _log_audit_trace(
        self,
        event_data: Dict[str, Any],
        computation_trace: Dict[str, Any],
        output_data: Dict[str, Any],
        status: str,
    ) -> None:
        """Ghi nhận vào bảng log tư duy riêng biệt chuẩn hóa theo schema 12 Agents."""
        if self.log_table == "log_equity_research" and status == "SUCCESS":
            return  # EquityResearchAgent writes the enriched, point-in-time row itself.
        try:
            from app.infrastructure.database.pg_pool import get_conn
            import uuid

            is_replay = bool(event_data.get("is_replay", False))
            target_date = _resolve_target_date(event_data, output_data, is_replay)
            created_timestamp = _resolve_created_at(target_date, is_replay)

            ticker = output_data.get("ticker") or event_data.get("ticker") or "PORTFOLIO"
            if isinstance(ticker, list):
                ticker = ticker[0] if ticker else "PORTFOLIO"
            ticker = str(ticker).upper()[:16]

            # Xác định câu lệnh INSERT theo từng bảng log cụ thể
            if self.log_table == "log_market_surveillance":
                sql = "INSERT INTO log_market_surveillance (date, inputs, computation_trace, outputs, created_at) VALUES (%s, %s, %s, %s, %s)"
                params = (target_date, json.dumps(event_data, default=str), json.dumps(computation_trace, default=str), json.dumps(output_data, default=str), created_timestamp)
            elif self.log_table == "log_universe_discovery":
                sql = "INSERT INTO log_universe_discovery (date, filtered_counts, beneish_trace, exclusion_log, created_at) VALUES (%s, %s, %s, %s, %s)"
                params = (target_date, json.dumps(output_data, default=str), json.dumps(computation_trace, default=str), json.dumps(output_data.get("exclusion_log", []), default=str), created_timestamp)
            elif self.log_table == "log_equity_research":
                sql = "INSERT INTO log_equity_research (ticker, date, factor_raw_metrics, business_quality_evidence, llm_prompt_tokens, created_at) VALUES (%s, %s, %s, %s, %s, %s)"
                params = (ticker, target_date, json.dumps(output_data, default=str), json.dumps(computation_trace, default=str), 0, created_timestamp)
            elif self.log_table == "log_investment_thesis":
                thesis_id = output_data.get("thesis_id") or str(uuid.uuid4())
                pre_mortem = (
                    output_data.get("pre_mortem_scenarios")
                    or (output_data.get("thesis_body", {}).get("pre_mortem") if isinstance(output_data.get("thesis_body"), dict) else [])
                    or []
                )
                sql = "INSERT INTO log_investment_thesis (thesis_id, ticker, pre_mortem_scenarios, thesis_text, analysis_date, is_replay, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                params = (thesis_id, ticker, json.dumps(pre_mortem, default=str), json.dumps(output_data, default=str), target_date, is_replay, created_timestamp)
            elif self.log_table == "log_counter_thesis":
                thesis_id = output_data.get("thesis_id") or str(uuid.uuid4())
                verdict = str(output_data.get("verdict", "PROCEED"))[:16]
                sql = "INSERT INTO log_counter_thesis (thesis_id, ticker, debate_challenge_text, llm_prompt_response, verdict, created_at) VALUES (%s, %s, %s, %s, %s, %s)"
                params = (thesis_id, ticker, str(output_data.get("rationale", "")), json.dumps(computation_trace, default=str), verdict, created_timestamp)
            elif self.log_table == "log_portfolio_risk":
                sql = "INSERT INTO log_portfolio_risk (date, es_97_5_inputs, covariance_matrix, garch_cash_trace, created_at) VALUES (%s, %s, %s, %s, %s)"
                params = (target_date, json.dumps(event_data, default=str), json.dumps(computation_trace, default=str), json.dumps(output_data, default=str), created_timestamp)
            elif self.log_table == "log_portfolio_allocation":
                weight_pct = float(output_data.get("allocated_weight_pct", 0.0))
                sql = "INSERT INTO log_portfolio_allocation (ticker, kelly_math_steps, allocated_weight_pct, rationale, created_at) VALUES (%s, %s, %s, %s, %s)"
                params = (ticker, json.dumps(computation_trace, default=str), weight_pct, str(output_data.get("rationale", "")), created_timestamp)
            elif self.log_table == "log_trade_execution":
                order_id = output_data.get("order_id") or str(uuid.uuid4())
                sql = "INSERT INTO log_trade_execution (order_id, ticker, slicing_schedule, orderbook_depth_snapshot, created_at) VALUES (%s, %s, %s, %s, %s)"
                params = (order_id, ticker, json.dumps(computation_trace, default=str), json.dumps(output_data, default=str), created_timestamp)
            elif self.log_table == "log_position_monitoring":
                pnl = float(output_data.get("pnl_pct", 0.0))
                stop_loss = bool(output_data.get("stop_loss_triggered", False))
                invalidated = bool(output_data.get("thesis_invalidated", False))
                sql = "INSERT INTO log_position_monitoring (ticker, pnl_pct, stop_loss_triggered, thesis_invalidated, created_at) VALUES (%s, %s, %s, %s, %s)"
                params = (ticker, pnl, stop_loss, invalidated, created_timestamp)
            elif self.log_table == "log_reinforcement_learning":
                sql = "INSERT INTO log_reinforcement_learning (date, ic_rolling_scores, reward_signals, policy_weight_updates, created_at) VALUES (%s, %s, %s, %s, %s)"
                params = (target_date, json.dumps(output_data.get("ic_by_factor", {}), default=str), json.dumps(computation_trace, default=str), json.dumps(output_data.get("policy_weights", {}), default=str), created_timestamp)
            elif self.log_table == "log_system_governance":
                sql = "INSERT INTO log_system_governance (rule_id, action_type, audit_trail_verification, created_at) VALUES (%s, %s, %s, %s)"
                params = ("RULE_ALL", "GOVERNANCE_AUDIT", json.dumps(output_data, default=str), created_timestamp)
            elif self.log_table == "log_strategy_cio":
                sql = "INSERT INTO log_strategy_cio (trigger_type, debate_synthesis, resolution_payload, created_at) VALUES (%s, %s, %s, %s)"
                params = ("CIO_ARBITRATION", str(output_data.get("executive_rationale", "")), json.dumps(output_data, default=str), created_timestamp)
            else:
                return

            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
        except Exception as e:
            logger.warning(f"Không thể ghi audit log cho {self.agent_name} vào {self.log_table}: {e}")

    def as_tool(self) -> Dict[str, Any]:
        """Cung cấp metadata phục vụ FastMCP / Chatbot Tool Call."""
        return {
            "name": f"agent_{self.agent_name}",
            "description": f"Truy vấn dữ liệu và trạng thái từ Agent {self.agent_name}",
            "state_tables": self.state_tables,
            "log_table": self.log_table,
            "enabled": self.enabled,
        }
