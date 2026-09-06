"""Integration tests for BCTC Deduplication and Idempotency via Database Flags."""

import sys
import uuid
import pytest
from dotenv import load_dotenv

load_dotenv("d:/AIInvest/ai-engine/.env")

from app.domain.repositories.bctc_pipeline_repository import BctcPipelineRepository
from app.domain.services.r2_storage import R2StorageService


def test_db_flags_deduplication_lifecycle():
    repo = BctcPipelineRepository()
    # Cách ly hoàn toàn môi trường test: dùng bucket aiinvest-bctc-test
    r2 = R2StorageService(bucket_name="aiinvest-bctc-test")

    ticker = f"TCK_{uuid.uuid4().hex[:6].upper()}"
    year = 2025
    quarter = 4
    scope = "HN"

    try:
        # [Step 1] Kiem tra DB ban dau -> should_skip_classification phai la False
        assert repo.should_skip_classification(ticker, year, quarter, scope) is False

        # [Step 2] Tien hanh upload file PDF pruned len R2
        dummy_pdf = b"%PDF-1.4 Mock Pruned PDF for HPG Q4/2025"
        upload_res = r2.upload_bctc_pruned_pdf(
            ticker=ticker,
            year=year,
            quarter=quarter,
            scope=scope,
            pdf_source=dummy_pdf,
        )
        assert upload_res["status"] == "UPLOADED"

        # [Step 3] Luu co trang thai vao Database
        repo.save_classification_result(
            ticker=ticker,
            year=year,
            quarter=quarter,
            scope=scope,
            total_raw_pages=75,
            retained_pages=40,
            r2_pdf_key=upload_res["key"],
            r2_pdf_url=upload_res["url"],
            pdf_sha256=upload_res["sha256"],
            is_audited=True,
            auditor_name="KPMG VIETNAM",
        )

        # [Step 4] Kiem tra lai DB -> Bay gio should_skip_classification la True (0ms, chan ngay lap tuc)
        assert repo.should_skip_classification(ticker, year, quarter, scope) is True

        # [Step 5] Kiem tra OCR Flag ban dau
        assert repo.should_skip_ocr(ticker, year, quarter, scope) is False

        # [Step 6] Chay OCR xong -> Upload Markdown len R2 va luu co vao DB
        dummy_md = "# TAP DOAN HOA PHAT - BCTC HOP NHAT Q4/2025"
        upload_md_res = r2.upload_bctc_parsed_markdown(
            ticker=ticker,
            year=year,
            quarter=quarter,
            scope=scope,
            markdown_content=dummy_md,
        )
        assert upload_md_res["status"] == "UPLOADED"

        repo.save_ocr_result(
            ticker=ticker,
            year=year,
            quarter=quarter,
            scope=scope,
            r2_md_key=upload_md_res["key"],
            r2_md_url=upload_md_res["url"],
        )

        # [Step 7] Kiem tra lai DB cho OCR -> should_skip_ocr la True!
        assert repo.should_skip_ocr(ticker, year, quarter, scope) is True

    finally:
        # [TEARDOWN] Dọn sạch dữ liệu test trong Database và Cloudflare R2 để không làm ô nhiễm môi trường
        try:
            repo.storage.execute(
                "DELETE FROM bctc_pipeline_records WHERE ticker = %s;",
                (ticker,),
            )
        except Exception as e:
            print(f"Warning cleaning test ticker {ticker} in DB: {e}")

        try:
            if "upload_res" in locals() and upload_res and "key" in upload_res:
                r2.delete_object(upload_res["key"])
            if "upload_md_res" in locals() and upload_md_res and "key" in upload_md_res:
                r2.delete_object(upload_md_res["key"])
        except Exception as e:
            print(f"Warning cleaning test files on R2 for {ticker}: {e}")
