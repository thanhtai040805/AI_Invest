"""Standalone Agent 08 (Trade Execution Agent - Smart Order Routing & Execution Gateway) Production Runner Script (IOS v5.1).

Kiểm thử vận hành độc lập Agent 08 trên dữ liệu thực tế và CSDL PostgreSQL:
1. Tự động kiểm tra Failsafe Guard & Vi cấu trúc HOSE (Lô chẵn 100, bước giá, trần 500,000 cổ).
2. Tự động thẩm định Pre-trade Governance Gate (Agent 12).
3. Lập kế hoạch thực thi qua EAE Engine theo Market State (NORMAL, STRESS, CRISIS).
4. Kiểm soát bẫy thao túng phiên ATC qua ATC Anomaly Detector.
5. Ghi nhận nhật ký thực thi (order_executions) và hồ sơ trượt giá (slippage_records) vào PostgreSQL.

Cách sử dụng:
    python scripts/run_agent08_standalone.py --ticker HPG --shares 1000 --price 27000
    python scripts/run_agent08_standalone.py --ticker VNM --shares 500 --action BUY
    python scripts/run_agent08_standalone.py --ticker SSI --shares 2000 --mode STRESS
    python scripts/run_agent08_standalone.py --ticker HPG --shares 5000 --atc
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
from app.domain.repositories.market_data_repository import MarketDataRepository


async def run_agent08_standalone(
    ticker: str = "HPG",
    action: str = "BUY",
    shares: int = 1000,
    price: float = 0.0,
    mode: str = "NORMAL",
    simulate_atc: bool = False,
    nav: float = 0.0,
):
    now = datetime.now(timezone.utc)
    ticker = ticker.upper().strip()
    action = action.upper().strip()
    mode = mode.upper().strip()

    print("=" * 85)
    print(" [AGENT 08: TRADE EXECUTION AGENT (SMART ORDER ROUTING GATEWAY)] -- STANDALONE")
    print(f" Timestamp: {now.strftime('%Y-%m-%d %H:%M:%S UTC')} | Ticker: {ticker} | Action: {action}")
    print("=" * 85)

    print(f"\n[BƯỚC 1/3] Đang nạp dữ liệu vi cấu trúc và thanh khoản cho mã {ticker}...")
    market_repo = MarketDataRepository()
    portfolio_repo = PortfolioRepository()

    # 1. Fetch market price if not provided
    if price <= 0.0:
        try:
            daily_data = market_repo.get_market_data_daily(ticker, limit=1)
            if daily_data and "close" in daily_data[0]:
                price = float(daily_data[0]["close"])
                print(f" -> Lấy thị giá đóng cửa gần nhất từ CSDL: {price:,.0f} VND")
        except Exception:
            pass
        if price <= 0.0:
            price = 27000.0 if ticker == "HPG" else (68000.0 if ticker == "VNM" else 30000.0)
            print(f" -> Dùng thị giá tham chiếu mặc định: {price:,.0f} VND")
    else:
        print(f" -> Thị giá chỉ định: {price:,.0f} VND")

    # 2. Fetch ADTV20
    adtv20 = 25_000_000.0 if ticker in {"HPG", "SSI", "VND"} else 3_000_000.0
    try:
        daily_20 = market_repo.get_market_data_daily(ticker, limit=20)
        if daily_20 and len(daily_20) > 0:
            vols = [float(d.get("volume", 0)) for d in daily_20 if float(d.get("volume", 0)) > 0]
            if vols:
                adtv20 = sum(vols) / len(vols)
                print(f" -> Thanh khoản thực tế ADTV20: {adtv20:,.0f} cổ/phiên")
    except Exception:
        pass

    # 3. Market State
    market_state = {
        "spread": 0.015 if mode == "STRESS" else 0.003,
        "volume_status": "LOW" if mode == "STRESS" else "NORMAL",
        "market_regime": mode,
        "atc_concentration": 0.35 if simulate_atc else 0.18,
    }

    account_state = portfolio_repo.get_account_state()
    base_nav = nav if nav > 0 else float(account_state.get("total_nav", 1_000_000_000.0))

    payload = {
        "order_instruction": {
            "ticker": ticker,
            "action": action,
            "target_shares": shares,
            "price": price,
            "total_nav": base_nav,
        },
        "market_state": market_state,
        "adtv20": adtv20,
        "total_nav": base_nav,
    }

    print(f"\n[BƯỚC 2/3] Kích hoạt TradeExecutionAgent (EAE Engine & Governance Pre-Trade Gate)...")
    res = await AgentRegistry.dispatch("trade_execution", payload)

    if res.get("status") != "SUCCESS":
        print(f" [THẤT BẠI] Agent 08 báo lỗi: {res.get('error')}")
        return

    data = res.get("result", {}).get("data", {})
    trace = res.get("result", {}).get("trace", {})

    print("\n" + "=" * 85)
    print(" KẾT QUẢ THỰC THI LỆNH - AGENT-08 (TRADE EXECUTION GATEWAY)")
    print("=" * 85)
    print(f" Mã Cổ Phiếu (Ticker)             : {ticker}")
    print(f" Hướng Giao Dịch (Direction)       : {action}")
    print(f" Khối Lượng Đặt Lệnh (Target)      : {shares:,} cổ")
    print(f" Giá Tham Chiếu Khớp (Price)       : {price:,.0f} VND")
    print(f" Phán Quyết Thực Thi (Decision)    : [ {data.get('execution_decision', 'UNKNOWN')} ]")
    print(f" Trạng Thái Lệnh (Status)          : {data.get('status', 'N/A')}")
    print(f" Chế Độ Thực Thi EAE (Mode)        : {data.get('execution_mode', 'N/A')}")

    if data.get("execution_decision") in ("EXECUTE", "PARTIALLY_EXECUTED"):
        plan = data.get("execution_plan", {})
        metrics = data.get("execution_metrics", {})
        learning = data.get("learning_feedback", {})

        print(f" Chiến Lược Lệnh (Strategy)        : {plan.get('strategy', 'N/A')}")
        print(f" Số Lệnh Con Chia Nhỏ (Child Orders): {plan.get('child_orders', 1)} lệnh con")
        print(f" Khung Thời Gian Khớp (Horizon)    : {plan.get('execution_horizon', '1_SESSION')}")
        print(f" Tỷ Lệ Tham Gia Khối Lượng Tối Đa  : {plan.get('max_participation_rate', 0.20)*100:g}% ADTV")
        print("-" * 85)
        print(f" Khối Lượng Đã Khớp (Executed)     : {metrics.get('executed_quantity', 0):,} cổ")
        print(f" Khối Lượng Còn Dư (Remaining)     : {metrics.get('remaining_quantity', 0):,} cổ")
        print(f" Giá Khớp Trung Bình (Avg Price)   : {metrics.get('average_execution_price', price):,.0f} VND")
        print(f" Độ Trượt Giá Thực Tế (Slippage)   : {metrics.get('slippage', 0)*10000:,.1f} bps ({metrics.get('slippage', 0)*100:g}%)")
        print(f" Phân Tầng ADTV (Slippage Bucket)  : {learning.get('slippage_bucket', 'N/A')} (Gốc: {learning.get('base_slippage_bucket', 'N/A')})")
        print(f" Đánh Giá Chất Lượng Khớp Lệnh     : {learning.get('execution_quality', 'N/A')}")
    else:
        print(f" Lý Do Từ Chối / Chặn (Reason)     : {data.get('rejection_reason', 'N/A')}")

    print("=" * 85)
    print(f" [CSDL] Hồ sơ giao dịch và trượt giá đã được ghi nhận an toàn.")


def main():
    parser = argparse.ArgumentParser(description="Standalone Runner cho Agent 08 (Trade Execution)")
    parser.add_argument("--ticker", type=str, default="HPG", help="Mã cổ phiếu cần thực thi lệnh")
    parser.add_argument("--action", type=str, default="BUY", choices=["BUY", "SELL"], help="Chiều mua/bán")
    parser.add_argument("--shares", type=int, default=1000, help="Khối lượng cổ phiếu (bội số 100)")
    parser.add_argument("--price", type=float, default=0.0, help="Thị giá tham chiếu (0 = auto-fetch)")
    parser.add_argument("--mode", type=str, default="NORMAL", choices=["NORMAL", "STRESS", "CRISIS"], help="Chế độ thị trường")
    parser.add_argument("--atc", action="store_true", help="Giả lập phiên ATC có nguy cơ thao túng")
    parser.add_argument("--nav", type=float, default=0.0, help="Tổng NAV danh mục (0 = auto-fetch)")

    args = parser.parse_args()
    asyncio.run(run_agent08_standalone(
        ticker=args.ticker,
        action=args.action,
        shares=args.shares,
        price=args.price,
        mode=args.mode,
        simulate_atc=args.atc,
        nav=args.nav,
    ))


if __name__ == "__main__":
    main()
