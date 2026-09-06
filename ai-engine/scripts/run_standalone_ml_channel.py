"""Standalone Pure-ML Fund Production CLI Runner (IOS v5.1).

Vận hành và giám sát kênh học máy tự hành độc lập:
- Chạy dự báo và phân bổ danh mục thuần ML (20% NAV / mã).
- Tự động ghi nhận lệnh vào Tài khoản riêng biệt (STANDALONE_ML_ACCOUNT_ID).
- Đo đạc xác suất tiền mở cửa và đối soát độ chính xác thực tế sau T+2.5 / T+3.

Ví dụ sử dụng:
    # 1. Chạy chu trình tự hành cho phiên giao dịch gần nhất
    python scripts/run_standalone_ml_channel.py --run-cycle --date 2026-09-04

    # 2. Đối soát độ chính xác thực tế trên thị trường (Realized Survival & Hit Rate)
    python scripts/run_standalone_ml_channel.py --evaluate --days 60

    # 3. Xem danh mục vị thế và số dư tài khoản độc lập
    python scripts/run_standalone_ml_channel.py --portfolio

    # 4. Chạy toàn bộ (Chu trình + Đối soát + Danh mục)
    python scripts/run_standalone_ml_channel.py --all --date 2026-09-04
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date
from typing import Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

from tabulate import tabulate

from app.domain.services.ml.standalone_ml_channel import (
    StandaloneExecutionMode,
    standalone_ml_channel,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("standalone_ml_runner")


async def run_cycle(target_date: str, mode: str, max_candidates: int, nav: Optional[float] = None):
    acc_st = standalone_ml_channel.get_account_state()
    active_nav = nav if nav is not None else float(acc_st.get("total_nav", 500_000_000.0))

    print("\n" + "=" * 80)
    print(f"  STANDALONE PURE-ML FUND — AUTONOMOUS RUNNER (IOS v5.1)")
    print(f"  Account ID    : {standalone_ml_channel.account_id}")
    print(f"  Target Date   : {target_date}")
    print(f"  Execution Mode: {mode}")
    print(f"  Portfolio NAV : {active_nav:,.0f} VND (từ CSDL PostgreSQL | Sizing: {standalone_ml_channel.position_weight:.0%} NAV/mã)")
    print("=" * 80)

    res = await standalone_ml_channel.run_autonomous_cycle(
        target_date=target_date,
        execution_mode=mode,
        max_candidates=max_candidates,
        nav=active_nav,
    )

    orders = res.get("orders", [])
    print(f"\n[PHÁT HIỆN & PHÂN BỔ] {len(orders)} Lệnh Đạt Chuẩn Thuần ML:")

    if not orders:
        print("  (Không có mã nào thỏa mãn điều kiện giải ngân hôm nay)")
    else:
        table_data = []
        for i, o in enumerate(orders, 1):
            table_data.append([
                i,
                o["ticker"],
                o["tier"],
                f"{o['surv_prob']:.1%}",
                f"{o['mom_pred']:+.2%}",
                f"{o['z_score']:+.2f}",
                f"{o['shares']:,}",
                f"{o['price']:,.0f} VND",
                f"{o['shares'] * o['price']:,.0f} VND",
                f"{o['target_weight_pct']:.0%}",
                o["action"],
            ])
        headers = [
            "#", "Ticker", "Tier", "P(Surv T+2.5)", "E[Mom 3D]",
            "Z-Score", "Shares", "Price", "Position Value", "Weight", "Action"
        ]
        print(tabulate(table_data, headers=headers, tablefmt="fancy_grid"))

    return res


def run_evaluation(days: int):
    print("\n" + "=" * 80)
    print(f"  ĐỐI SOÁT ĐỘ CHÍNH XÁC THỰC TẾ (REALIZED ACCURACY & SURVIVAL TRACKING)")
    print(f"  Account ID   : {standalone_ml_channel.account_id}")
    print(f"  Lookback Days: {days} ngày")
    print("=" * 80)

    metrics = standalone_ml_channel.evaluate_forward_accuracy(lookback_days=days)

    summary_table = [
        ["Tổng số dự báo đã đối soát (T+3)", f"{metrics.get('total_evaluated', 0):,} mã"],
        ["Dự báo vừa cập nhật mới", f"{metrics.get('newly_evaluated', 0):,} mã"],
        ["Tỷ lệ Sống Sót thực tế (No drawdown > -3.5%)", f"{metrics.get('realized_survival_rate_pct', 0.0):.2f}%"],
        ["Xác suất Sống Sót mô hình dự báo TB", f"{metrics.get('predicted_avg_survival_prob_pct', 0.0):.2f}%"],
        ["Directional Hit Rate (Đúng chiều tăng/giảm)", f"{metrics.get('directional_hit_rate_pct', 0.0):.2f}%"],
        ["Lợi nhuận T+3 Thực Tế Bình Quân", f"{metrics.get('avg_realized_3d_return_pct', 0.0):+.2f}%"],
        ["Lợi nhuận T+3 Kỳ Vọng Mô Hình TB", f"{metrics.get('avg_predicted_3d_return_pct', 0.0):+.2f}%"],
    ]
    print(tabulate(summary_table, headers=["Chỉ Số Đo Đạc Độ Chính Xác", "Giá Trị Thực Nghiệm"], tablefmt="fancy_grid"))

    if metrics.get("realized_survival_rate_pct", 0) >= 80.0:
        print("\n [ĐÁNH GIÁ CHUẨN ĐO ĐẠC] Mô hình đạt chuẩn an toàn cao. Sẵn sàng xem xét áp dụng.")
    elif metrics.get("realized_survival_rate_pct", 0) >= 65.0:
        print("\n [ĐÁNH GIÁ CHUẨN ĐO ĐẠC] Mô hình hoạt động ổn định trong biên độ cho phép.")
    else:
        print("\n⚠️ [ĐÁNH GIÁ CHUẨN ĐO ĐẠC] Cần tiếp tục chạy Shadow Runner để thu thập thêm mẫu dữ liệu.")

    return metrics


def show_portfolio():
    print("\n" + "=" * 80)
    print(f"  DANH MỤC & SỐ DƯ TÀI KHOẢN STANDALONE PURE-ML")
    print(f"  Account ID: {standalone_ml_channel.account_id}")
    print("=" * 80)

    state = standalone_ml_channel.get_account_state()
    positions = standalone_ml_channel.get_open_positions()

    cash = float(state.get("cash_balance", 0.0))
    nav = float(state.get("total_nav", 0.0))

    print(f"  Tiền mặt khả dụng: {cash:,.0f} VND")
    print(f"  Tổng giá trị NAV : {nav:,.0f} VND")
    print(f"  Số vị thế mở     : {len(positions)} mã")

    if positions:
        pos_table = []
        for p in positions:
            pos_table.append([
                p.get("symbol", p.get("ticker")),
                f"{int(p.get('quantity', p.get('shares', 0))):,}",
                f"{float(p.get('avg_price', p.get('average_price', 0))):,.0f} VND",
                f"{float(p.get('market_value', 0)):,.0f} VND",
                f"{float(p.get('weight_pct', 0)):.1f}%",
            ])
        print(tabulate(pos_table, headers=["Mã", "Số Lượng", "Giá Mua TB", "Giá Trị TT", "Tỷ Trọng"], tablefmt="fancy_grid"))
    else:
        print("  (Danh mục hiện tại đang giữ 100% tiền mặt)")


def main():
    parser = argparse.ArgumentParser(description="Standalone Pure-ML Fund Production Runner (IOS v5.1)")
    parser.add_argument("--date", type=str, default="2026-09-04", help="Ngày chạy phân tích (YYYY-MM-DD)")
    parser.add_argument("--mode", type=str, default="SHADOW_RUNNER", choices=["LIVE", "SHADOW_RUNNER", "DISABLED"])
    parser.add_argument("--nav", type=float, default=None, help="Tổng NAV của Quỹ Standalone (Mặc định tự động truy vấn từ CSDL PostgreSQL)")
    parser.add_argument("--max", type=int, default=5, help="Số vị thế tối đa (mỗi vị thế 20%% NAV)")
    parser.add_argument("--days", type=int, default=60, help="Số ngày lookback để đối soát độ chính xác")

    parser.add_argument("--run-cycle", action="store_true", help="Chạy chu trình tự hành tạo lệnh")
    parser.add_argument("--evaluate", action="store_true", help="Đối soát độ chính xác thực tế")
    parser.add_argument("--portfolio", action="store_true", help="Xem danh mục và số dư tài khoản độc lập")
    parser.add_argument("--all", action="store_true", help="Thực hiện toàn bộ các chức năng")

    args = parser.parse_args()

    # Mặc định nếu không truyền cờ nào thì chạy --all
    if not (args.run_cycle or args.evaluate or args.portfolio or args.all):
        args.all = True

    if args.all or args.run_cycle:
        asyncio.run(run_cycle(target_date=args.date, mode=args.mode, max_candidates=args.max, nav=args.nav))

    if args.all or args.evaluate:
        run_evaluation(days=args.days)

    if args.all or args.portfolio:
        show_portfolio()


if __name__ == "__main__":
    main()
