"""Day-0 Bootstrap Universe Seeding Script (IOS v5.1).

Mục tiêu:
1. Quét danh sách các mã cổ phiếu trong Universe tại môi trường Local.
2. Tuyển chọn Bộ 3 tài liệu vàng bằng ActiveDocumentSelector:
   - 1 BCTC Kiểm toán năm (ANNUAL_BACKBONE)
   - 1 BCTC Quý gần nhất (LATEST_QUARTER)
   - 1 Báo cáo Quản trị (GOVERNANCE_REPORT)
3. Nạp vào SAG Engine qua endpoint by-ticker (tự động tạo Source BCTC_{TICKER}).
4. Chạy Thuật toán đồ thị thuần túy GIL (Zero Token) trên SAG để tính:
   - Chu trình rút ruột vốn khép kín (NetworkX simple_cycles)
   - Tỷ lệ phơi nhiễm giao dịch bên liên quan (RPT Ratio)
5. Ghi nhận sẵn cờ gil_flag (PASS / WARNING / CATASTROPHIC) vào bảng universe_securities.
6. Sẵn sàng 100% dữ liệu để migrate lên Production (migrate_local_to_pg.py với chi phí 0đ)!

Cách sử dụng:
    # 1. Chạy thử nghiệm cho các mã tiêu biểu:
    python scripts/bootstrap_universe_local.py --tickers HPG,FPT,VNM,MWG

    # 2. Chạy quét 20 mã đầu tiên của Universe:
    python scripts/bootstrap_universe_local.py --limit 20

    # 3. Dry-run kiểm tra tài liệu vàng mà không nạp SAG:
    python scripts/bootstrap_universe_local.py --tickers HPG --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from typing import Any, Dict, List

# Đảm bảo UTF-8 trên Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Thêm đường dẫn gốc ai-engine vào sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.domain.services.document_selector import ActiveDocumentSelector, TickerDocumentSet
from app.domain.services.bctc_to_sag_pipeline import BctcToSagPipeline
from app.adapters.sag_connector import sag_connector
from app.domain.repositories.universe_repository import UniverseRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("bootstrap_universe")


async def run_bootstrap(
    tickers: List[str],
    dry_run: bool = False,
    equity_vnd: float = 0.0,
) -> None:
    selector = ActiveDocumentSelector()
    pipeline = BctcToSagPipeline(selector=selector, connector=sag_connector)

    print("\n" + "=" * 75)
    print("🚀 BẮT ĐẦU CHƯƠNG TRÌNH DAY-0 BOOTSTRAP UNIVERSE SEEDING (LOCAL)")
    print(f"• Tổng số mã cần xử lý: {len(tickers)}")
    print(f"• Chế độ Dry-Run: {dry_run}")
    print("=" * 75 + "\n")

    summary_stats = {
        "processed": 0,
        "complete_golden_set": 0,
        "partial_golden_set": 0,
        "pass_count": 0,
        "warning_count": 0,
        "catastrophic_count": 0,
    }

    start_all = time.time()

    for idx, sym in enumerate(tickers, start=1):
        ticker = sym.upper().strip()
        print(f"[{idx}/{len(tickers)}] Đang xử lý mã: {ticker}...")

        # 1. Kiểm tra tài liệu vàng
        doc_set: TickerDocumentSet = selector.select_active_documents(ticker)
        docs = doc_set.all_documents
        is_complete = doc_set.is_complete

        ann_title = doc_set.annual_audited.title if doc_set.annual_audited else "KHÔNG TÌM THẤY"
        q_title = doc_set.latest_quarter.title if doc_set.latest_quarter else "KHÔNG TÌM THẤY"
        gov_title = doc_set.governance_report.title if doc_set.governance_report else "KHÔNG TÌM THẤY"

        print(f"  • BCTC Kiểm toán năm:  {ann_title}")
        print(f"  • BCTC Quý gần nhất:    {q_title}")
        print(f"  • Báo cáo Quản trị:     {gov_title}")

        if is_complete:
            summary_stats["complete_golden_set"] += 1
        else:
            summary_stats["partial_golden_set"] += 1

        if dry_run:
            print("  [Dry-Run] Bỏ qua bước nạp SAG và đánh giá đồ thị.\n")
            summary_stats["processed"] += 1
            continue

        # 2. Chạy Pipeline nạp SAG và đánh giá đồ thị GIL
        try:
            res = await pipeline.process_ticker(ticker=ticker, equity_vnd=equity_vnd)
            gil_flag = res.get("gil_flag", "PASS")
            gil_info = res.get("gil_result", {})

            if gil_flag == "PASS":
                summary_stats["pass_count"] += 1
                flag_str = "🟢 PASS (An toàn)"
            elif gil_flag == "WARNING":
                summary_stats["warning_count"] += 1
                flag_str = "🟡 WARNING (Cảnh báo)"
            else:
                summary_stats["catastrophic_count"] += 1
                flag_str = "🔴 CATASTROPHIC (Nguy hiểm)"

            cycles = gil_info.get("cycles_detected", 0)
            rpt_ratio = gil_info.get("rpt_ratio", 0.0)

            print(f"  • Kết quả GIL: {flag_str}")
            print(f"    - Chu trình rút ruột: {cycles}")
            print(f"    - Tỷ lệ RPT / Vốn: {rpt_ratio:.1%}")
            print(f"    - Đã lưu vào universe_securities: {res.get('db_updated')}")
        except Exception as err:
            logger.error(f"  ❌ Lỗi xử lý mã {ticker}: {err}")

        summary_stats["processed"] += 1
        print()

    total_time = time.time() - start_all

    print("\n" + "=" * 75)
    print("📊 BÁO CÁO TỔNG KẾT DAY-0 BOOTSTRAP UNIVERSE")
    print(f"• Tổng thời gian: {total_time:.2f}s (Trung bình: {total_time/len(tickers):.2f}s/mã)")
    print(f"• Đã xử lý: {summary_stats['processed']} / {len(tickers)} mã")
    print(f"• Đủ Bộ 3 tài liệu vàng: {summary_stats['complete_golden_set']} mã")
    print(f"• Chưa đủ Bộ 3 tài liệu: {summary_stats['partial_golden_set']} mã")
    if not dry_run:
        print(f"• Phân loại cờ GIL:")
        print(f"    - 🟢 PASS:         {summary_stats['pass_count']} mã")
        print(f"    - 🟡 WARNING:      {summary_stats['warning_count']} mã")
        print(f"    - 🔴 CATASTROPHIC: {summary_stats['catastrophic_count']} mã")
    print("=" * 75 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Day-0 Bootstrap Universe Seeding Script (Local)")
    parser.add_argument("--tickers", type=str, help="Danh sách mã cổ phiếu (phân tách bởi dấu phẩy, ví dụ: HPG,FPT,VNM)")
    parser.add_argument("--limit", type=int, default=10, help="Số lượng mã tối đa cần quét nếu không truyền tickers (mặc định 10, 0 là quét tất cả)")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ kiểm tra tài liệu vàng mà không nạp SAG")
    parser.add_argument("--equity", type=float, default=0.0, help="VCSH mặc định (VND) nếu cần tính RPT Ratio")

    args = parser.parse_args()

    target_tickers = []
    if args.tickers:
        target_tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        u_repo = UniverseRepository()
        stocks = u_repo.get_all_stocks(exchange="HOSE")
        all_syms = [s["symbol"] for s in stocks if s.get("symbol")]
        if args.limit > 0:
            target_tickers = all_syms[:args.limit]
        else:
            target_tickers = all_syms

    if not target_tickers:
        # Fallback mẫu nếu DB chưa có danh sách stocks
        target_tickers = ["HPG", "FPT", "VNM", "MWG", "TCB", "MBB", "VHM", "VIC", "SSI", "STB"]

    asyncio.run(run_bootstrap(
        tickers=target_tickers,
        dry_run=args.dry_run,
        equity_vnd=args.equity,
    ))


if __name__ == "__main__":
    main()
