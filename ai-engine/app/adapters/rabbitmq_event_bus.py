"""RabbitMQ Event Bus Adapter (AMQP 0-9-1 & In-Memory Async Fallback).
Implements Topic Exchange 'aiinvest.events' with Dead-Letter Exchange 'aiinvest.dlx'.

Production-Ready:
- Kết nối thật đến RabbitMQ Broker qua aio_pika (AMQP 0-9-1).
- Auto-reconnect với exponential backoff khi mất kết nối.
- Dual-mode: AMQP publish lên broker + In-Memory dispatch cho local subscribers.
- Dead-Letter Exchange (DLX) + Dead-Letter Queue (DLQ) trên broker thật.
- Fallback In-Memory khi broker không khả dụng (dev/test/single-instance).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Callable, Coroutine, Dict, List, Optional
import uuid

from app.application.ports.event_bus import EventBusPort, EventMessage
from app.core.event_topics import EventTopics

logger = logging.getLogger(__name__)

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
EXCHANGE_NAME = "aiinvest.events"
DLX_EXCHANGE_NAME = "aiinvest.dlx"
DLQ_QUEUE_NAME = "aiinvest.dlq"

# Reconnection settings
MAX_RECONNECT_ATTEMPTS = 10
RECONNECT_BASE_DELAY_S = 1.0
RECONNECT_MAX_DELAY_S = 60.0


class RabbitMQEventBus(EventBusPort):
    """
    Adapter RabbitMQ Event Bus cho 12 Agents.
    Hỗ trợ:
    - Topic-based Routing (`market.pulse`, `policy.weights`, v.v.)
    - Dead-Letter Queue (DLQ) cho message lỗi
    - Resilient In-Memory Async Routing fallback khi broker offline hoặc chạy test.
    - Auto-reconnect với exponential backoff.
    """

    def __init__(self, rabbitmq_url: Optional[str] = None):
        self.rabbitmq_url = rabbitmq_url or RABBITMQ_URL
        self._is_connected = False
        self._use_in_memory_fallback = True
        self._subscribers: Dict[str, List[Dict[str, Any]]] = {}
        self._published_history: List[EventMessage] = []
        self._dlq_messages: List[Dict[str, Any]] = []

        # aio_pika live connection objects
        self._connection: Any = None
        self._channel: Any = None
        self._exchange: Any = None
        self._dlx_exchange: Any = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._reconnect_attempts = 0

    async def connect(self) -> None:
        """Kết nối đến RabbitMQ thật qua aio_pika. Fallback In-Memory nếu broker không khả dụng."""
        try:
            import aio_pika

            connection = await aio_pika.connect_robust(
                self.rabbitmq_url,
                timeout=5.0,
                reconnect_interval=5.0,
            )
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=10)

            # Declare Topic Exchange
            exchange = await channel.declare_exchange(
                EXCHANGE_NAME,
                aio_pika.ExchangeType.TOPIC,
                durable=True,
            )

            # Declare Dead-Letter Exchange & Queue
            dlx_exchange = await channel.declare_exchange(
                DLX_EXCHANGE_NAME,
                aio_pika.ExchangeType.FANOUT,
                durable=True,
            )
            dlq_queue = await channel.declare_queue(
                DLQ_QUEUE_NAME,
                durable=True,
            )
            await dlq_queue.bind(dlx_exchange)

            # Store references
            self._connection = connection
            self._channel = channel
            self._exchange = exchange
            self._dlx_exchange = dlx_exchange

            self._is_connected = True
            self._use_in_memory_fallback = False
            self._reconnect_attempts = 0

            # Register close callback for auto-reconnect
            connection.close_callbacks.add(self._on_connection_lost)

            logger.info(
                f"✅ [RabbitMQ] Connected to LIVE broker at {self.rabbitmq_url} "
                f"| Exchange: {EXCHANGE_NAME} | DLX: {DLX_EXCHANGE_NAME}"
            )

        except ImportError:
            self._is_connected = False
            self._use_in_memory_fallback = True
            logger.warning(
                "⚠️ [RabbitMQ] aio_pika not installed — operating in IN-MEMORY mode. "
                "Install with: pip install aio-pika"
            )

        except Exception as e:
            self._is_connected = False
            self._use_in_memory_fallback = True
            logger.warning(
                f"⚠️ [RabbitMQ] Cannot connect to broker ({e}). "
                f"Operating in IN-MEMORY fallback mode. "
                f"Events will be dispatched locally only."
            )

    def _on_connection_lost(self, *args, **kwargs) -> None:
        """Callback khi mất kết nối RabbitMQ — kích hoạt auto-reconnect."""
        logger.error("❌ [RabbitMQ] Connection lost! Switching to IN-MEMORY fallback.")
        self._is_connected = False
        self._use_in_memory_fallback = True
        self._exchange = None
        self._channel = None

        # Schedule reconnect in background
        try:
            loop = asyncio.get_running_loop()
            if self._reconnect_task is None or self._reconnect_task.done():
                self._reconnect_task = loop.create_task(self._auto_reconnect())
        except RuntimeError:
            logger.warning("[RabbitMQ] No running event loop for auto-reconnect.")

    async def _auto_reconnect(self) -> None:
        """Auto-reconnect với exponential backoff."""
        while self._reconnect_attempts < MAX_RECONNECT_ATTEMPTS:
            self._reconnect_attempts += 1
            delay = min(
                RECONNECT_BASE_DELAY_S * (2 ** (self._reconnect_attempts - 1)),
                RECONNECT_MAX_DELAY_S,
            )
            logger.info(
                f"🔄 [RabbitMQ] Reconnect attempt {self._reconnect_attempts}/{MAX_RECONNECT_ATTEMPTS} "
                f"in {delay:.1f}s..."
            )
            await asyncio.sleep(delay)

            try:
                await self.connect()
                if self._is_connected and not self._use_in_memory_fallback:
                    logger.info("✅ [RabbitMQ] Reconnected successfully!")
                    return
            except Exception as e:
                logger.warning(f"[RabbitMQ] Reconnect attempt {self._reconnect_attempts} failed: {e}")

        logger.error(
            f"❌ [RabbitMQ] Failed to reconnect after {MAX_RECONNECT_ATTEMPTS} attempts. "
            f"Remaining in IN-MEMORY mode until manual restart."
        )

    async def close(self) -> None:
        """Đóng kết nối an toàn."""
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()

        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception as e:
                logger.warning(f"[RabbitMQ] Error closing connection: {e}")

        self._is_connected = False
        self._use_in_memory_fallback = True
        self._connection = None
        self._channel = None
        self._exchange = None
        logger.info("🔌 [RabbitMQ] EventBus connection closed.")

    async def publish(
        self,
        topic: str,
        payload: Dict[str, Any],
        source_agent: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> EventMessage:
        """
        Phát tin nhắn lên Topic Exchange:
        - Nếu có broker thật: Publish lên AMQP Exchange + dispatch local subscribers.
        - Nếu fallback: Chỉ dispatch local In-Memory subscribers.
        """
        event_msg = EventMessage(
            topic=topic,
            payload=payload,
            event_id=str(uuid.uuid4()),
            timestamp=datetime.now().isoformat(),
            source_agent=source_agent,
            correlation_id=correlation_id,
        )

        self._published_history.append(event_msg)

        # 1. Publish lên AMQP broker thật (nếu connected)
        amqp_published = False
        if not self._use_in_memory_fallback and self._exchange is not None:
            try:
                import aio_pika

                message_body = json.dumps({
                    "event_id": event_msg.event_id,
                    "topic": event_msg.topic,
                    "payload": event_msg.payload,
                    "source_agent": event_msg.source_agent,
                    "correlation_id": event_msg.correlation_id,
                    "timestamp": event_msg.timestamp,
                }, default=str).encode("utf-8")

                amqp_message = aio_pika.Message(
                    body=message_body,
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    message_id=event_msg.event_id,
                    correlation_id=correlation_id,
                    timestamp=datetime.now(),
                    headers={"source_agent": source_agent or "SYSTEM"},
                )

                await self._exchange.publish(
                    amqp_message,
                    routing_key=topic,
                )
                amqp_published = True
                logger.info(
                    f"📤 [AMQP PUBLISH] [{topic}] from {source_agent or 'SYSTEM'} "
                    f"(id={event_msg.event_id[:8]})"
                )

            except Exception as e:
                logger.error(
                    f"❌ [AMQP PUBLISH FAILED] [{topic}] error: {e}. "
                    f"Falling back to In-Memory dispatch."
                )
                # Don't lose the event — still dispatch locally
                amqp_published = False

        if not amqp_published:
            logger.info(
                f"📤 [IN-MEMORY PUBLISH] [{topic}] from {source_agent or 'SYSTEM'} "
                f"(id={event_msg.event_id[:8]})"
            )

        # 2. Dispatch tới local In-Memory subscribers (always — for same-process agents)
        matching_handlers = self._find_matching_handlers(topic)
        for sub in matching_handlers:
            queue_name = sub["queue_name"]
            handler = sub["handler"]
            try:
                if asyncio.iscoroutinefunction(handler):
                    # Await instead of fire-and-forget to prevent lost events
                    try:
                        await asyncio.wait_for(handler(event_msg), timeout=30.0)
                    except asyncio.TimeoutError:
                        logger.warning(
                            f"⏱️ [EVENT_BUS] Handler timeout on queue {queue_name} "
                            f"for topic {topic}"
                        )
                else:
                    handler(event_msg)
            except Exception as e:
                logger.error(
                    f"❌ [EVENT_BUS] Error handling event {topic} on queue {queue_name}: {e}"
                )
                self._dlq_messages.append({
                    "event_id": event_msg.event_id,
                    "topic": topic,
                    "error": str(e),
                    "failed_at": datetime.now().isoformat(),
                    "queue": queue_name,
                    "payload_summary": str(payload)[:500],
                })
                # Also publish to DLX on broker if available
                await self._publish_to_dlx(event_msg, queue_name, str(e))

        return event_msg

    async def _publish_to_dlx(
        self, event_msg: EventMessage, failed_queue: str, error: str
    ) -> None:
        """Publish failed message vào Dead-Letter Exchange trên broker."""
        if self._use_in_memory_fallback or self._dlx_exchange is None:
            return
        try:
            import aio_pika

            dlx_body = json.dumps({
                "original_event": {
                    "event_id": event_msg.event_id,
                    "topic": event_msg.topic,
                    "source_agent": event_msg.source_agent,
                    "timestamp": event_msg.timestamp,
                },
                "error": error,
                "failed_queue": failed_queue,
                "failed_at": datetime.now().isoformat(),
            }, default=str).encode("utf-8")

            await self._dlx_exchange.publish(
                aio_pika.Message(
                    body=dlx_body,
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key="",
            )
            logger.warning(
                f"☠️ [DLX] Event {event_msg.event_id[:8]} sent to Dead-Letter Queue "
                f"(queue={failed_queue}, error={error[:100]})"
            )
        except Exception as dlx_err:
            logger.error(f"[DLX] Failed to publish to DLX: {dlx_err}")

    async def subscribe(
        self,
        queue_name: str,
        routing_keys: List[str],
        handler: Callable[[EventMessage], Coroutine[Any, Any, None]],
    ) -> None:
        """
        Đăng ký lắng nghe sự kiện từ Topic Exchange:
        - queue_name: Tên hàng đợi của Agent (vd: "queue.equity_research")
        - routing_keys: Danh sách các topics cần nhận (vd: ["discovery.candidates"])

        Nếu có broker thật: Declare queue trên RabbitMQ + bind routing keys.
        Luôn đăng ký In-Memory subscriber cho same-process dispatch.
        """
        # 1. Declare queue trên AMQP broker nếu connected
        if not self._use_in_memory_fallback and self._channel is not None:
            try:
                import aio_pika

                # Declare queue với DLX
                queue = await self._channel.declare_queue(
                    queue_name,
                    durable=True,
                    arguments={
                        "x-dead-letter-exchange": DLX_EXCHANGE_NAME,
                    },
                )

                # Bind all routing keys
                for rk in routing_keys:
                    await queue.bind(self._exchange, routing_key=rk)

                # Start consuming from AMQP queue
                async def _amqp_consumer(message: aio_pika.IncomingMessage):
                    async with message.process():
                        try:
                            body = json.loads(message.body.decode("utf-8"))
                            event_msg = EventMessage(
                                topic=body.get("topic", ""),
                                payload=body.get("payload", {}),
                                event_id=body.get("event_id", str(uuid.uuid4())),
                                timestamp=body.get("timestamp", datetime.now().isoformat()),
                                source_agent=body.get("source_agent"),
                                correlation_id=body.get("correlation_id"),
                            )
                            await handler(event_msg)
                        except Exception as e:
                            logger.error(
                                f"❌ [AMQP CONSUMER] Error processing message "
                                f"on queue {queue_name}: {e}"
                            )
                            # Message will go to DLX via nack
                            raise

                await queue.consume(_amqp_consumer)
                logger.info(
                    f"📥 [AMQP SUBSCRIBE] Queue '{queue_name}' bound to "
                    f"exchange '{EXCHANGE_NAME}' with keys: {routing_keys}"
                )

            except Exception as e:
                logger.warning(
                    f"⚠️ [AMQP SUBSCRIBE] Failed to subscribe on broker ({e}). "
                    f"Using In-Memory only for queue '{queue_name}'."
                )

        # 2. Always register In-Memory subscriber for local dispatch
        if queue_name not in self._subscribers:
            self._subscribers[queue_name] = []

        self._subscribers[queue_name].append({
            "routing_keys": routing_keys,
            "handler": handler,
        })
        logger.info(
            f"📥 [IN-MEMORY SUBSCRIBE] Queue '{queue_name}' subscribed to topics: {routing_keys}"
        )

    def _find_matching_handlers(self, topic: str) -> List[Dict[str, Any]]:
        """Tìm tất cả handlers có pattern routing key khớp với topic (hỗ trợ wildcard # và *)."""
        matched = []
        for queue_name, subs in self._subscribers.items():
            for sub in subs:
                for rk in sub["routing_keys"]:
                    if self._match_routing_key(rk, topic):
                        matched.append({
                            "queue_name": queue_name,
                            "handler": sub["handler"],
                        })
                        break
        return matched

    @staticmethod
    def _match_routing_key(pattern: str, topic: str) -> bool:
        """Khớp AMQP Topic Pattern (# khớp nhiều từ, * khớp 1 từ)."""
        if pattern == "#" or pattern == topic:
            return True
        regex_pattern = pattern.replace(".", "\\.").replace("*", "[^.]+").replace("#", ".*")
        return bool(re.fullmatch(regex_pattern, topic))

    def get_published_events(self, topic_filter: Optional[str] = None) -> List[EventMessage]:
        """Lấy lịch sử sự kiện đã phát (phục vụ audit & testing)."""
        if not topic_filter:
            return self._published_history
        return [e for e in self._published_history if self._match_routing_key(topic_filter, e.topic)]

    def get_dlq_messages(self) -> List[Dict[str, Any]]:
        """Lấy danh sách tin nhắn trong Dead-Letter Queue."""
        return self._dlq_messages

    @property
    def is_live_broker(self) -> bool:
        """True nếu đang kết nối trực tiếp đến RabbitMQ broker thật."""
        return self._is_connected and not self._use_in_memory_fallback

    @property
    def mode(self) -> str:
        """Trả về mode hiện tại: 'AMQP_LIVE' hoặc 'IN_MEMORY'."""
        return "AMQP_LIVE" if self.is_live_broker else "IN_MEMORY"

    def clear(self) -> None:
        """Xóa sạch bộ đệm sự kiện."""
        self._subscribers.clear()
        self._published_history.clear()
        self._dlq_messages.clear()


# Global Singleton Instance
event_bus = RabbitMQEventBus()
