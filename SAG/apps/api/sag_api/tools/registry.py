"""Registry công cụ —— tra cứu công cụ theo name；thêm công cụ mới đăng ký tại đây（phản chiếu connectors/registry）."""

from __future__ import annotations

from sag_api.core.errors import NotFoundError
from sag_api.tools.base import Tool
from sag_api.tools.builtin import (
    GetEntityTool,
    GetTimeTool,
    OpenWebPageTool,
    SearchContextTool,
    WebSearchTool,
)


class ToolRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._by_name[tool.meta.name] = tool

    def get(self, name: str) -> Tool:
        tool = self._by_name.get(name)
        if tool is None:
            raise NotFoundError(f"Không biết công cụ：{name}")
        return tool

    def has(self, name: str) -> bool:
        return name in self._by_name

    def all(self) -> list[Tool]:
        return list(self._by_name.values())

    def schemas(self, names: list[str]) -> list[dict]:
        """Cho danh sách tên công cụ → danh sách OpenAI function schema（để truyền tools=）. """
        return [self._by_name[n].meta.to_openai_schema() for n in names if n in self._by_name]

    def overlay(self, tools: list[Tool]) -> ToolRegistry:
        """Tạo lớp chồng：công cụ tích hợp + công cụ MCP của yêu cầu này（không sửa singleton toàn cục）."""
        child = ToolRegistry()
        child._by_name = {**self._by_name, **{t.meta.name: t for t in tools}}
        return child


registry = ToolRegistry()
registry.register(SearchContextTool())
registry.register(GetEntityTool())
registry.register(GetTimeTool())
registry.register(WebSearchTool())
registry.register(OpenWebPageTool())
# Công cụ MCP từ xa được tiêm động theo ràng buộc của Agent khi chạy.
