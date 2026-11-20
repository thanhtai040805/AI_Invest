"""sag 的 MCP 层 —— 信源即 MCP 端点，agent 作为 MCP 客户端挂载。

- `server`：把一个信源的检索/实体/原文能力包成 MCP server（供外部 Claude Desktop /
  Cursor 挂载，也供进程内 agent 复用暖引擎）。
- `mount`：把 Streamable-HTTP 端点挂进 FastAPI（`/mcp?source_id=…`）。
- 客户端适配在 `sag_api.tools.mcp`：把远端 MCP 工具适配成统一的 `Tool` 接口。
"""

from sag_api.mcp.server import MCPScope, build_source_mcp, use_scope

__all__ = ["MCPScope", "build_source_mcp", "use_scope"]
