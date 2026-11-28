"""MinerU Official API v4 (`https://mineru.net/api/v4`) exclusive adapter.

Supports two execution modes against Official MinerU v4:
1. Zero-Upload Direct URL Task (`POST /api/v4/extract/task` + `GET /api/v4/extract/task/{task_id}`)
   when an HTTPS Presigned R2 GET URL (`remote_source_url`) is available, never persisting
   the secret URL into `state` / `job.payload`, and automatically falling back to batch
   presigned upload if MinerU cannot pull the URL directly.
2. Batch Presigned Streaming Upload (`POST /api/v4/file-urls/batch` + 256 KB streaming `PUT`
   + `GET /api/v4/extract-results/batch/{batch_id}`) for local files up to 200 MB.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

import httpx

from sag_api.core.config import Settings
from sag_api.core.errors import (
    ApiError,
    ConfigurationError,
    ServiceUnavailableError,
    UpstreamError,
    ValidationError,
)
from sag_api.parsing import mineru as mineru_core
from sag_api.parsing.mineru import (
    ParsePaused,
    PauseCallback,
    StateCallback,
    check_mineru_response,
    download_mineru_result_markdown,
)

log = logging.getLogger(__name__)

_QUEUE_STATES = {"waiting-file", "pending", "queued"}
_RUNNING_STATES = {"running", "processing", "converting"}
_PENDING_STATES = _QUEUE_STATES | _RUNNING_STATES

MINERU_VALID_LANGUAGES: frozenset[str] = frozenset(
    {
        "ch",
        "en",
        "korean",
        "japan",
        "chinese_cht",
        "ta",
        "te",
        "ka",
        "latin",
        "arabic",
        "cyrillic",
        "devanagari",
        "th",
        "el",
        "east_slavic",
    }
)

_MINERU_LANGUAGE_ALIASES: dict[str, str] = {
    # MinerU official OCR packs group Vietnamese under the extended Latin script pack (`latin`).
    "vi": "latin",
    "vie": "latin",
    "vn": "latin",
    "vietnamese": "latin",
    "zh": "ch",
    "zh-cn": "ch",
    "zh_cn": "ch",
    "cn": "ch",
    "zh-tw": "chinese_cht",
    "zh_tw": "chinese_cht",
    "zh-hk": "chinese_cht",
    "ja": "japan",
    "jp": "japan",
    "ko": "korean",
    "kr": "korean",
}


def normalize_mineru_language(raw: str | None, *, default: str = "latin") -> str:
    """Map application language codes (`vi`, `zh`, ...) to canonical MinerU Official API v4 packs."""
    cleaned = (raw or "").strip().lower()
    if not cleaned:
        return default
    if cleaned in _MINERU_LANGUAGE_ALIASES:
        return _MINERU_LANGUAGE_ALIASES[cleaned]
    if cleaned in MINERU_VALID_LANGUAGES:
        return cleaned
    return default


def _official_pending_status(raw_state: str) -> str:
    return "queued" if raw_state in _QUEUE_STATES else "running"


_POOL_LOCK = threading.Lock()
_RR_COUNTER: int = 0
_IN_FLIGHT_BY_FP: dict[str, int] = defaultdict(int)
_COOLDOWN_UNTIL_BY_FP: dict[str, float] = {}
_TOKEN_SEMAPHORES: dict[tuple[int, str, int], asyncio.Semaphore] = {}


def _token_fp(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def reset_mineru_token_pool_state() -> None:
    """Reset process-wide Round-Robin cursor, in-flight counters, and cooldowns (for tests)."""
    global _RR_COUNTER
    with _POOL_LOCK:
        _RR_COUNTER = 0
        _IN_FLIGHT_BY_FP.clear()
        _COOLDOWN_UNTIL_BY_FP.clear()
        _TOKEN_SEMAPHORES.clear()


def get_mineru_token_pool_snapshot(tokens: tuple[str, ...]) -> list[dict[str, Any]]:
    """Return redacted per-token load and health snapshot (never exposes raw tokens)."""
    now = time.monotonic()
    with _POOL_LOCK:
        out: list[dict[str, Any]] = []
        for idx, tok in enumerate(tokens):
            fp = _token_fp(tok)
            cd_until = _COOLDOWN_UNTIL_BY_FP.get(fp, 0.0)
            out.append(
                {
                    "slot": idx,
                    "token_fp": fp,
                    "in_flight": _IN_FLIGHT_BY_FP.get(fp, 0),
                    "healthy": now >= cd_until,
                    "cooldown_remaining_s": round(max(0.0, cd_until - now), 2),
                }
            )
        return out


def _select_ordered_tokens(tokens: tuple[str, ...]) -> list[str]:
    """Select candidate tokens using Atomic Round-Robin + Least-In-Flight load balancing."""
    global _RR_COUNTER
    if len(tokens) <= 1:
        return list(tokens)
    now = time.monotonic()
    with _POOL_LOCK:
        start = _RR_COUNTER % len(tokens)
        _RR_COUNTER += 1
        rotated = [tokens[(start + i) % len(tokens)] for i in range(len(tokens))]
        healthy: list[str] = []
        cooled: list[str] = []
        for tok in rotated:
            fp = _token_fp(tok)
            if now >= _COOLDOWN_UNTIL_BY_FP.get(fp, 0.0):
                healthy.append(tok)
            else:
                cooled.append(tok)
        # Python Timsort is stable: equal in-flight counts preserve strict Round-Robin order!
        healthy.sort(key=lambda tok: _IN_FLIGHT_BY_FP.get(_token_fp(tok), 0))
        return healthy + cooled


def _mark_token_cooldown(token: str, seconds: float, reason: str) -> None:
    if seconds <= 0:
        return
    fp = _token_fp(token)
    with _POOL_LOCK:
        _COOLDOWN_UNTIL_BY_FP[fp] = max(_COOLDOWN_UNTIL_BY_FP.get(fp, 0.0), time.monotonic() + seconds)
    log.warning("MinerU token fp=%s entered %.0fs failover cooldown (%s)", fp, seconds, reason)


def _failover_cooldown_seconds(exc: Exception) -> float:
    """Return >0 cooldown seconds if `exc` warrants immediate failover to the next MinerU token."""
    if isinstance(exc, ConfigurationError):
        # HTTP 401/403 (e.g. A0211 user token expired or revoked key)
        return 300.0
    if isinstance(exc, ServiceUnavailableError):
        # HTTP 429 rate-limit, 5xx, or transient upstream timeout
        return 30.0
    if isinstance(exc, UpstreamError):
        msg = str(exc).lower()
        if any(
            kw in msg
            for kw in ("a0211", "token", "quota", "limit", "exceed", "frequency", "鉴权", "过期", "上限")
        ):
            return 120.0
    return 0.0


@asynccontextmanager
async def _acquire_token_slot(token: str, per_token_limit: int) -> AsyncIterator[None]:
    fp = _token_fp(token)
    loop_id = id(asyncio.get_running_loop())
    limit = max(1, int(per_token_limit))
    key = (loop_id, fp, limit)
    with _POOL_LOCK:
        sem = _TOKEN_SEMAPHORES.get(key)
        if sem is None:
            sem = asyncio.Semaphore(limit)
            _TOKEN_SEMAPHORES[key] = sem
    async with sem:
        with _POOL_LOCK:
            _IN_FLIGHT_BY_FP[fp] += 1
        try:
            yield
        finally:
            with _POOL_LOCK:
                _IN_FLIGHT_BY_FP[fp] = max(0, _IN_FLIGHT_BY_FP[fp] - 1)


class OfficialMinerUClient:
    """Exclusive MinerU Official API v4 client with Multi-Token Round-Robin & Auto-Failover."""

    def __init__(self, settings: Settings, *, language: str | None = None):
        if not settings.mineru_configured:
            raise ConfigurationError(
                "MinerU Official API 尚未配置 Base URL 与 SAG_MINERU_OFFICIAL_API_TOKEN"
            )
        self._base_url = str(settings.mineru_base_url).rstrip("/")
        self._api_root = _official_api_root(self._base_url)
        raw_pool = getattr(settings, "effective_mineru_official_api_tokens", None)
        if raw_pool:
            tokens = tuple(str(t).strip() for t in raw_pool if str(t).strip())
        else:
            single = str(getattr(settings, "mineru_official_api_token", "") or "").strip()
            tokens = (single,) if single else ()
        if not tokens:
            raise ConfigurationError(
                "MinerU Official API 尚未配置 Base URL 与 SAG_MINERU_OFFICIAL_API_TOKEN"
            )
        self._tokens: tuple[str, ...] = tokens
        self._api_token = self._tokens[0]
        self._per_token_concurrency = max(
            1, int(getattr(settings, "mineru_per_token_concurrency", 8))
        )
        self._official_model = settings.mineru_official_model
        self._parse_method = settings.mineru_parse_method
        raw_lang = language if language is not None else getattr(settings, "sag_language", "vi")
        self._language = normalize_mineru_language(str(raw_lang or ""), default="latin")
        self._request_timeout = max(1.0, float(settings.mineru_request_timeout))
        self._poll_interval = max(0.05, float(settings.mineru_poll_interval))
        self._poll_timeout = max(self._poll_interval, float(settings.mineru_poll_timeout))
        self._result_limit = max(1, int(settings.mineru_result_max_mb)) * 1024 * 1024

    @property
    def tokens(self) -> tuple[str, ...]:
        return self._tokens

    def _pin_token_by_fp(self, token_fp: object) -> None:
        if not isinstance(token_fp, str) or not token_fp:
            return
        for tok in self._tokens:
            if _token_fp(tok) == token_fp:
                self._api_token = tok
                return

    @property
    def signature(self) -> str:
        base = f"mineru-official-{self._official_model}-{self._parse_method}"
        return base if self._language == "en" else f"{base}-{self._language}"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_token}"}

    async def parse(
        self,
        path: str,
        *,
        state: dict[str, Any] | None = None,
        on_state: StateCallback | None = None,
        should_pause: PauseCallback | None = None,
        remote_source_url: str | None = None,
    ) -> str:
        filename = os.path.basename(path)
        current = dict(state or {})
        saved_filename = current.get("filename")
        if isinstance(saved_filename, str) and saved_filename:
            filename = saved_filename

        current["mineru_service"] = "official"
        current["mineru_model"] = self._official_model

        existing_task_id = current.get("task_id")
        existing_batch_id = current.get("batch_id")
        has_task_id = isinstance(existing_task_id, str) and bool(existing_task_id.strip())
        has_batch_id = isinstance(existing_batch_id, str) and bool(existing_batch_id.strip())
        if has_task_id or has_batch_id:
            self._pin_token_by_fp(current.get("token_fp"))

        # Mode 1: Zero-upload direct URL task via POST /api/v4/extract/task
        if has_task_id or (
            not has_batch_id
            and isinstance(remote_source_url, str)
            and remote_source_url.startswith("https://")
        ):
            try:
                if not has_task_id:
                    assert isinstance(remote_source_url, str)
                    task_id = await self._create_url_task(remote_source_url)
                    current.update(
                        {
                            "task_id": task_id,
                            "token_fp": _token_fp(self._api_token),
                            "filename": filename,
                            "upload_source": "remote_presigned",
                            "status": "queued",
                        }
                    )
                    if on_state:
                        await on_state(dict(current))
                else:
                    task_id = str(existing_task_id).strip()

                async with _acquire_token_slot(self._api_token, self._per_token_concurrency):
                    result_url = await self._poll_task(
                        task_id,
                        state=current,
                        on_state=on_state,
                        should_pause=should_pause,
                    )
                    markdown = await self._download_markdown(result_url)
                if on_state:
                    await on_state({**current, "status": "done"})
                return markdown
            except (ConfigurationError, ParsePaused):
                raise
            except ApiError as remote_exc:
                if not os.path.isfile(path):
                    raise
                log.info(
                    "MinerU Official direct URL task fell back to batch presigned upload (%s): %s",
                    type(remote_exc).__name__,
                    remote_exc,
                )
                current.pop("task_id", None)
                current.pop("upload_source", None)

        # Mode 2: Batch presigned PUT streaming upload via POST /api/v4/file-urls/batch
        batch_id = current.get("batch_id")
        upload_url = current.get("upload_url")
        upload_completed = current.get("upload_completed") is True

        if not isinstance(batch_id, str) or not batch_id.strip():
            batch_id, upload_url = await self._request_upload(filename)
            current.update(
                {
                    "mineru_service": "official",
                    "mineru_model": self._official_model,
                    "batch_id": batch_id,
                    "token_fp": _token_fp(self._api_token),
                    "filename": filename,
                    "upload_url": upload_url,
                    "upload_completed": False,
                    "status": "uploading",
                }
            )
            if on_state:
                await on_state(dict(current))

        async with _acquire_token_slot(self._api_token, self._per_token_concurrency):
            if not upload_completed:
                if not isinstance(upload_url, str) or not upload_url:
                    raise UpstreamError("MinerU 官方任务缺少待续传的签名上传地址")
                await self._upload_file(path, upload_url)
                current["upload_completed"] = True
                current.pop("upload_url", None)
                current["status"] = "queued"
                if on_state:
                    await on_state(dict(current))

            result_url = await self._poll_batch(
                batch_id,
                filename,
                state=current,
                on_state=on_state,
                should_pause=should_pause,
            )
            markdown = await self._download_markdown(result_url)
        if on_state:
            await on_state({**current, "status": "done"})
        return markdown

    async def _create_url_task(self, remote_url: str) -> str:
        payload: dict[str, Any] = {
            "url": remote_url,
            "model_version": self._official_model,
            "language": self._language,
            "enable_table": True,
        }
        if self._parse_method == "txt":
            payload["is_ocr"] = False
        elif self._parse_method == "ocr":
            payload["is_ocr"] = True

        candidates = _select_ordered_tokens(self._tokens)
        for idx, cand in enumerate(candidates):
            try:
                response = await self._official_request(
                    "POST", "extract/task", token_override=cand, json=payload
                )
                data = _official_data(response, "创建 URL 解析任务")
                task_id = data.get("task_id")
                if not isinstance(task_id, str) or not task_id.strip():
                    raise UpstreamError("MinerU 官方 URL 解析响应中没有 task_id")
                self._api_token = cand
                return task_id.strip()
            except Exception as exc:
                cooldown = _failover_cooldown_seconds(exc)
                if cooldown > 0 and idx + 1 < len(candidates):
                    _mark_token_cooldown(cand, cooldown, type(exc).__name__)
                    continue
                raise
        raise ConfigurationError("MinerU Official API 无可用 Token")

    async def _poll_task(
        self,
        task_id: str,
        *,
        state: dict[str, Any] | None = None,
        on_state: StateCallback | None = None,
        should_pause: PauseCallback | None = None,
    ) -> str:
        deadline = time.monotonic() + self._poll_timeout
        base_state = dict(state or {})
        last_status: str | None = None
        while True:
            if should_pause is not None and await should_pause():
                raise ParsePaused()
            response = await self._official_request("GET", f"extract/task/{task_id}")
            data = _official_data(response, "查询解析任务")
            task_state = str(data.get("state") or "").lower()
            if task_state == "done":
                result_url = data.get("full_zip_url")
                if not isinstance(result_url, str) or not result_url:
                    raise UpstreamError("MinerU 官方任务已完成，但响应中没有 full_zip_url")
                return result_url
            if task_state == "failed":
                message = str(data.get("err_msg") or "未知错误")
                raise UpstreamError(f"MinerU 官方解析失败：{message}")
            if task_state not in _PENDING_STATES:
                raise UpstreamError(f"MinerU 官方返回未知任务状态：{task_state or '空'}")
            mapped_status = _official_pending_status(task_state)
            if mapped_status != last_status:
                last_status = mapped_status
                if on_state is not None:
                    await on_state({**base_state, "status": mapped_status})
            if time.monotonic() >= deadline:
                raise ServiceUnavailableError(
                    f"MinerU 官方解析等待超时（任务 {task_id}），后台将继续重试"
                )
            await asyncio.sleep(self._poll_interval)

    async def _request_upload(self, filename: str) -> tuple[str, str]:
        file_config: dict[str, Any] = {"name": filename}
        if self._parse_method == "txt":
            file_config["is_ocr"] = False
        elif self._parse_method == "ocr":
            file_config["is_ocr"] = True
        body = {
            "files": [file_config],
            "model_version": self._official_model,
            "language": self._language,
            "enable_table": True,
        }
        candidates = _select_ordered_tokens(self._tokens)
        for idx, cand in enumerate(candidates):
            try:
                response = await self._official_request(
                    "POST",
                    "file-urls/batch",
                    token_override=cand,
                    json=body,
                )
                data = _official_data(response, "申请上传地址")
                batch_id = data.get("batch_id")
                upload_urls = data.get("file_urls")
                if not isinstance(batch_id, str) or not batch_id.strip():
                    raise UpstreamError("MinerU 官方响应中没有 batch_id")
                if (
                    not isinstance(upload_urls, list)
                    or len(upload_urls) != 1
                    or not isinstance(upload_urls[0], str)
                    or not upload_urls[0]
                ):
                    raise UpstreamError("MinerU 官方响应中没有唯一的签名上传地址")
                self._api_token = cand
                return batch_id.strip(), upload_urls[0]
            except Exception as exc:
                cooldown = _failover_cooldown_seconds(exc)
                if cooldown > 0 and idx + 1 < len(candidates):
                    _mark_token_cooldown(cand, cooldown, type(exc).__name__)
                    continue
                raise
        raise ConfigurationError("MinerU Official API 无可用 Token")

    async def _upload_file(self, path: str, upload_url: str) -> None:
        try:
            if os.path.getsize(path) > 200 * 1024 * 1024:
                raise ValidationError("MinerU 官方接口限制文件不得超过 200MB")
        except OSError as exc:
            raise UpstreamError(f"无法读取待解析文件：{exc}") from exc
        await _validate_signed_url(upload_url)
        try:
            async with httpx.AsyncClient(timeout=self._request_timeout) as client:
                response = await client.request(
                    "PUT",
                    upload_url,
                    content=_file_chunks(path),
                )
        except OSError as exc:
            raise UpstreamError(f"无法读取待解析文件：{exc}") from exc
        except httpx.TimeoutException as exc:
            raise ServiceUnavailableError("上传文件到 MinerU 官方服务超时") from exc
        except httpx.RequestError as exc:
            raise ServiceUnavailableError(
                f"无法上传文件到 MinerU 官方服务：{exc}"
            ) from exc
        check_mineru_response(response, "上传文件到 MinerU 官方服务")

    async def _poll_batch(
        self,
        batch_id: str,
        filename: str,
        *,
        state: dict[str, Any] | None = None,
        on_state: StateCallback | None = None,
        should_pause: PauseCallback | None = None,
        api_token: str | None = None,
    ) -> str:
        deadline = time.monotonic() + self._poll_timeout
        base_state = dict(state or {})
        last_status: str | None = None
        while True:
            if should_pause is not None and await should_pause():
                raise ParsePaused()
            response = await self._official_request(
                "GET", f"extract-results/batch/{batch_id}", token_override=api_token
            )
            data = _official_data(response, "查询解析任务")
            results = data.get("extract_result")
            if not isinstance(results, list):
                raise UpstreamError("MinerU 官方响应中没有 extract_result")
            result = next(
                (
                    item
                    for item in results
                    if isinstance(item, dict) and item.get("file_name") == filename
                ),
                None,
            )
            if result is None and len(results) == 1 and isinstance(results[0], dict):
                result = results[0]
            if result is None:
                raise UpstreamError(
                    f"MinerU 官方响应中没有文件 {filename!r} 的解析结果"
                )
            batch_state = str(result.get("state") or "").lower()
            if batch_state == "done":
                result_url = result.get("full_zip_url")
                if not isinstance(result_url, str) or not result_url:
                    raise UpstreamError(
                        "MinerU 官方任务已完成，但响应中没有 full_zip_url"
                    )
                return result_url
            if batch_state == "failed":
                message = str(result.get("err_msg") or "未知错误")
                raise UpstreamError(f"MinerU 官方解析失败：{message}")
            if batch_state not in _PENDING_STATES:
                raise UpstreamError(
                    f"MinerU 官方返回未知任务状态：{batch_state or '空'}"
                )
            mapped_status = _official_pending_status(batch_state)
            if mapped_status != last_status:
                last_status = mapped_status
                if on_state is not None:
                    await on_state({**base_state, "status": mapped_status})
            if time.monotonic() >= deadline:
                raise ServiceUnavailableError(
                    f"MinerU 解析等待超时（批次 {batch_id}），后台将继续重试"
                )
            await asyncio.sleep(self._poll_interval)

    async def _download_markdown(self, url: str) -> str:
        return await download_mineru_result_markdown(
            url,
            request_timeout=self._request_timeout,
            result_limit=self._result_limit,
        )

    async def _official_request(
        self,
        method: str,
        path: str,
        *,
        token_override: str | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        url = f"{self._api_root}/{path.lstrip('/')}"
        await _validate_api_url(url)
        headers = (
            {"Authorization": f"Bearer {token_override}"}
            if token_override
            else self._headers
        )
        try:
            async with httpx.AsyncClient(timeout=self._request_timeout) as client:
                if method == "POST":
                    response = await client.post(
                        url, headers=headers, **kwargs
                    )
                else:
                    response = await client.request(
                        method, url, headers=headers, **kwargs
                    )
        except httpx.TimeoutException as exc:
            raise ServiceUnavailableError("MinerU 官方请求超时") from exc
        except httpx.RequestError as exc:
            raise ServiceUnavailableError(
                f"无法连接 MinerU 官方服务：{exc}"
            ) from exc
        return check_mineru_response(response, "调用 MinerU 官方服务")


def _official_api_root(base_url: str) -> str:
    parsed = urlparse(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError("MinerU 官方 Base URL 必须是安全的 HTTPS 地址")
    path = parsed.path.rstrip("/")
    if path in {"", "/api/v4", "/api/v4/extract/task", "/api/v4/file-urls/batch"}:
        api_path = "/api/v4"
    else:
        raise ConfigurationError(
            "MinerU 官方 Base URL 路径必须为空、/api/v4、/api/v4/extract/task 或 /api/v4/file-urls/batch"
        )
    return f"https://{parsed.netloc}{api_path}"


async def _validate_api_url(url: str) -> None:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or 443
    await mineru_core._assert_public_host(host, port)


async def _validate_signed_url(url: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise UpstreamError("MinerU 官方返回了不安全的签名上传地址")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise UpstreamError("MinerU 官方返回了不安全的签名上传地址") from exc
    await mineru_core._assert_public_host(parsed.hostname, port)


async def _file_chunks(path: str) -> AsyncIterator[bytes]:
    with open(path, "rb") as source:
        while chunk := await asyncio.to_thread(source.read, 256 * 1024):
            yield chunk


def _official_data(response: httpx.Response, action: str) -> dict[str, Any]:
    payload = mineru_core._response_payload(response)
    if not isinstance(payload, dict):
        raise UpstreamError(f"MinerU 官方{action}响应格式无效")
    code = payload.get("code")
    if str(code) != "0":
        message = mineru_core._find_error_message(payload) or f"错误码 {code}"
        trace_id = payload.get("trace_id")
        trace = f"（trace_id: {trace_id}）" if trace_id else ""
        raise UpstreamError(f"MinerU 官方{action}失败：{message}{trace}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise UpstreamError(f"MinerU 官方{action}响应中没有 data")
    return data
