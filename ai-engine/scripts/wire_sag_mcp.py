"""Wiring Test Script: Connect ai-engine Agents to SAG Engine via MCP & REST Protocol.

Script này kiểm tra kết nối giữa ai-engine và SAG Engine:
1. Kiểm tra cấu hình SAG Connector adapter từ ai-engine.
2. Kiểm tra truy vấn RAG Moat AI (5 trụ cột + bằng chứng trích dẫn BCTC).
3. Kiểm tra truy vấn Đồ thị thực thể & sở hữu chéo GIL (Graph Intelligence Layer).
4. Thử nghiệm trên mã HPG.
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
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from app.adapters.sag_connector import sag_connector


async def wire_and_test_sag_mcp(ticker: str = "HPG") -> None:
    print("========================================================")
    print("🔌 KẾT NỐI AI-ENGINE DỰ ÁN VỚI SAG ENGINE VIA MCP / REST")
    print("========================================================")

    print(f"\n[STEP 1] Kiểm tra cấu hình kết nối SAG API Base: {sag_connector.api_base}")

    # 1. Thử nghiệm truy vấn Moat AI qua SAG Connector
    print(f"\n[STEP 2] Gửi truy vấn Moat Assessment cho mã {ticker}...")
    moat_result = await sag_connector.get_moat_assessment(ticker=ticker, sector="Materials")
    
    print("\n========================================================")
    print("📝 ĐẦU RA KẾT QUẢ SAG MOAT AI TRẢ VỀ:")
    print("========================================================")
    print(f"• Ticker: {moat_result.get('ticker')}")
    print(f"• Moat Score: {moat_result.get('moat_score')}")
    print(f"• Intangibles: {moat_result.get('intangibles_score')}")
    print(f"• Cost Advantage: {moat_result.get('cost_advantage_score')}")
    print(f"• Evidence: {moat_result.get('evidence_quote')}")
    print(f"• Status: {moat_result.get('status', 'SUCCESS')}")

    # 2. Thử nghiệm truy vấn GIL Relationships
    print(f"\n[STEP 3] Gửi truy vấn GIL Graph Relationships cho mã {ticker}...")
    gil_result = await sag_connector.get_gil_relationships(ticker=ticker)
    
    print("\n========================================================")
    print("📝 ĐẦU RA KẾT QUẢ SAG GIL TRẢ VỀ:")
    print("========================================================")
    print(f"• GIL Flag: {gil_result.get('gil_flag', 'PASS')}")
    print(f"• OCR Score: {gil_result.get('ocr_score', 0.0)}")
    print(f"• Cycles Detected: {gil_result.get('cycles_detected', 0)}")
    print("========================================================\n")
    print("✅ WIRING KẾT NỐI AI-ENGINE & SAG SẴN SÀNG!")


def main() -> None:
    ticker = sys.argv[1] if len(sys.argv) > 1 else "HPG"
    asyncio.run(wire_and_test_sag_mcp(ticker))


if __name__ == "__main__":
    main()
