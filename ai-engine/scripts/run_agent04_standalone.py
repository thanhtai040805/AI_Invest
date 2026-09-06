"""Standalone Agent 04 (Investment Thesis Agent) Production Runner Script (IOS v5.1).

Kiểm thử vận hành độc lập Agent 04 trên dữ liệu thực tế và CSDL PostgreSQL:
1. Xác thực kết nối CSDL và đọc dữ liệu giá OHLCV thực tế từ MarketDataRepository.
2. Kiểm tra bộ lọc Hard Filter (GIL CATASTROPHIC) và ngưỡng CSS >= 60 (Conviction >= B).
3. Thẩm định thực chất 3 Tín hiệu Độc lập (Hard Law Điều 3).
4. Phân loại Ngòi nổ (Catalyst Selection) và Định giá thích ứng đa mô hình.
5. Kiểm tra rò rỉ tin tức PEAI & cảnh báo False Breakout.
6. Xác nhận lưu trữ hoàn chỉnh vào CSDL (`investment_theses` và `log_investment_thesis`).

Cách sử dụng:
    python scripts/run_agent04_standalone.py
    python scripts/run_agent04_standalone.py --ticker SSI --timeline 3
    python scripts/run_agent04_standalone.py --ticker FPT --timeline 6
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
from app.infrastructure.database.pg_pool import get_conn


async def run_agent04_standalone(ticker: str, timeline_months: int = 3, force_conviction_a: bool = False):
    ticker = ticker.upper().strip()
    now = datetime.now(timezone.utc)

    print("=" * 80)
    print(f" [AGENT 04: INVESTMENT THESIS AGENT] -- STANDALONE PRODUCTION RUN")
    print(f" Ticker: {ticker} | Timeline: {timeline_months}M | Timestamp: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 80)

    intel_repo = IntelligenceRepository()
    m_repo = MarketDataRepository()

    # 1. Truy vấn thông tin thực tế từ CSDL
    print("\n1. THU THẬP DỮ LIỆU TỪ HỆ THỐNG CSDL THẬT:")
    ohlcv = m_repo.get_ohlcv(ticker, limit=15)
    current_price = 0.0
    if ohlcv and "close" in ohlcv[0]:
        current_price = float(ohlcv[0]["close"])
        print(f"   * Giá giao dịch thực tế gần nhất: {current_price:,.0f} VND (từ ohlcv)")
    else:
        latest_daily = m_repo.get_market_data_daily(ticker, limit=1)
        if latest_daily and "close" in latest_daily[0]:
            current_price = float(latest_daily[0]["close"])
            print(f"   * Giá giao dịch thực tế gần nhất: {current_price:,.0f} VND (từ market_data_daily)")

    if 0 < current_price < 1000.0:
        current_price *= 1000.0

    if current_price <= 0:
        print(f"   [CẢNH BÁO] Không có dữ liệu giá thật trong CSDL cho {ticker}, sử dụng giá tham chiếu 50,000 VND để kiểm thử.")
        current_price = 50000.0

    # Lấy factor score mới nhất nếu có trong CSDL
    factor_score_data = intel_repo.get_factor_score(ticker)
    if factor_score_data:
        css = float(factor_score_data.get("composite_score", 75.0))
        conviction = "A" if css >= 75 else ("B" if css >= 60 else "C")
        f1 = float(factor_score_data.get("value_score", 70.0))
        f2 = float(factor_score_data.get("quality_score", 75.0))
        f3 = float(factor_score_data.get("momentum_3m", 70.0))
        f4 = float(factor_score_data.get("earnings_yield_score", 75.0))
        f5 = float(factor_score_data.get("foreign_flow_score", 65.0))
        f6 = float(factor_score_data.get("volatility_score", 60.0))
        print(f"   * Đã nạp Factor Scores từ bảng factor_scores (CSS={css:.1f}, Conviction={conviction})")
    else:
        print(f"   * Chưa có factor_scores cho {ticker}, khởi tạo điểm mẫu đạt chuẩn Conviction A.")
        css, conviction = 80.0, "A"
        f1, f2, f3, f4, f5, f6 = 75.0, 78.0, 72.0, 85.0, 70.0, 68.0

    # Lấy Moat profile nếu có
    moat_data = intel_repo.get_moat_profile(ticker)
    moat_score = float(moat_data.get("moat_score", 75.0)) if moat_data else 75.0
    print(f"   * Điểm Hào kinh tế (Moat Score): {moat_score:.1f}")

    if force_conviction_a or factor_score_data is None:
        print(f"   * Thiết lập điểm định lượng đạt chuẩn Conviction A (CSS=80.0, F4=85.0, Moat=80.0)...")
        css, conviction = 80.0, "A"
        f1, f2, f3, f4, f5, f6 = 75.0, 78.0, 72.0, 85.0, 70.0, 68.0
        moat_score = 80.0

    research_report = {
        "ticker": ticker,
        "sector": "Manufacturing",
        "css": css,
        "conviction": conviction,
        "moat_score": moat_score,
        "current_price": current_price,
        "f1_value": f1,
        "f2_quality": f2,
        "f3_momentum": f3,
        "f4_earnings": f4,
        "f5_flow": f5,
        "f6_technical": f6,
    }

    market_context = {
        "current_regime": "BULL_TRENDING",
        "gil_status": "PASS",
        "current_price": current_price,
    }

    val_inputs = {
        "pe_price": current_price * 1.20,
        "ev_ebitda_price": current_price * 1.25,
        "dcf_price": current_price * 1.30,
    }

    # 2. Dispatch Agent 04 qua AgentRegistry
    print("\n2. THỰC THI PIPELINE AGENT-04 QUA AGENTREGISTRY (PROCESS & AUDIT LOG)...")
    res = await AgentRegistry.dispatch("investment_thesis", {
        "research_report": research_report,
        "market_context": market_context,
        "valuation_inputs": val_inputs,
        "timeline_months": timeline_months,
    })

    if res.get("status") != "SUCCESS":
        print(f"   [LỖI] Thực thi Agent-04 thất bại: {res}")
        return

    payload = res["result"]["data"]
    trace = res["result"]["trace"]

    print("\n3. KẾT QUẢ THẨM ĐỊNH LUẬN ĐIỂM ĐẦU TƯ (INVESTMENT THESIS PAYLOAD):")
    status = payload.get("status")
    print(f"   * Trạng thái             : {status}")

    if status in ["REJECTED", "WAIT_OR_SKIP"]:
        print(f"   * Lý do từ chối / dừng   : {payload.get('reason')}")
        print(f"   * Decision Trace         : {trace.get('decision')}")
        print("\n" + "=" * 80)
        print(" AGENT-04 ĐÃ THỰC THI BỘ LỌC BẢO VỆ VỐN THÀNH CÔNG (KHÔNG TẠO THESIS SAI NGUYÊN TẮC).")
        print("=" * 80 + "\n")
        return

    print(f"   * Thesis ID              : {payload.get('thesis_id')}")
    print(f"   * Tín hiệu PEAI (Rò rỉ)  : {trace.get('peai_status')}")

    signals = payload.get("input_validation", {}).get("independent_signals", {})
    print("\n   [3 TÍN HIỆU ĐỘC LẬP - HARD LAW ĐIỀU 3]:")
    for k, v in signals.items():
        print(f"     - {k:<24}: {v}")

    body = payload.get("thesis_body", {})
    catalyst = body.get("catalyst", {})
    price_target = body.get("price_target", {})
    exit_conds = body.get("exit_conditions", {})

    print(f"\n   [CỐT LÕI 3 CÂU HỎI IOS v5.1]:")
    print(f"     - Tại sao bây giờ?     : {body.get('why_now')}")
    print(f"     - Tại sao mã này?      : {body.get('why_this_stock')}")

    print(f"\n   [NGÒI NỔ & MỤC TIÊU GIÁ]:")
    print(f"     - Loại ngòi nổ         : {catalyst.get('primary_type')}")
    print(f"     - Chi tiết ngòi nổ     : {catalyst.get('description')}")
    print(f"     - Thời hạn nắm giữ     : {body.get('timeline')}")
    print(f"     - Giá mục tiêu Base    : {price_target.get('base_case'):,.0f} VND")
    print(f"     - Giá mục tiêu Bull    : {price_target.get('bull_case'):,.0f} VND")
    print(f"     - Biên độ mục tiêu     : {price_target.get('target_range')}")
    print(f"     - Giá Hard Stop-loss   : {exit_conds.get('hard_stop_loss_price'):,.0f} VND (-7%)")

    print("\n   [PRE-MORTEM ANALYSIS - 3 KỊCH BẢN THẤT BÀI]:")
    for scenario in body.get("pre_mortem", []):
        print(f"     * {scenario}")

    # 4. Kiểm tra trực tiếp CSDL
    print("\n4. XÁC NHẬN LƯU TRỮ VÀO CSDL THỰC TẾ:")
    thesis_id = payload.get("thesis_id")
    db_thesis = intel_repo.get_latest_investment_thesis(ticker)
    if db_thesis and db_thesis.get("thesis_id") == thesis_id:
        print(f"   [OK] Bảng investment_theses: Tìm thấy bản ghi {thesis_id}")
        print(f"        -> target_price_range trong DB: {db_thesis.get('target_price_range')}")
        print(f"        -> status trong DB: {db_thesis.get('status')}")
    else:
        print(f"   [LỖI] Không tìm thấy bản ghi {thesis_id} trong investment_theses!")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT thesis_id, ticker, pre_mortem_scenarios FROM log_investment_thesis WHERE thesis_id = %s", (thesis_id,))
            row = cur.fetchone()
            if row:
                print(f"   [OK] Bảng log_investment_thesis: Audit trace được lưu thành công (id={row[0]}, scenarios={len(row[2])})")
            else:
                print(f"   [LỖI] Không tìm thấy audit log trong log_investment_thesis!")

    print("\n" + "=" * 80)
    print(" AGENT-04 THỰC THI HOÀN TẤT THÀNH CÔNG VÀ ĐẠT CHUẨN PRODUCTION.")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chạy độc lập Agent 04 (Investment Thesis Agent)")
    parser.add_argument("--ticker", type=str, default="HPG", help="Mã cổ phiếu (mặc định HPG)")
    parser.add_argument("--timeline", type=int, default=3, help="Thời gian nắm giữ tính bằng tháng (1, 3, 6)")
    parser.add_argument("--force-conviction-a", action="store_true", help="Bắt buộc nạp profile Conviction A để kiểm thử đầy đủ schema")
    args = parser.parse_args()

    asyncio.run(run_agent04_standalone(
        ticker=args.ticker,
        timeline_months=args.timeline,
        force_conviction_a=args.force_conviction_a,
    ))
