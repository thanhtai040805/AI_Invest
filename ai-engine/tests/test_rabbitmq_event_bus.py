"""Test Suite for RabbitMQ Event Bus (AMQP & Async Pub/Sub Architecture)."""

import asyncio
import pytest
from app.adapters.rabbitmq_event_bus import event_bus, RabbitMQEventBus
from app.core.event_topics import EventTopics
from app.core.registry import AgentRegistry
import app.domain.agents  # Nạp toàn bộ 12 agents


@pytest.fixture(autouse=True)
def clean_event_bus():
    """Làm sạch bộ đệm Event Bus trước mỗi test."""
    event_bus.clear()
    yield
    event_bus.clear()


def test_rabbitmq_event_bus_publish_and_subscribe():
    """Kiểm tra việc phát sự kiện và nhận qua hàng đợi RabbitMQ."""
    async def _test():
        received_messages = []

        async def sample_subscriber(event_msg):
            received_messages.append(event_msg)

        # Đăng ký hàng đợi
        await event_bus.subscribe(
            queue_name="queue.test_subscriber",
            routing_keys=[EventTopics.MARKET_PULSE, EventTopics.POLICY_WEIGHTS],
            handler=sample_subscriber,
        )

        # Bắn tin nhắn
        msg1 = await event_bus.publish(
            topic=EventTopics.MARKET_PULSE,
            payload={"regime": "BULL_MARKET", "vix_analog": 18.5},
            source_agent="market_surveillance",
        )

        msg2 = await event_bus.publish(
            topic=EventTopics.POLICY_WEIGHTS,
            payload={"f1_value": 0.20, "f2_quality": 0.30},
            source_agent="reinforcement_learning",
        )

        # Đợi async queue xử lý
        await asyncio.sleep(0.05)

        assert len(received_messages) == 2
        assert received_messages[0].topic == EventTopics.MARKET_PULSE
        assert received_messages[0].payload["regime"] == "BULL_MARKET"
        assert received_messages[1].topic == EventTopics.POLICY_WEIGHTS
        assert received_messages[1].source_agent == "reinforcement_learning"

    asyncio.run(_test())


def test_rabbitmq_topic_wildcard_routing():
    """Kiểm tra tính năng khớp mẫu Wildcard (# và *) chuẩn AMQP."""
    async def _test():
        market_events = []
        all_events = []

        async def market_handler(msg):
            market_events.append(msg)

        async def wildcard_handler(msg):
            all_events.append(msg)

        # Sub 1 lắng nghe mọi event bắt đầu bằng 'market.*'
        await event_bus.subscribe("q.market_watch", ["market.*"], market_handler)
        # Sub 2 lắng nghe toàn bộ event qua wildcard '#'
        await event_bus.subscribe("q.all_audit", ["#"], wildcard_handler)

        await event_bus.publish("market.pulse", {"data": 1})
        await event_bus.publish("market.anomaly", {"data": 2})
        await event_bus.publish("order.instruction", {"data": 3})

        await asyncio.sleep(0.05)

        assert len(market_events) == 2
        assert len(all_events) == 3

    asyncio.run(_test())


def test_base_agent_native_rabbitmq_integration():
    """Kiểm tra 12 BaseAgent sử dụng RabbitMQ bus trực tiếp."""
    async def _test():
        research_agent = AgentRegistry.get_agent("equity_research")
        assert research_agent is not None

        # Agent phát sự kiện
        published = await research_agent.publish_event(
            topic=EventTopics.RESEARCH_REPORT,
            payload={"ticker": "FPT", "css": 85.0, "conviction": "A+"},
        )

        assert published.topic == EventTopics.RESEARCH_REPORT
        assert published.source_agent == "equity_research"
        assert published.payload["css"] == 85.0

    asyncio.run(_test())
