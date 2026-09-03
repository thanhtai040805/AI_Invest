"""Repository Quản lý Vòng đời Pipeline BCTC và Trạng thái Cloudflare R2 (IOS v5.1).

Quản lý bảng `bctc_pipeline_records` trong PostgreSQL:
- Theo dõi cờ trạng thái:
    + is_classified, classifier_status
    + r2_pdf_uploaded, r2_pdf_key, r2_pdf_url, pdf_sha256
    + is_ocr_completed, ocr_status, r2_md_uploaded, r2_md_key
- Đảm bảo cơ chế Idempotent:
    + should_skip_classification(): Không bao giờ cắt tỉa hay upload lại nếu file đã có.
    + should_skip_ocr(): Không bao giờ gọi lại OCR nếu Markdown đã được lưu trữ.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.adapters.postgres_adapter import PostgresAdapter

logger = logging.getLogger("ai_engine.repositories.bctc_pipeline")


class BctcPipelineRepository:
    """Repository giám sát và lưu trữ cờ trạng thái xử lý BCTC."""

    def __init__(self, storage: Optional[PostgresAdapter] = None) -> None:
        self.storage = storage or PostgresAdapter()

    @staticmethod
    def make_record_id(ticker: str, year: int, quarter: Any, scope: str = "CONSOLIDATED") -> str:
        q_str = f"Q{quarter}" if str(quarter).isdigit() else str(quarter).upper()
        return f"{ticker.upper().strip()}_{year}_{q_str}_{scope.upper().strip()}"

    def get_record(
        self,
        ticker: str,
        year: int,
        quarter: Any,
        scope: str = "CONSOLIDATED",
    ) -> Optional[Dict[str, Any]]:
        """Lấy bản ghi trạng thái BCTC theo mã, năm, quý, phạm vi báo cáo."""
        rec_id = self.make_record_id(ticker, year, quarter, scope)
        query = """
            SELECT id, ticker, fiscal_year, fiscal_quarter, report_scope,
                   is_classified, classifier_status, total_raw_pages, retained_pages,
                   r2_pdf_uploaded, r2_pdf_key, r2_pdf_url, pdf_sha256,
                   is_ocr_completed, ocr_status, r2_md_uploaded, r2_md_key, r2_md_url,
                   is_audited, auditor_name, audit_opinion, announcement_date,
                   created_at, updated_at
            FROM bctc_pipeline_records
            WHERE id = %s
        """
        try:
            rows = self.storage.fetch_all(query, (rec_id,))
            if rows:
                r = rows[0]
                return {
                    "id": r[0],
                    "ticker": r[1],
                    "fiscal_year": r[2],
                    "fiscal_quarter": r[3],
                    "report_scope": r[4],
                    "is_classified": bool(r[5]),
                    "classifier_status": r[6],
                    "total_raw_pages": r[7],
                    "retained_pages": r[8],
                    "r2_pdf_uploaded": bool(r[9]),
                    "r2_pdf_key": r[10],
                    "r2_pdf_url": r[11],
                    "pdf_sha256": r[12],
                    "is_ocr_completed": bool(r[13]),
                    "ocr_status": r[14],
                    "r2_md_uploaded": bool(r[15]),
                    "r2_md_key": r[16],
                    "r2_md_url": r[17],
                    "is_audited": bool(r[18]),
                    "auditor_name": r[19],
                    "audit_opinion": r[20],
                    "announcement_date": r[21].isoformat() if r[21] else None,
                    "created_at": r[22].isoformat() if r[22] else None,
                    "updated_at": r[23].isoformat() if r[23] else None,
                }
        except Exception as err:
            logger.warning("Lỗi get_record %s: %s", rec_id, err)
        return None

    def should_skip_classification(
        self,
        ticker: str,
        year: int,
        quarter: Any,
        scope: str = "CONSOLIDATED",
    ) -> bool:
        """Kiểm tra xem BCTC này đã được cắt tỉa và upload PDF lên R2 chưa.
        Nếu rồi -> BỎ QUA để tránh lãng phí CPU và băng thông.
        """
        rec = self.get_record(ticker, year, quarter, scope)
        if not rec:
            return False
        return bool(rec.get("is_classified") and rec.get("r2_pdf_uploaded"))

    def should_skip_ocr(
        self,
        ticker: str,
        year: int,
        quarter: Any,
        scope: str = "CONSOLIDATED",
    ) -> bool:
        """Kiểm tra xem BCTC này đã hoàn thành OCR và upload Markdown lên R2 chưa.
        Nếu rồi -> BỎ QUA để tránh tốn tiền gọi lại API MinerU OCR.
        """
        rec = self.get_record(ticker, year, quarter, scope)
        if not rec:
            return False
        return bool(rec.get("is_ocr_completed") and rec.get("r2_md_uploaded"))

    def save_classification_result(
        self,
        ticker: str,
        year: int,
        quarter: int,
        scope: str,
        total_raw_pages: int,
        retained_pages: int,
        r2_pdf_key: str,
        r2_pdf_url: str,
        pdf_sha256: str = "",
        is_audited: bool = False,
        auditor_name: str = "",
        audit_opinion: str = "UNQUALIFIED",
        announcement_date: Optional[str] = None,
    ) -> None:
        """Lưu hoặc cập nhật trạng thái sau khi Classifier cắt tỉa và upload PDF lên R2 thành công."""
        rec_id = self.make_record_id(ticker, year, quarter, scope)
        q_num = int(quarter) if str(quarter).isdigit() else 4

        query = """
            INSERT INTO bctc_pipeline_records (
                id, ticker, fiscal_year, fiscal_quarter, report_scope,
                is_classified, classifier_status, total_raw_pages, retained_pages,
                r2_pdf_uploaded, r2_pdf_key, r2_pdf_url, pdf_sha256,
                is_audited, auditor_name, audit_opinion, announcement_date,
                updated_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                TRUE, 'SUCCESS', %s, %s,
                TRUE, %s, %s, %s,
                %s, %s, %s, %s,
                CURRENT_TIMESTAMP
            )
            ON CONFLICT (id) DO UPDATE SET
                is_classified = TRUE,
                classifier_status = 'SUCCESS',
                total_raw_pages = EXCLUDED.total_raw_pages,
                retained_pages = EXCLUDED.retained_pages,
                r2_pdf_uploaded = TRUE,
                r2_pdf_key = EXCLUDED.r2_pdf_key,
                r2_pdf_url = EXCLUDED.r2_pdf_url,
                pdf_sha256 = EXCLUDED.pdf_sha256,
                is_audited = EXCLUDED.is_audited,
                auditor_name = EXCLUDED.auditor_name,
                audit_opinion = EXCLUDED.audit_opinion,
                announcement_date = EXCLUDED.announcement_date,
                updated_at = CURRENT_TIMESTAMP;
        """
        self.storage.execute(
            query,
            (
                rec_id,
                ticker.upper().strip(),
                year,
                q_num,
                scope.upper().strip(),
                total_raw_pages,
                retained_pages,
                r2_pdf_key,
                r2_pdf_url,
                pdf_sha256,
                is_audited,
                auditor_name,
                audit_opinion,
                announcement_date,
            ),
        )
        logger.info("Saved classification status for %s (r2_key=%s)", rec_id, r2_pdf_key)

    def save_ocr_result(
        self,
        ticker: str,
        year: int,
        quarter: int,
        scope: str,
        r2_md_key: str,
        r2_md_url: str,
    ) -> None:
        """Lưu hoặc cập nhật trạng thái sau khi SAG OCR hoàn thành và cất file Markdown lên R2."""
        rec_id = self.make_record_id(ticker, year, quarter, scope)
        query = """
            UPDATE bctc_pipeline_records
            SET is_ocr_completed = TRUE,
                ocr_status = 'SUCCESS',
                r2_md_uploaded = TRUE,
                r2_md_key = %s,
                r2_md_url = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s;
        """
        self.storage.execute(query, (r2_md_key, r2_md_url, rec_id))
        logger.info("Saved OCR status for %s (r2_md_key=%s)", rec_id, r2_md_key)

    def set_active_sag_role(
        self,
        ticker: str,
        year: int,
        quarter: Any,
        scope: str,
        role: str,
    ) -> None:
        """Kích hoạt tài liệu này vào Cửa sổ Active của SAG ('ANNUAL_BACKBONE' hoặc 'LATEST_QUARTER').
        Đồng thời tự động lưu kho (ARCHIVED) các tài liệu cùng vai trò trước đó.
        """
        rec_id = self.make_record_id(ticker, year, quarter, scope)
        ticker_clean = ticker.upper().strip()

        # 1. Lưu kho các tài liệu cũ cùng vai trò của mã này
        if role == "ANNUAL_BACKBONE":
            archive_query = """
                UPDATE bctc_pipeline_records
                SET is_active_for_sag = FALSE,
                    sag_doc_role = 'ARCHIVED',
                    updated_at = CURRENT_TIMESTAMP
                WHERE ticker = %s AND sag_doc_role = 'ANNUAL_BACKBONE' AND id != %s;
            """
            self.storage.execute(archive_query, (ticker_clean, rec_id))
        elif role == "LATEST_QUARTER":
            archive_query = """
                UPDATE bctc_pipeline_records
                SET is_active_for_sag = FALSE,
                    sag_doc_role = 'ARCHIVED',
                    updated_at = CURRENT_TIMESTAMP
                WHERE ticker = %s AND sag_doc_role = 'LATEST_QUARTER' AND id != %s;
            """
            self.storage.execute(archive_query, (ticker_clean, rec_id))

        # 2. Đánh dấu tài liệu này là Active
        update_query = """
            UPDATE bctc_pipeline_records
            SET is_active_for_sag = TRUE,
                sag_doc_role = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s;
        """
        self.storage.execute(update_query, (role, rec_id))
        logger.info("Set active SAG role for %s -> %s (Archived previous)", rec_id, role)

    def get_active_sag_documents(self, ticker: str) -> List[Dict[str, Any]]:
        """Lấy danh sách các tài liệu BCTC đang Active cho SAG của mã cổ phiếu."""
        query = """
            SELECT id, ticker, fiscal_year, fiscal_quarter, report_scope,
                   sag_doc_role, r2_md_key, r2_md_url, is_audited, auditor_name, audit_opinion
            FROM bctc_pipeline_records
            WHERE ticker = %s AND is_active_for_sag = TRUE
            ORDER BY sag_doc_role ASC;
        """
        rows = self.storage.fetch_all(query, (ticker.upper().strip(),))
        result = []
        for r in rows:
            result.append({
                "id": r[0],
                "ticker": r[1],
                "fiscal_year": r[2],
                "fiscal_quarter": r[3],
                "report_scope": r[4],
                "sag_doc_role": r[5],
                "r2_md_key": r[6],
                "r2_md_url": r[7],
                "is_audited": bool(r[8]),
                "auditor_name": r[9],
                "audit_opinion": r[10],
            })
        return result

