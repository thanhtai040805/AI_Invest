"""Standalone Agent 05 (Counter Thesis Agent - Devil's Advocate) Production Runner Script (IOS v5.1).

Kiểm thử vận hành độc lập Agent 05 trên dữ liệu thực tế và CSDL PostgreSQL:
1. Tự động tìm kiếm hoặc nạp Investment Thesis mới nhất từ CSDL (`investment_theses`).
2. Nạp dữ liệu thị trường thực tế (Giá realtime/EOD, Volume, MA20, P/E, P/B, HMM Regime).
3. Thẩm định đồ thị sở hữu chéo GIL từ SAG FastMCP (kèm cơ chế bảo vệ khi mạng lỗi).
4. Tính toán định lượng 3-Tier Counter-Thesis Score (CTS):
   - Base CTS: Rủi ro kinh doanh (45%) + Rủi ro thị trường (35%) + Rủi ro mô hình (20%)
   - ML Interaction Multiplier: Quét các cặp rủi ro cộng hưởng phi tuyến
   - Regime Multiplier: Hệ số xu hướng thị trường 6 trạng thái HMM
5. Thẩm định Ngoại lệ Bắt đáy Khoa học (Capitulation Rebound - Bẫy 3).
6. Thẩm định Hard Law Điều 3 (Rule of Three) và lỗ hổng luận điểm.
7. Ra phán quyết: PROCEED / CONDITIONAL (kèm execution_constraints) / BLOCK (kèm block_reasons).
8. Cập nhật đồng bộ trạng thái `investment_theses.status` và lưu trữ `counter_thesis_verdicts`.

Cách sử dụng:
    python scripts/run_agent05_standalone.py
    python scripts/run_agent05_standalone.py --ticker HPG
    python scripts/run_agent05_standalone.py --ticker SSI
    python scripts/run_agent05_standalone.py --ticker FPT
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
import sys

# Đảm bảo đường dẫn import cho ai-engine
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from app.core.registry import AgentRegistry
import app.domain.agents  # Tự động đăng ký 12 agents vào AgentRegistry
from app.domain.repositories.intelligence_repository import IntelligenceRepository
from app.domain.repositories.market_data_repository import MarketDataRepository
from app.domain.repositories.financial_repository import FinancialRepository
from app.infrastructure.database.pg_pool import get_conn


async def run_agent05_standalone(ticker: str):
    ticker = ticker.upper().strip()
    now = datetime.now(timezone.utc)

    print("=" * 80)
    print(f" [AGENT 05: COUNTER THESIS AGENT - DEVIL'S ADVOCATE] -- STANDALONE RUN")
    print(f" Ticker: {ticker} | Timestamp: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 80)

    intel_repo = IntelligenceRepository()
    m_repo = MarketDataRepository()
    f_repo = FinancialRepository()

    # 1. Tra cứu Luận điểm Đầu tư (Investment Thesis) từ CSDL
    print("\n1. TRA CỨU INVESTMENT THESIS TỪ CSDL:")
    thesis = intel_repo.get_latest_investment_thesis(ticker)
    if not thesis:
        print(f"   [!] Chưa có Thesis nào trong bảng `investment_theses` cho mã {ticker}.")
        print("   -> Tự động khởi tạo Thesis mẫu hợp lệ cho mục đích kiểm thử...")
        thesis_id = f"THESIS_HOSE_{ticker}_{now.strftime('%YQ3')}_001"
        thesis = {
            "thesis_id": thesis_id,
            "ticker": ticker,
            "catalyst_type": "Earnings Expansion",
            "catalyst_description": "Tăng trưởng lợi nhuận quý 3 vượt kỳ vọng kết hợp định giá phục hồi.",
            "timeline_months": 3,
            "target_price": 36000.0,
            "entry_price_estimated": 28000.0,
            "confirming_signals": {
                "signal_1_factor": "PASS (F4 SUE=85.0, CSS=80.0)",
                "signal_2_surveillance": "PASS (Moat=80.0, F5 Flow=70.0)",
                "signal_3_macro_hmm": "PASS (Regime=BULL_TRENDING)",
            },
            "invalidation_conditions": ["Lợi nhuận gộp sụt giảm > 15%", "Thị trường thủng MA200"],
            "pre_mortem_scenarios": ["Biên lợi nhuận thép giảm", "Xung đột thương mại", "Cung vượt cầu"],
            "status": "PENDING_COUNTER_ANALYSIS",
        }
        intel_repo.save_investment_thesis(thesis)
        print(f"   * Đã nạp và lưu Thesis mẫu: {thesis_id}")
    else:
        print(f"   * Tìm thấy Thesis: {thesis.get('thesis_id')} (Trạng thái hiện tại: {thesis.get('status')})")
        print(f"   * Ngòi nổ: {thesis.get('catalyst_type')} | Target: {thesis.get('target_price'):,.0f} VND")

    # 2. Thu thập Dữ liệu Thị trường & Tài chính Thực tế
    print("\n2. THU THẬP THÔNG SỐ ĐỊNH GIÁ & THỊ TRƯỜNG THỰC TẾ:")
    px = m_repo.get_realtime_or_latest_price(ticker, allow_eod_fallback=True) or 0.0
    print(f"   * Giá thị trường tham chiếu: {px:,.0f} VND")

    ratios = f_repo.get_latest_ratios(ticker) or {}
    pe = float(ratios.get("pe", 0.0))
    pb = float(ratios.get("pb", 0.0))
    print(f"   * Chỉ số định giá: P/E = {pe:.1f} | P/B = {pb:.2f}")

    regime_info = m_repo.get_latest_market_regime() or {}
    regime_label = regime_info.get("regime_label", "BULL_MARKET")
    breadth = float(regime_info.get("breadth_ma50", 0.5) * 100.0)
    print(f"   * Trạng thái thị trường (HMM Regime): {regime_label} (Độ rộng MA50: {breadth:.1f}%)")

    # 3. Kích hoạt Agent-05 qua AgentRegistry (Kiến trúc chuẩn Plug-and-Play)
    print("\n3. THỰC THI DEVIL'S ADVOCATE (AGENT-05) QUA AGENT REGISTRY:")
    event_payload = {
        "ticker": ticker,
        "investment_thesis": thesis,
    }
    dispatch_res = await AgentRegistry.dispatch("counter_thesis", event_payload)

    if dispatch_res.get("status") != "SUCCESS":
        print(f"   [X] Lỗi thực thi Agent-05: {dispatch_res.get('error')}")
        return

    result_data = dispatch_res.get("result", {}).get("data", {})
    trace_data = dispatch_res.get("result", {}).get("trace", {})

    verdict = result_data.get("verdict", "UNKNOWN")
    cts_score = float(result_data.get("cts_score", 0.0))
    base_cts = float(result_data.get("base_cts", 0.0))
    interaction_m = float(result_data.get("interaction_multiplier", 1.0))
    regime_m = float(result_data.get("regime_multiplier", 1.0))
    r3_passed = result_data.get("rule_of_three_passed", False)
    is_cap = result_data.get("is_capitulation_rebound", False)

    print("\n" + "-" * 80)
    print(f" KẾT QUẢ PHẢN BIỆN (DEVIL'S ADVOCATE REPORT)")
    print("-" * 80)
    print(f"  * PHÁN QUYẾT CUỐI CÙNG   : [{verdict}]")
    print(f"  * ĐIỂM FINAL CTS         : {cts_score:.1f} / 100.0 (Ngưỡng: <=30 PROCEED | 31-60 CONDITIONAL | >60 BLOCK)")
    print(f"  * Base CTS               : {base_cts:.1f}")
    print(f"  * ML Interaction Mult    : {interaction_m:.2f}x (Quét rủi ro cộng hưởng)")
    print(f"  * Regime Mult            : {regime_m:.2f}x (Hệ số môi trường vĩ mô)")
    print(f"  * Rule of Three Đạt?     : {'THỎA MÃN (>= 3 tín hiệu độc lập)' if r3_passed else 'VI PHẠM (< 3 tín hiệu độc lập)'}")
    print(f"  * Ngoại lệ Bắt đáy Bẫy 3 : {'KÍCH HOẠT (Capitulation Rebound)' if is_cap else 'Không kích hoạt'}")

    block_reasons = result_data.get("block_reasons", [])
    if block_reasons:
        print("\n  [!] DANH SÁCH LÝ DO PHỦ QUYẾT / CẢNH BÁO CHÍ MẠNG (BLOCK REASONS):")
        for i, r in enumerate(block_reasons, 1):
            print(f"      {i}. {r}")

    constraints = result_data.get("execution_constraints")
    if constraints:
        print("\n  [*] RÀNG BUỘC THỰC THI ÁP ĐẶT CHO AGENT 07 & AGENT 08 (CONSTRAINTS):")
        print(f"      - Hệ số quy mô vị thế : {constraints.get('max_position_size_multiplier', 1.0)*100:.0f}% kích thước chuẩn")
        print(f"      - Cắt lỗ cưỡng chế     : {constraints.get('stop_loss_pct_override', 0.0)*100:.1f}% NAV")
        print(f"      - Kế hoạch giải ngân   : {constraints.get('tranche_allocation', [])}")
        ceiling_px = constraints.get("entry_ceiling_price")
        if ceiling_px:
            print(f"      - Giá trần giải ngân   : {ceiling_px:,.0f} VND")
        print(f"      - Lý do ràng buộc      : {constraints.get('reason')}")

    holes = result_data.get("holes", [])
    if holes:
        print("\n  [*] CÁC LỖ HỔNG LUẬN ĐIỂM ĐƯỢC CHỈ RA:")
        for h in holes[:4]:
            print(f"      - {h}")

    # 4. Xác minh Toàn vẹn Lưu trữ CSDL
    print("\n4. XÁC MINH TOÀN VẸN CSDL POSTGRESQL:")
    thesis_id = result_data.get("thesis_id")
    if thesis_id:
        db_verdict = intel_repo.get_counter_thesis_verdict(thesis_id)
        if db_verdict:
            print(f"   [OK] Đã xác nhận lưu phán quyết vào `counter_thesis_verdicts` (thesis_id: {thesis_id})")
        else:
            print(f"   [X] Không tìm thấy bản ghi trong `counter_thesis_verdicts`")

        updated_thesis = intel_repo.get_latest_investment_thesis(ticker)
        if updated_thesis:
            print(f"   [OK] Đã cập nhật trạng thái `investment_theses.status` -> '{updated_thesis.get('status')}'")

        # Kiểm tra audit log
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM log_counter_thesis WHERE thesis_id = %s;", (thesis_id,))
                cnt = cur.fetchone()[0]
                print(f"   [OK] Đã ghi nhận {cnt} bản ghi audit trail vào `log_counter_thesis`")

    print("\n" + "=" * 80)
    print(f" HOÀN TẤT STANDALONE PRODUCTION RUN CHO AGENT-05 ({ticker})")
    print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chạy kiểm thử vận hành độc lập AGENT-05 (Counter Thesis).")
    parser.add_argument("--ticker", type=str, default="HPG", help="Mã cổ phiếu cần thẩm định phản biện (VD: HPG, SSI, FPT)")
    args = parser.parse_args()

    asyncio.run(run_agent05_standalone(ticker=args.ticker))
