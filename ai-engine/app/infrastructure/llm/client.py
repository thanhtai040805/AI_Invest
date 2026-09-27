"""Unified OpenAI-compatible LLM client for Xkiro and EvoMap."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Union

import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


def clean_and_parse_json(raw_text: str) -> Dict[str, Any]:
    """
    Bóc tách và parse an toàn chuỗi JSON từ LLM output:
    1. Cắt bỏ markdown code block: ```json ... ``` hoặc ``` ... ```
    2. Tìm block {...} hợp lệ nếu có văn bản kèm theo.
    3. Thử tải JSON và tự động sửa các lỗi phổ biến (trailing commas, unclosed strings, truncated brackets).
    """
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("LLM trả về nội dung rỗng.")

    # 1. Bóc tách khối code ```json ... ```
    pattern_fence = r"```(?:json)?\s*(\{.*?\})\s*```"
    match_fence = re.search(pattern_fence, text, re.DOTALL)
    if match_fence:
        text = match_fence.group(1).strip()

    # 2. Tìm điểm bắt đầu của JSON object '{'
    start_idx = text.find("{")
    if start_idx != -1:
        end_idx = text.rfind("}")
        if end_idx != -1 and end_idx > start_idx:
            candidate = text[start_idx : end_idx + 1].strip()
        else:
            candidate = text[start_idx:].strip()
    else:
        candidate = text

    # Thử parse chuẩn trước
    try:
        return json.loads(candidate)
    except Exception:
        pass

    # 3. Thử sửa lỗi dấu phẩy thừa trước ngoặc đóng (trailing commas)
    fixed_text = re.sub(r",\s*([\]}])", r"\1", candidate)
    try:
        return json.loads(fixed_text)
    except Exception:
        pass

    # 4. Tự động sửa chữa JSON bị ngắt giữa chừng (Truncated JSON / Unterminated String repair)
    in_string = False
    escape = False
    open_brackets: list[str] = []

    for char in candidate:
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if not in_string:
            if char in ("{", "["):
                open_brackets.append(char)
            elif char == "}":
                if open_brackets and open_brackets[-1] == "{":
                    open_brackets.pop()
            elif char == "]":
                if open_brackets and open_brackets[-1] == "[":
                    open_brackets.pop()

    repaired = candidate
    if in_string:
        repaired += '"'

    # Xóa dấu phẩy treo ở cuối chuỗi
    repaired = re.sub(r",\s*$", "", repaired.strip())

    # Đóng tất cả các ngoặc mở còn lại theo thứ tự đảo ngược
    for b in reversed(open_brackets):
        if b == "{":
            repaired += "}"
        elif b == "[":
            repaired += "]"

    repaired = re.sub(r",\s*([\]}])", r"\1", repaired)
    return json.loads(repaired)


class UnifiedLLMClient:
    """OpenAI-compatible LLM client with Xkiro primary and EvoMap fallback."""

    COOLDOWN_SECONDS: float = 60.0

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        settings = get_settings()

        self.providers: List[Dict[str, Any]] = []
        self._cooldown_until: Dict[str, float] = {}
        self._last_provider: Optional[str] = None
        self._last_model: Optional[str] = None
        self._last_request_id: Optional[str] = None

        if api_key and base_url and model:
            self.providers.append({
                "name": "CUSTOM",
                "api_key": api_key,
                "base_url": base_url.rstrip("/"),
                "model": model,
            })
        else:
            if settings.xkiro_api_key:
                self.providers.append({
                    "name": "XKIRO_QWEN",
                    "api_key": settings.xkiro_api_key,
                    "base_url": "https://api.xkiro.com/v1",
                    "model": "qwen/qwen3.7-plus:free",
                })
            if settings.evomap_api_key:
                evomap_key = settings.evomap_api_key
                if not evomap_key.startswith("sk-evomap-"):
                    evomap_key = f"sk-evomap-{evomap_key}"
                self.providers.append({
                    "name": "EVOMAP_DEEPSEEK",
                    "api_key": evomap_key,
                    "base_url": "https://api.evomap.ai/v1",
                    "model": "evomap-deepseek-v4-flash",
                })

    @property
    def is_configured(self) -> bool:
        return len(self.providers) > 0

    @property
    def provider(self) -> str:
        return self.providers[0]["name"] if self.providers else "NONE"

    def _is_slot_available(self, p_name: str) -> bool:
        return time.monotonic() >= self._cooldown_until.get(p_name, 0.0)

    async def _get_ordered_candidates(self) -> List[Dict[str, Any]]:
        """Prefer providers outside cooldown, preserving configured priority."""
        available = [p for p in self.providers if self._is_slot_available(p["name"])]
        cooling = [p for p in self.providers if not self._is_slot_available(p["name"])]
        return available + cooling

    async def chat(
        self,
        prompt_or_messages: Union[str, List[Dict[str, str]]],
        temperature: float = 0.2,
        max_tokens: int = 1500,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        """Send a completion through Xkiro, falling back to EvoMap on failure."""
        if not self.is_configured:
            message = "No LLM provider configured; set XKIRO_API_KEY or EVOMAP_API_KEY."
            logger.error("[UnifiedLLMClient] %s", message)
            raise ValueError(message)

        if isinstance(prompt_or_messages, str):
            messages = [{"role": "user", "content": prompt_or_messages}]
        else:
            messages = prompt_or_messages

        timeout = httpx.Timeout(connect=5.0, read=40.0, write=5.0, pool=5.0)
        last_error = None

        candidates = await self._get_ordered_candidates()

        for p in candidates:
            p_name = p["name"]
            p_key = p["api_key"]
            p_url = p["base_url"]
            p_model = p["model"]
            # Nếu slot này đang trong thời gian Cooldown (do dính 429), bỏ qua sang slot kế tiếp
            if not self._is_slot_available(p_name):
                last_error = RuntimeError(f"Provider {p_name} is cooling down")
                continue

            payload: Dict[str, Any] = {
                "model": p_model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if response_format:
                payload["response_format"] = response_format

            headers = {
                "Authorization": f"Bearer {p_key}",
                "Content-Type": "application/json",
            }

            request_started = time.perf_counter()
            resp = None
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        f"{p_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    )

                    if resp.status_code == 200:
                        data = resp.json()
                        choices = data.get("choices", [])
                        if choices:
                            msg = choices[0].get("message", {})
                            content = msg.get("content", "")
                            if not content and msg.get("reasoning_content"):
                                content = msg.get("reasoning_content", "")
                            if content:
                                elapsed = time.perf_counter() - request_started
                                request_id = (
                                    resp.headers.get("x-evomap-request-id")
                                    or resp.headers.get("x-request-id")
                                    or resp.headers.get("request-id", "unknown")
                                )
                                if elapsed >= 5.0 or p_name != self.providers[0]["name"]:
                                    logger.warning(
                                        "[UnifiedLLMClient] Provider %s model=%s succeeded in %.2fs after fallback=%s (request_id=%s).",
                                        p_name, p_model, elapsed, p_name != self.providers[0]["name"], request_id,
                                    )
                                self._last_provider = p_name
                                self._last_model = str(data.get("model") or p_model)
                                self._last_request_id = request_id
                                return content
                        elapsed = time.perf_counter() - request_started
                        request_id = resp.headers.get("x-evomap-request-id") or resp.headers.get("x-request-id") or resp.headers.get("request-id", "unknown")
                        err_msg = f"Provider {p_name} model={p_model} returned HTTP 200 with empty content after {elapsed:.2f}s (request_id={request_id})"
                        logger.warning("[UnifiedLLMClient] %s; trying next provider.", err_msg)
                        last_error = RuntimeError(err_msg)
                        continue

                    if resp.status_code == 429:
                        retry_after = resp.headers.get("retry-after")
                        try:
                            cooldown_seconds = max(1.0, float(retry_after)) if retry_after else self.COOLDOWN_SECONDS
                        except ValueError:
                            cooldown_seconds = self.COOLDOWN_SECONDS
                        self._cooldown_until[p_name] = time.monotonic() + cooldown_seconds
                        elapsed = time.perf_counter() - request_started
                        request_id = (
                            resp.headers.get("x-evomap-request-id")
                            or resp.headers.get("x-request-id")
                            or resp.headers.get("request-id", "unknown")
                        )
                        err_msg = (
                            f"Provider {p_name} returned HTTP 429 after {elapsed:.2f}s; "
                            f"cooldown={cooldown_seconds:g}s (request_id={request_id}): {resp.text[:200]}"
                        )
                        logger.warning("[UnifiedLLMClient] %s; trying next provider.", err_msg)
                        last_error = RuntimeError(err_msg)
                        continue

                    elapsed = time.perf_counter() - request_started
                    request_id = (resp.headers.get("x-evomap-request-id") or resp.headers.get("x-request-id") or resp.headers.get("request-id", "unknown"))
                    err_msg = f"Provider {p_name} model={p_model} returned HTTP {resp.status_code} after {elapsed:.2f}s (request_id={request_id}): {resp.text[:200]}"
                    logger.warning("[UnifiedLLMClient] %s; trying next provider.", err_msg)
                    last_error = RuntimeError(err_msg)
            except Exception as e:
                elapsed = time.perf_counter() - request_started
                request_id = (
                    resp.headers.get("x-evomap-request-id")
                    or resp.headers.get("x-request-id")
                    or resp.headers.get("request-id", "unknown")
                ) if resp is not None else "unknown"
                status = f" HTTP {resp.status_code}" if resp is not None else ""
                err_msg = f"Provider {p_name} model={p_model}{status} failed after {elapsed:.2f}s (request_id={request_id}; {type(e).__name__}): {e!r}"
                logger.warning("[UnifiedLLMClient] %s; trying next provider.", err_msg)
                last_error = e

        message = f"All configured LLM providers failed; last error: {last_error!r}"
        logger.error("[UnifiedLLMClient] %s", message)
        raise RuntimeError(message)

    async def complete_json(
        self,
        prompt_or_messages: Union[str, List[Dict[str, str]]],
        temperature: float = 0.1,
        max_tokens: int = 2000,
        json_schema: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Request JSON or strict schema-constrained JSON once, then parse it."""
        response_format = (
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_response",
                    "strict": True,
                    "schema": json_schema,
                },
            }
            if json_schema
            else {"type": "json_object"}
        )
        raw = await self.chat(
            prompt_or_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        try:
            return clean_and_parse_json(raw)
        except Exception as e:
            logger.warning(
                "[UnifiedLLMClient] JSON parsing failed after provider=%s model=%s request_id=%s (%s): %r; response_len=%d response_prefix=%r",
                self._last_provider or "unknown",
                self._last_model or "unknown",
                self._last_request_id or "unknown",
                type(e).__name__, e, len(raw), raw[:240],
            )
            raise


_unified_llm_client: Optional[UnifiedLLMClient] = None


def get_unified_llm_client() -> UnifiedLLMClient:
    """Singleton getter cho UnifiedLLMClient."""
    global _unified_llm_client
    if _unified_llm_client is None:
        _unified_llm_client = UnifiedLLMClient()
    return _unified_llm_client
