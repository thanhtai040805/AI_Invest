from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from app.domain.services.document_selector import ActiveDocumentSelector, TickerDocumentSet, ActiveDocument
from app.domain.repositories.bctc_pipeline_repository import BctcPipelineRepository, OCR_CACHE_VERSION
from app.domain.services.r2_storage import R2StorageService
from app.adapters.sag_connector import sag_connector, SAGConnector

logger = logging.getLogger("ai_engine.pipeline.bctc_to_sag")
OCR_DOCUMENT_CONCURRENCY = max(1, int(os.getenv("SAG_OCR_ACTIVE_JOBS", "20")))
OCR_UPLOAD_CONCURRENCY = max(1, int(os.getenv("SAG_OCR_UPLOAD_CONCURRENCY", "20")))
EXTRACTION_DOCUMENT_CONCURRENCY = max(1, int(os.getenv("SAG_EXTRACTION_ACTIVE_JOBS", "3")))
_OCR_DOCUMENT_SEMAPHORE = asyncio.Semaphore(OCR_DOCUMENT_CONCURRENCY)
_OCR_UPLOAD_SEMAPHORE = asyncio.Semaphore(OCR_UPLOAD_CONCURRENCY)
_EXTRACTION_DOCUMENT_SEMAPHORE = asyncio.Semaphore(EXTRACTION_DOCUMENT_CONCURRENCY)


def _source_url_candidates(urls: tuple[str, ...]) -> tuple[str, ...]:
    """Return distinct source URLs in their supplied order.

    The source registry is authoritative; provider-specific host rewriting
    does not belong in the BCTC-to-SAG pipeline.
    """

    candidates: list[str] = []
    for raw_url in urls:
        url = str(raw_url or "").strip()
        if not url:
            continue
        candidates.append(url)
    return tuple(dict.fromkeys(candidates))


class _SingleDocumentSelector:
    """Selector adapter used by the document-level parallel coordinator."""

    def __init__(self, document: ActiveDocument) -> None:
        self.document = document

    def select_active_documents(self, ticker: str) -> TickerDocumentSet:
        kwargs = {"ticker": ticker}
        if self.document.role == "ANNUAL_BACKBONE":
            kwargs["annual_audited"] = self.document
        elif self.document.role == "LATEST_QUARTER":
            kwargs["latest_quarter"] = self.document
        elif self.document.role == "GOVERNANCE_REPORT":
            kwargs["governance_report"] = self.document
        return TickerDocumentSet(**kwargs)


def _sag_fiscal_quarter(quarter: Any) -> Optional[int]:
    """Convert selector periods to the API's calendar-quarter contract.

    The selector uses ``YEAR`` and ``6M`` for annual and half-year documents,
    while SAG accepts only a calendar quarter (1..4) or ``null``.  A half-year
    report therefore ends in Q2; an annual report has no quarter value.
    """
    clean = str(quarter).upper().strip() if quarter is not None else ""
    if clean in ("6M", "H1", "6") or quarter == 6:
        return 2
    if clean.startswith("Q"):
        clean = clean[1:]
    if clean.isdigit() and 1 <= int(clean) <= 4:
        return int(clean)
    return None


class BctcToSagPipeline:
    """Pipeline tự động hóa 100% nạp tài liệu BCTC từ ai-engine sang SAG.
    
    Tích hợp khép kín:
    1. ActiveDocumentSelector: Tuyển chọn Bộ 3 tài liệu vàng từ knowledge_documents.
    2. SAG Backend: Tải PDF trực tiếp từ URL, MinerU OCR, xây dựng Tree, Full Chunking và Embedding.
    3. BctcPipelineRepository: Ghi nhận trạng thái bctc_pipeline_records (chống xử lý trùng lặp).
    5. Cất giữ Markdown sau OCR lên R2 và cập nhật bctc_pipeline_records (is_ocr_completed, sag_doc_role).
    6. SAGConnector: Phân tích đồ thị quan hệ & rủi ro GIL, cập nhật gil_flag vào universe_securities.
    """

    def __init__(
        self,
        selector: Optional[ActiveDocumentSelector] = None,
        connector: Optional[SAGConnector] = None,
        repo: Optional[BctcPipelineRepository] = None,
        r2: Optional[R2StorageService] = None,
    ) -> None:
        self.selector = selector or ActiveDocumentSelector()
        self.connector = connector or sag_connector
        self.repo = repo or BctcPipelineRepository()
        self.r2 = r2 or R2StorageService()

    async def process_ticker(
        self,
        ticker: str,
        equity_vnd: float = 0.0,
        mock_markdowns: Optional[Dict[str, str]] = None,
        force_reprocess: bool = False,
        ocr_only: bool = False,
        extraction_only: bool = False,
        _single_document: bool = False,
        _skip_gil: bool = False,
    ) -> Dict[str, Any]:
        """Thực thi chu trình nạp, lưu trữ R2, OCR và phân tích cho 1 mã cổ phiếu.
        
        Tham số:
            ocr_only: Nếu True, chỉ chạy đến OCR và lưu Markdown lên R2 & DB.
                      Bỏ qua bước phân tích GIL và ghi universe_securities (dùng để cày quota OCR theo ngày).
        """
        ticker_clean = ticker.upper().strip()
        logger.info(f"==> Bắt đầu BCTC to SAG Pipeline cho mã {ticker_clean} (ocr_only={ocr_only})")

        # 0. Chốt chặn BctcIngestionGuard: Bỏ qua các mã thuộc danh sách rác / đóng băng thanh khoản (Đỉnh 2Y < 5B)
        from app.domain.rules.bctc_ingestion_guard import should_ingest_bctc
        allow_ingest, guard_reason = should_ingest_bctc(ticker_clean, force_reprocess=force_reprocess)
        if not allow_ingest:
            logger.info(f"⏭️ [BCTC-SAG Guard] {guard_reason}. Bỏ qua nạp để tiết kiệm token & tài nguyên.")
            return {
                "ticker": ticker_clean,
                "status": "SKIPPED_PURGED_LOW_LIQUIDITY",
                "reason": guard_reason,
                "ingested_documents": [],
                "documents": [],
                "gil_flag": "DORMANT_LOW_LIQUIDITY",
                "db_updated": False,
            }

        # 1. Tuyển chọn Bộ 3 tài liệu vàng từ PostgreSQL
        doc_set: TickerDocumentSet = self.selector.select_active_documents(ticker_clean)

        if not _single_document:
            return await self._process_ticker_parallel(
                ticker_clean,
                doc_set,
                equity_vnd=equity_vnd,
                mock_markdowns=mock_markdowns,
                force_reprocess=force_reprocess,
                ocr_only=ocr_only,
                extraction_only=extraction_only,
            )

        ingested_docs = []

        # 2. Xử lý từng tài liệu: Cắt tỉa CPU -> R2 -> SAG OCR & Chunking -> R2 Markdown -> DB Records
        for doc in doc_set.all_documents:
            # Xác định năm, quý, phạm vi từ ActiveDocument
            year = doc.fiscal_year
            quarter = doc.fiscal_quarter
            scope = doc.scope or ("SEPARATE" if "riêng" in doc.title.lower() else "CONSOLIDATED")

            # Nếu chưa có fiscal_year/quarter từ doc, dùng parse_fiscal_period
            if year is None or quarter is None:
                from app.domain.services.document_selector import parse_fiscal_period
                parsed_y, parsed_q = parse_fiscal_period(doc.title, doc.published_date, doc.role)
                year = year or parsed_y
                quarter = quarter if quarter is not None else parsed_q

            q_clean = str(quarter).upper().strip() if quarter is not None else ""
            if q_clean in ("YEAR", "ANNUAL", "FY", "0") or quarter == 0:
                q_label = "YEAR"
            elif q_clean in ("6M", "H1", "6") or quarter == 6:
                q_label = "6M"
            elif q_clean.isdigit():
                q_label = f"Q{q_clean}"
            elif q_clean.startswith("Q"):
                q_label = q_clean
            else:
                q_label = q_clean or "YEAR"

            doc_info = {
                "role": doc.role,
                "title": doc.title,
                "year": year,
                "quarter": quarter,
                "q_label": q_label,
                "scope": scope,
                "status": "PROCESSING",
                "r2_pdf_key": None,
                "r2_md_key": None,
                "sag_doc_id": None,
            }

            extraction_md_key = None
            if extraction_only:
                cached_record = self.repo.get_record(ticker_clean, year, quarter, scope)
                extraction_md_key = (cached_record or {}).get("r2_md_key")
                if extraction_md_key:
                    doc_info["r2_md_key"] = extraction_md_key

            # Legacy classifier and PDF staging are intentionally bypassed.
            # SAG fetches the source URL directly into its ephemeral storage.

            # BƯỚC 2B: Kiểm tra xem đã hoàn tất OCR chưa (Idempotency)
            if extraction_only and extraction_md_key:
                try:
                    md_bytes = self.r2.download_bytes(extraction_md_key)
                    md_content = md_bytes.decode("utf-8")
                    sag_res = await self.connector.ingest_bctc_document(
                        ticker=ticker_clean,
                        title=doc.title,
                        text_content=md_content,
                        doc_role=doc.role,
                        is_active=True,
                        fiscal_year=year,
                        fiscal_quarter=_sag_fiscal_quarter(quarter),
                    )
                    if sag_res and sag_res.get("status") not in ("FAILED", "error"):
                        doc_info["sag_doc_id"] = sag_res.get("id")
                        doc_info["status"] = "EXTRACTION_COMPLETED"
                    else:
                        doc_info["status"] = "FAILED"
                        doc_info["error"] = (sag_res or {}).get("error", "Không thể ingest Markdown R2 vào SAG")
                except Exception as e:
                    doc_info["status"] = "FAILED"
                    doc_info["error"] = f"Không thể đọc Markdown R2 để extraction: {e}"
                ingested_docs.append(doc_info)
                continue

            if not force_reprocess and self.repo.should_skip_ocr(
                ticker_clean, year, quarter, scope,
                source_document_id=doc.doc_id,
                cache_version=OCR_CACHE_VERSION,
            ):
                rec_ocr = self.repo.get_record(ticker_clean, year, quarter, scope)
                logger.info(f"⚡ [OCR Cache] {ticker_clean} {year} {q_label} đã OCR trước đó. Bỏ qua gọi lại MinerU.")
                doc_info["r2_md_key"] = rec_ocr.get("r2_md_key")
                # Pruned PDFs are staging artifacts. OCR_READY records no
                # longer require the PDF; keep the historical key in DB but
                # make its deletion explicit in the pipeline result.
                doc_info["r2_pdf_deleted"] = True
                doc_info["status"] = "SUCCESS_CACHED"

                # OCR-only là terminal phase. Không ingest Markdown cache vào
                # SAG lần nữa, vì việc đó sẽ dựng tree/extraction cho artifact
                # vốn đã hoàn tất OCR.
                if ocr_only:
                    doc_info["status"] = "OCR_COMPLETED"
                    ingested_docs.append(doc_info)
                    continue

                # Extraction benchmark path: OCR Markdown already exists in
                # the authoritative R2 cache, so ingest that artifact into a
                # clean SAG DB without downloading/re-running MinerU.
                if extraction_only:
                    try:
                        md_bytes = self.r2.download_bytes(doc_info["r2_md_key"])
                        md_content = md_bytes.decode("utf-8")
                        sag_res = await self.connector.ingest_bctc_document(
                            ticker=ticker_clean,
                            title=doc.title,
                            text_content=md_content,
                            doc_role=doc.role,
                            is_active=True,
                            fiscal_year=year,
                            fiscal_quarter=_sag_fiscal_quarter(quarter),
                        )
                        if sag_res and sag_res.get("status") not in ("FAILED", "error"):
                            doc_info["sag_doc_id"] = sag_res.get("id")
                            doc_info["status"] = "EXTRACTION_COMPLETED"
                        else:
                            doc_info["status"] = "FAILED"
                            doc_info["error"] = (sag_res or {}).get("error", "Không thể ingest Markdown R2 vào SAG")
                    except Exception as e:
                        doc_info["status"] = "FAILED"
                        doc_info["error"] = f"Không thể đọc Markdown R2 để extraction: {e}"
                    ingested_docs.append(doc_info)
                    continue

                # Period lock: an already completed period is immutable.  Do
                # not re-ingest its cached Markdown when a later upload for
                # the same fiscal period is discovered.
                if doc_info["r2_md_key"]:
                    logger.info(
                        "[Period lock] %s %s đã hoàn tất; giữ artifact hiện tại",
                        ticker_clean,
                        q_label,
                    )
                    ingested_docs.append(doc_info)
                    continue

                if doc_info["r2_md_key"]:
                    try:
                        md_bytes = self.r2.download_bytes(doc_info["r2_md_key"])
                        md_content = md_bytes.decode("utf-8")
                        sag_res = await self.connector.ingest_bctc_document(
                            ticker=ticker_clean,
                            title=doc.title,
                            text_content=md_content,
                            doc_role=doc.role,
                            is_active=True,
                            fiscal_year=year,
                            fiscal_quarter=_sag_fiscal_quarter(quarter),
                        )
                        if sag_res and sag_res.get("status") not in ("FAILED", "error"):
                            doc_info["sag_doc_id"] = sag_res.get("id")
                            doc_info["status"] = "INGESTED_TO_SAG_FROM_R2_MARKDOWN"
                        else:
                            doc_info["status"] = "FAILED"
                            doc_info["error"] = (sag_res or {}).get("error", "Không thể ingest Markdown R2 vào SAG")
                    except Exception as e:
                        doc_info["status"] = "FAILED"
                        doc_info["error"] = f"Không thể đọc Markdown từ R2 để ingest SAG: {e}"
                ingested_docs.append(doc_info)
                continue

            # BƯỚC 2C: SAG fetches the source URL, or consumes one legacy R2
            # staging PDF during migration.
            sag_res = None
            backlog_rec = self.repo.get_record(ticker_clean, year, quarter, scope) if not force_reprocess else None
            if mock_markdowns and (doc.role in mock_markdowns or doc.title in mock_markdowns):
                # Deterministic test/in-memory path remains available.
                content_md = mock_markdowns.get(doc.role) or mock_markdowns.get(doc.title) or ""
                sag_res = await self.connector.ingest_bctc_document(
                    ticker=ticker_clean,
                    title=doc.title,
                    text_content=content_md,
                    doc_role=doc.role,
                    is_active=True,
                    fiscal_year=year,
                    fiscal_quarter=_sag_fiscal_quarter(quarter),
                )
            elif backlog_rec and backlog_rec.get("r2_pdf_key"):
                async with _OCR_UPLOAD_SEMAPHORE:
                    sag_res = await self.connector.upload_bctc_pdf_from_r2(
                        ticker=ticker_clean,
                        object_uri=f"r2://{self.r2.bucket_name}/{backlog_rec['r2_pdf_key']}",
                        title=doc.title,
                        doc_role=doc.role,
                        fiscal_year=year,
                        fiscal_quarter=_sag_fiscal_quarter(quarter),
                        is_active=not ocr_only,
                        ocr_only=ocr_only,
                    )
                doc_info["r2_pdf_key"] = backlog_rec["r2_pdf_key"]

                # A legacy pruned PDF may be corrupt (for example a malformed
                # page tree). Keep it for audit, but recover from the
                # authoritative source URL instead of failing the document.
                if sag_res and sag_res.get("status") in ("FAILED", "error") and doc.pdf_url:
                    logger.warning(
                        "Legacy R2 PDF lỗi cho %s (%s), fallback URL nguồn: %s",
                        doc.title,
                        (sag_res or {}).get("error", "unknown error"),
                        doc.pdf_url,
                    )
                    source_urls = _source_url_candidates(doc.pdf_urls or (doc.pdf_url,))
                    for source_url in source_urls:
                        async with _OCR_UPLOAD_SEMAPHORE:
                            sag_res = await self.connector.upload_bctc_pdf_from_url(
                                ticker=ticker_clean,
                                source_url=source_url,
                                title=doc.title,
                                doc_role=doc.role,
                                fiscal_year=year,
                                fiscal_quarter=_sag_fiscal_quarter(quarter),
                                is_active=not ocr_only,
                                ocr_only=ocr_only,
                            )
                        if sag_res and sag_res.get("status") not in ("FAILED", "error"):
                            break
                        logger.warning(
                            "Fallback source URL thất bại cho %s (%s): %s",
                            doc.title,
                            source_url,
                            (sag_res or {}).get("error", "unknown error"),
                        )
            elif doc.pdf_url:
                source_urls = _source_url_candidates(doc.pdf_urls or (doc.pdf_url,))
                for source_url in source_urls:
                    async with _OCR_UPLOAD_SEMAPHORE:
                        sag_res = await self.connector.upload_bctc_pdf_from_url(
                            ticker=ticker_clean,
                            source_url=source_url,
                            title=doc.title,
                            doc_role=doc.role,
                            fiscal_year=year,
                            fiscal_quarter=_sag_fiscal_quarter(quarter),
                            is_active=not ocr_only,
                            ocr_only=ocr_only,
                        )
                    if sag_res and sag_res.get("status") not in ("FAILED", "error"):
                        break
                    logger.warning(
                        "Source URL thất bại cho %s (%s): %s",
                        doc.title,
                        source_url,
                        (sag_res or {}).get("error", "unknown error"),
                    )

            if sag_res and sag_res.get("status") not in ("FAILED", "error"):
                sag_doc_id = sag_res.get("id")
                doc_info["sag_doc_id"] = sag_doc_id
                doc_info["status"] = "INGESTED_TO_SAG"

                if not mock_markdowns and sag_doc_id:
                    # OCR/structure là một artifact độc lập với extraction.
                    # Phải lưu Markdown ngay khi structure hoàn tất, kể cả khi
                    # LLM extraction hoặc embedding về sau bị lỗi.
                    ready_doc = await self._wait_sag_ready(doc_id=sag_doc_id, timeout_s=900)
                    if ready_doc and ready_doc.get("status") in {"READY", "OCR_READY"}:
                        full_pipeline_ready = ready_doc.get("status") == "READY"
                        doc_info["status"] = "SUCCESS" if full_pipeline_ready else "OCR_COMPLETED"
                        if not full_pipeline_ready:
                            doc_info["extraction_status"] = ready_doc.get("extraction_status")
                            doc_info["extraction_error"] = ready_doc.get("error")
                        doc_info["chunk_count"] = ready_doc.get("chunk_count", 0)
                        doc_info["token_count"] = ready_doc.get("token_usage", 0)

                        # SAG owns the durable Markdown artifact. ai-engine only
                        # records the object returned by SAG and never uploads it.
                        md_key = self.r2.object_key_from_uri(ready_doc.get("object_uri"))
                        md_url = ready_doc.get("object_uri") or ""
                        md_saved = bool(md_key and ready_doc.get("canonical_markdown_sha256"))

                        if md_saved:
                            # Chỉ ghi cache OCR sau khi upload R2 thành công;
                            # không tạo record trỏ tới một object không tồn tại.
                            self.repo.save_ocr_result(
                                ticker=ticker_clean,
                                year=year,
                                quarter=quarter,
                                scope=scope,
                                r2_md_key=md_key,
                                r2_md_url=md_url,
                                source_document_id=doc.doc_id,
                                cache_version=OCR_CACHE_VERSION,
                            )
                            self.repo.set_active_sag_role(
                                ticker=ticker_clean,
                                year=year,
                                quarter=quarter,
                                scope=scope,
                                role=doc.role,
                            )
                            doc_info["r2_md_key"] = md_key

                            # The pruned PDF is only an OCR transport artifact.
                            # Delete it after Markdown is durable and the DB
                            # OCR flag has been written. A cleanup failure must
                            # not turn a successful OCR into a failed document.
                            pdf_key = doc_info.get("r2_pdf_key")
                            if pdf_key and self.r2.is_configured:
                                try:
                                    doc_info["r2_pdf_deleted"] = bool(self.r2.delete_object(pdf_key))
                                    if doc_info["r2_pdf_deleted"]:
                                        logger.info("🗑️ Đã xóa PDF staging sau OCR: %s", pdf_key)
                                    else:
                                        logger.warning("Không xóa được PDF staging sau OCR: %s", pdf_key)
                                except Exception as cleanup_err:
                                    doc_info["r2_pdf_deleted"] = False
                                    logger.warning(
                                        "Không thể cleanup PDF staging sau OCR (%s): %s",
                                        pdf_key,
                                        cleanup_err,
                                    )
                        else:
                            doc_info["r2_status"] = "NOT_SAVED"
                else:
                    doc_info["status"] = "SUCCESS"
            else:
                doc_info["status"] = "FAILED"
                doc_info["error"] = sag_res.get("error") if sag_res else "Không có dữ liệu PDF"

            ingested_docs.append(doc_info)

        # Nếu chỉ chạy OCR (ocr_only=True): Dừng tại đây, bỏ qua phân tích GIL để tiết kiệm tài nguyên
        if ocr_only:
            logger.info(f"==> [OCR ONLY] Hoàn tất cắt tỉa và OCR cho {ticker_clean}. Markdown đã lưu R2 & CSDL. Bỏ qua bước GIL.")
            return {
                "ticker": ticker_clean,
                "status": "OCR_COMPLETED",
                "documents_count": len(ingested_docs),
                "documents": ingested_docs,
                "gil_result": None,
                "gil_flag": "PENDING_GIL",
                "db_updated": False,
            }

        if _skip_gil:
            return {
                "ticker": ticker_clean,
                "status": "SUCCESS",
                "documents_count": len(ingested_docs),
                "documents": ingested_docs,
                "gil_result": None,
                "gil_flag": "PENDING_GIL",
                "db_updated": False,
            }

        # 3. Kích hoạt đánh giá đồ thị thực thể & rủi ro GIL từ SAG
        gil_res = await self.connector.get_gil_relationships(
            ticker=ticker_clean,
            equity_vnd=equity_vnd,
        )
        gil_flag = gil_res.get("gil_flag") or "DATA_INSUFFICIENT"
        gil_status = gil_res.get("analysis_status") or gil_res.get("assessment_status") or "DATA_INSUFFICIENT"
        gil_version = gil_res.get("policy_version") or gil_res.get("analysis_version")

        # 4. Cập nhật cờ gil_flag vào CSDL (bảng universe_securities)
        db_updated = False
        try:
            from app.infrastructure.database.pg_pool import get_conn
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO universe_securities (
                            ticker, universe_group, trading_status, beneish_status,
                            gil_flag, gil_analysis_status, gil_analysis_version, gil_assessed_at, updated_at
                        )
                        VALUES (%s, 'A', 'NORMAL', 'PENDING', %s, %s, %s, NOW(), NOW())
                        ON CONFLICT (ticker) 
                        DO UPDATE SET
                            gil_flag = EXCLUDED.gil_flag,
                            gil_analysis_status = EXCLUDED.gil_analysis_status,
                            gil_analysis_version = EXCLUDED.gil_analysis_version,
                            gil_assessed_at = EXCLUDED.gil_assessed_at,
                            updated_at = NOW();
                        """,
                        (ticker_clean, gil_flag, gil_status, gil_version),
                    )
                conn.commit()
            db_updated = True
        except Exception as e:
            logger.debug(f"Không thể cập nhật cờ gil_flag vào universe_securities: {e}")

        logger.info(f"==> Hoàn tất Full Pipeline cho {ticker_clean}: GIL Flag = {gil_flag} (DB Updated: {db_updated})")
        return {
            "ticker": ticker_clean,
            "status": "SUCCESS",
            "documents_count": len(ingested_docs),
            "documents": ingested_docs,
            "gil_result": gil_res,
            "gil_flag": gil_flag,
            "db_updated": db_updated,
        }

    async def _process_ticker_parallel(
        self,
        ticker: str,
        doc_set: TickerDocumentSet,
        *,
        equity_vnd: float,
        mock_markdowns: Optional[Dict[str, str]],
        force_reprocess: bool,
        ocr_only: bool,
        extraction_only: bool,
    ) -> Dict[str, Any]:
        """Run each selected document independently with one global OCR limit."""

        async def run_document(document: ActiveDocument) -> Dict[str, Any]:
            semaphore = _EXTRACTION_DOCUMENT_SEMAPHORE if extraction_only else _OCR_DOCUMENT_SEMAPHORE
            async with semaphore:
                started = asyncio.get_running_loop().time()
                child = BctcToSagPipeline(
                    selector=_SingleDocumentSelector(document),
                    connector=self.connector,
                    repo=self.repo,
                    r2=self.r2,
                )
                outcome = await child.process_ticker(
                    ticker=ticker,
                    equity_vnd=equity_vnd,
                    mock_markdowns=mock_markdowns,
                    force_reprocess=force_reprocess,
                    ocr_only=ocr_only,
                    extraction_only=extraction_only,
                    _single_document=True,
                    _skip_gil=not ocr_only,
                )
                elapsed = round(asyncio.get_running_loop().time() - started, 2)
                for item in outcome.get("documents") or []:
                    item["document_elapsed_seconds"] = elapsed
                return outcome

        outcomes = await asyncio.gather(
            *(run_document(document) for document in doc_set.all_documents),
            return_exceptions=True,
        )
        documents: list[Dict[str, Any]] = []
        for document, outcome in zip(doc_set.all_documents, outcomes):
            if isinstance(outcome, Exception):
                documents.append({
                    "role": document.role,
                    "title": document.title,
                    "status": "FAILED",
                    "error": f"{type(outcome).__name__}: {outcome}",
                    "document_elapsed_seconds": None,
                })
            else:
                documents.extend(outcome.get("documents") or [])

        if ocr_only:
            return {
                "ticker": ticker,
                "status": "OCR_COMPLETED",
                "documents_count": len(documents),
                "documents": documents,
                "gil_result": None,
                "gil_flag": "PENDING_GIL",
                "db_updated": False,
            }

        gil_res = await self.connector.get_gil_relationships(
            ticker=ticker,
            equity_vnd=equity_vnd,
        )
        gil_flag = gil_res.get("gil_flag") or "DATA_INSUFFICIENT"
        gil_status = gil_res.get("analysis_status") or gil_res.get("assessment_status") or "DATA_INSUFFICIENT"
        gil_version = gil_res.get("policy_version") or gil_res.get("analysis_version")
        db_updated = False
        try:
            from app.infrastructure.database.pg_pool import get_conn
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO universe_securities (
                            ticker, universe_group, trading_status, beneish_status,
                            gil_flag, gil_analysis_status, gil_analysis_version, gil_assessed_at, updated_at
                        ) VALUES (%s, 'A', 'NORMAL', 'PENDING', %s, %s, %s, NOW(), NOW())
                        ON CONFLICT (ticker) DO UPDATE SET
                            gil_flag = EXCLUDED.gil_flag,
                            gil_analysis_status = EXCLUDED.gil_analysis_status,
                            gil_analysis_version = EXCLUDED.gil_analysis_version,
                            gil_assessed_at = EXCLUDED.gil_assessed_at,
                            updated_at = NOW();
                        """,
                        (ticker, gil_flag, gil_status, gil_version),
                    )
                conn.commit()
            db_updated = True
        except Exception as error:
            logger.debug("Không thể cập nhật cờ gil_flag vào universe_securities: %s", error)
        return {
            "ticker": ticker,
            "status": "SUCCESS",
            "documents_count": len(documents),
            "documents": documents,
            "gil_result": gil_res,
            "gil_flag": gil_flag,
            "db_updated": db_updated,
        }

    async def _wait_sag_ready(
        self,
        doc_id: str,
        source_id: Optional[str] = None,
        timeout_s: int = 900,
    ) -> Optional[Dict[str, Any]]:
        """Đợi OCR/structure hoặc toàn bộ xử lý SAG hoàn tất.

        OCR_READY cho phép pipeline lưu canonical Markdown lên R2 trước khi
        extraction/embedding hoàn tất hoặc thất bại.
        """
        start_time = asyncio.get_event_loop().time()
        while (asyncio.get_event_loop().time() - start_time) < timeout_s:
            try:
                status_doc = await self.connector.get_document_status(source_id, doc_id)
                if status_doc:
                    st = str(status_doc.get("status") or "").upper()
                    structure_status = str(status_doc.get("structure_status") or "").upper()
                    extraction_status = str(status_doc.get("extraction_status") or "").upper()
                    if st == "OCR_READY":
                        return {
                            "status": "OCR_READY",
                            "chunk_count": 0,
                            "token_usage": 0,
                            "extraction_status": extraction_status,
                            "error": status_doc.get("error"),
                            "object_uri": status_doc.get("object_uri"),
                            "canonical_markdown_sha256": status_doc.get("canonical_markdown_sha256"),
                        }
                    if st == "READY":
                        return {
                            "status": "READY",
                            "chunk_count": status_doc.get("chunk_count", 0),
                            "token_usage": status_doc.get("token_usage", 0),
                            "object_uri": status_doc.get("object_uri"),
                            "canonical_markdown_sha256": status_doc.get("canonical_markdown_sha256"),
                        }
                    if structure_status == "COMPLETE":
                        return {
                            "status": "OCR_READY",
                            "chunk_count": status_doc.get("chunk_count", 0),
                            "token_usage": status_doc.get("token_usage", 0),
                            "extraction_status": extraction_status,
                            "error": status_doc.get("error"),
                            "object_uri": status_doc.get("object_uri"),
                            "canonical_markdown_sha256": status_doc.get("canonical_markdown_sha256"),
                        }
                    elif st in {"FAILED", "CANCELLED"}:
                        return {"status": st, "error": status_doc.get("error")}
            except Exception as e:
                logger.debug(f"Lỗi khi polling document status: {e}")

            await asyncio.sleep(3)
        return None
