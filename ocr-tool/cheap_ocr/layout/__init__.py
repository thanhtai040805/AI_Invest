"""Async wrapper around the GPU-co-located layout detector.

The detector (TensorRT/ONNX RT-DETR by default, or the PyTorch PP-DocLayoutV3)
shares the GPU with the vLLM OCR server. Its forward passes are serialized on a
single worker thread so two documents never run it concurrently, while the event
loop stays free to render PDFs, crop regions, and stream OCR requests.
"""

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from PIL import Image

from cheap_ocr.config import EngineConfig
from cheap_ocr.models import Page, Region, StageTiming

logger = logging.getLogger(__name__)


class _Detector(Protocol):
    def detect_pages(self, pages: list[Page]) -> dict[int, list[Region]]: ...


def _build_detector(config: EngineConfig) -> _Detector:
    if config.layout_backend == "onnx":
        try:
            from cheap_ocr.layout.onnx import OnnxLayoutDetector, tensorrt_providers

            os.makedirs(config.trt_layout_cache, exist_ok=True)
            providers = tensorrt_providers(
                config.trt_layout_cache,
                opt_batch=config.layout_batch_size,
                max_batch=config.layout_batch_size,
                opt_level=config.trt_builder_opt_level,
            )
            return OnnxLayoutDetector(
                providers=providers,
                batch_size=config.layout_batch_size,
                score_threshold=config.layout_threshold,
            )
        except Exception as exc:
            logger.warning("onnx_layout_init_failed falling_back_to_transformers error=%s: %s", type(exc).__name__, exc)

    from cheap_ocr.layout.torch import LayoutDetector

    return LayoutDetector(batch_size=config.layout_batch_size, threshold=config.layout_threshold)


class LayoutEngine:
    """Serialize layout detection on a single worker thread.

    The layout fields of :class:`~cheap_ocr.config.EngineConfig` are
    startup-scoped: the detector is built once per engine start, and the
    TensorRT engine profile is compiled to match the batch size.
    ``layout_threshold=None`` uses each backend's calibrated default.
    """

    def __init__(self, config: EngineConfig | None = None) -> None:
        """Load the configured detector and create its single worker thread."""
        self._config = config or EngineConfig()
        self._detector = _build_detector(self._config)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="layout")

    @property
    def backend(self) -> str:
        """Return the active layout backend and, for ONNX, its execution providers."""
        active = getattr(self._detector, "active_providers", None)
        configured = self._config.layout_backend
        return f"{configured}:{active}" if active else configured

    def warmup(self) -> None:
        """Run one tiny forward pass to settle GPU memory / build the TRT engine before vLLM."""
        image = Image.new("RGB", (256, 256), color="white")
        page = Page(page_index=0, width=256, height=256, image=image)

        self._detector.detect_pages([page])

        image.close()

    async def detect(self, pages: list[Page]) -> tuple[dict[int, list[Region]], StageTiming]:
        """Detect layout regions for rendered pages, with queue/exec timing."""
        if not pages:
            return {}, StageTiming()
        loop = asyncio.get_running_loop()
        submitted = time.perf_counter()

        def _detect() -> tuple[dict[int, list[Region]], StageTiming]:
            started = time.perf_counter()
            regions = self._detector.detect_pages(pages)
            finished = time.perf_counter()
            return regions, StageTiming(
                queue_seconds=started - submitted,
                exec_seconds=finished - started,
                wall_seconds=finished - submitted,
            )

        return await loop.run_in_executor(self._executor, _detect)

    def close(self) -> None:
        """Shut down the layout worker thread."""
        self._executor.shutdown(wait=False)
