"""工具注册表 —— 按 name 查找工具；新增工具在此登记（镜像 connectors/registry）。"""

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
            raise NotFoundError(f"未知工具：{name}")
        return tool

    def has(self, name: str) -> bool:
        return name in self._by_name

    def all(self) -> list[Tool]:
        return list(self._by_name.values())

    def schemas(self, names: list[str]) -> list[dict]:
        """给定工具名列表 → OpenAI function schema 列表（供 tools= 传参）。"""
        return [self._by_name[n].meta.to_openai_schema() for n in names if n in self._by_name]

    def overlay(self, tools: list[Tool]) -> ToolRegistry:
        """派生一个叠加层：内置工具 + 本请求的 MCP 工具（不改动全局单例）。"""
        child = ToolRegistry()
        child._by_name = {**self._by_name, **{t.meta.name: t for t in tools}}
        return child


registry = ToolRegistry()
registry.register(SearchContextTool())
registry.register(GetEntityTool())
registry.register(GetTimeTool())
registry.register(WebSearchTool())
registry.register(OpenWebPageTool())
# MCP 远端工具在运行时按 Agent 绑定动态注入。
