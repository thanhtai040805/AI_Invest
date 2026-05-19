#!/usr/bin/env python3
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app.config import get_dnse_client


def get_stocks_by_market(client, market_id):
    """Hàm phụ dùng để quét toàn bộ cổ phiếu của một sàn cụ thể"""
    market_instruments = []
    current_page = 1
    limit_per_page = 100

    print(f"-> Đang quét dữ liệu sàn: {market_id}...")

    while True:
        status, body = client.get_instruments(
            symbol="",
            market_id=market_id,  # Lọc theo sàn được truyền vào (STO hoặc HNX)
            security_group_id="ST",  # Chỉ lấy Cổ phiếu (Stock)
            index_name="",
            limit=limit_per_page,
            page=current_page,
            dry_run=False,
        )

        if status != 200:
            print(f"   Lỗi khi gọi API tại sàn {market_id}, trang {current_page}")
            break
        try:
            parsed_body = json.loads(body) if isinstance(body, str) else body
        except Exception as e:
            print(f"   Lỗi giải mã JSON: {e}")
            break

        data_list = (
            parsed_body
            if isinstance(parsed_body, list)
            else parsed_body.get("data", [])
        )

        if not data_list:
            break

        market_instruments.extend(data_list)

        if len(data_list) < limit_per_page:
            break

        current_page += 1

    print(f"   Xong! Tìm thấy {len(market_instruments)} mã thuộc sàn {market_id}.")
    return market_instruments


def main():
    client = get_dnse_client()
    print("=== BẮT ĐẦU QUÉT CỔ PHIẾU HAI SÀN CHỦ LỰC (HOSE & HNX) ===")
    hose_stocks = get_stocks_by_market(client, market_id="STO")
    hnx_stocks = get_stocks_by_market(client, market_id="HNX")

    all_core_stocks = hose_stocks + hnx_stocks

    all_symbols = [item.get("symbol") for item in all_core_stocks if item.get("symbol")]
    
    if all_symbols:
        print(all_symbols)
        print(f"Mảng danh sách để truyền vào WebSocket có: {len(all_symbols)} mã.")


if __name__ == "__main__":
    main()