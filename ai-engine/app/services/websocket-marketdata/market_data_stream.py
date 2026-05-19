#!/usr/bin/env python3
import asyncio
from datetime import datetime
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from dnse import DNSEClient
from dnse.websocket.client import TradingClient

from dnse.websocket.models import (
    ExpectedPrice,
    ForeignInvestor,
    MarketIndex,
    Ohlc,
    Quote,
    SecurityDefinition,
    Trade,
    TradeExtra,
)

from app.config import (
    DNSE_API_KEY,
    DNSE_API_SECRET,
    DNSE_BASE_URL,
    DNSE_WS_URL,
    BOARD_ID,
    ENCODING,
) 


def get_core_symbols():
    rest_client = DNSEClient(
        api_key=DNSE_API_KEY,
        api_secret=DNSE_API_SECRET,
        base_url=DNSE_BASE_URL,
    )
    core_symbols = []

    # Quét cả 2 sàn STO (HOSE) và HNX
    for market in ["STO", "HNX"]:
        current_page = 1
        while True:
            status, body = rest_client.get_instruments(
                symbol="",
                market_id=market,
                security_group_id="ST",
                index_name="",
                limit=100,
                page=current_page,
            )
            if status != 200:
                break

            parsed_body = json.loads(body) if isinstance(body, str) else body
            data_list = (
                parsed_body
                if isinstance(parsed_body, list)
                else parsed_body.get("data", [])
            )

            if not data_list:
                break

            for item in data_list:
                symbol = item.get("symbol")
                if symbol:
                    core_symbols.append(symbol)

            if len(data_list) < 100:
                break
            current_page += 1

    print(f"-> Đã quét xong danh mục. Tổng cộng: {len(core_symbols)} mã.")
    return core_symbols


# --- CÁC HÀM XỬ LÝ SỰ KIỆN (CALLBACK HANDLERS) ---
# Tại đây, dữ liệu nhận về bạn có thể viết thêm code đẩy vào Redis hoặc DB của bạn


def get_time(received_at_ts):
    if received_at_ts:
        return datetime.fromtimestamp(received_at_ts).strftime("%H:%M:%S.%f")[
            :-3
        ]
    return "N/A"
def handle_expected_price(data: ExpectedPrice):
    print(f"[{get_time(data.receivedAt)}]  EXPECTED PRICE: {data}")
def handle_foreign_trading(data: ForeignInvestor):
    print(f"[{get_time(data.receivedAt)}] 🔴 FOREIGN TRADING: {data}")
def handle_market_index(data: MarketIndex):
    print(f"[{get_time(data.receivedAt)}] 📈 MARKET INDEX: {data}")
def handle_ohlc_closed(data: Ohlc):
    print(f"[{get_time(data.receivedAt)}] ⏱️ OHLC CLOSED: {data}")
def handle_ohlc(data: Ohlc):
    print(f"[{get_time(data.receivedAt)}]  OHLC LIVE: {data}")
def handle_quote(data: Quote):
    print(f"[{get_time(data.receivedAt)}] ⚡ QUOTE (ORDERBOOK): {data}")
def handle_security_definition(data: SecurityDefinition):
    print(f"[{get_time(data.receivedAt)}] ⚙️ SEC DEF: {data}")
def handle_trade_extra(data: TradeExtra):
    print(f"[{get_time(data.receivedAt)}] 💎 TRADE EXTRA: {data}")
def handle_trade(data: Trade):
    print(f"[{get_time(data.receivedAt)}] 💵 TRADE (MATCHED): {data}")


# --- LUỒNG CHÍNH ---
async def main():
    # 1. Lấy danh sách ~700 mã đã lọc sạch rác
    symbols = get_core_symbols()
    if not symbols:
        print("Không có mã nào để subscribe. Dừng!")
        return

    client = TradingClient(
        api_key=DNSE_API_KEY,
        api_secret=DNSE_API_SECRET,
        base_url=DNSE_WS_URL,
        encoding=ENCODING,
    )

    print("\nConnecting to WebSocket gateway...")
    await client.connect()
    print(f"Connected! Session ID: {client._session_id}\n")

    print("--- ĐANG ĐĂNG KÝ TẤT CẢ CÁC KÊNH DỮ LIỆU TRÊN 1 KẾT NỐI TỔNG ---")

    # 3. Kích hoạt toàn bộ các kênh đăng ký nối tiếp nhau
    await client.subscribe_expected_price(
        symbols,
        on_expected_price=handle_expected_price,
        encoding=ENCODING,
        board_id=BOARD_ID,
    )
    await client.subscribe_foreign_trading(
        symbols,
        on_trade=handle_foreign_trading,
        encoding=ENCODING,
        board_id=BOARD_ID,
    )
    await client.subscribe_quotes(
        symbols, on_quote=handle_quote, encoding=ENCODING, board_id=BOARD_ID
    )
    await client.subscribe_sec_def(
        symbols,
        on_sec_def=handle_security_definition,
        encoding=ENCODING,
        board_id=BOARD_ID,
    )
    await client.subscribe_trade_extra(
        symbols,
        on_trade_extra=handle_trade_extra,
        encoding=ENCODING,
        board_id=BOARD_ID,
    )
    await client.subscribe_trades(
        symbols, on_trade=handle_trade, encoding=ENCODING, board_id=BOARD_ID
    )

    # Riêng nến OHLC cần thêm tham số resolution (ví dụ: chốt nến 1 phút)
    await client.subscribe_ohlc_closed(
        symbols, resolution="1", on_ohlc=handle_ohlc_closed, encoding=ENCODING
    )
    await client.subscribe_ohlc(
        symbols, resolution="1", on_ohlc=handle_ohlc, encoding=ENCODING
    )

    # Chỉ số thị trường (Market Index) nhận vào chuỗi tên sàn chứ không nhận mảng cổ phiếu
    await client.subscribe_market_index(
        market_index="HNX", on_market_index=handle_market_index, encoding=ENCODING
    )
    await client.subscribe_market_index(
        market_index="HOSE", on_market_index=handle_market_index, encoding=ENCODING
    )

    print("\n[OK] Đã bật toàn bộ các kênh thành công. Đang hứng luồng dữ liệu...")

    # Giữ luồng chạy vô hạn (ở đây để tạm 8 tiếng tương đương 1 phiên giao dịch)
    try:
        await asyncio.sleep(60 * 60 * 8)
    except KeyboardInterrupt:
        print("\nĐang ngắt kết nối an toàn...")
    finally:
        await client.disconnect()
        print("Disconnected!")


if __name__ == "__main__":
    asyncio.run(main())