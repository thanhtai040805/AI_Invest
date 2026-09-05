"""CLI Script: Run Production End-of-Day Causal Learning Pipeline (IOS v5.1).

Cách sử dụng:
    python scripts/run_eod_pipeline.py
    python scripts/run_eod_pipeline.py --date 2026-08-24
    python scripts/run_eod_pipeline.py --date 2026-08-24 --force
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import date

# Đảm bảo đường dẫn import cho ai-engine
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from app.domain.pipeline.eod_pipeline import eod_runner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_eod_pipeline")


def print_eod_report(result: dict):
    """In bản báo cáo tổng kết EOD chuẩn Quant Fund."""
    print("\n" + "=" * 70)
    print("      AIINVEST QUANTITATIVE SYSTEM — EOD PIPELINE REPORT")
    print("=" * 70)
    print(f"  Trạng thái thực thi   : {result.get('status')}")
    print(f"  Ngày giao dịch        : {result.get('run_date')}")
    print(f"  Thời gian hoàn tất    : {result.get('executed_at')}")
    print(f"  Thời lượng xử lý      : {result.get('duration_seconds')} giây")
    print(f"  Market Regime         : {result.get('regime')} ({result.get('session_context')})")
    print(f"  Lệnh Paper đóng       : {result.get('paper_trades_settled')} vị thế")
    print(f"  CDC Triggered         : {result.get('cdc_status')}")
    print(f"  Governance Status     : {result.get('governance_status')}")
    print(f"  Audit Hash (SHA-256)  : {result.get('audit_sha256')}")
    print("-" * 70)

    weights = result.get("policy_weights", {})
    if weights:
        print("  BỘ TRỌNG SỐ THÍCH ỨNG CẬP NHẬT (AGENT-10 ADAPTIVE FACTOR WEIGHTS):")
        for k, v in weights.items():
            print(f"    - {k:<16} : {v * 100:.2f}%")
        print("-" * 70)

    kelly = result.get("kelly_matrix", {})
    if kelly:
        print("  MA TRẬN XÁC SUẤT KELLY HIỆU CHUẨN (EMPIRICAL BAYES MATRIX):")
        for tier in ["A+", "A", "B"]:
            if tier in kelly:
                t_data = kelly[tier]
                p = t_data.get("win_rate_p", 0.0) * 100
                b = t_data.get("payoff_ratio_b", 0.0)
                n = t_data.get("sample_size", 0)
                method = t_data.get("calibration_method", "N/A")
                print(f"    - Tier {tier:<3} : Win Rate = {p:5.1f}% | Payoff = {b:4.2f}x | Mẫu N = {n:<3} | ({method})")
        print("-" * 70)

    print("=" * 70 + "\n")


async def main():
    parser = argparse.ArgumentParser(description="Run Production EOD Pipeline (IOS v5.1)")
    parser.add_argument("--date", type=str, default=None, help="Ngày giao dịch YYYY-MM-DD (mặc định hôm nay)")
    parser.add_argument("--force", action="store_true", help="Bắt buộc chạy bất kể trạng thái")
    args = parser.parse_args()

    target_d = args.date or date.today().isoformat()
    logger.info(f"Khởi chạy EOD Pipeline cho ngày {target_d} (force={args.force})...")

    result = await eod_runner.run(target_date=target_d, force=args.force)
    print_eod_report(result)

    if result.get("status") == "SUCCESS":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
