"""Typed SAG v2 REST connector.

Module này chịu trách nhiệm:
Technical failures are surfaced as technical errors; only SAG itself may return
DATA_INSUFFICIENT after it has read the evidence state.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import zipfile
from typing import Any, Dict, Optional
from urllib.parse import urlsplit, urlunsplit
import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


def _cafef1_fallback_url(source_url: str) -> Optional[str]:
    parts = urlsplit(source_url)
    if parts.hostname and parts.hostname.lower() == "cafefnew.mediacdn.vn":
        return urlunsplit((parts.scheme, "cafef1.mediacdn.vn", parts.path, parts.query, parts.fragment))
    return None


def _is_not_found_error(result: Dict[str, Any]) -> bool:
    return "404" in str(result.get("error") or "")


def _can_use_local_source_fallback(error: str) -> bool:
    text = str(error or "").lower()
    return any(marker in text for marker in (
        "readtimeout",
        "timeout",
        "readerror",
        "remoteprotocolerror",
        "server disconnected",
        "http 401",
        "http 403",
        "zip",
        "source fetch failed",
        "parsing failed",
        "retry limit reached",
        "replace the file",
    ))


def _extract_pdf_candidates_from_zip(
    zip_path: str, doc_role: str, scope: str = "SEPARATE"
) -> list[tuple[str, str]]:
    """Extract PDFs in role/scope order; language is validated by OCR, not filename."""
    with zipfile.ZipFile(zip_path) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".pdf")]
        if not names:
            raise ValueError("ZIP archive contains no PDF files")
        role = str(doc_role or "").upper()
        scope_upper = str(scope or "SEPARATE").upper()

        def score(name: str) -> tuple[int, int]:
            lower = name.lower()
            value = 0
            if any(k in lower for k in ("giai_trinh", "giai-trinh", "giaitrinh", "bien_dong_ln", "explanation")):
                value -= 100
            if any(k in lower for k in ("tom_tat", "tomtat", "summary", "brief")):
                value -= 80

            if role == "GOVERNANCE_REPORT":
                if any(k in lower for k in ("quantri", "quan_tri", "governance")):
                    value += 100
                if any(k in lower for k in ("ban_day_du", "bandaydu", "full")):
                    value += 20
                if any(k in lower for k in ("report_on_corporate_governance", "bc_tinh_hinh_quan_tri")):
                    value += 50
                if "bc_" in lower or "bcthqt" in lower or "quan_tri" in lower:
                    value += 30

            if role in {"ANNUAL_BACKBONE", "LATEST_QUARTER"}:
                if any(k in lower for k in ("bctc", "financial", "interim", "statement", "baocaotaichinh")):
                    value += 100
                if scope_upper == "SEPARATE":
                    if any(k in lower for k in ("congtyme", "cong_ty_me", "rieng", "parent", "separate")):
                        value += 60
                    if any(k in lower for k in ("hopnhat", "hop_nhat", "consolidated")):
                        value -= 50
                elif scope_upper == "CONSOLIDATED":
                    if any(k in lower for k in ("hopnhat", "hop_nhat", "consolidated")):
                        value += 60
                    if any(k in lower for k in ("congtyme", "cong_ty_me", "rieng")):
                        value -= 50
                if any(k in lower for k in ("bctc", "baocaotaichinh", "soat_xet", "kiem_toan")):
                    value += 30

            return value, -len(name)

        candidates: list[tuple[str, str]] = []
        for selected in sorted(names, key=score, reverse=True):
            fd, out_path = tempfile.mkstemp(prefix="sag-zip-extracted-", suffix=".pdf")
            os.close(fd)
            with open(out_path, "wb") as out_f:
                out_f.write(archive.read(selected))
            candidates.append((out_path, selected))
        return candidates


class SAGConnector:
    def __init__(self, api_base: Optional[str] = None):
        cfg = get_settings()
        self.api_base = (api_base or cfg.sag_api_base or os.getenv("SAG_API_BASE", "http://localhost:8000/api/v2")).rstrip("/")
        self.service_token = cfg.sag_service_token or os.getenv("SAG_SERVICE_TOKEN", "")
        self._source_fallback_semaphore = asyncio.Semaphore(1)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.service_token}"} if self.service_token else {}

    @staticmethod
    def _technical_error(ticker: str, kind: str, message: str) -> Dict[str, Any]:
        return {
            "ticker": ticker.upper().strip(),
            "analysis_status": "TECHNICAL_ERROR",
            "assessment_status": "TECHNICAL_ERROR",
            "status": "FALLBACK",
            "gil_flag": "DATA_INSUFFICIENT",
            "quality_score": None,
            "coverage_ratio": 0.0,
            "technical_error": {"kind": kind, "message": message},
            "reasons": [message],
        }

    async def get_financial_quality_assessment(self, ticker: str, sector: str = "general") -> Dict[str, Any]:
        """Truy vấn Financial Quality evidence-first từ nguồn dữ liệu tài chính."""
        ticker_clean = ticker.upper().strip()
        try:
            timeout_config = httpx.Timeout(connect=2.0, read=30.0, write=5.0, pool=2.0)
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                res = await client.get(
                    f"{self.api_base}/tickers/{ticker_clean}/assessments/financial-quality",
                    headers=self._headers(),
                )
                if res.status_code == 200:
                    return res.json()
                if res.status_code in (401, 403):
                    return self._technical_error(ticker_clean, "AUTH", f"SAG auth failed: HTTP {res.status_code}")
                return self._technical_error(ticker_clean, "HTTP", f"Financial Quality returned HTTP {res.status_code}")
        except Exception as e:
            logger.warning(f"Không thể kết nối nguồn Financial Quality cho {ticker}: {e}")
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
            # The SAG extraction call may legitimately use the configured
            # 900-second LLM timeout, followed by embedding and persistence.
            # Keep the client alive longer than that server-side budget so a
            # slow but valid extraction is not reported as a transport error.
            timeout_config = httpx.Timeout(connect=5.0, read=1800.0, write=30.0, pool=5.0)
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
        cafef1_url = _cafef1_fallback_url(source_url)
        if cafef1_url:
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
                    follow_redirects=True,
                ) as probe:
                    if (await probe.head(source_url)).status_code == 404:
                        logger.warning("CafeF source returned 404; using cafef1 for %s", ticker_clean)
                        source_url = cafef1_url
            except httpx.HTTPError:
                pass
        if source_url.lower().split("?", 1)[0].endswith(".zip"):
            local_result = await self._upload_source_via_local_stream(
                ticker=ticker_clean,
                source_url=source_url,
                title=title,
                doc_role=doc_role,
                is_active=is_active,
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                ocr_only=ocr_only,
            )
            cafef1_url = _cafef1_fallback_url(source_url)
            if cafef1_url and _is_not_found_error(local_result):
                return await self.upload_bctc_pdf_from_url(
                    ticker=ticker_clean,
                    source_url=cafef1_url,
                    title=title,
                    doc_role=doc_role,
                    is_active=is_active,
                    fiscal_year=fiscal_year,
                    fiscal_quarter=fiscal_quarter,
                    ocr_only=ocr_only,
                )
            return local_result
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
        if _can_use_local_source_fallback(last_error):
            logger.warning("Direct source failed; using local stream fallback for %s: %s", ticker_clean, last_error)
            local_result = await self._upload_source_via_local_stream(
                ticker=ticker_clean,
                source_url=source_url,
                title=title,
                doc_role=doc_role,
                is_active=is_active,
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                ocr_only=ocr_only,
            )
            cafef1_url = _cafef1_fallback_url(source_url)
            if cafef1_url and _is_not_found_error(local_result):
                logger.warning("CafeF source returned 404; retrying cafef1 for %s", ticker_clean)
                return await self.upload_bctc_pdf_from_url(
                    ticker=ticker_clean,
                    source_url=cafef1_url,
                    title=title,
                    doc_role=doc_role,
                    is_active=is_active,
                    fiscal_year=fiscal_year,
                    fiscal_quarter=fiscal_quarter,
                    ocr_only=ocr_only,
                )
            return local_result
        cafef1_url = _cafef1_fallback_url(source_url)
        if cafef1_url and "404" in last_error:
            logger.warning("CafeF source returned 404; retrying cafef1 for %s", ticker_clean)
            return await self.upload_bctc_pdf_from_url(
                ticker=ticker_clean,
                source_url=cafef1_url,
                title=title,
                doc_role=doc_role,
                is_active=is_active,
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                ocr_only=ocr_only,
            )
        return {
            "status": "FAILED",
            "ticker": ticker_clean,
            "title": title,
            "doc_role": doc_role,
            "error": f"Không thể để SAG đọc PDF từ URL: {last_error or 'unknown error'}",
        }

    async def _upload_source_via_local_stream(
        self,
        *,
        ticker: str,
        source_url: str,
        title: str,
        doc_role: str,
        is_active: bool,
        fiscal_year: Optional[int],
        fiscal_quarter: Optional[int],
        ocr_only: bool,
    ) -> Dict[str, Any]:
        """Last-resort fetch: stream to one temp file (PDF or ZIP), extract if ZIP, upload to SAG, then delete."""
        async with self._source_fallback_semaphore:
            temp_path: Optional[str] = None
            extracted_pdf_paths: list[str] = []
            try:
                fd, temp_path = tempfile.mkstemp(prefix="sag-source-", suffix=".bin")
                os.close(fd)
                max_bytes = max(1, int(os.getenv("SAG_LOCAL_FALLBACK_MAX_MB", "250"))) * 1024 * 1024
                size = 0
                first_chunk = b""
                timeout = httpx.Timeout(connect=15.0, read=90.0, write=30.0, pool=15.0)
                headers = {
                    "User-Agent": "Mozilla/5.0 (compatible; AIInvest/1.0)",
                    "Referer": "https://finance.vietstock.vn/",
                    "Accept": "application/pdf,*/*;q=0.8",
                }
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                    async with client.stream("GET", source_url, headers=headers) as response:
                        response.raise_for_status()
                        with open(temp_path, "wb") as output:
                            async for chunk in response.aiter_bytes(1024 * 1024):
                                if not first_chunk:
                                    first_chunk = chunk[:8]
                                size += len(chunk)
                                if size > max_bytes:
                                    raise ValueError(f"File exceeds {max_bytes // 1024 // 1024} MB limit")
                                output.write(chunk)

                if first_chunk.startswith(b"PK\x03\x04"):
                    candidates = _extract_pdf_candidates_from_zip(
                        temp_path, doc_role, scope="SEPARATE"
                    )
                    last_result: Dict[str, Any] = {
                        "status": "FAILED",
                        "error": "ZIP candidates exhausted",
                    }
                    for extracted_pdf_path, selected_filename in candidates:
                        extracted_pdf_paths.append(extracted_pdf_path)
                        last_result = await self.upload_bctc_pdf_file(
                            ticker=ticker,
                            pdf_path=extracted_pdf_path,
                            filename=os.path.basename(selected_filename),
                            doc_role=doc_role,
                            is_active=is_active,
                            fiscal_year=fiscal_year,
                            fiscal_quarter=fiscal_quarter,
                            ocr_only=ocr_only,
                        )
                        if last_result and last_result.get("status") not in ("FAILED", "error"):
                            return last_result
                    return last_result
                elif first_chunk.startswith(b"%PDF"):
                    upload_target = temp_path
                    upload_filename = os.path.basename(temp_path) + ".pdf"
                else:
                    raise ValueError("Source did not return a valid PDF or ZIP archive")

                return await self.upload_bctc_pdf_file(
                    ticker=ticker,
                    pdf_path=upload_target,
                    filename=upload_filename,
                    doc_role=doc_role,
                    is_active=is_active,
                    fiscal_year=fiscal_year,
                    fiscal_quarter=fiscal_quarter,
                    ocr_only=ocr_only,
                )
            except Exception as error:
                return {
                    "status": "FAILED",
                    "ticker": ticker,
                    "title": title,
                    "doc_role": doc_role,
                    "error": f"Local source fallback failed: {type(error).__name__}: {error}",
                }
            finally:
                if temp_path:
                    try:
                        os.unlink(temp_path)
                    except FileNotFoundError:
                        pass
                for extracted_pdf_path in extracted_pdf_paths:
                    try:
                        os.unlink(extracted_pdf_path)
                    except FileNotFoundError:
                        pass

    async def upload_bctc_pdf_file(
        self,
        *,
        ticker: str,
        pdf_path: str,
        filename: str,
        doc_role: str,
        is_active: bool,
        fiscal_year: Optional[int],
        fiscal_quarter: Optional[int],
        ocr_only: bool,
    ) -> Dict[str, Any]:
        data: Dict[str, str] = {"doc_role": doc_role, "is_active": str(is_active).lower()}
        if fiscal_year is not None:
            data["fiscal_year"] = str(fiscal_year)
        if fiscal_quarter is not None:
            data["fiscal_quarter"] = str(fiscal_quarter)
        if ocr_only:
            data["processing_mode"] = "OCR_ONLY"
        period_start = _period_start(fiscal_year, fiscal_quarter, doc_role)
        period_end = _period_end(fiscal_year, fiscal_quarter)
        if period_start:
            data["period_start"] = period_start
        if period_end:
            data["period_end"] = period_end
        try:
            timeout = httpx.Timeout(connect=5.0, read=900.0, write=30.0, pool=5.0)
            with open(pdf_path, "rb") as pdf_file:
                files = {"file": (filename, pdf_file, "application/pdf")}
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(
                        f"{self.api_base}/tickers/{ticker}/documents/upload",
                        data=data,
                        files=files,
                        headers=self._headers(),
                    )
            if response.status_code in (200, 201):
                return response.json()
            return {"status": "FAILED", "ticker": ticker, "doc_role": doc_role,
                    "error": f"SAG local upload HTTP {response.status_code}: {response.text[:500]}"}
        except Exception as error:
            return {"status": "FAILED", "ticker": ticker, "doc_role": doc_role,
                    "error": f"SAG local upload failed: {type(error).__name__}: {error}"}

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
