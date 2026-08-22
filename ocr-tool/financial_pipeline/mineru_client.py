import os
import json
import time
import urllib.request
import urllib.error
from typing import Optional, Dict, Any


class MinerUQuotaExceededError(Exception):
    """Ngoại lệ khi MinerU hết quota gói miễn phí hoặc chạm rào cản Rate Limit."""
    pass


class MinerUClient:
    """Client kết nối tới MinerU Cloud API (OpenDataLab) xử lý OCR & Layout Analysis."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base_url: str = "https://mineru.net/api/v4",
        timeout_seconds: int = 180
    ):
        self.api_key = api_key or os.getenv("MINERU_API_KEY", "")
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @property
    def is_configured(self) -> bool:
        """Kiểm tra xem API Key đã được cấu hình hay chưa."""
        return bool(self.api_key.strip())

    def extract_pdf_bytes(self, pdf_bytes: bytes, filename: str = "document.pdf") -> Optional[str]:
        """Gửi PDF bytes tới MinerU API và đợi kết quả Markdown.

        Raises:
            MinerUQuotaExceededError: Khi vượt quá quota/rate limit hoặc nhận lỗi HTTP 429/403.
            RuntimeError: Khi gặp lỗi hệ thống khác từ MinerU.
        """
        if not self.is_configured:
            raise MinerUQuotaExceededError("MINERU_API_KEY chưa được cấu hình.")

        # 1. Tạo task lấy URL upload hoặc gửi payload
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "AIInvest-FinancialOCR/1.0"
        }

        # Submit task qua MinerU API v4
        submit_url = f"{self.api_base_url}/extract/task"
        
        # Đóng gói multipart/form-data hoặc JSON upload
        boundary = f"----WebKitFormBoundary{int(time.time()*1000)}"
        body = []
        body.append(f"--{boundary}".encode("utf-8"))
        body.append(f'Content-Disposition: form-data; name="file"; filename="{filename}"'.encode("utf-8"))
        body.append(b"Content-Type: application/pdf\r\n")
        body.append(pdf_bytes)
        body.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        payload_bytes = b"\r\n".join(body)

        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"

        req = urllib.request.Request(submit_url, data=payload_bytes, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            if err.code in (429, 403, 402):
                raise MinerUQuotaExceededError(f"MinerU API Quota Limit/Rate Limit (HTTP {err.code}): {err.reason}")
            raise RuntimeError(f"Lỗi HTTP MinerU API ({err.code}): {err.reason}")
        except Exception as e:
            raise RuntimeError(f"Không thể kết nối MinerU API: {e}")

        # Kiểm tra mã lỗi từ JSON payload
        code = res_data.get("code") or res_data.get("status_code", 0)
        if code in (429, 403, 402) or "quota" in str(res_data).lower() or "limit" in str(res_data).lower():
            raise MinerUQuotaExceededError(f"MinerU API Quota Exceeded: {res_data.get('msg', 'Quota limit reached')}")

        task_id = res_data.get("data", {}).get("task_id") or res_data.get("task_id")
        if not task_id:
            # Nếu MinerU trả trực tiếp kết quả markdown
            markdown = res_data.get("data", {}).get("markdown") or res_data.get("markdown")
            if markdown:
                return str(markdown)
            raise RuntimeError(f"MinerU không trả về task_id hợp lệ: {res_data}")

        # 2. Polling task status
        return self._poll_task_result(task_id)

    def _poll_task_result(self, task_id: str) -> str:
        """Polling kết quả task từ MinerU API cho đến khi hoàn thành."""
        query_url = f"{self.api_base_url}/extract/task/{task_id}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "AIInvest-FinancialOCR/1.0"
        }

        start_time = time.time()
        while time.time() - start_time < self.timeout_seconds:
            req = urllib.request.Request(query_url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    res_data = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as err:
                if err.code in (429, 403, 402):
                    raise MinerUQuotaExceededError(f"MinerU Quota Exceeded during polling (HTTP {err.code})")
                raise RuntimeError(f"Polling HTTP Error ({err.code}): {err.reason}")

            state = (res_data.get("data", {}).get("state") or res_data.get("state", "")).lower()
            if state in ("done", "completed", "success"):
                markdown = res_data.get("data", {}).get("markdown") or res_data.get("markdown", "")
                if not markdown and "full_zip_url" in str(res_data):
                    # Tải zip / markdown nếu có URL
                    zip_url = res_data.get("data", {}).get("full_zip_url")
                    if zip_url:
                        markdown = self._download_markdown_from_url(zip_url)
                return markdown or ""
            elif state in ("failed", "error"):
                msg = res_data.get("msg") or res_data.get("error", "Unknown error")
                if "quota" in str(msg).lower() or "limit" in str(msg).lower():
                    raise MinerUQuotaExceededError(f"MinerU Task Failed - Quota Limit: {msg}")
                raise RuntimeError(f"MinerU Extraction Task Failed: {msg}")

            time.sleep(2.0)

        raise TimeoutError(f"MinerU Task {task_id} timed out after {self.timeout_seconds}s")

    def _download_markdown_from_url(self, url: str) -> str:
        """Tải dữ liệu từ URL kết quả (nếu MinerU trả về URL thay vì chuỗi markdown trực tiếp)."""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AIInvest-FinancialOCR/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                content = resp.read()
                return content.decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"[!] Warning: Không thể download markdown kết quả từ {url}: {e}")
            return ""
