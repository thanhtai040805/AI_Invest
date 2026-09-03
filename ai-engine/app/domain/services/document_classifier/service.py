"""Document Classifier Service trong Phân hệ AI-Engine (IOS v5.1).

Quản lý toàn bộ chu trình tiền xử lý BCTC:
1. Kiểm tra cờ PostgreSQL (BctcPipelineRepository.should_skip_classification):
   - Nếu đã có cờ r2_pdf_uploaded -> BỎ QUA NGAY (0ms, 0 VNĐ, 0 tài nguyên).
2. Chạy bộ phân loại trang & cắt tỉa CPU (PageClassifier):
   - Loại bỏ trang bìa, quảng cáo, danh sách công ty thành viên, chữ ký.
   - Giữ lại: Bảng CĐKT, KQKD, LCTT, Thuyết minh BCTC.
3. Upload duy nhất 1 file PDF đã cắt tỉa (pruned.pdf) lên Cloudflare R2:
   - Đường dẫn chuẩn hóa O(1): bctc/{TICKER}/{YEAR}/Q{QUARTER}/{TICKER}_{YEAR}_Q{Q}_{SCOPE}_pruned.pdf
4. Ghi cờ hoàn tất vào PostgreSQL:
   - Cập nhật total_raw_pages, retained_pages, r2_pdf_key, pdf_sha256.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

from app.domain.repositories.bctc_pipeline_repository import BctcPipelineRepository
from app.domain.services.r2_storage import R2StorageService
from .config import load_profile
from .page_classifier import PageClassifier, PageClassificationResult

logger = logging.getLogger("ai_engine.services.document_classifier")


class DocumentClassifierService:
    """Service điều phối phân loại, cắt tỉa và đẩy BCTC lên Cloudflare R2."""

    def __init__(
        self,
        repo: Optional[BctcPipelineRepository] = None,
        r2: Optional[R2StorageService] = None,
        profile_path: Optional[str] = None,
    ) -> None:
        self.repo = repo or BctcPipelineRepository()
        self.r2 = r2 or R2StorageService()
        self.config = load_profile(profile_path or "financial_profile.yaml")
        self.classifier = PageClassifier(config=self.config)

    def process_bctc(
        self,
        ticker: str,
        year: int,
        quarter: Union[int, str],
        scope: str,
        pdf_path: Union[str, Path],
        force: bool = False,
        announcement_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Quy trình trọn gói xử lý BCTC."""
        ticker = ticker.upper().strip()
        scope = scope.upper().strip()
        q_label = f"Q{quarter}" if str(quarter).isdigit() else str(quarter).upper()
        pdf_path = Path(pdf_path)

        if not pdf_path.is_file():
            raise FileNotFoundError(f"Không tìm thấy file PDF đầu vào: {pdf_path}")

        # 1. BƯỚC 1: Kiểm tra cờ trong Database (Tốc độ 0.5ms)
        if not force and self.repo.should_skip_classification(ticker, year, quarter, scope):
            rec = self.repo.get_record(ticker, year, quarter, scope)
            logger.info("⚡ [DB-CHECK] %s %d %s %s đã được xử lý và có trên R2. Bỏ qua.", ticker, year, q_label, scope)
            return {
                "status": "SKIPPED_ALREADY_EXISTS",
                "ticker": ticker,
                "year": year,
                "quarter": q_label,
                "scope": scope,
                "r2_key": rec.get("r2_pdf_key") if rec else None,
                "r2_url": rec.get("r2_pdf_url") if rec else None,
                "retained_pages": rec.get("retained_pages") if rec else None,
            }

        logger.info("🚀 Bắt đầu phân loại trang BCTC: %s (%s)", ticker, pdf_path.name)

        # 2. BƯỚC 2: Chạy PageClassifier trên CPU
        pdf_bytes = pdf_path.read_bytes()
        result: PageClassificationResult = self.classifier.classify_and_prune(pdf_bytes)

        logger.info(
            "📊 Hoàn thành cắt tỉa %s: Giữ lại %d/%d trang (Tỉ lệ tiết kiệm: %.1f%%)",
            ticker,
            result.retained_pages_count,
            result.total_pages,
            (1.0 - result.retained_pages_count / max(result.total_pages, 1)) * 100,
        )

        # 3. BƯỚC 3: Upload file PDF cắt tỉa lên Cloudflare R2
        upload_res = self.r2.upload_bctc_pruned_pdf(
            ticker=ticker,
            year=year,
            quarter=quarter,
            scope=scope,
            pdf_source=result.pruned_pdf_bytes,
        )

        # 4. BƯỚC 4: Ghi nhận cờ trạng thái vào Database
        self.repo.save_classification_result(
            ticker=ticker,
            year=year,
            quarter=int(quarter) if str(quarter).isdigit() else 4,
            scope=scope,
            total_raw_pages=result.total_pages,
            retained_pages=result.retained_pages_count,
            r2_pdf_key=upload_res["key"],
            r2_pdf_url=upload_res["url"],
            pdf_sha256=upload_res["sha256"],
            announcement_date=announcement_date,
        )

        return {
            "status": "SUCCESS",
            "ticker": ticker,
            "year": year,
            "quarter": q_label,
            "scope": scope,
            "total_raw_pages": result.total_pages,
            "retained_pages": result.retained_pages_count,
            "r2_key": upload_res["key"],
            "r2_url": upload_res["url"],
            "pdf_sha256": upload_res["sha256"],
        }
