"""Lớp MCP của sag — nguồn chính là endpoint MCP, agent đóng vai MCP client gắn vào.

- `server`: đóng gói khả năng truy vấn/thực thể/văn bản gốc của một nguồn thành MCP server
  (cho Claude Desktop / Cursor bên ngoài gắn vào, cũng cho agent trong tiến trình dùng lại warm engine).
- `mount`: gắn endpoint Streamable-HTTP vào FastAPI (`/mcp?source_id=…`).
- Adapter phía client ở `sag_api.tools.mcp`: chuyển các tool MCP từ xa thành interface `Tool` thống nhất.
"""

from sag_api.mcp.server import MCPScope, build_source_mcp, use_scope

__all__ = ["MCPScope", "build_source_mcp", "use_scope"]
