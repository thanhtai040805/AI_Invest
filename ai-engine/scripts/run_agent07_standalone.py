"""Standalone Agent 07 (Portfolio Allocation Agent - Chief Capital Allocator) Production Runner Script (IOS v5.1).

Kiểm thử vận hành độc lập Agent 07 trên dữ liệu thực tế và CSDL PostgreSQL:
1. Tự động nạp tài khoản và danh mục vị thế thực tế từ `PortfolioRepository`.
2. Tự động nạp Luận điểm Đầu tư (`investment_theses`), Phản biện (`counter_thesis_verdicts`) và Snapshot Rủi ro (`risk_snapshots`).
3. Vận hành quy trình 8 Engine nghiệp vụ chuẩn định chế:
   - Engine 1: Eligibility Engine (Thẩm định Research, Thesis, Counter-Thesis)
   - Engine 2: Probability Engine (Hiệu chuẩn p_win và payoff R)
   - Engine 3: Position Sizing Engine (Quarter Kelly f* và hệ số co giãn Regime)
   - Engine 4: Portfolio Construction Engine (Ràng buộc trần ngành 35%, tương quan cặp < 0.5)
   - Engine 5: Dynamic Allocation Engine (Đệm tiền mặt chủ động theo Drawdown / Risk Snapshot)
   - Engine 6: Liquidity Engine (Tuân thủ Điều 2 HOSE: <= 15% ADTV20 phiên)
   - Engine 7: Rebalancing Engine (Ngưỡng Deadband >= 2.0%, phân mảnh T+2.5)
   - Engine 8: Decision Output Engine (Đóng gói 4 nhóm Output A, B, C, D)
4. Lưu quyết định vào bảng `portfolio_decisions` và phát sự kiện `ORDER_INSTRUCTION`.

Cách sử dụng:
    python scripts/run_agent07_standalone.py
    python scripts/run_agent07_standalone.py --ticker HPG
    python scripts/run_agent07_standalone.py --ticker SSI --conviction A
    python scripts/run_agent07_standalone.py --ticker FPT --regime BULL_MARKET
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
from app.domain.repositories.portfolio_repository import PortfolioRepository
from app.domain.repositories.intelligence_repository import IntelligenceRepository
from app.domain.repositories.market_data_repository import MarketDataRepository


async def run_agent07_standalone(
    ticker: str = "HPG",
    price: float = 0.0,
    conviction: str = "",
    regime: str = "",
    sector: str = "",
):
    now = datetime.now(timezone.utc)
    ticker = ticker.upper().strip()

    print("=" * 85)
    print(" [AGENT 07: PORTFOLIO ALLOCATION AGENT (CHIEF CAPITAL ALLOCATOR)] -- STANDALONE")
    print(f" Timestamp: {now.strftime('%Y-%m-%d %H:%M:%S UTC')} | Ticker: {ticker}")
    print("=" * 85)

    port_repo = PortfolioRepository()
    intel_repo = IntelligenceRepository()
    mkt_repo = MarketDataRepository()

    # 1. Trạng thái tài khoản thực tế
    print("\n1. TRẠNG THÁI TÀI KHOẢN & VỊ THẾ DANH MỤC (PORTFOLIO REPOSITORY):")
    acc = port_repo.get_account_state()
    total_nav = float(acc.get("total_nav", 1000000000.0))
    cash_bal = float(acc.get("cash_balance", 1000000000.0))
    peak_nav = float(acc.get("peak_nav", total_nav))
    dd_tier = str(acc.get("drawdown_tier", "GREEN"))
    print(f"   - Tổng NAV danh mục     : {total_nav:,.0f} VND")
    print(f"   - Tiền mặt khả dụng     : {cash_bal:,.0f} VND ({cash_bal / total_nav * 100:.1f}%)")
    print(f"   - Drawdown Tier hiện tại: [ {dd_tier} ] (Peak NAV: {peak_nav:,.0f} VND)")

    open_pos = port_repo.get_open_positions()
    pos_ticker = next((p for p in open_pos if p["ticker"] == ticker), None)
    if pos_ticker:
        cur_shares = pos_ticker.get("shares", 0)
        avail = pos_ticker.get("available_shares", cur_shares)
        locked = pos_ticker.get("locked_t25_shares", 0)
        print(f"   - Vị thế hiện có ({ticker}) : {cur_shares:,d} cổ (Khả dụng: {avail:,d}, T+2.5 Locked: {locked:,d})")
    else:
        print(f"   - Vị thế hiện có ({ticker}) : 0 cổ (Chưa nắm giữ)")

    # 2. Thu thập dữ liệu thị trường & định giá thực tế
    print("\n2. DỮ LIỆU THỊ TRƯỜNG & GIÁ THỰC TẾ:")
    if price <= 0:
        price = mkt_repo.get_realtime_or_latest_price(ticker, allow_eod_fallback=True) or 25000.0
    print(f"   - Giá thị trường tham chiếu: {price:,.1f} VND")

    if not regime:
        regime_info = mkt_repo.get_latest_market_regime() or {}
        regime = str(regime_info.get("regime_label", "BULL_MARKET")).upper()
    print(f"   - Trạng thái Thị trường    : [ {regime} ]")

    # 3. Tra cứu Luận điểm Đầu tư & Phản biện từ CSDL
    print("\n3. TRA CỨU TIỀN ĐỀ THẨM ĐỊNH TỪ CSDL (THESIS & COUNTER-THESIS):")
    thesis = intel_repo.get_latest_investment_thesis(ticker)
    if thesis:
        print(f"   - Tìm thấy Thesis CSDL     : {thesis.get('thesis_id')} (Status: {thesis.get('status')})")
        if not conviction:
            conviction = str(thesis.get("conviction", "B")).upper()
        if not sector and thesis.get("sector"):
            sector = str(thesis.get("sector"))
    else:
        print(f"   - [!] Chưa có Thesis trong CSDL cho {ticker}, sẽ dựa trên auto-fallback.")
        if not conviction:
            conviction = "A"

    counter = intel_repo.get_latest_counter_thesis_verdict(ticker)
    if counter:
        print(f"   - Tìm thấy Counter Verdict : [ {counter.get('verdict')} ] (CTS: {counter.get('cts_score', 0):.1f})")
    else:
        print(f"   - [!] Chưa có Counter-Thesis trong CSDL cho {ticker}.")

    # Snapshot rủi ro gần nhất từ Agent 06
    risk_snap = intel_repo.get_latest_risk_snapshot()
    if risk_snap:
        print(f"   - Snapshot Rủi ro (Agent 06): Drawdown Tier: [ {risk_snap.get('drawdown_tier')} ], Cash Target: {risk_snap.get('garch_cash_target', 0):.1f}% NAV")

    # 4. Thực thi Agent-07
    print("\n4. THỰC THI AGENT-07: PORTFOLIO ALLOCATION AGENT (8 ENGINES)...")
    alloc_agent = AgentRegistry.get_agent("portfolio_allocation")
    if not alloc_agent:
        from app.domain.agents.portfolio_allocation import PortfolioAllocationAgent
        alloc_agent = PortfolioAllocationAgent()

    candidate_payload = {
        "ticker": ticker,
        "price": price,
        "conviction": conviction,
        "sector": sector or "Tài nguyên Cơ bản",
        "regime": regime,
    }

    result = await alloc_agent.process({
        "candidate": candidate_payload,
        "investment_thesis": thesis,
        "counter_thesis": counter,
    })

    data = result.get("data", {})
    trace = result.get("trace", {})

    # 5. Báo cáo 4 nhóm Output
    print("\n" + "=" * 85)
    print(" KẾT QUẢ PHÂN BỔ VỐN CỦA CHIEF CAPITAL ALLOCATOR:")
    print("=" * 85)

    # Nhóm A: Decision
    group_a = data.get("portfolio_decision", {})
    print(f" [NHÓM A] QUYẾT ĐỊNH DANH MỤC : [ {group_a.get('portfolio_decision')} ] ({data.get('action')})")
    print(f"  - Quyết định Mã (Decision ID) : {data.get('decision_id')}")
    print(f"  - Chiều Lệnh (Side)           : {data.get('side')}")
    print(f"  - Mức độ Khẩn cấp             : {group_a.get('execution_urgency')}")

    # Nhóm B: Capital Allocation
    group_b = data.get("capital_allocation", {})
    print("\n [NHÓM B] BẢN ĐỒ 4 TẦNG PHÂN BỔ TỶ TRỌNG VỐN:")
    print(f"  - 1. Preliminary Target (Kelly) : {group_b.get('preliminary_target', 0)*100:.2f}% NAV")
    print(f"  - 2. Portfolio Target (Const.)  : {group_b.get('portfolio_target', 0)*100:.2f}% NAV")
    print(f"  - 3. Executable Target (Liq.)   : {group_b.get('executable_target', 0)*100:.2f}% NAV (Mục tiêu: {group_b.get('target_shares', 0):,d} cổ)")
    print(f"  - 4. Incremental Order (Lệnh)   : {group_b.get('incremental_weight', 0)*100:.2f}% NAV ({group_b.get('incremental_shares', 0):,d} cổ ~ {group_b.get('order_value_vnd', 0):,.0f} VND)")

    # Nhóm C: Portfolio Impact
    group_c = data.get("portfolio_impact", {})
    print("\n [NHÓM C] TÁC ĐỘNG DANH MỤC & RỦI RO BIÊN:")
    print(f"  - Tỷ trọng Ngành Sau Lệnh       : {group_c.get('sector_exposure_after', 0)*100:.2f}% NAV (Trần: {group_c.get('sector_limit', 0)*100:.1f}%)")
    print(f"  - Tiền mặt Sau Phân bổ          : {group_c.get('cash_after', 0)*100:.2f}% NAV (Mục tiêu tối thiểu: {group_c.get('min_cash_target', 0)*100:.1f}%)")
    print(f"  - Rủi ro Biên (Marginal Risk)   : {group_c.get('marginal_risk')} ({group_c.get('marginal_risk_pct', 0)*100:.2f}%)")

    # Nhóm D: Decision Log & Kelly Math
    group_d = data.get("decision_log", {})
    print("\n [NHÓM D] THẨM ĐỊNH ĐỊNH LƯỢNG & KELLY MATHEMATICS:")
    print(f"  - Xác suất Thắng Hiệu chuẩn (p) : {group_d.get('p_calibrated', 0)*100:.1f}%")
    print(f"  - Tỷ số Lợi nhuận/Rủi ro (R)   : {group_d.get('payoff_ratio', 0):.2f}")
    print(f"  - Lợi thế Kỳ vọng (Edge)        : {group_d.get('expected_edge', 0)*100:.2f}%")
    print(f"  - Quarter Kelly f* (Raw)        : {group_d.get('quarter_kelly_raw', 0)*100:.2f}% NAV")
    print(f"  - Ngưỡng Deadband (>= 2%)       : {group_d.get('deadband_passed')}")
    print(f"  - Rationale                     : {data.get('rationale')}")

    # 6. Kiểm tra CSDL
    latest_dec = port_repo.get_latest_decision(ticker)
    if latest_dec:
        print(f"\n[CSDL] Đã ghi nhận Quyết định vào bảng `portfolio_decisions`: ID {latest_dec.get('decision_id')} ({latest_dec.get('action')}, {latest_dec.get('target_shares')} cổ, {latest_dec.get('allocated_weight_pct')}%)")

    print("\n" + "=" * 85)
    print(" [AGENT 07 RUNNER] HOÀN TẤT THỰC THI THÀNH CÔNG KHÔNG LỖI.")
    print("=" * 85)


def main():
    parser = argparse.ArgumentParser(description="Chạy kiểm thử độc lập Agent 07 Portfolio Allocation Agent (IOS v5.1)")
    parser.add_argument("--ticker", type=str, default="HPG", help="Mã cổ phiếu phân bổ (mặc định HPG)")
    parser.add_argument("--price", type=float, default=0.0, help="Giá mua đề xuất (0 = lấy giá thị trường)")
    parser.add_argument("--conviction", type=str, default="", help="Mức Conviction (A+, A, B)")
    parser.add_argument("--regime", type=str, default="", help="Trạng thái thị trường HMM")
    parser.add_argument("--sector", type=str, default="", help="Ngành của cổ phiếu")

    args = parser.parse_args()

    asyncio.run(
        run_agent07_standalone(
            ticker=args.ticker,
            price=args.price,
            conviction=args.conviction,
            regime=args.regime,
            sector=args.sector,
        )
    )


if __name__ == "__main__":
    main()
