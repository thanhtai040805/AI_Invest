import json
import os
import sys
import urllib.request
from typing import Tuple, Dict, Any, Optional, List

os.environ["AIOHTTP_NO_EXTENSIONS"] = "1"
try:
    import aiohttp
    import aiohttp.resolver
    aiohttp.resolver.DefaultResolver = aiohttp.ThreadedResolver
    aiohttp.resolver.AsyncResolver = aiohttp.ThreadedResolver
except Exception:
    pass

import modal

from .config import FinancialProfileConfig, load_profile
from .page_classifier import PageClassifier, PageClassificationResult, PageMeta
from .region_classifier import RegionClassifier
from .benchmarking import BenchmarkTracker, PipelineMetrics
from .mineru_client import MinerUClient, MinerUQuotaExceededError


class FinancialOcrPipeline:
    """Financial OCR Pipeline cho Báo cáo tài chính doanh nghiệp Việt Nam (Dự án AIInvest HOSE).

    Kiến trúc Hybrid Ingestion:
    1. MinerU Cloud API (Primary - Free Tier) với tự động Fallback sang Modal GPU (GLM-OCR / Qwen2.5-VL)
    2. Standard BCTC (≤ 40 trang): Document-level Batching (GpuWorker.process_batch.map)
    3. CPU PageClassifier & RegionClassifier filtering
    """

    def __init__(self, profile_path: str = "financial_profile.yaml"):
        self.config = load_profile(profile_path)
        self.page_classifier = PageClassifier(self.config)
        self.region_classifier = RegionClassifier(self.config)
        self.mineru_client = MinerUClient(
            api_key=os.getenv(self.config.mineru.api_key_env, ""),
            api_base_url=self.config.mineru.api_base_url,
            timeout_seconds=self.config.mineru.timeout_seconds
        )

    def process_pdf_bytes(
        self,
        pdf_bytes: bytes,
        filename: str = "document.pdf",
        uri: str = "",
        enable_filtering: bool = True
    ) -> Tuple[str, PipelineMetrics, PageClassificationResult]:
        """Xử lý 1 file PDF đơn lẻ qua pipeline."""
        tracker = BenchmarkTracker(document_id="doc_fin_1", filename=filename)
        tracker.pdf_size_bytes = len(pdf_bytes)

        # Giai đoạn 1: Page Classification & Pruning (CPU)
        tracker.start_classification()
        if enable_filtering:
            class_result = self.page_classifier.classify_and_prune(pdf_bytes)
            target_pdf_bytes = class_result.pruned_pdf_bytes
        else:
            import fitz
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            total_pages = len(doc)
            doc.close()
            class_result = PageClassificationResult(
                total_pages=total_pages,
                retained_pages_count=total_pages,
                skipped_pages_count=0,
                pages_meta=[],
                retained_page_indices=list(range(total_pages)),
                pruned_pdf_bytes=pdf_bytes
            )
            target_pdf_bytes = pdf_bytes

        tracker.end_classification(
            total_pages=class_result.total_pages,
            retained_pages=class_result.retained_pages_count
        )

        # Giai đoạn 2: Gọi OCR Provider (Thử MinerU -> Fallback Modal GPU)
        raw_markdown = None
        if self.config.mineru.enabled and self.mineru_client.is_configured:
            tracker.start_modal_ocr(provider="mineru")
            try:
                print(f"[+] 🚀 Gửi file '{filename}' tới MinerU Cloud API (Free Quota)...")
                raw_markdown = self.mineru_client.extract_pdf_bytes(target_pdf_bytes, filename=filename)
            except MinerUQuotaExceededError as q_err:
                print(f"[!] ⚠️ MinerU Quota Limit: {q_err} -> Tự động chuyển (Fallback) sang Modal Serverless GPU!")
            except Exception as err:
                print(f"[!] ⚠️ Lỗi MinerU API: {err} -> Chuyển (Fallback) sang Modal Serverless GPU!")

        if not raw_markdown:
            tracker.start_modal_ocr(provider="modal_gpu")
            print(f"[+] ⚡ Đang thực thi Modal GPU Worker (GLM-OCR)...")
            raw_markdown = self._call_modal_gpu_ocr(target_pdf_bytes, filename=filename, uri=uri)

        tracker.end_modal_ocr()


        # Giai đoạn 3: Region Classifier & Markdown Post-Processing
        tracker.start_region_filtering()
        if enable_filtering and raw_markdown:
            final_markdown, region_stats = self.region_classifier.filter_markdown_sections(raw_markdown)
        else:
            final_markdown = raw_markdown or ""
            region_stats = {}
        tracker.end_region_filtering(final_markdown)

        # Giai đoạn 4: In báo cáo Benchmark
        metrics = tracker.print_summary_report()

        return final_markdown, metrics, class_result

    def process_pdf_url(
        self,
        pdf_url: str,
        enable_filtering: bool = True
    ) -> Tuple[str, PipelineMetrics, PageClassificationResult]:
        """Tải 1 file PDF từ URL và thực thi qua pipeline."""
        print(f"[+] Downloading PDF from URL: {pdf_url}")
        req = urllib.request.Request(
            pdf_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req) as response:
            pdf_bytes = response.read()

        filename = pdf_url.split("/")[-1].split("?")[0] or "financial_report.pdf"
        if not filename.endswith(".pdf"):
            filename += ".pdf"

        return self.process_pdf_bytes(
            pdf_bytes=pdf_bytes,
            filename=filename,
            uri=pdf_url,
            enable_filtering=enable_filtering
        )

    def process_batch_urls_map(
        self,
        pdf_urls: List[str],
        enable_filtering: bool = True
    ) -> List[Dict[str, Any]]:
        """Xử lý danh sách nhiều file BCTC bằng Document-level Batching (process_batch.map).

        Chiến lược Nền tảng AIInvest: Chạy song song N GPU container L40S trên Modal cho Mùa BCTC.
        """
        print(f"\n🚀 Khởi chạy Batch Ingestion cho {len(pdf_urls)} file BCTC qua Modal GPU Map...")
        from cheap_ocr.models import DocumentInput
        from cheap_ocr.config import OcrConfig

        doc_inputs = []
        class_results = []
        trackers = []

        # 1. Pre-filtering hàng loạt trên CPU SONG SONG ĐA LUỒNG (16 workers tận dụng hết CPU Cores)
        from concurrent.futures import ThreadPoolExecutor

        def _process_one_cpu(item_tuple):
            idx, url = item_tuple
            filename = url.split("/")[-1].split("?")[0] or f"document_{idx+1}.pdf"
            if not filename.endswith(".pdf"):
                filename += ".pdf"

            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req) as resp:
                pdf_bytes = resp.read()

            tracker = BenchmarkTracker(document_id=f"doc_{idx+1}", filename=filename)
            tracker.pdf_size_bytes = len(pdf_bytes)
            tracker.start_classification()

            if enable_filtering:
                c_res = self.page_classifier.classify_and_prune(pdf_bytes)
                target_bytes = c_res.pruned_pdf_bytes
            else:
                target_bytes = pdf_bytes
                c_res = None

            tracker.end_classification(
                total_pages=c_res.total_pages if c_res else 0,
                retained_pages=c_res.retained_pages_count if c_res else 0
            )

            doc_input = DocumentInput(
                input_id=f"batch_doc_{idx+1}",
                uri=url,
                relative_path=filename,
                data=target_bytes
            )
            return idx, doc_input, c_res, tracker

        workers = getattr(self.config.pipeline, "max_cpu_workers", 10)
        print(f"[+] ⚡ Chạy CPU Classify SONG SONG cho {len(pdf_urls)} file (ThreadPool {workers} Cores)...")
        indexed_urls = list(enumerate(pdf_urls))
        with ThreadPoolExecutor(max_workers=min(workers, len(pdf_urls))) as executor:
            cpu_outputs = list(executor.map(_process_one_cpu, indexed_urls))


        # Sắp xếp lại theo đúng thứ tự ban đầu
        cpu_outputs.sort(key=lambda x: x[0])
        doc_inputs = [x[1] for x in cpu_outputs]
        class_results = [x[2] for x in cpu_outputs]
        trackers = [x[3] for x in cpu_outputs]


        # 2. Gọi Modal GpuWorker.process_batch.map() song song N GPU Containers
        print(f"\n⚡ Đang kích hoạt Modal GPU Map cho {len(doc_inputs)} container GPU L40S song song...")
        GpuWorker = modal.Cls.from_name("cheap-ocr", "GpuWorker")
        config = OcrConfig(force=True, pdf_dpi=150)

        # Đóng gói danh sách batch cho map (mỗi document_input là 1 item list)
        items = [[d] for d in doc_inputs]

        results = list(GpuWorker().process_batch.map(
            items,
            kwargs={
                "config": config,
                "source": "ainvest_batch",
                "target": "output",
                "write": False,
                "return_payloads": True
            },
            order_outputs=True
        ))

        # 3. Tổng hợp kết quả & Lọc Region
        final_outputs = []
        for idx, res_batch in enumerate(results):
            tracker = trackers[idx]
            tracker.start_modal_ocr()
            tracker.end_modal_ocr()

            docs = res_batch.get("documents", [])
            raw_md = ""
            if docs:
                d = docs[0]
                raw_md = d.get("markdown", "") if isinstance(d, dict) else getattr(d, "markdown", "")

            tracker.start_region_filtering()
            if enable_filtering and raw_md:
                final_md, _ = self.region_classifier.filter_markdown_sections(raw_md)
            else:
                final_md = raw_md
            tracker.end_region_filtering(final_md)

            metrics = tracker.get_metrics()
            final_outputs.append({
                "url": pdf_urls[idx],
                "filename": tracker.filename,
                "markdown": final_md,
                "metrics": metrics,
                "page_result": class_results[idx]
            })

        print(f"\n✅ Đã hoàn thành Batch Ingestion cho {len(final_outputs)} file BCTC thành công!")
        return final_outputs

    def process_batch_urls_cloud(
        self,
        pdf_urls: List[str],
        enable_filtering: bool = True,
        config: Optional[Dict[str, Any]] = None,
        batch_policy: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
        detach: bool = False,
    ) -> Optional[List[Dict[str, Any]]]:
        """Xử lý danh sách BCTC với orchestration chạy hoàn toàn trên Modal CPU.

        Download + page classify chạy song song trên Modal CPU
        (``classify_one.map``), GPU work farm qua ``GpuWorker.process_batch``
        (cheap-ocr app) theo batch policy, kết quả checkpoint trên volume để
        resume. Máy local chỉ submit URLs và đợi.

        Args:
            pdf_urls: Danh sách URL file BCTC.
            enable_filtering: Bật page + region filtering.
            config: Override OcrConfig (dict). Mặc định ``{force: True, pdf_dpi: 200}``.
            batch_policy: Grouping GPU batch ``{max_docs, max_bytes_mb, max_pages}``.
            max_retries: Số lần retry tối đa cho 1 batch GPU thất bại.
            detach: True → spawn supervisor, in function_call_id, trả None.

        Returns:
            List các dict giống ``process_batch_urls_map`` (url, filename,
            markdown, metrics: PipelineMetrics, page_result) hoặc None khi detach.
        """
        from financial_pipeline.modal.supervisor import BatchSupervisor

        print(f"\n🚀 Khởi chạy Batch Ingestion cho {len(pdf_urls)} file BCTC qua financial-ocr supervisor (Modal CPU)...")
        BatchSupervisorHandle = modal.Cls.from_name("financial-ocr", "BatchSupervisor")
        if detach:
            call = BatchSupervisorHandle().run.spawn(
                pdf_urls, config, batch_policy, enable_filtering, max_retries
            )
            print(json.dumps({"status": "submitted", "function_call_id": getattr(call, "object_id", None)}))
            return None

        payload = BatchSupervisorHandle().run.remote(
            pdf_urls, config, batch_policy, enable_filtering, max_retries
        )
        return self._reconstruct_outputs(payload)

    def _reconstruct_outputs(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Chuyển JSON-safe payload từ supervisor về đúng shape process_batch_urls_map."""
        outputs = []
        for item in payload.get("outputs", []):
            metrics = PipelineMetrics(**item["metrics"])
            pr = item["page_result"]
            page_result = PageClassificationResult(
                total_pages=pr["total_pages"],
                retained_pages_count=pr["retained_pages_count"],
                skipped_pages_count=pr["skipped_pages_count"],
                pages_meta=[PageMeta(**meta) for meta in pr["pages_meta"]],
                retained_page_indices=pr["retained_page_indices"],
                pruned_pdf_bytes=b"",
            )
            outputs.append({
                "url": item["url"],
                "filename": item["filename"],
                "markdown": item["markdown"],
                "metrics": metrics,
                "page_result": page_result,
            })
        if payload.get("failed"):
            print(f"[!] {len(payload['failed'])} documents failed at GPU and were not checkpointed.")
        return outputs

    def _call_modal_gpu_ocr(self, pdf_bytes: bytes, filename: str, uri: str) -> str:
        """Đóng gói bytes và gửi sang Modal GPU Worker."""
        from cheap_ocr.models import DocumentInput
        from cheap_ocr.config import OcrConfig

        GpuWorker = modal.Cls.from_name("cheap-ocr", "GpuWorker")

        doc_input = DocumentInput(
            input_id="doc_financial_pipeline",
            uri=uri or filename,
            relative_path=filename,
            data=pdf_bytes
        )

        config = OcrConfig(
            force=True,
            pdf_dpi=150
        )

        res = GpuWorker().process_batch.remote(
            [doc_input],
            config,
            source="financial_pipeline",
            target="output",
            batch_id="batch_fin_1",
            write=False,
            return_payloads=True
        )

        documents = res.get("documents", [])
        if not documents:
            return ""

        doc = documents[0]
        if isinstance(doc, dict):
            md = doc.get("markdown", "")
            if not md and "result" in doc and doc["result"] is not None:
                res_obj = doc["result"]
                md = getattr(res_obj, "markdown", "") if hasattr(res_obj, "markdown") else str(res_obj)
            return md
        else:
            if hasattr(doc, "markdown") and doc.markdown:
                return doc.markdown
            elif hasattr(doc, "result") and doc.result is not None:
                return getattr(doc.result, "markdown", "") or ""
            return ""
