"""Typed SAG v2 REST connector.

Module này chịu trách nhiệm:
Technical failures are surfaced as technical errors; only SAG itself may return
DATA_INSUFFICIENT after it has read the evidence state.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional
import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


class SAGConnector:
    def __init__(self, api_base: Optional[str] = None):
        cfg = get_settings()
        self.api_base = (api_base or cfg.sag_api_base or os.getenv("SAG_API_BASE", "http://localhost:8000/api/v2")).rstrip("/")
        self.service_token = cfg.sag_service_token or os.getenv("SAG_SERVICE_TOKEN", "")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.service_token}"} if self.service_token else {}

    @staticmethod
    def _technical_error(ticker: str, kind: str, message: str) -> Dict[str, Any]:
        return {
            "ticker": ticker.upper().strip(),
            "analysis_status": "TECHNICAL_ERROR",
            "assessment_status": "TECHNICAL_ERROR",
            "gil_flag": "DATA_INSUFFICIENT",
            "moat_score": None,
            "multiplier": None,
            "coverage_ratio": 0.0,
            "technical_error": {"kind": kind, "message": message},
            "reasons": [message],
        }

    async def get_moat_assessment(self, ticker: str, sector: str = "general") -> Dict[str, Any]:
        """Truy vấn MOAT v2 evidence-first từ SAG. Không sinh điểm khi dữ liệu thiếu."""
        ticker_clean = ticker.upper().strip()
        try:
            timeout_config = httpx.Timeout(connect=2.0, read=30.0, write=5.0, pool=2.0)
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                res = await client.get(
                    f"{self.api_base}/tickers/{ticker_clean}/assessments/moat",
                    headers=self._headers(),
                )
                if res.status_code == 200:
                    return res.json()
                if res.status_code in (401, 403):
                    return self._technical_error(ticker_clean, "AUTH", f"SAG auth failed: HTTP {res.status_code}")
                return self._technical_error(ticker_clean, "HTTP", f"SAG moat returned HTTP {res.status_code}")
        except Exception as e:
            logger.warning(f"Không thể kết nối SAG API để lấy Moat cho {ticker}: {e}")
            return self._technical_error(ticker_clean, "NETWORK", str(e))

    async def get_gil_relationships(
        self, ticker: str, equity_vnd: float = 0.0, source_id: str | None = None
    ) -> Dict[str, Any]:
        """Truy vấn đồ thị sở hữu chéo và rủi ro quan hệ bên liên quan (GIL) từ SAG."""
        ticker_clean = ticker.upper().strip()
        try:
            timeout_config = httpx.Timeout(connect=2.0, read=30.0, write=5.0, pool=2.0)
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                res = await client.get(
                    f"{self.api_base}/tickers/{ticker_clean}/assessments/gil",
                    headers=self._headers(),
                )
                if res.status_code == 200:
                    return res.json()
                if res.status_code in (401, 403):
                    return self._technical_error(ticker_clean, "AUTH", f"SAG auth failed: HTTP {res.status_code}")
                return self._technical_error(ticker_clean, "HTTP", f"SAG gil returned HTTP {res.status_code}")
        except Exception as e:
            logger.warning(f"Lỗi truy vấn GIL Graph từ SAG cho {ticker}: {e}")
            return self._technical_error(ticker_clean, "NETWORK", str(e))

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
        """Send canonical Markdown to SAG v2 by ticker."""
        ticker_clean = ticker.upper().strip()
        payload = {
            "title": title,
            "markdown": text_content,
            "doc_role": doc_role,
            "activate": is_active,
            "fiscal_year": fiscal_year,
            "fiscal_quarter": fiscal_quarter,
            "period_start": _period_start(fiscal_year, fiscal_quarter, doc_role),
            "period_end": _period_end(fiscal_year, fiscal_quarter),
        }

        try:
            # Inline SAG processing includes extraction and embeddings.  A normal
            # financial statement regularly exceeds a 30-second read window.
            timeout_config = httpx.Timeout(connect=5.0, read=600.0, write=30.0, pool=5.0)
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                res = await client.post(
                    f"{self.api_base}/tickers/{ticker_clean}/documents",
                    json=payload,
                    headers=self._headers(),
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
        ocr_only: bool = False,
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
        period_start = _period_start(fiscal_year, fiscal_quarter, doc_role)
        period_end = _period_end(fiscal_year, fiscal_quarter)
        if period_start:
            data["period_start"] = period_start
        if period_end:
            data["period_end"] = period_end
        if ocr_only:
            data["processing_mode"] = "OCR_ONLY"

        files = {
            "file": (filename, pdf_bytes, "application/pdf")
        }

        try:
            timeout_config = httpx.Timeout(connect=5.0, read=900.0, write=30.0, pool=5.0)
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                res = await client.post(
                    f"{self.api_base}/tickers/{ticker_clean}/documents/upload",
                    data=data,
                    files=files,
                    headers=self._headers(),
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

    async def upload_bctc_pdf_from_r2(
        self,
        ticker: str,
        object_uri: str,
        title: str,
        doc_role: str = "LATEST_QUARTER",
        is_active: bool = False,
        fiscal_year: Optional[int] = None,
        fiscal_quarter: Optional[int] = None,
        ocr_only: bool = True,
    ) -> Dict[str, Any]:
        """Ask SAG to read the PDF from R2, keeping PDF bytes off the VPS."""
        ticker_clean = ticker.upper().strip()
        payload: Dict[str, Any] = {
            "title": title,
            "object_uri": object_uri,
            "doc_role": doc_role,
            "activate": is_active,
            "processing_mode": "OCR_ONLY" if ocr_only else "FULL",
        }
        if fiscal_year is not None:
            payload["fiscal_year"] = fiscal_year
        if fiscal_quarter is not None:
            payload["fiscal_quarter"] = fiscal_quarter
        period_start = _period_start(fiscal_year, fiscal_quarter, doc_role)
        period_end = _period_end(fiscal_year, fiscal_quarter)
        if period_start:
            payload["period_start"] = period_start
        if period_end:
            payload["period_end"] = period_end
        timeout_config = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)
        retryable_statuses = {408, 425, 429, 500, 502, 503, 504}
        last_error = ""
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=timeout_config) as client:
                    res = await client.post(
                        f"{self.api_base}/tickers/{ticker_clean}/documents/from-object/submit",
                        json=payload,
                        headers=self._headers(),
                    )
                    if res.status_code == 202:
                        submit_result = res.json()
                        job_id = submit_result.get("job_id")
                        if not job_id:
                            last_error = "SAG không trả job_id cho OCR submit"
                        else:
                            # Keep the client alive for submission and polling.
                            # Polling after the context manager closes caused
                            # legacy R2 backlog jobs to fail with "client has
                            # been closed" before OCR was even observed.
                            deadline = asyncio.get_running_loop().time() + 1800.0
                            while asyncio.get_running_loop().time() < deadline:
                                status_res = await client.get(
                                    f"{self.api_base}/jobs/{job_id}",
                                    headers=self._headers(),
                                )
                                if status_res.status_code == 200:
                                    job_status = status_res.json()
                                    if job_status.get("status") == "succeeded" and job_status.get("document_id"):
                                        doc_res = await client.get(
                                            f"{self.api_base}/documents/{job_status['document_id']}",
                                            headers=self._headers(),
                                        )
                                        if doc_res.status_code == 200:
                                            return doc_res.json()
                                    if job_status.get("status") in {"failed", "cancelled"}:
                                        last_error = f"SAG OCR job {job_id} {job_status.get('status')}: {job_status.get('error')}"
                                        break
                                await asyncio.sleep(5.0)
                    elif res.status_code in (200, 201):
                        return res.json()
                    if not last_error:
                        last_error = f"HTTP {res.status_code}: {res.text[:1000]}"
                    logger.warning("SAG R2 PDF ingest %s attempt=%d: %s", ticker_clean, attempt + 1, last_error)
                    if res.status_code not in retryable_statuses or attempt == 2:
                        break
            except (httpx.TimeoutException, httpx.TransportError) as error:
                last_error = f"{type(error).__name__}: {error}"
                logger.warning("SAG R2 PDF transport %s attempt=%d: %s", ticker_clean, attempt + 1, last_error)
                if attempt == 2:
                    break
            await asyncio.sleep(2 ** attempt)
        return {
            "status": "FAILED",
            "ticker": ticker_clean,
            "title": title,
            "doc_role": doc_role,
            "error": f"Không thể để SAG đọc PDF từ R2: {last_error or 'unknown error'}",
        }

    async def upload_bctc_pdf_from_url(
        self,
        ticker: str,
        source_url: str,
        title: str,
        doc_role: str = "LATEST_QUARTER",
        is_active: bool = False,
        fiscal_year: Optional[int] = None,
        fiscal_quarter: Optional[int] = None,
        ocr_only: bool = True,
    ) -> Dict[str, Any]:
        """Queue SAG OCR from the source URL; never stage the PDF in R2."""
        ticker_clean = ticker.upper().strip()
        payload: Dict[str, Any] = {
            "title": title,
            "source_url": source_url,
            "doc_role": doc_role,
            "activate": is_active,
            "processing_mode": "OCR_ONLY" if ocr_only else "FULL",
        }
        if fiscal_year is not None:
            payload["fiscal_year"] = fiscal_year
        if fiscal_quarter is not None:
            payload["fiscal_quarter"] = fiscal_quarter
        period_start = _period_start(fiscal_year, fiscal_quarter, doc_role)
        period_end = _period_end(fiscal_year, fiscal_quarter)
        if period_start:
            payload["period_start"] = period_start
        if period_end:
            payload["period_end"] = period_end

        timeout_config = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
        retryable_statuses = {408, 425, 429, 500, 502, 503, 504}
        last_error = ""
        async with httpx.AsyncClient(timeout=timeout_config) as client:
            for attempt in range(3):
                try:
                    res = await client.post(
                        f"{self.api_base}/tickers/{ticker_clean}/documents/from-url/submit",
                        json=payload,
                        headers=self._headers(),
                    )
                    if res.status_code == 202:
                        submit_result = res.json()
                        job_id = submit_result.get("job_id")
                        if not job_id:
                            last_error = "SAG không trả job_id cho source URL OCR"
                        else:
                            deadline = asyncio.get_running_loop().time() + 1800.0
                            while asyncio.get_running_loop().time() < deadline:
                                status_res = await client.get(
                                    f"{self.api_base}/jobs/{job_id}", headers=self._headers()
                                )
                                if status_res.status_code == 200:
                                    job_status = status_res.json()
                                    if job_status.get("status") == "succeeded" and job_status.get("document_id"):
                                        doc_res = await client.get(
                                            f"{self.api_base}/documents/{job_status['document_id']}",
                                            headers=self._headers(),
                                        )
                                        if doc_res.status_code == 200:
                                            return doc_res.json()
                                    if job_status.get("status") in {"failed", "cancelled"}:
                                        last_error = (
                                            f"SAG URL OCR job {job_id} {job_status.get('status')}: "
                                            f"{job_status.get('error')}"
                                        )
                                        break
                                await asyncio.sleep(5.0)
                    elif res.status_code in (200, 201):
                        return res.json()
                    if not last_error:
                        last_error = f"HTTP {res.status_code}: {res.text[:1000]}"
                    logger.warning("SAG source URL ingest %s attempt=%d: %s", ticker_clean, attempt + 1, last_error)
                    if res.status_code not in retryable_statuses or attempt == 2:
                        break
                except (httpx.TimeoutException, httpx.TransportError) as error:
                    last_error = f"{type(error).__name__}: {error}"
                    logger.warning("SAG source URL transport %s attempt=%d: %s", ticker_clean, attempt + 1, last_error)
                    if attempt == 2:
                        break
                await asyncio.sleep(2 ** attempt)
        return {
            "status": "FAILED",
            "ticker": ticker_clean,
            "title": title,
            "doc_role": doc_role,
            "error": f"Không thể để SAG đọc PDF từ URL: {last_error or 'unknown error'}",
        }

    async def get_document_status(self, source_id: str, document_id: str) -> Optional[Dict[str, Any]]:
        """Truy vấn trạng thái Document từ SAG API qua HTTP."""
        try:
            timeout_config = httpx.Timeout(connect=2.0, read=10.0, write=5.0, pool=2.0)
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                res = await client.get(f"{self.api_base}/documents/{document_id}", headers=self._headers())
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
                direct_res = await client.get(
                    f"{self.api_base}/documents/{document_id}/markdown",
                    headers=self._headers(),
                )
                if direct_res.status_code == 200:
                    return direct_res.text
                tree_res = await client.get(
                    f"{self.api_base}/documents/{document_id}/tree",
                    headers=self._headers(),
                )
                if tree_res.status_code != 200:
                    return None
                nodes = tree_res.json().get("nodes") or []
                root = next((node for node in nodes if node.get("parent_id") is None), None)
                if not root:
                    return None
                res = await client.get(
                    f"{self.api_base}/documents/{document_id}/nodes/{root.get('node_id')}/content",
                    headers=self._headers(),
                )
                if res.status_code == 200:
                    body = res.json()
                    return body.get("content") or res.text
        except Exception as e:
            logger.warning(f"Lỗi khi tải parsed markdown từ SAG ({document_id}): {e}")
        return None


sag_connector = SAGConnector()


def _period_end(fiscal_year: Optional[int], fiscal_quarter: Optional[int]) -> Optional[str]:
    if not fiscal_year:
        return None
    if fiscal_quarter in (1, 2, 3, 4):
        month_day = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[int(fiscal_quarter)]
        return f"{fiscal_year}-{month_day}"
    return f"{fiscal_year}-12-31"


def _period_start(
    fiscal_year: Optional[int], fiscal_quarter: Optional[int], doc_role: str
) -> Optional[str]:
    if not fiscal_year:
        return None
    if doc_role == "GOVERNANCE_REPORT":
        # Governance reports describe an accumulated period (H1 or full year).
        return f"{fiscal_year}-01-01"
    if fiscal_quarter in (1, 2, 3, 4):
        month_day = {1: "01-01", 2: "04-01", 3: "07-01", 4: "10-01"}[int(fiscal_quarter)]
        return f"{fiscal_year}-{month_day}"
    return f"{fiscal_year}-01-01"
