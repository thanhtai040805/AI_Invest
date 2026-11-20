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
import logging
import os
import time
from collections.abc import AsyncIterator
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


def _official_pending_status(raw_state: str) -> str:
    return "queued" if raw_state in _QUEUE_STATES else "running"


class OfficialMinerUClient:
    """Exclusive MinerU Official API v4 client authenticated via `SAG_MINERU_OFFICIAL_API_TOKEN`."""

    def __init__(self, settings: Settings):
        if not settings.mineru_configured:
            raise ConfigurationError(
                "MinerU Official API 尚未配置 Base URL 与 SAG_MINERU_OFFICIAL_API_TOKEN"
            )
        self._base_url = str(settings.mineru_base_url).rstrip("/")
        self._api_root = _official_api_root(self._base_url)
        self._api_token = str(settings.mineru_official_api_token).strip()
        self._official_model = settings.mineru_official_model
        self._parse_method = settings.mineru_parse_method
        self._request_timeout = max(1.0, float(settings.mineru_request_timeout))
        self._poll_interval = max(0.05, float(settings.mineru_poll_interval))
        self._poll_timeout = max(self._poll_interval, float(settings.mineru_poll_timeout))
        self._result_limit = max(1, int(settings.mineru_result_max_mb)) * 1024 * 1024

    @property
    def signature(self) -> str:
        return f"mineru-official-{self._official_model}-{self._parse_method}"

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
                            "filename": filename,
                            "upload_source": "remote_presigned",
                            "status": "queued",
                        }
                    )
                    if on_state:
                        await on_state(dict(current))
                else:
                    task_id = str(existing_task_id).strip()

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
                    "filename": filename,
                    "upload_url": upload_url,
                    "upload_completed": False,
                    "status": "uploading",
                }
            )
            if on_state:
                await on_state(dict(current))

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
        }
        if self._parse_method == "txt":
            payload["is_ocr"] = False
        elif self._parse_method == "ocr":
            payload["is_ocr"] = True

        response = await self._official_request("POST", "extract/task", json=payload)
        data = _official_data(response, "创建 URL 解析任务")
        task_id = data.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise UpstreamError("MinerU 官方 URL 解析响应中没有 task_id")
        return task_id.strip()

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
        response = await self._official_request(
            "POST",
            "file-urls/batch",
            json={
                "files": [file_config],
                "model_version": self._official_model,
            },
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
        return batch_id.strip(), upload_urls[0]

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
    ) -> str:
        deadline = time.monotonic() + self._poll_timeout
        base_state = dict(state or {})
        last_status: str | None = None
        while True:
            if should_pause is not None and await should_pause():
                raise ParsePaused()
            response = await self._official_request(
                "GET", f"extract-results/batch/{batch_id}"
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
        self, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        url = f"{self._api_root}/{path.lstrip('/')}"
        await _validate_api_url(url)
        try:
            async with httpx.AsyncClient(timeout=self._request_timeout) as client:
                if method == "POST":
                    response = await client.post(
                        url, headers=self._headers, **kwargs
                    )
                else:
                    response = await client.request(
                        method, url, headers=self._headers, **kwargs
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
