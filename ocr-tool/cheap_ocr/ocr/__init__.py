# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
"""Async OCR client for the in-container vLLM OpenAI-compatible server.

Each detected region crop is sent as its own chat-completion request, using the
inline base64 data URL prepared by the crop workers. A single shared semaphore
bounds how many requests are in flight at once, which is what keeps vLLM's
continuous batcher full and the GPU saturated. There is no manual client-side
batching: dynamic batching happens inside the engine.
"""

import asyncio
import logging
import time
from typing import Any

import aiohttp

from cheap_ocr.config import OcrConfig
from cheap_ocr.models import PreparedRegion, RecognizedRegion, Task, TokenUsage
from cheap_ocr.ocr.constants import SERVED_MODEL_NAME
from cheap_ocr.ocr.prompts import PROMPTS
from cheap_ocr.stats import percentile

logger = logging.getLogger(__name__)


def _max_tokens_for_task(task_type: Task, config: OcrConfig) -> int:
    if task_type == Task.TABLE:
        return config.max_tokens_table
    if task_type == Task.FORMULA:
        return config.max_tokens_formula
    return config.max_tokens_text


class VllmOcrClient:
    """Send detected region crops to the local vLLM server concurrently."""

    def __init__(
        self,
        *,
        session: aiohttp.ClientSession,
        base_url: str,
        config: OcrConfig,
        request_timeout: float = 600.0,
    ) -> None:
        """Initialize the OCR client."""
        self._session = session
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._config = config
        self._semaphore = asyncio.Semaphore(max(1, int(config.ocr_request_concurrency)))
        self._timeout = aiohttp.ClientTimeout(total=request_timeout)
        self._metrics_started = time.perf_counter()
        self._requests_started = 0
        self._requests_completed = 0
        self._requests_failed = 0
        self._inflight_http = 0
        self._max_inflight_http = 0
        self._queue_latencies_ms: list[int] = []
        self._request_latencies_ms: list[int] = []
        self._input_tokens = 0
        self._output_tokens = 0
        self._total_tokens = 0
        self._last_progress_log = self._metrics_started

    async def recognize(self, regions: list[PreparedRegion]) -> list[RecognizedRegion]:
        """Recognize a list of prepared regions concurrently, preserving order."""
        if not regions:
            return []
        return list(await asyncio.gather(*(self._recognize_one(region) for region in regions)))

    async def _recognize_one(self, prepared: PreparedRegion) -> RecognizedRegion:
        task_type = prepared.region.task_type if prepared.region.task_type in PROMPTS else Task.TEXT
        payload = {
            "model": SERVED_MODEL_NAME,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": prepared.image_url},
                        },
                        {"type": "text", "text": PROMPTS[task_type]},
                    ],
                }
            ],
            "max_tokens": _max_tokens_for_task(task_type, self._config),
            "temperature": self._config.temperature,
            "top_p": self._config.top_p,
        }
        queued_at = time.perf_counter()
        async with self._semaphore:
            acquired_at = time.perf_counter()
            self._requests_started += 1
            self._inflight_http += 1
            self._max_inflight_http = max(self._max_inflight_http, self._inflight_http)
            try:
                async with self._session.post(self._url, json=payload, timeout=self._timeout) as response:
                    if response.status != 200:
                        text = await response.text()
                        request_latency_ms = int((time.perf_counter() - acquired_at) * 1000)
                        result = self._failed(prepared, response.status, text[:500], request_latency_ms)
                    else:
                        data = await response.json()
                        request_latency_ms = int((time.perf_counter() - acquired_at) * 1000)
                        result = self._recognized(prepared, data, request_latency_ms)
            except Exception as exc:
                request_latency_ms = int((time.perf_counter() - acquired_at) * 1000)
                result = self._failed(prepared, 500, f"{type(exc).__name__}: {exc}", request_latency_ms)
            finally:
                self._inflight_http -= 1
        queue_latency_ms = int((acquired_at - queued_at) * 1000)
        self._record_result(result, queue_latency_ms, request_latency_ms)
        return result

    def _recognized(self, prepared: PreparedRegion, data: dict[str, Any], latency_ms: int) -> RecognizedRegion:
        choices = data.get("choices") or []
        content = ""
        if choices:
            message = choices[0].get("message") or {}
            content = str(message.get("content") or "").strip()
        usage_raw = data.get("usage") or {}
        # The vLLM/OpenAI response reports prompt_tokens/completion_tokens; store them
        # under the input_tokens/output_tokens names used everywhere downstream.
        usage = TokenUsage(
            input_tokens=int(usage_raw.get("prompt_tokens") or 0),
            output_tokens=int(usage_raw.get("completion_tokens") or 0),
            total_tokens=int(usage_raw.get("total_tokens") or 0),
        )
        return RecognizedRegion(
            region=prepared.region,
            content=content,
            usage=usage,
            status_code=200,
            latency_ms=latency_ms,
        )

    def _record_result(self, result: RecognizedRegion, queue_latency_ms: int, request_latency_ms: int) -> None:
        self._requests_completed += 1
        self._queue_latencies_ms.append(queue_latency_ms)
        self._request_latencies_ms.append(request_latency_ms)
        if result.status_code != 200:
            self._requests_failed += 1
        usage = result.usage or TokenUsage()
        self._input_tokens += usage.input_tokens
        self._output_tokens += usage.output_tokens
        self._total_tokens += usage.total_tokens
        now = time.perf_counter()
        if now - self._last_progress_log >= 30.0:
            self._last_progress_log = now
            snapshot = self.metrics_snapshot(now=now)
            logger.info(
                "ocr_client_progress completed=%d failed=%d inflight_http=%d max_inflight_http=%d "
                "output_tokens_per_second=%.1f request_latency_p50_ms=%s request_latency_p95_ms=%s queue_p95_ms=%s",
                snapshot["requests"]["completed"],
                snapshot["requests"]["failed"],
                snapshot["requests"]["inflight_http"],
                snapshot["requests"]["max_inflight_http"],
                snapshot["throughput"].get("output_tokens_per_second") or 0.0,
                snapshot["latency_ms"].get("request_p50"),
                snapshot["latency_ms"].get("request_p95"),
                snapshot["latency_ms"].get("queue_p95"),
            )

    def metrics_snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        """Return cumulative client-side OCR request metrics."""
        finished = time.perf_counter() if now is None else now
        wall_seconds = finished - self._metrics_started
        return {
            "wall_seconds": wall_seconds,
            "requests": {
                "started": self._requests_started,
                "completed": self._requests_completed,
                "failed": self._requests_failed,
                "inflight_http": self._inflight_http,
                "max_inflight_http": self._max_inflight_http,
            },
            "tokens": {
                "input_tokens": self._input_tokens,
                "output_tokens": self._output_tokens,
                "total_tokens": self._total_tokens,
            },
            "throughput": {
                "requests_per_second": self._requests_completed / wall_seconds if wall_seconds > 0 else None,
                "input_tokens_per_second": self._input_tokens / wall_seconds if wall_seconds > 0 else None,
                "output_tokens_per_second": self._output_tokens / wall_seconds if wall_seconds > 0 else None,
                "total_tokens_per_second": self._total_tokens / wall_seconds if wall_seconds > 0 else None,
            },
            "latency_ms": {
                "request_p50": percentile(self._request_latencies_ms, 50),
                "request_p95": percentile(self._request_latencies_ms, 95),
                "request_max": max(self._request_latencies_ms) if self._request_latencies_ms else None,
                "queue_p50": percentile(self._queue_latencies_ms, 50),
                "queue_p95": percentile(self._queue_latencies_ms, 95),
                "queue_max": max(self._queue_latencies_ms) if self._queue_latencies_ms else None,
            },
        }

    @staticmethod
    def _failed(
        prepared: PreparedRegion, status_code: int, message: str, latency_ms: int | None = None
    ) -> RecognizedRegion:
        logger.warning(
            "ocr_request_failed page=%s region=%s status=%s error=%s",
            prepared.region.page_index,
            prepared.region.region_index,
            status_code,
            message,
        )
        return RecognizedRegion(
            region=prepared.region,
            content=None,
            status_code=status_code,
            latency_ms=latency_ms,
        )
