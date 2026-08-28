"""Wiring Test Script: Connect ai-engine Agents to SAG Engine via MCP Protocol.

Script này nối trực tiếp ai-engine với SAG FastMCP Server:
1. Đọc cấu hình mcp_servers.sag từ ai-engine.
2. Khởi tạo MCP Client kết nối tới SAG MCP Server (stdio / http).
3. Đăng ký các công cụ mcp_sag_search, mcp_sag_get_entity, mcp_sag_read vào ToolRegistry của ai-engine.
4. Chạy thử nghiệm truy vấn trực tiếp trên dữ liệu BCTC HPG Q1/2026 từ SAG!
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Đảm bảo import được các module của ai-engine
AI_ENGINE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(AI_ENGINE_DIR))

# Đảm bảo UTF-8 output trên Windows terminal
sys.stdout.reconfigure(encoding="utf-8")

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class MCPServerConfig:
    type: str = "stdio"
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)


@dataclass
class AgentConfig:
    mcp_servers: Dict[str, MCPServerConfig] = field(default_factory=dict)


async def wire_and_test_sag_mcp(source_config_id: str | None = None) -> None:
    print("========================================================")
    print("🔌 KẾT NỐI AI-ENGINE DỰ ÁN VỚI SAG ENGINE VIA MCP")
    print("========================================================")

    # 1. Đường dẫn tới môi trường Python của SAG API chứa module sag_api
    sag_python = Path(r"d:\AIInvest\SAG\apps\api\.venv\Scripts\python.exe")
    if not sag_python.exists():
        sag_python = Path(sys.executable)

    # 2. Tạo cấu hình MCPServerConfig nối tới SAG MCP Server
    sag_api_dir = str(Path(r"d:\AIInvest\SAG\apps\api").resolve())
    python_path = os.environ.get("PYTHONPATH", "")
    new_pythonpath = f"{sag_api_dir};{python_path}" if python_path else sag_api_dir

    mcp_config = MCPServerConfig(
        type="stdio",
        command=str(sag_python),
        args=["-m", "sag_api.mcp.server"],
        env={
            **os.environ,
            "PYTHONUTF8": "1",
            "PYTHONPATH": new_pythonpath,
            "SAG_MCP_SOURCE_ID": source_config_id or "",
        },
    )

    agent_config = AgentConfig(
        mcp_servers={"sag": mcp_config}
    )

    print("\n[STEP 1] Đang kết nối tới SAG FastMCP Server và đăng ký Tools...")
    
    def warn_cb(msg: str) -> None:
        print(f"  ⚠️ Warning: {msg}")

    # Build ToolRegistry của ai-engine kèm MCP tools từ SAG
    registry = build_registry(
        agent_config=agent_config,
        include_shell_tools=False,
        warn_callback=warn_cb,
    )

    sag_tools = [tool for name, tool in registry._tools.items() if name.startswith("mcp_sag_")]
    print(f"  ✓ Đã đăng ký thành công {len(sag_tools)} công cụ SAG MCP vào ai-engine!")
    for t in sag_tools:
        print(f"    • [{t.name}] {t.description[:80]}...")

    if not sag_tools:
        print("❌ Không tìm thấy công cụ SAG MCP nào! Kiểm tra lại kết quả kết nối.")
        return

    # 3. Thử nghiệm truy vấn bằng công cụ mcp_sag_search từ ai-engine
    print("\n[STEP 2] Chạy truy vấn thử nghiệm từ ai-engine qua công cụ mcp_sag_search...")
    search_tool = registry.get("mcp_sag_search")
    if search_tool:
        query = "Biên lợi nhuận gộp và Hàng tồn kho của HPG trong Quý 1/2026 là bao nhiêu?"
        print(f"  ❓ Question: {query}")
        try:
            result = search_tool.execute(query=query, top_k=4)
            print("\n========================================================")
            print("📝 ĐẦU RA KẾT QUẢ SAG MCP TRẢ VỀ CHO AGENT:")
            print("========================================================")
            print(result)
            print("========================================================\n")
            print("✅ NỐI WIRING KẾT NỐI AI-ENGINE & SAG THÀNH CÔNG 100%!")
        except Exception as err:
            print(f"❌ Lỗi khi thực thi công cụ mcp_sag_search: {err}")
    else:
        print("❌ Không tìm thấy công cụ mcp_sag_search trong registry!")


def main() -> None:
    source_id = sys.argv[1] if len(sys.argv) > 1 else ""
    asyncio.run(wire_and_test_sag_mcp(source_id))


if __name__ == "__main__":
    main()
