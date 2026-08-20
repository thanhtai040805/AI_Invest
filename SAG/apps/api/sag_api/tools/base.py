"""Trừu tượng hóa công cụ —— đơn vị năng lực có thể gắn vào Agent.

Thiết kế đồng bộ với `connectors/`：một「công cụ」tự mô tả（name + tham số JSON-Schema），
đăng ký qua registry；vòng lặp Agent đưa schema công cụ cho LLM（native function-calling），
rồi dispatch tool_call của LLM tới công cụ tương ứng. Truy vấn chỉ là một trong các công cụ tích hợp，
công cụ MCP bên ngoài sau khi thích ứng giao diện tương tự thì hoàn toàn giống công cụ tích hợp với Agent.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sag_api.db.models import Agent, Source
    from sag_api.sag import EngineManager


@dataclass
class ToolMeta:
    """Tự mô tả công cụ. `parameters` là JSON-Schema（object），cho mô hình function-calling."""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolContext:
    """Ngữ cảnh runtime cần cho việc thực thi công cụ（do vòng lặp Agent tiêm vào，tương tự tiêm singleton của job handler）."""

    engine_manager: EngineManager
    sources: list[Source] = field(default_factory=list)
    persona: dict[str, Any] = field(default_factory=dict)
    agent: Agent | None = None
    # Độ lệch số hiệu bằng chứng toàn cục：vòng lặp đặt trước mỗi lần dispatch，đảm bảo [n] tăng dần không trùng qua các lượt
    citation_offset: int = 0


@dataclass
class ToolResult:
    """Kết quả thực thi công cụ. `content` trả về cho mô hình；`citations` cho UI truy vết；`data` kèm cấu trúc."""

    content: str
    citations: list[dict] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


class Tool(ABC):
    """Lớp cơ sở của mọi công cụ. Thêm công cụ mới = kế thừa + triển khai invoke + đăng ký trong registry."""

    meta: ToolMeta

    @abstractmethod
    async def invoke(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Thực thi công cụ. Tham số đã do mô hình đưa ra（hoặc tự gieo），trả về kết quả có thể điền ngược."""
        raise NotImplementedError