"""Unified LLM Client for AI-Engine (IOS v5.1)

Hỗ trợ gọi đa nhà cung cấp tương thích OpenAI API (Groq Multi-Model rotation, EvoMap DeepSeek V4 Flash).
Cung cấp bộ trích xuất JSON chống lỗi Markdown block ```json ``` và fallback an toàn.
"""

from __future__ import annotations

import json
import logging
import re
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
    Async LLM Client chuẩn hóa cho ai-engine.
    Hỗ trợ auto-fallback qua ma trận 2 Key x 3 Model (6 tầng Groq $0đ) -> EvoMap:
    1. qwen/qwen3.8-27b (Key 0 -> Key 1)
    2. qwen/qwen3.6-27b (Key 0 -> Key 1)
    3. groq/compound-mini (Key 0 -> Key 1)
    4. EvoMap (evomap-deepseek-v4-flash)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        settings = get_settings()

        self.providers: List[Dict[str, Any]] = []

        # Nếu truyền custom cụ thể
        if api_key and base_url and model:
            self.providers.append({
                "name": "CUSTOM",
                "api_key": api_key,
                "base_url": base_url.rstrip("/"),
                "model": model,
            })
        else:
            # 1. Cấu hình Groq Multi-Key & Multi-Model Rate-Limit Rotation
            # Khi Key 0 chạm trần (429) ở model tốt nhất -> Nhảy sang Key 1 để tiếp tục giữ chất lượng model.
            # Khi cả 2 Key đều chạm trần -> Hạ cấp model sang 3.6-27b -> compound-mini (kho 70k tokens x 2).
            groq_models = ["qwen/qwen3.8-27b", "qwen/qwen3.6-27b", "groq/compound-mini"]

            # Đưa model user cấu hình lên đầu nếu có và không trùng
            preferred_m0 = settings.llm_groq_model0
            if preferred_m0 and preferred_m0 in groq_models:
                groq_models.remove(preferred_m0)
                groq_models.insert(0, preferred_m0)

            groq_keys = []
            if settings.llm_groq_key0:
                groq_keys.append(("GROQ_K0", settings.llm_groq_key0))
            if settings.llm_groq_key1 and settings.llm_groq_key1 != settings.llm_groq_key0:
                groq_keys.append(("GROQ_K1", settings.llm_groq_key1))

            # Xếp thứ tự: Model mạnh nhất (Key 0 -> Key 1) rồi mới đến Model tiếp theo
            for g_model in groq_models:
                clean_name = g_model.split("/")[-1].replace(".", "").replace("-", "_")
                for key_label, g_key in groq_keys:
                    self.providers.append({
                        "name": f"{key_label}_{clean_name}",
                        "api_key": g_key,
                        "base_url": "https://api.groq.com/openai/v1",
                        "model": g_model,
                    })

            # 2. Cấu hình EvoMap (DeepSeek V4 Flash)
            if settings.evomap_api_key:
                evo_key = settings.evomap_api_key
                self.providers.append({
                    "name": "EVOMAP_DEEPSEEK",
                    "api_key": evo_key,
                    "base_url": "https://api.evomap.ai/v1",
                    "model": "evomap-deepseek-v4-flash",
                })


    @property
    def is_configured(self) -> bool:
        return len(self.providers) > 0

    @property
    def provider(self) -> str:
        return self.providers[0]["name"] if self.providers else "NONE"

    async def chat(
        self,
        prompt_or_messages: Union[str, List[Dict[str, str]]],
        temperature: float = 0.2,
        max_tokens: int = 1500,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        """Gửi yêu cầu chat completion với cơ chế auto-fallback qua danh sách providers."""
        if not self.is_configured:
            raise ValueError("Chưa cấu hình API Key cho bất kỳ LLM Provider nào.")

        if isinstance(prompt_or_messages, str):
            messages = [{"role": "user", "content": prompt_or_messages}]
        else:
            messages = prompt_or_messages

        timeout = httpx.Timeout(connect=5.0, read=40.0, write=5.0, pool=5.0)
        last_error = None

        for p in self.providers:
            p_name = p["name"]
            p_key = p["api_key"]
            p_url = p["base_url"]
            p_model = p["model"]

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
