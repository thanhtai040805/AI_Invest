"""SAG Connector Adapter (FastMCP & REST Bridge to d:/AIInvest/SAG)

Module này chịu trách nhiệm:
1. Kết nối an toàn sang phân hệ SAG (Smart Analytics & Graph).
2. Thực hiện truy vấn RAG Moat AI (5 trụ cột + bằng chứng trích dẫn).
3. Thực hiện truy vấn Đồ thị thực thể & sở hữu chéo GIL (Graph Intelligence Layer).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Optional
import httpx

logger = logging.getLogger(__name__)


class SAGConnector:
    def __init__(self, api_base: Optional[str] = None):
        self.api_base = api_base or os.getenv("SAG_API_BASE", "http://localhost:8000/api/v1")

    async def get_moat_assessment(self, ticker: str, sector: str = "general") -> Dict[str, Any]:
        """Truy vấn Dịch vụ RAG Moat AI từ SAG Backend.
        
        Rubric 100đ:
        - Awareness (40đ): Ban lãnh đạo nhắc đến lợi thế cạnh tranh cốt lõi.
        - Action (40đ): Hành động CapEx/R&D phát triển Moat.
        - Intangible (20đ): Tài sản vô hình, thương hiệu, giấy phép.
        Kill-switch: Bắt buộc có trích dẫn (evidence_quote).
        """
        prompt = f"""Hãy đánh giá Lợi thế cạnh tranh (Economic Moat) của mã {ticker.upper()} thuộc ngành {sector}.
Chấm điểm theo Rubric 100 điểm:
- Awareness (40đ): Ban lãnh đạo có nhắc đích danh đến lợi thế cạnh tranh trong tài liệu không?
- Action (40đ): Doanh nghiệp có hành động (CapEx, R&D, mở rộng) để củng cố con hào này không?
- Intangible (20đ): Có tài sản vô hình (Thương hiệu, bản quyền, vị trí độc tôn) không?

LƯU Ý QUAN TRỌNG:
Bắt buộc phải trích dẫn nguyên văn (evidence_quote) một đoạn trong tài liệu để chứng minh.
Nếu không tìm thấy bất kỳ bằng chứng nào, điểm Moat tự động bằng 0.

Trả về kết quả dưới định dạng JSON với các keys:
- "moat_score" (số từ 0 đến 100)
- "intangibles_score" (số từ 0 đến 100)
- "switching_costs_score" (số từ 0 đến 100)
- "network_effect_score" (số từ 0 đến 100)
- "cost_advantage_score" (số từ 0 đến 100)
- "efficient_scale_score" (số từ 0 đến 100)
- "evidence_quote" (chuỗi trích dẫn nguyên văn)
- "multiplier" (0.75 nếu moat_score=0; 1.0 nếu <=50; 1.2 nếu >50)
"""
        payload = {
            "query": prompt,
            "filter": {"ticker": ticker.upper()},
            "stream": False,
        }

        default_result = {
            "ticker": ticker.upper(),
            "moat_score": 0.0,
            "intangibles_score": 0.0,
            "switching_costs_score": 0.0,
            "network_effect_score": 0.0,
            "cost_advantage_score": 0.0,
            "efficient_scale_score": 0.0,
            "evidence_quote": "Không tìm thấy bằng chứng trong kho SAG.",
            "multiplier": 0.75,
            "status": "FALLBACK",
        }

        try:
            timeout_config = httpx.Timeout(connect=1.0, read=5.0, write=5.0, pool=2.0)
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                res = await client.post(f"{self.api_base}/generation", json=payload)
                if res.status_code == 200:
                    data = res.json()
                    moat_data = data.get("response", {})
                    if isinstance(moat_data, dict) and "moat_score" in moat_data:
                        moat_data["ticker"] = ticker.upper()
                        return moat_data

                    # Parse JSON từ response text
                    text_resp = data.get("text", "")
                    match = re.search(r"\{.*\}", text_resp, re.DOTALL)
                    if match:
                        parsed = json.loads(match.group(0))
                        parsed["ticker"] = ticker.upper()
                        return parsed
        except Exception as e:
            logger.warning(f"Không thể kết nối SAG API để lấy Moat cho {ticker}: {e}")

        return default_result

    async def get_gil_relationships(
        self, ticker: str, equity_vnd: float = 0.0, source_id: str | None = None
    ) -> Dict[str, Any]:
        """Truy vấn đồ thị sở hữu chéo và rủi ro quan hệ bên liên quan (GIL) từ SAG."""
        ticker_clean = ticker.upper().strip()
        target_source = source_id or f"source_{ticker_clean.lower()}"
        try:
            timeout_config = httpx.Timeout(connect=1.0, read=10.0, write=5.0, pool=2.0)
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                res = await client.get(
                    f"{self.api_base}/gil/sources/{target_source}",
                    params={"equity_vnd": equity_vnd},
                )
                if res.status_code == 200:
                    return res.json()
        except Exception as e:
            logger.warning(f"Lỗi truy vấn GIL Graph từ SAG cho {ticker}: {e}")

        return {
            "ticker": ticker_clean,
            "gil_flag": "DATA_ERROR",
            "risk_level": "UNKNOWN",
            "rpt_ratio": 0.0,
            "total_rpt_exposure_vnd": 0.0,
            "equity_vnd": equity_vnd,
            "cycles_detected": 0,
            "cycle_paths": [],
            "reasons": ["Chưa kết nối được SAG FastMCP hoặc chưa có dữ liệu đồ thị sở hữu chéo. Gán cờ DATA_ERROR bảo vệ rủi ro theo IOS v5.1."],
            "status": "FALLBACK",
        }

    async def ingest_bctc_document(
        self,
        ticker: str,
        title: str,
        text_content: str,
        doc_role: str = "LATEST_QUARTER",
        is_active: bool = True,
        fiscal_year: Optional[int] = None,
        fiscal_quarter: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Bắn nội dung Markdown BCTC đã cắt tỉa sang SAG Backend qua endpoint by-ticker.
        
        SAG sẽ tự động:
        1. Tạo Source BCTC_{TICKER} nếu chưa có.
        2. Lưu Document và tự động lưu kho (ARCHIVED) quý cũ nếu doc_role='LATEST_QUARTER'.
        3. Kích hoạt bóc tách Graph và Vector Indexing.
        """
        ticker_clean = ticker.upper().strip()
        payload = {
            "title": title,
            "text": text_content,
            "doc_role": doc_role,
            "is_active": is_active,
            "fiscal_year": fiscal_year,
            "fiscal_quarter": fiscal_quarter,
        }

        try:
            timeout_config = httpx.Timeout(connect=2.0, read=30.0, write=10.0, pool=2.0)
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                res = await client.post(
                    f"{self.api_base}/sources/by-ticker/{ticker_clean}/documents/ingest",
                    json=payload,
                )
                if res.status_code in (200, 201):
                    return res.json()
                logger.warning(f"SAG ingest trả về mã {res.status_code}: {res.text}")
        except Exception as e:
            logger.error(f"Lỗi khi gửi tài liệu BCTC của {ticker_clean} sang SAG: {e}")

        return {
            "status": "FAILED",
            "ticker": ticker_clean,
            "title": title,
            "doc_role": doc_role,
            "error": "Không thể kết nối hoặc nạp tài liệu vào SAG API",
        }

    async def upload_bctc_pdf(
        self,
        ticker: str,
        pdf_bytes: bytes,
        filename: str = "bctc.pdf",
        doc_role: str = "LATEST_QUARTER",
        is_active: bool = True,
        fiscal_year: Optional[int] = None,
        fiscal_quarter: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Tải file PDF thực tế lên SAG Backend để kích hoạt MinerU OCR & Tree building."""
        ticker_clean = ticker.upper().strip()
        data = {
            "doc_role": doc_role,
            "is_active": str(is_active).lower(),
        }
        if fiscal_year is not None:
            data["fiscal_year"] = str(fiscal_year)
        if fiscal_quarter is not None:
            data["fiscal_quarter"] = str(fiscal_quarter)

        files = {
            "file": (filename, pdf_bytes, "application/pdf")
        }

        try:
            timeout_config = httpx.Timeout(connect=5.0, read=60.0, write=30.0, pool=5.0)
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                res = await client.post(
                    f"{self.api_base}/sources/by-ticker/{ticker_clean}/documents/upload",
                    data=data,
                    files=files,
                )
                if res.status_code in (200, 201):
                    return res.json()
                logger.warning(f"SAG PDF upload trả về mã {res.status_code}: {res.text}")
        except Exception as e:
            logger.error(f"Lỗi khi upload PDF của {ticker_clean} sang SAG: {e}")

        return {
            "status": "FAILED",
            "ticker": ticker_clean,
            "filename": filename,
            "doc_role": doc_role,
            "error": "Không thể upload file PDF sang SAG API",
        }

    async def get_document_status(self, source_id: str, document_id: str) -> Optional[Dict[str, Any]]:
        """Truy vấn trạng thái Document từ SAG API qua HTTP."""
        try:
            timeout_config = httpx.Timeout(connect=2.0, read=10.0, write=5.0, pool=2.0)
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                res = await client.get(f"{self.api_base}/sources/{source_id}/documents/{document_id}")
                if res.status_code == 200:
                    return res.json()
        except Exception as e:
            logger.debug(f"Lỗi khi kiểm tra document status từ SAG: {e}")
        return None

    async def get_document_parsed_markdown(self, source_id: str, document_id: str) -> Optional[str]:
        """Tải toàn bộ nội dung Markdown sau khi OCR & parsing hoàn tất từ SAG API."""
        try:
            timeout_config = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                res = await client.get(f"{self.api_base}/sources/{source_id}/documents/{document_id}/parsed")
                if res.status_code == 200:
                    return res.text
        except Exception as e:
            logger.warning(f"Lỗi khi tải parsed markdown từ SAG ({document_id}): {e}")
        return None


sag_connector = SAGConnector()


