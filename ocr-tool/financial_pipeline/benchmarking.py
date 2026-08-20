import time
import json
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any


@dataclass
class PipelineMetrics:
    document_id: str
    filename: str
    pdf_size_bytes: int
    total_pdf_pages: int
    retained_pages: int
    skipped_pages: int
    page_reduction_ratio_pct: float
    time_page_classification_sec: float
    time_modal_ocr_sec: float
    time_region_filtering_sec: float
    total_elapsed_sec: float
    markdown_char_count: int
    estimated_tokens: int
    modal_gpu: str = "L40S"


class BenchmarkTracker:
    """Theo dõi và ghi nhận toàn bộ thông số hiệu năng (Latency, Token, Cost Savings) của Financial OCR Pipeline."""

    def __init__(self, document_id: str = "doc_1", filename: str = "document.pdf"):
        self.document_id = document_id
        self.filename = filename
        self.pdf_size_bytes = 0
        
        self.start_time = time.time()
        self.t_classification_start = 0.0
        self.t_classification_end = 0.0
        self.t_modal_start = 0.0
        self.t_modal_end = 0.0
        self.t_region_start = 0.0
        self.t_region_end = 0.0

        self.total_pages = 0
        self.retained_pages = 0
        self.skipped_pages = 0
        self.markdown_char_count = 0

    def start_classification(self):
        self.t_classification_start = time.time()

    def end_classification(self, total_pages: int, retained_pages: int):
        self.t_classification_end = time.time()
        self.total_pages = total_pages
        self.retained_pages = retained_pages
        self.skipped_pages = total_pages - retained_pages

    def start_modal_ocr(self):
        self.t_modal_start = time.time()

    def end_modal_ocr(self):
        self.t_modal_end = time.time()

    def start_region_filtering(self):
        self.t_region_start = time.time()

    def end_region_filtering(self, markdown_text: str):
        self.t_region_end = time.time()
        self.markdown_char_count = len(markdown_text)

    def get_metrics(self) -> PipelineMetrics:
        total_elapsed = time.time() - self.start_time
        time_class = max(0.0, self.t_classification_end - self.t_classification_start)
        time_modal = max(0.0, self.t_modal_end - self.t_modal_start)
        time_region = max(0.0, self.t_region_end - self.t_region_start)

        page_reduction_pct = (
            (self.skipped_pages / self.total_pages * 100.0) if self.total_pages > 0 else 0.0
        )
        estimated_tokens = int(self.markdown_char_count / 3.5)

        return PipelineMetrics(
            document_id=self.document_id,
            filename=self.filename,
            pdf_size_bytes=self.pdf_size_bytes,
            total_pdf_pages=self.total_pages,
            retained_pages=self.retained_pages,
            skipped_pages=self.skipped_pages,
            page_reduction_ratio_pct=round(page_reduction_pct, 1),
            time_page_classification_sec=round(time_class, 3),
            time_modal_ocr_sec=round(time_modal, 3),
            time_region_filtering_sec=round(time_region, 3),
            total_elapsed_sec=round(total_elapsed, 3),
            markdown_char_count=self.markdown_char_count,
            estimated_tokens=estimated_tokens
        )

    def print_summary_report(self) -> PipelineMetrics:
        total_elapsed = time.time() - self.start_time
        time_class = max(0.0, self.t_classification_end - self.t_classification_start)
        time_modal = max(0.0, self.t_modal_end - self.t_modal_start)
        time_region = max(0.0, self.t_region_end - self.t_region_start)

        page_reduction_pct = (
            (self.skipped_pages / self.total_pages * 100.0) if self.total_pages > 0 else 0.0
        )
        # Ước tính số token tiếng Việt (~3.5 chars / token cho tiếng Việt & markdown)
        estimated_tokens = int(self.markdown_char_count / 3.5)

        metrics = PipelineMetrics(
            document_id=self.document_id,
            filename=self.filename,
            pdf_size_bytes=self.pdf_size_bytes,
            total_pdf_pages=self.total_pages,
            retained_pages=self.retained_pages,
            skipped_pages=self.skipped_pages,
            page_reduction_ratio_pct=round(page_reduction_pct, 1),
            time_page_classification_sec=round(time_class, 3),
            time_modal_ocr_sec=round(time_modal, 3),
            time_region_filtering_sec=round(time_region, 3),
            total_elapsed_sec=round(total_elapsed, 3),
            markdown_char_count=self.markdown_char_count,
            estimated_tokens=estimated_tokens
        )

        print("\n" + "=" * 65)
        print(" 📊 BÁO CÁO BENCHMARK FINANCIAL OCR PIPELINE (BCTC VIỆT NAM)")
        print("=" * 65)
        print(f" 📄 File kiểm tra         : {metrics.filename}")
        print(f" 📦 Kích thước PDF        : {metrics.pdf_size_bytes / (1024*1024):.2f} MB")
        print(f" 📑 Tổng số trang PDF     : {metrics.total_pdf_pages} trang")
        print(f" ✂️ Trang được lọc (KEEP) : {metrics.retained_pages} trang")
        print(f" 🚫 Trang bỏ qua (SKIP)   : {metrics.skipped_pages} trang ({metrics.page_reduction_ratio_pct}% tiết kiệm)")
        print("-" * 65)
        print(" ⏱️  THỜI GIAN XỬ LÝ (LATENCY):")
        print(f"   • Page Classifier (CPU) : {metrics.time_page_classification_sec:.3f} s")
        print(f"   • Modal GPU Worker      : {metrics.time_modal_ocr_sec:.3f} s")
        print(f"   • Region Filtering      : {metrics.time_region_filtering_sec:.3f} s")
        print(f"   • TỔNG THỜI GIAN        : {metrics.total_elapsed_sec:.3f} s")
        print("-" * 65)
        print(" 🔤 DỮ LIỆU ĐẦU RA:")
        print(f"   • Ký tự Markdown        : {metrics.markdown_char_count:,} chars")
        print(f"   • Ước tính Tokens        : ~{metrics.estimated_tokens:,} tokens")
        print("=" * 65 + "\n")

        return metrics
