"""Script tự động hóa 100% nạp tài liệu BCTC vào SAG Engine cho Pipeline sản xuất.

Tự động hóa hoàn toàn:
1. Đảm bảo Nguồn dữ liệu (Source) theo Ticker.
2. Upload tệp BCTC vào SAG Engine API.
3. Kích hoạt toàn bộ Pipeline làm sạch (markdown_noise_cleaner.py), Chunking cấu trúc (heading_strict),
   và Trích xuất Đồ thị Tri thức (Entities & Events).
4. Giám sát tiến độ realtime, phân loại lỗi chuẩn (Error Taxonomy: layer, stage) và tính toán chi phí LLM.
5. Tự động kích hoạt cập nhật Tổng quan Đồ thị Vũ trụ (Universe Overview Index).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

# Cấu hình cổng API mặc định của SAG Backend
DEFAULT_API_BASE = "http://localhost:8000/api/v1"


class SAGIngestPipeline:
    def __init__(self, api_base: str = DEFAULT_API_BASE, timeout: float = 120.0) -> None:
        self.api_base = api_base.rstrip("/")
        self.client = httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0))

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> SAGIngestPipeline:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def check_health(self) -> dict[str, Any]:
        """Kiểm tra trạng thái sẵn sàng của SAG Service."""
        try:
            res = self.client.get(f"{self.api_base}/system/health")
            if res.status_code == 200:
                return res.json()
        except Exception as err:
            raise RuntimeError(
                f"Không thể kết nối tới SAG Service tại {self.api_base}. "
                "Hãy đảm bảo Uvicorn đã chạy (uvicorn sag_api.main:app --port 8000)."
            ) from err
        return {}

    def get_or_create_source(self, ticker: str, description: str | None = None) -> dict[str, Any]:
        """Lấy hoặc tạo Nguồn tri thức (Source) riêng cho từng Ticker doanh nghiệp."""
        source_name = f"BCTC_{ticker.upper().strip()}"
        res = self.client.get(f"{self.api_base}/sources")
        if res.status_code == 200:
            sources = res.json()
            for s in sources:
                if s.get("name") == source_name:
                    return s

        # Tạo mới nếu chưa tồn tại
        create_payload = {
            "name": source_name,
            "connector_kind": "upload",
            "description": description or f"Nguồn tài liệu BCTC hợp nhất cho mã cổ phiếu {ticker.upper()}",
        }
        res_create = self.client.post(f"{self.api_base}/sources", json=create_payload)
        if res_create.status_code != 201:
            raise RuntimeError(f"Tạo Nguồn tri thức thất bại: {res_create.text}")
        return res_create.json()

    def upload_document(self, source_id: str, file_path: Path) -> dict[str, Any]:
        """Đẩy tệp BCTC vào SAG Engine để bắt đầu Pipeline làm sạch & trích xuất."""
        if not file_path.exists():
            raise FileNotFoundError(f"Tệp không tồn tại: {file_path}")

        mime_type = "text/markdown" if file_path.suffix.lower() in {".md", ".markdown"} else "application/octet-stream"
        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f, mime_type)}
            res = self.client.post(f"{self.api_base}/sources/{source_id}/documents", files=files)

        if res.status_code != 201:
            raise RuntimeError(f"Tải tài liệu lên SAG thất bại: {res.text}")
        return res.json()

    def poll_until_ready(self, source_id: str, document_id: str, poll_interval: float = 2.0) -> dict[str, Any]:
        """Giám sát tiến độ realtime cho tới khi trích xuất hoàn tất 100%."""
        print(f"\n⚡ Bắt đầu giám sát Pipeline xử lý tài liệu (Doc ID: {document_id})...")
        start_time = time.time()
        last_progress = -1

        while True:
            res = self.client.get(f"{self.api_base}/sources/{source_id}/documents/{document_id}")
            if res.status_code != 200:
                raise RuntimeError(f"Không thể kiểm tra trạng thái tài liệu: {res.text}")

            doc = res.json()
            status = doc.get("status")
            progress = doc.get("progress", 0)

            if progress != last_progress:
                elapsed = time.time() - start_time
                print(f"  [Progress {progress:3d}%] Status: {status:<12} | Elapsed: {elapsed:.1f}s")
                last_progress = progress

            if status == "READY":
                elapsed_total = time.time() - start_time
                print("\n========================================================")
                print(f"✅ XỬ LÝ HOÀN TẤT 100%! (Tổng thời gian: {elapsed_total:.2f}s)")
                print(f"  • Số Chunks (Đã cắt theo Heading): {doc.get('chunk_count', 0)}")
                print(f"  • Số Events (Đã bóc tách vào Graph): {doc.get('event_count', 0)}")
                print(f"  • Tổng Tokens tiêu thụ: {doc.get('token_usage', 0):,}")
                print("========================================================\n")
                return doc

            if status == "FAILED":
                error_msg = doc.get("error", "Lỗi không xác định")
                error_layer = doc.get("error_layer", "UNKNOWN")
                error_stage = doc.get("error_stage", "UNKNOWN")
                raise RuntimeError(
                    f"\n❌ Xử lý tài liệu thất bại!\n"
                    f"  • Error Layer: {error_layer}\n"
                    f"  • Error Stage: {error_stage}\n"
                    f"  • Lý do: {error_msg}"
                )

            time.sleep(poll_interval)

    def trigger_universe_index(self, user_id: str = "default") -> dict[str, Any]:
        """Cập nhật lại Tổng quan Đồ thị Vũ trụ (Universe Overview) sau khi nạp xong."""
        try:
            res = self.client.post(f"{self.api_base}/universe/rebuild", json={"user_id": user_id})
            if res.status_code in {200, 201, 202}:
                print("🌐 Đã tự động kích hoạt làm mới Tổng quan Đồ thị Tri thức (Universe Overview).")
                return res.json()
        except Exception:
            # Universe rebuild là tùy chọn phụ, không block pipeline chính
            pass
        return {}

    def run_full_pipeline(self, file_path: str | Path, ticker: str) -> dict[str, Any]:
        """Chạy toàn bộ Pipeline 100% tự động từ A-Z."""
        path = Path(file_path)
        print("========================================================")
        print(f"🚀 KÍCH HOẠT SAG AUTOMATED PIPELINE FOR {ticker.upper()}")
        print(f"📄 Tệp BCTC: {path.resolve()}")
        print("========================================================")

        # 1. Health check
        self.check_health()

        # 2. Source provisioning
        source = self.get_or_create_source(ticker)
        source_id = source["id"]

        # 3. Document ingestion & upload
        document = self.upload_document(source_id, path)
        doc_id = document["id"]

        # 4. Polling & Monitoring
        result = self.poll_until_ready(source_id, doc_id)

        # 5. Refresh Universe Graph Overview
        self.trigger_universe_index()

        return result


def main() -> None:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Tự động hóa nạp BCTC vào SAG Engine qua REST API")
    parser.add_argument("--file", "-f", required=True, help="Đường dẫn tới tệp BCTC (.md, .pdf, .docx)")
    parser.add_argument("--ticker", "-t", required=True, help="Mã cổ phiếu (Vd: HPG, MBB, VIC)")
    parser.add_argument("--api", default=DEFAULT_API_BASE, help="URL SAG API Base (Mặc định: http://localhost:8000/api/v1)")

    args = parser.parse_args()

    with SAGIngestPipeline(api_base=args.api) as pipeline:
        try:
            pipeline.run_full_pipeline(args.file, args.ticker)
        except Exception as error:
            print(f"\n❌ LỖI PIPELINE: {error}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
