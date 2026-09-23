"""Unified LLM Client for AI-Engine (IOS v5.1)

Hỗ trợ gọi đa nhà cung cấp tương thích OpenAI API (Groq Multi-Model rotation, EvoMap DeepSeek V4 Flash).
Cung cấp bộ trích xuất JSON chống lỗi Markdown block ```json ``` và fallback an toàn.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
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
    3. Thử tải JSON và trả về dictionary.
    """
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("LLM trả về nội dung rỗng.")

    # 1. Bóc tách khối code ```json ... ```
    pattern_fence = r"```(?:json)?\s*(\{.*?\})\s*```"
    match_fence = re.search(pattern_fence, text, re.DOTALL)
    if match_fence:
        text = match_fence.group(1).strip()

    # 2. Nếu vẫn chưa parse được hoặc không có fence, tìm cặp ngoặc {} đầu tiên và cuối cùng
    if not (text.startswith("{") and text.endswith("}")):
        start_idx = text.find("{")
        end_idx = text.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            text = text[start_idx : end_idx + 1].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 3. Thử sửa lỗi dấu phẩy thừa trước ngoặc đóng (trailing commas)
        fixed_text = re.sub(r",\s*([\]}])", r"\1", text)
        return json.loads(fixed_text)


class UnifiedLLMClient:
    """
    Async LLM Client chuẩn hóa cho ai-engine (IOS v5.1).
    Hỗ trợ xoay tua chủ động (Active Round-Robin) qua ma trận:
      (Key 0, Key 1) x (openai/gpt-oss-120b, openai/gpt-oss-20b, qwen/qwen3.8-27b)
    Tích hợp bộ kiểm soát RPM (Sliding Window Rate Limiter < 25 RPM/slot)
    và tự động Cooldown 60s khi gặp 429 để tránh bị khóa API Groq Free Tier.
    Fallback cuối: EvoMap (evomap-deepseek-v4-flash).
    """

    MAX_RPM_PER_SLOT: int = 25   # Groq Free tier giới hạn 30 RPM -> đặt 25 an toàn
    COOLDOWN_SECONDS: float = 60.0  # Khi bị 429 thì tạm ngừng slot đó 60 giây

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        settings = get_settings()

        self.providers: List[Dict[str, Any]] = []
        self._cursor: int = 0
        self._lock = asyncio.Lock()
        self._call_history: Dict[str, deque[float]] = defaultdict(deque)
        self._cooldown_until: Dict[str, float] = {}

        # Nếu truyền custom cụ thể
        if api_key and base_url and model:
            self.providers.append({
                "name": "CUSTOM",
                "api_key": api_key,
                "base_url": base_url.rstrip("/"),
                "model": model,
                "is_groq": False,
            })
        else:
            # 1. Cấu hình Groq 3 mô hình Free Tier mạnh nhất
            groq_models = [
                "openai/gpt-oss-120b",
                "openai/gpt-oss-20b",
                "qwen/qwen3.8-27b",
            ]

            # Đưa model user cấu hình riêng lên đầu nếu hợp lệ
            preferred_m0 = settings.llm_groq_model0
            if preferred_m0 and preferred_m0 in groq_models:
                groq_models.remove(preferred_m0)
                groq_models.insert(0, preferred_m0)

            groq_keys = []
            if settings.llm_groq_key0:
                groq_keys.append(("GROQ_K0", settings.llm_groq_key0))
            if settings.llm_groq_key1 and settings.llm_groq_key1 != settings.llm_groq_key0:
                groq_keys.append(("GROQ_K1", settings.llm_groq_key1))

            # Xếp thứ tự đan xen các model & keys để phân tán tải tối ưu:
            # Ví dụ: K0-120b, K1-120b, K0-20b, K1-20b, K0-qwen, K1-qwen
            for g_model in groq_models:
                clean_name = g_model.split("/")[-1].replace(".", "").replace("-", "_")
                for key_label, g_key in groq_keys:
                    self.providers.append({
                        "name": f"{key_label}_{clean_name}",
                        "api_key": g_key,
                        "base_url": "https://api.groq.com/openai/v1",
                        "model": g_model,
                        "is_groq": True,
                    })

            # 2. Cấu hình EvoMap (DeepSeek V4 Flash) làm Fallback cuối cùng
            if settings.evomap_api_key:
                evo_key = settings.evomap_api_key
                self.providers.append({
                    "name": "EVOMAP_DEEPSEEK",
                    "api_key": evo_key,
                    "base_url": "https://api.evomap.ai/v1",
                    "model": "evomap-deepseek-v4-flash",
                    "is_groq": False,
                })

    @property
    def is_configured(self) -> bool:
        return len(self.providers) > 0

    @property
    def provider(self) -> str:
        return self.providers[0]["name"] if self.providers else "NONE"

    def _is_slot_available(self, p_name: str, is_groq: bool) -> bool:
        """Kiểm tra xem slot có đang bị cooldown hoặc chạm trần RPM hay không."""
        now = time.monotonic()

        # Kiểm tra cooldown (do dính 429 gần đây)
        cooldown = self._cooldown_until.get(p_name, 0.0)
        if now < cooldown:
            return False

        if not is_groq:
            return True

        # Kiểm tra sliding window RPM (tối đa MAX_RPM_PER_SLOT requests trong 60s)
        history = self._call_history[p_name]
        while history and now - history[0] > 60.0:
            history.popleft()

        if len(history) >= self.MAX_RPM_PER_SLOT:
            return False

        return True

    async def _get_ordered_candidates(self) -> List[Dict[str, Any]]:
        """Lấy danh sách providers xoay tua theo Round-Robin và lọc trạng thái Cooldown/RPM."""
        if not self.providers:
            return []

        async with self._lock:
            start_idx = self._cursor
            self._cursor = (self._cursor + 1) % len(self.providers)

        # Xoay vòng danh sách bắt đầu từ start_idx
        ordered = self.providers[start_idx:] + self.providers[:start_idx]

        # Ưu tiên các slot khả dụng (chưa chạm RPM, không cooldown)
        available = [p for p in ordered if self._is_slot_available(p["name"], p.get("is_groq", False))]
        unavailable = [p for p in ordered if not self._is_slot_available(p["name"], p.get("is_groq", False))]

        return available + unavailable

    async def chat(
        self,
        prompt_or_messages: Union[str, List[Dict[str, str]]],
        temperature: float = 0.2,
        max_tokens: int = 1500,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        """Gửi yêu cầu chat completion với cơ chế Active Round-Robin, Rate-Limit Pacing và Auto-Fallback."""
        if not self.is_configured:
            raise ValueError("Chưa cấu hình API Key cho bất kỳ LLM Provider nào.")

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
            is_groq = p.get("is_groq", False)

            # Nếu slot này đang trong thời gian Cooldown (do dính 429), bỏ qua sang slot kế tiếp
            now = time.monotonic()
            if now < self._cooldown_until.get(p_name, 0.0):
                continue

            # Ghi nhận thời điểm gọi vào lịch sử rate limiter
            self._call_history[p_name].append(now)

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
                                return content

                    if resp.status_code == 429:
                        self._cooldown_until[p_name] = time.monotonic() + self.COOLDOWN_SECONDS
                        err_msg = (
                            f"Slot {p_name} chạm trần 429 Rate Limit. "
                            f"Kích hoạt Cooldown {self.COOLDOWN_SECONDS}s, tự động xoay sang slot khác..."
                        )
                        logger.warning(f"[UnifiedLLMClient] {err_msg}")
                        last_error = RuntimeError(err_msg)
                        continue

                    err_msg = f"Provider {p_name} trả về HTTP {resp.status_code}: {resp.text[:200]}"
                    logger.warning(f"[UnifiedLLMClient] {err_msg}. Kích hoạt fallback...")
                    last_error = RuntimeError(err_msg)
            except Exception as e:
                err_msg = f"Provider {p_name} lỗi kết nối: {e}"
                logger.warning(f"[UnifiedLLMClient] {err_msg}. Kích hoạt fallback...")
                last_error = e

        raise RuntimeError(f"Toàn bộ LLM Providers đều thất bại. Lỗi cuối: {last_error}")

    async def complete_json(
        self,
        prompt_or_messages: Union[str, List[Dict[str, str]]],
        temperature: float = 0.1,
        max_tokens: int = 2000,
    ) -> Dict[str, Any]:
        """Gửi prompt và bóc tách kết quả thành Python Dict an toàn."""
        try:
            raw = await self.chat(
                prompt_or_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception:
            raw = await self.chat(
                prompt_or_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=None,
            )

        return clean_and_parse_json(raw)


_unified_llm_client: Optional[UnifiedLLMClient] = None


def get_unified_llm_client() -> UnifiedLLMClient:
    """Singleton getter cho UnifiedLLMClient."""
    global _unified_llm_client
    if _unified_llm_client is None:
        _unified_llm_client = UnifiedLLMClient()
    return _unified_llm_client
