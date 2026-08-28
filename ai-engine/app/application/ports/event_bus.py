"""Event Bus Port Interface for Asynchronous Multi-Agent Communication.
Supports publish/subscribe over Topic Exchanges, Dead-Letter Queues, and Message Acknowledgement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Coroutine, Dict, List, Optional
import uuid


@dataclass
class EventMessage:
    """Standardized Event Message Envelope across all Agents."""
    topic: str
    payload: Dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    source_agent: Optional[str] = None
    correlation_id: Optional[str] = None
    reply_to: Optional[str] = None


class EventBusPort(ABC):
    """Abstract Port for Event Bus infrastructure."""

    @abstractmethod
    async def connect(self) -> None:
        """Kết nối đến Message Broker (RabbitMQ)."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Đóng kết nối an toàn."""
        pass

    @abstractmethod
    async def publish(
        self,
        topic: str,
        payload: Dict[str, Any],
        source_agent: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> EventMessage:
        """Phát sự kiện lên Topic Exchange."""
        pass

    @abstractmethod
    async def subscribe(
        self,
        queue_name: str,
        routing_keys: List[str],
        handler: Callable[[EventMessage], Coroutine[Any, Any, None]],
    ) -> None:
        """Đăng ký lắng nghe sự kiện từ hàng đợi với danh sách routing keys."""
        pass
