#!/usr/bin/env python3
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app.config import get_dnse_client


def main():
    client = get_dnse_client()

    all_instruments = []
    current_page = 1
    limit_per_page = 100

    print("Đang bắt đầu quét toàn bộ danh sách mã chứng khoán...")

    while True:
        print(f"Đang tải dữ liệu trang {current_page}...")

        status, body = client.get_instruments(
            symbol="",
            market_id="",
            security_group_id="ST",
            index_name="",
            limit=limit_per_page,
            page=current_page,
            dry_run=False,
        )

        if status != 200:
            print(f"Lỗi khi gọi API tại trang {current_page}, Status code: {status}")
            break

        # 2. SỬA ĐOẠN NÀY: Ép kiểu dữ liệu từ chuỗi str sang Dictionary/List của Python
        try:
            if isinstance(body, str):
                parsed_body = json.loads(
                    body
                )  # Giải mã chuỗi JSON thành Dict/List
            else:
                parsed_body = body
        except Exception as e:
            print(f"Không thể giải mã JSON từ Server: {e}")
            print(f"Nội dung thô nhận được: {body}")
            break

        # 3. Trích xuất danh sách mảng dựa trên dữ liệu đã parse
        data_list = (
            parsed_body
            if isinstance(parsed_body, list)
            else parsed_body.get("data", [])
        )

        if not data_list:
            print("Đã quét xong toàn bộ các trang dữ liệu!")
            break

        all_instruments.extend(data_list)

        if len(data_list) < limit_per_page:
            print("Đã chạm tới trang cuối cùng.")
            break

        current_page += 1

    print("-" * 50)
    print(f"Tổng số mã cổ phiếu/chứng quyền thu thập được: {len(all_instruments)}")

    if all_instruments:
        print("Ví dụ dữ liệu 2 mã đầu tiên:")
        print(all_instruments[:2])


if __name__ == "__main__":
    main()