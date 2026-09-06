"""Standalone Agent 06 (Portfolio Risk Agent - Chief Risk Officer Engine) Production Runner Script (IOS v5.1).

Kiểm thử vận hành độc lập Agent 06 trên dữ liệu thực tế và CSDL PostgreSQL:
1. Tự động nạp tài khoản và danh mục vị thế thực tế từ `PortfolioRepository`.
2. Nạp hạn mức rủi ro thể chế từ bảng `risk_limits` (Single stock 15%, Sector 35%, Stop-loss 2%).
3. Thẩm định 5 lớp rủi ro độc lập:
   - Lớp 1: Hard Laws thể chế (Single Stock, Sector, ADTV, T+2.5 Floor Loss).
   - Lớp 2: Quản trị rủi ro kẹt hàng T+2.5 (Locked Exposure, Đệm rủi ro 2 cây sàn -13.51%).
   - Lớp 3: Cảm biến Dị thường Giá & Volume VSA (Churning, Upthrust, Breakdown).
   - Lớp 4: Đo lường Tail Risk (Historical ES 97.5%, EGARCH-t, Stress Matrix) & Drawdown Protocol.
   - Lớp 5: Giám sát Suy thoái Mô hình (CDC Tiers: IC Decay, Slippage Spike).
4. Phê chuẩn / Điều chỉnh quy mô / Phủ quyết lệnh (PASS / REDUCE / BLOCK).
5. Tự động ghi nhật ký vào bảng `risk_snapshots` và phát sự kiện RabbitMQ.

Cách sử dụng:
    python scripts/run_agent06_standalone.py
    python scripts/run_agent06_standalone.py --ticker HPG --shares 5000
    python scripts/run_agent06_standalone.py --ticker SSI --shares 20000 --price 32500
    python scripts/run_agent06_standalone.py --eod
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


async def run_agent06_standalone(
    ticker: str = "HPG",
    shares: int = 5000,
    price: float = 0.0,
    stop_loss_price: float = 0.0,
    sector: str = "Tài nguyên Cơ bản",
    is_eod_only: bool = False,
):
    now = datetime.now(timezone.utc)
    ticker = ticker.upper().strip()

    print("=" * 85)
    print(" [AGENT 06: PORTFOLIO RISK AGENT (CHIEF RISK OFFICER)] -- STANDALONE RUN")
    print(f" Timestamp: {now.strftime('%Y-%m-%d %H:%M:%S UTC')} | Scope: 100% HOSE Spot Equity")
    print("=" * 85)

    port_repo = PortfolioRepository()
    intel_repo = IntelligenceRepository()
    mkt_repo = MarketDataRepository()

    # 1. Nạp tài khoản và vị thế thực tế
    print("\n1. TRẠNG THÁI TÀI KHOẢN & VỊ THẾ NẮM GIỮ (PORTFOLIO DATABASE):")
    account = port_repo.get_account_state()
    total_nav = float(account.get("total_nav", 1000000000.0))
    peak_nav = float(account.get("peak_nav", total_nav))
    cash_bal = float(account.get("cash_balance", 1000000000.0))
    print(f"   - Tổng NAV danh mục   : {total_nav:,.0f} VND")
    print(f"   - Tiền mặt khả dụng   : {cash_bal:,.0f} VND ({cash_bal / total_nav * 100:.1f}%)")
    print(f"   - Peak NAV lịch sử    : {peak_nav:,.0f} VND")

    open_positions = port_repo.get_open_positions()
    print(f"   - Số vị thế hiện tại : {len(open_positions)} mã")
    for pos in open_positions[:5]:
        sym = pos.get("ticker", pos.get("symbol", ""))
        qty = pos.get("shares", pos.get("quantity", 0))
        val = pos.get("market_value", 0.0)
        t25_locked = pos.get("locked_t25_shares", 0)
        print(f"     + {sym:6s}: {qty:>6,d} cổ ({val:,.0f} VND) | T+2.5 Locked: {t25_locked:>5,d} cổ")

    # 2. Nạp hạn mức rủi ro thể chế
    print("\n2. HẠN MỨC RỦI RO THỂ CHẾ (BẢNG risk_limits):")
    limits = intel_repo.get_risk_limits("HOSE_EQUITY")
    print(f"   - Max Single Stock Limit : {limits.get('max_single_stock_pct', 15.0):.1f}% NAV")
    print(f"   - Max Sector Limit       : {limits.get('max_sector_pct', 35.0):.1f}% NAV")
    print(f"   - Hard Stop Loss Limit   : {limits.get('hard_stop_loss_pct', 2.0):.1f}% NAV (T+2.5 Floor Gap)")

    # 3. Chuẩn bị proposed order nếu không phải EOD only
    proposed_order = None
    if not is_eod_only:
        print(f"\n3. LỆNH MUA ĐỀ XUẤT (PROPOSED ORDER FOR {ticker}):")
        if price <= 0:
            price = mkt_repo.get_realtime_or_latest_price(ticker, allow_eod_fallback=True) or 28000.0
        if stop_loss_price <= 0:
            stop_loss_price = round(price * 0.93, 2)

        order_val = price * shares
        print(f"   - Ticker         : {ticker}")
        print(f"   - Khối lượng     : {shares:,d} cổ")
        print(f"   - Giá đề xuất    : {price:,.1f} VND")
        print(f"   - Giá trị lệnh   : {order_val:,.0f} VND ({(order_val / total_nav) * 100:.2f}% NAV)")
        print(f"   - Giá Stop Loss  : {stop_loss_price:,.1f} VND (-{((price - stop_loss_price) / price) * 100:.1f}%)")
        print(f"   - Sector         : {sector}")

        proposed_order = {
            "ticker": ticker,
            "side": "BUY",
            "quantity": shares,
            "target_shares": shares,
            "price": price,
            "target_price": price,
            "stop_loss_price": stop_loss_price,
            "sector": sector,
            "adtv20": 5000000.0,
        }
    else:
        print("\n3. CHẾ ĐỘ GIÁM SÁT RỦI RO CUỐI NGÀY (EOD PORTFOLIO RISK EVALUATION)")

    # 4. Thực thi Agent 06 qua AgentRegistry
    print("\n4. THỰC THI AGENT-06: PORTFOLIO RISK AGENT...")
    risk_agent = AgentRegistry.get_agent("portfolio_risk")
    if not risk_agent:
        from app.domain.agents.portfolio_risk import PortfolioRiskAgent
        risk_agent = PortfolioRiskAgent()

    payload = {
        "event_name": "risk.order.review" if not is_eod_only else "risk.eod.evaluation",
        "proposed_order": proposed_order,
    }

    result = await risk_agent.process(payload)
    data = result.get("data", {})
    trace = result.get("trace", {})

    # 5. Báo cáo kết quả thẩm định rủi ro
    print("\n" + "=" * 85)
    print(" KẾT QUẢ THẨM ĐỊNH RỦI RO CỦA CHIEF RISK OFFICER:")
    print("=" * 85)
    print(f" Phán Quyết Toàn Thể (Risk Status) : [ {data.get('risk_status')} ]")
    print(f" Quyết Định Mã (Decision ID)       : {data.get('decision_id')}")

    decision = data.get("decision", {})
    if decision:
        print(f" Hành động Khuyến nghị            : {decision.get('action')} ({decision.get('side')})")
        print(f" Khối lượng Được phê duyệt         : {decision.get('approved_shares', 0):,d} cổ (Gốc: {decision.get('original_shares', 0):,d})")
        if decision.get("approved_weight_pct"):
            print(f" Tỷ trọng Phân bổ Thực tế         : {decision.get('approved_weight_pct')}% NAV")
        print(f" Cash Target Tối thiểu            : {decision.get('min_cash_target_pct', 0.0):.1f}% NAV")
        print(f" Rationale (Lý do phán quyết)     : {decision.get('rationale')}")

    print("\nCHI TIẾT CÁC LỚP THẨM ĐỊNH:")
    hl = data.get("hard_laws", {})
    print(f" 1. Hard Laws Thể Chế              : Single Stock [{hl.get('single_stock')}], Sector [{hl.get('sector')}], Risk [{hl.get('position_risk')}], Liquidity [{hl.get('liquidity_limit')}]")

    tape = data.get("tape_anomaly", {})
    print(f" 2. VSA Tape Anomaly Sensor        : Dị thường: {tape.get('detected')} | Mức độ: {tape.get('severity')} | Loại: {tape.get('anomaly_type')}")
    if tape.get("reason"):
        print(f"    Chi tiết                       : {tape.get('reason')}")

    tail = data.get("tail_risk", {})
    print(f" 3. Tail Risk & Phân Phối Đuôi Dày : Historical ES 97.5%: {tail.get('historical_es_97_5', 0)*100:.2f}% | Verdict: {tail.get('tail_risk_verdict')}")

    dd = data.get("drawdown", {})
    print(f" 4. Drawdown Protocol Tier         : [ {dd.get('tier')} ] (Drawdown: {dd.get('current_drawdown_pct', 0):.2f}%, Re-risking: {dd.get('re_risking_state')})")

    cdc = data.get("cdc", {})
    print(f" 5. Capital Degradation (CDC)      : [ {cdc.get('tier')} ] (Active: {cdc.get('is_cdc_active')}, IC Decay: {cdc.get('ic_decay_pct', 0)*100:.1f}%)")

    # 6. Kiểm tra việc lưu snapshot CSDL
    latest_snap = intel_repo.get_latest_risk_snapshot()
    if latest_snap:
        print(f"\n[CSDL] Đã ghi nhận Snapshot mới nhất vào bảng `risk_snapshots` (Ngày: {latest_snap.get('date')}, ES: {latest_snap.get('es_97_5'):.2f}%, Tier: {latest_snap.get('drawdown_tier')})")

    print("\n" + "=" * 85)
    print(" [AGENT 06 RUNNER] HOÀN TẤT THỰC THI KHÔNG LỖI.")
    print("=" * 85)


def main():
    parser = argparse.ArgumentParser(description="Chạy kiểm thử độc lập Agent 06 Portfolio Risk Agent (IOS v5.1)")
    parser.add_argument("--ticker", type=str, default="HPG", help="Mã cổ phiếu đề xuất (mặc định HPG)")
    parser.add_argument("--shares", type=int, default=5000, help="Số lượng cổ phiếu đề xuất (mặc định 5000)")
    parser.add_argument("--price", type=float, default=0.0, help="Giá mua đề xuất (0 = lấy giá thị trường)")
    parser.add_argument("--stop-loss", type=float, default=0.0, help="Giá cắt lỗ (0 = mặc định -7%)")
    parser.add_argument("--sector", type=str, default="Tài nguyên Cơ bản", help="Ngành của cổ phiếu")
    parser.add_argument("--eod", action="store_true", help="Chạy chế độ đánh giá rủi ro cuối ngày EOD")

    args = parser.parse_args()

    asyncio.run(
        run_agent06_standalone(
            ticker=args.ticker,
            shares=args.shares,
            price=args.price,
            stop_loss_price=args.stop_loss,
            sector=args.sector,
            is_eod_only=args.eod,
        )
    )


if __name__ == "__main__":
    main()
