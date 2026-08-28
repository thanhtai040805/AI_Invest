"""Agent Registry (Plug-and-Play Hub)

Cho phép:
1. Đăng ký/Hủy đăng ký Agent động tại runtime.
2. Lấy danh sách Agents đang hoạt động.
3. Kích hoạt Agent theo tên chức năng nghiệp vụ (Semantic Name).
4. Cung cấp danh mục Tools tự động cho Chatbot Supervisor.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from app.core.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class AgentRegistry:
    _instance: Optional[AgentRegistry] = None
    _agents: Dict[str, BaseAgent] = {}

    def __new__(cls) -> AgentRegistry:
        if cls._instance is None:
            cls._instance = super(AgentRegistry, cls).__new__(cls)
            cls._agents = {}
        return cls._instance

    @classmethod
    def register(cls, agent: BaseAgent) -> None:
        """Đăng ký một Agent mới vào hệ thống."""
        cls._agents[agent.agent_name] = agent
        logger.info(f" Đã đăng ký Agent [{agent.agent_name}] vào Registry.")

    @classmethod
    def unregister(cls, agent_name: str) -> bool:
        """Hủy đăng ký một Agent."""
        if agent_name in cls._agents:
            del cls._agents[agent_name]
            logger.info(f"🗑️ Đã gỡ bỏ Agent [{agent_name}] khỏi Registry.")
            return True
        return False

    @classmethod
    def get_agent(cls, agent_name: str) -> Optional[BaseAgent]:
        """Lấy Agent theo tên định danh."""
        return cls._agents.get(agent_name)

    @classmethod
    def list_agents(cls) -> List[Dict[str, Any]]:
        """Liệt kê toàn bộ các Agent đang có trong hệ thống."""
        return [agent.as_tool() for agent in cls._agents.values()]

    @classmethod
    async def dispatch(cls, agent_name: str, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Điều phối và kích hoạt Agent xử lý sự kiện."""
        agent = cls.get_agent(agent_name)
        if not agent:
            raise ValueError(f"Agent '{agent_name}' không tồn tại trong Registry!")
        return await agent.run_event(event_data)


agent_registry = AgentRegistry()
