"""Cleanup / Reset script for Historical Paper Trading.

Khôi phục tài khoản giao dịch về trạng thái ban đầu sạch sẽ để sẵn sàng lên PROD.
Xóa toàn bộ:
- positions (vị thế đang nắm giữ)
- orders & order_executions (sổ lệnh và lịch sử khớp lệnh)
- Reset cash_balance và total_nav về 1,000,000,000 VND (hoặc giá trị tùy chọn)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Add ai-engine to sys.path
ai_engine_dir = Path(__file__).resolve().parents[1]
if str(ai_engine_dir) not in sys.path:
    sys.path.insert(0, str(ai_engine_dir))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from app.domain.repositories.portfolio_repository import PortfolioRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dọn dẹp CSDL và khôi phục tài khoản Paper Trading về trạng thái ban đầu sạch sẽ (CHỈ CHẠY KHI ĐƯỢC YÊU CẦU)"
    )
    parser.add_argument(
        "--account-id",
        default=os.getenv("MULTI_AGENT_ACCOUNT_ID", "940b0c70-2010-42f3-b947-797e6419b794"),
        help="Account ID cần reset (mặc định lấy MULTI_AGENT_ACCOUNT_ID)",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=1_000_000_000.0,
        help="Số vốn ban đầu muốn khôi phục (mặc định: 1,000,000,000 VND)",
    )
    parser.add_argument(
        "--skip-agents",
        action="store_true",
        help="Bỏ qua việc dọn dẹp kết quả trung gian và 12 bảng Logs của Agents",
    )
    parser.add_argument(
        "--skip-artifacts",
        action="store_true",
        help="Bỏ qua việc xóa file ledger sqlite và file summary md ở ai-engine/.data/",
    )
    args = parser.parse_args()

    clean_agents = not args.skip_agents
    clean_artifacts = not args.skip_artifacts

    repo = PortfolioRepository()
    print(f"\n================================================================================")
    print(f"BẮT ĐẦU DỌN DẸP TOÀN DIỆN CSDL, KẾT QUẢ AGENTS & LOGS TRƯỚC KHI LÊN PROD")
    print(f"Target Account ID : {args.account_id}")
    print(f"Số vốn ban đầu   : {args.capital:,.0f} VND")
    print(f"Dọn dẹp Agents/Log: {'CÓ (Xóa sạch kết quả quyết định & 12 bảng log)' if clean_agents else 'KHÔNG'}")
    print(f"Dọn dẹp Artifacts : {'CÓ (Xóa sqlite & summary markdown)' if clean_artifacts else 'KHÔNG'}")
    print(f"================================================================================\n")

    res = repo.reset_paper_trading_account(
        user_id=args.account_id,
        initial_capital=args.capital,
        clean_agents_and_logs=clean_agents,
    )

    print(f"[THÀNH CÔNG] Chi tiết dọn dẹp:")
    print(f"  [1] Tài khoản & Sổ lệnh:")
    print(f"      - Bảng positions        : Đã xóa toàn bộ vị thế của {args.account_id}")
    print(f"      - Bảng orders           : Đã xóa toàn bộ lệnh của {args.account_id}")
    print(f"      - Bảng order_executions : Đã xóa toàn bộ lịch sử khớp của {args.account_id}")
    print(f"      - Bảng users            : cash_balance = {res['cash_balance']:,.0f} VND")
    print(f"      - Bảng portfolio_account: cash_balance = total_nav = peak_nav = {res['cash_balance']:,.0f} VND")

    if clean_agents:
        print(f"  [2] Kết quả quyết định của các Agents:")
        print(f"      - portfolio_decisions, investment_theses, counter_thesis_verdicts")
        print(f"      - cio_resolutions, cio_strategic_directives, strategic_allocations")
        print(f"      - stop_loss_events, position_health_ticks, risk_snapshots")
        print(f"      - slippage_records, audit_reports, violation_reports")
        print(f"  [3] 12 Bảng Log Tư duy (Audit Logs):")
        print(f"      - log_market_surveillance, log_universe_discovery, log_equity_research")
        print(f"      - log_investment_thesis, log_counter_thesis, log_strategy_cio")
        print(f"      - log_portfolio_allocation, log_portfolio_risk, log_trade_execution")
        print(f"      - log_position_monitoring, log_reinforcement_learning, log_system_governance")

    if clean_artifacts:
        data_dir = ai_engine_dir / ".data"
        removed_files = []
        if data_dir.exists():
            for f_path in list(data_dir.glob("*.sqlite*")) + list(data_dir.glob("agent_replay_*.md")):
                try:
                    f_path.unlink()
                    removed_files.append(f_path.name)
                except Exception as e_del:
                    logger.warning(f"Không thể xóa file {f_path.name}: {e_del}")
        print(f"  [4] Artifacts / Replay Tạm:")
        if removed_files:
            print(f"      - Đã xóa files tại .data/: {', '.join(removed_files)}")
        else:
            print(f"      - Thư mục .data/ sạch sẽ, không có file tạm cần xóa.")

    print(f"\n-> CSDL, Kết quả các Agents, Logs và Tài khoản đã hoàn toàn sạch sẽ, sẵn sàng cho PROD!\n")


if __name__ == "__main__":
    main()
