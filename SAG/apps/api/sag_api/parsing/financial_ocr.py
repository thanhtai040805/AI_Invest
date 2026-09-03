"""Financial OCR Client trong Phân hệ SAG.

Sử dụng MinerU 100% (OpenDataLab API v4 - Precision Mode vi):
- Toàn bộ việc nhận diện chữ, công thức và bảng biểu BCTC đều do MinerU xử lý.
- Tự động chạy qua Clean Pipeline:
    1. Xóa sạch 100% ảnh và URL hình ảnh (![image](...)).
    2. Xóa con dấu mờ kiểm toán và mã biểu mẫu kế toán (B09-DN/HN, C.T.T.N.H.H...).
    3. Chuẩn hóa bảng HTML <table> sang bảng Markdown chuẩn GFM tối ưu cho RAG/Graph.
- Không can thiệp bất kỳ bước phân loại hay cắt tỉa trang nào trong phân hệ SAG.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any, Optional, Union

from sag_api.core.config import Settings
from sag_api.core.logging import get_logger
from sag_api.parsing.markdown_noise_cleaner import clean_markdown
from sag_api.parsing.mineru import MinerUClient

log = get_logger("parsing.financial_ocr")


class FinancialOcrClient:
    """Client Financial OCR sử dụng 100% MinerU Cloud Precision Mode cho BCTC."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or Settings()
        self.mineru_client = MinerUClient(self.settings)
        log.info(
            "FinancialOcrClient initialized with 100%% MinerU (base_url=%s, mode=%s, lang=%s)",
            self.settings.mineru_base_url,
            self.settings.mineru_mode,
            self.settings.mineru_language,
        )

    @property
    def is_available(self) -> bool:
        """Kiểm tra xem MinerU API Key đã được cấu hình hay chưa."""
        return bool(self.settings.mineru_api_key and self.settings.mineru_api_key.strip())

    async def extract_pdf_file(self, pdf_path: Union[str, Path]) -> str:
        """Trích xuất Markdown sạch 100% từ file PDF qua MinerU."""
        path = Path(pdf_path)
        if not path.is_file():
            raise FileNotFoundError(f"Không tìm thấy file PDF: {path}")

        log.info("Sending %s to MinerU Cloud OCR (Precision vi)...", path.name)
        # 1. Gọi MinerU OCR 100%
        raw_markdown = await self.mineru_client.parse(str(path))

        # 2. Làm sạch noise: xóa ảnh, lọc con dấu kiểm toán, chuẩn hóa bảng HTML -> Markdown table
        clean_markdown_text, _ = clean_markdown(raw_markdown)
        log.info(
            "MinerU OCR completed for %s (Raw: %d chars -> Clean: %d chars)",
            path.name,
            len(raw_markdown),
            len(clean_markdown_text),
        )
        return clean_markdown_text

    async def extract_pdf_bytes(self, pdf_bytes: bytes, filename: str = "document.pdf") -> str:
        """Trích xuất Markdown sạch từ chuỗi bytes PDF qua MinerU."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = Path(tmp.name)

        try:
            return await self.extract_pdf_file(tmp_path)
        finally:
            try:
                if tmp_path.exists():
                    os.remove(tmp_path)
            except Exception as err:
                log.debug("Không thể xóa file tạm %s: %s", tmp_path, err)

    def extract_pdf_file_sync(self, pdf_path: Union[str, Path]) -> str:
        """Wrapper đồng bộ (synchronous) cho extract_pdf_file."""
        return asyncio.run(self.extract_pdf_file(pdf_path))

    def extract_pdf_bytes_sync(self, pdf_bytes: bytes, filename: str = "document.pdf") -> str:
        """Wrapper đồng bộ (synchronous) cho extract_pdf_bytes."""
        return asyncio.run(self.extract_pdf_bytes(pdf_bytes, filename=filename))
