from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import httpx

from app.domain.services.document_selector import ActiveDocumentSelector, TickerDocumentSet, ActiveDocument
from app.domain.services.document_classifier.service import DocumentClassifierService
from app.domain.repositories.bctc_pipeline_repository import BctcPipelineRepository
from app.domain.services.r2_storage import R2StorageService
from app.adapters.sag_connector import sag_connector, SAGConnector

logger = logging.getLogger("ai_engine.pipeline.bctc_to_sag")


class BctcToSagPipeline:
    """Pipeline tự động hóa 100% nạp tài liệu BCTC từ ai-engine sang SAG.
    
    Tích hợp khép kín:
    1. ActiveDocumentSelector: Tuyển chọn Bộ 3 tài liệu vàng từ knowledge_documents.
    2. DocumentClassifierService & R2Storage: Cắt tỉa PDF trên CPU, upload pruned.pdf lên Cloudflare R2.
    3. BctcPipelineRepository: Ghi nhận trạng thái bctc_pipeline_records (chống xử lý trùng lặp).
    4. SAG Backend: Gửi PDF sang SAG để MinerU OCR, xây dựng Tree, Full Chunking 1M tokens, Vector Embedding.
    5. Cất giữ Markdown sau OCR lên R2 và cập nhật bctc_pipeline_records (is_ocr_completed, sag_doc_role).
    6. SAGConnector: Phân tích đồ thị quan hệ & rủi ro GIL, cập nhật gil_flag vào universe_securities.
    """

    def __init__(
        self,
        selector: Optional[ActiveDocumentSelector] = None,
        connector: Optional[SAGConnector] = None,
        repo: Optional[BctcPipelineRepository] = None,
        r2: Optional[R2StorageService] = None,
        classifier: Optional[DocumentClassifierService] = None,
    ) -> None:
        self.selector = selector or ActiveDocumentSelector()
        self.connector = connector or sag_connector
        self.repo = repo or BctcPipelineRepository()
        self.r2 = r2 or R2StorageService()
        self.classifier = classifier or DocumentClassifierService(repo=self.repo, r2=self.r2)

    async def process_ticker(
        self,
        ticker: str,
        equity_vnd: float = 0.0,
        mock_markdowns: Optional[Dict[str, str]] = None,
        force_reprocess: bool = False,
        ocr_only: bool = False,
    ) -> Dict[str, Any]:
        """Thực thi chu trình nạp, lưu trữ R2, OCR và phân tích cho 1 mã cổ phiếu.
        
        Tham số:
            ocr_only: Nếu True, chỉ chạy đến bước cắt tỉa + OCR và lưu trữ Markdown lên R2 & DB.
                      Bỏ qua bước phân tích GIL và ghi universe_securities (dùng để cày quota OCR theo ngày).
        """
        ticker_clean = ticker.upper().strip()
        logger.info(f"==> Bắt đầu BCTC to SAG Pipeline cho mã {ticker_clean} (ocr_only={ocr_only})")

        # 1. Tuyển chọn Bộ 3 tài liệu vàng từ PostgreSQL
        doc_set: TickerDocumentSet = self.selector.select_active_documents(ticker_clean)
        ingested_docs = []

        # 2. Xử lý từng tài liệu: Cắt tỉa CPU -> R2 -> SAG OCR & Chunking -> R2 Markdown -> DB Records
        for doc in doc_set.all_documents:
            # Xác định năm, quý, phạm vi
            year = doc.fiscal_year or 2026
            quarter = doc.fiscal_quarter or (4 if doc.role == "ANNUAL_BACKBONE" else 2)
            scope = doc.scope or ("SEPARATE" if "riêng" in doc.title.lower() else "CONSOLIDATED")

            doc_info = {
                "role": doc.role,
                "title": doc.title,
                "year": year,
                "quarter": quarter,
                "scope": scope,
                "status": "PROCESSING",
                "r2_pdf_key": None,
                "r2_md_key": None,
                "sag_doc_id": None,
            }

            # BƯỚC 2A: Cắt tỉa PDF trên CPU và lưu lên Cloudflare R2
            pruned_pdf_bytes: Optional[bytes] = None
            if not force_reprocess and not mock_markdowns and self.repo.should_skip_classification(ticker_clean, year, quarter, scope):
                rec = self.repo.get_record(ticker_clean, year, quarter, scope)
                logger.info(f"⚡ [R2/DB Cache] {ticker_clean} {year} Q{quarter} {scope} đã có trên R2. Bỏ qua cắt tỉa.")
                doc_info["r2_pdf_key"] = rec.get("r2_pdf_key")
                try:
                    pruned_pdf_bytes = self.r2.download_bytes(rec["r2_pdf_key"])
                except Exception as e:
                    logger.warning(f"Không thể tải pruned PDF từ R2, sẽ tiến hành tải lại: {e}")

            if pruned_pdf_bytes is None:
                # Tải PDF gốc từ Cafef CDN
                if doc.pdf_url:
                    try:
                        async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=60.0) as client:
                            res_pdf = await client.get(doc.pdf_url)
                            if res_pdf.status_code == 200 and res_pdf.content[:4] == b"%PDF":
                                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
                                    tmp_file.write(res_pdf.content)
                                    tmp_path = tmp_file.name

                                # Chạy DocumentClassifierService: Cắt tỉa CPU + Upload R2 + Lưu bctc_pipeline_records
                                classify_res = self.classifier.process_bctc(
                                    ticker=ticker_clean,
                                    year=year,
                                    quarter=quarter,
                                    scope=scope,
                                    pdf_path=tmp_path,
                                    force=force_reprocess,
                                    announcement_date=doc.published_date,
                                )
                                doc_info["r2_pdf_key"] = classify_res.get("r2_key")
                                pruned_pdf_bytes = classify_res.get("pruned_bytes")
                                if not pruned_pdf_bytes and doc_info["r2_pdf_key"]:
                                    try:
                                        pruned_pdf_bytes = self.r2.download_bytes(doc_info["r2_pdf_key"])
                                    except Exception as dl_err:
                                        logger.warning(f"Không thể download pruned PDF từ R2: {dl_err}")
                                try:
                                    os.unlink(tmp_path)
                                except OSError:
                                    pass
                    except Exception as e:
                        logger.error(f"Lỗi khi xử lý cắt tỉa PDF cho {doc.title}: {e}")

            # BƯỚC 2B: Kiểm tra xem đã hoàn tất OCR chưa (Idempotency)
            if not force_reprocess and self.repo.should_skip_ocr(ticker_clean, year, quarter, scope):
                rec_ocr = self.repo.get_record(ticker_clean, year, quarter, scope)
                logger.info(f"⚡ [OCR Cache] {ticker_clean} {year} Q{quarter} đã OCR trước đó. Bỏ qua gọi lại MinerU.")
                doc_info["status"] = "SUCCESS_CACHED"
                doc_info["r2_md_key"] = rec_ocr.get("r2_md_key")
                ingested_docs.append(doc_info)
                continue

            # BƯỚC 2C: Đẩy sang SAG để MinerU OCR, Xây dựng Cây Heading, Full Chunking 1M tokens & Embedding
            sag_res = None
            if pruned_pdf_bytes:
                # Upload file PDF cắt tỉa sang SAG
                filename = f"{ticker_clean}_{year}_Q{quarter}_{scope}_pruned.pdf"
                sag_res = await self.connector.upload_bctc_pdf(
                    ticker=ticker_clean,
                    pdf_bytes=pruned_pdf_bytes,
                    filename=filename,
                    doc_role=doc.role,
                    is_active=True,
                    fiscal_year=year,
                    fiscal_quarter=quarter if isinstance(quarter, int) else None,
                )
            elif mock_markdowns and (doc.role in mock_markdowns or doc.title in mock_markdowns):
                # Fallback text markdown nếu được truyền vào
                content_md = mock_markdowns.get(doc.role) or mock_markdowns.get(doc.title) or ""
                sag_res = await self.connector.ingest_bctc_document(
                    ticker=ticker_clean,
                    title=doc.title,
                    text_content=content_md,
                    doc_role=doc.role,
                    is_active=True,
                    fiscal_year=year,
                    fiscal_quarter=quarter if isinstance(quarter, int) else None,
                )

            if sag_res and sag_res.get("status") not in ("FAILED", "error"):
                sag_doc_id = sag_res.get("id")
                sag_source_id = sag_res.get("source_id")
                doc_info["sag_doc_id"] = sag_doc_id
                doc_info["status"] = "INGESTED_TO_SAG"

                if not mock_markdowns and sag_doc_id:
                    # Đợi polling SAG hoàn tất OCR (tối đa 120s)
                    ready_doc = await self._wait_sag_ready(doc_id=sag_doc_id, source_id=sag_source_id, timeout_s=120)
                    if ready_doc and ready_doc.get("status") == "ready":
                        doc_info["status"] = "SUCCESS"
                        doc_info["chunk_count"] = ready_doc.get("chunk_count", 0)
                        doc_info["token_count"] = ready_doc.get("token_usage", 0)

                        # Tải Markdown sạch từ SAG và upload lên Cloudflare R2
                        md_key = f"bctc/{ticker_clean}/{year}/Q{quarter}/{ticker_clean}_{year}_Q{quarter}_{scope}_parsed.md"
                        md_url = f"{self.r2.endpoint_url}/{self.r2.bucket_name}/{md_key}"

                        if sag_source_id and self.r2.is_configured:
                            try:
                                md_content = await self.connector.get_document_parsed_markdown(sag_source_id, sag_doc_id)
                                if md_content:
                                    self.r2.upload_bytes(
                                        md_content.encode("utf-8"),
                                        s3_key=md_key,
                                        content_type="text/markdown; charset=utf-8",
                                    )
                                    logger.info(f"✅ Đã tải Markdown từ SAG và upload lên R2: {md_key}")
                            except Exception as md_err:
                                logger.warning(f"Không thể upload parsed.md lên R2: {md_err}")

                        # Lưu cờ OCR và vai trò Active vào CSDL bctc_pipeline_records
                        self.repo.save_ocr_result(
                            ticker=ticker_clean,
                            year=year,
                            quarter=int(quarter) if str(quarter).isdigit() else 4,
                            scope=scope,
                            r2_md_key=md_key,
                            r2_md_url=md_url,
                        )
                        self.repo.set_active_sag_role(
                            ticker=ticker_clean,
                            year=year,
                            quarter=quarter,
                            scope=scope,
                            role=doc.role,
                        )
                        doc_info["r2_md_key"] = md_key
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

        # 3. Kích hoạt đánh giá đồ thị thực thể & rủi ro GIL từ SAG
        gil_res = await self.connector.get_gil_relationships(
            ticker=ticker_clean,
            equity_vnd=equity_vnd,
        )
        gil_flag = gil_res.get("gil_flag", "PASS")

        # 4. Cập nhật cờ gil_flag vào CSDL (bảng universe_securities)
        db_updated = False
        try:
            from app.infrastructure.database.pg_pool import get_conn
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO universe_securities (ticker, universe_group, trading_status, beneish_status, gil_flag, updated_at)
                        VALUES (%s, 'A', 'NORMAL', 'PENDING', %s, NOW())
                        ON CONFLICT (ticker) 
                        DO UPDATE SET gil_flag = EXCLUDED.gil_flag, updated_at = NOW();
                        """,
                        (ticker_clean, gil_flag),
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

    async def _wait_sag_ready(
        self,
        doc_id: str,
        source_id: Optional[str] = None,
        timeout_s: int = 120,
    ) -> Optional[Dict[str, Any]]:
        """Theo dõi background worker của SAG qua REST API cho đến khi tài liệu chuyển sang READY."""
        if not source_id:
            logger.warning("Không có source_id để kiểm tra trạng thái document qua SAG API")
            return None

        start_time = asyncio.get_event_loop().time()
        while (asyncio.get_event_loop().time() - start_time) < timeout_s:
            try:
                status_doc = await self.connector.get_document_status(source_id, doc_id)
                if status_doc:
                    st = status_doc.get("status")
                    if st == "ready":
                        return {
                            "status": "ready",
                            "chunk_count": status_doc.get("chunk_count", 0),
                            "event_count": status_doc.get("event_count", 0),
                            "token_usage": status_doc.get("token_usage", 0),
                        }
                    elif st == "failed":
                        return {"status": "failed", "error": status_doc.get("error")}
            except Exception as e:
                logger.debug(f"Lỗi khi polling document status: {e}")

            await asyncio.sleep(3)
        return None

