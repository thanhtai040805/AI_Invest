"""Concrete Chat LLM implementation using Groq-0 (llama-3.3-70b-versatile).

Provides the ``stream_chat()`` and ``chat()`` interface that ``AgentLoop``
expects, backed by the sync Groq SDK.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from dotenv import load_dotenv
from groq import Groq

logger = logging.getLogger(__name__)

# Load .env so we can read GROQ_API_KEY0 / GROQ_MODEL0
env_path = __file__.rsplit("app", 1)[0] + ".env"
load_dotenv(dotenv_path=env_path)


@dataclass
class ToolCall:
    """Represents a single tool call from the model."""
    id: str
    type: str = "function"
    function: Dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.function.get("name", "")

    @property
    def arguments(self) -> dict:
        args = self.function.get("arguments", {})
        return args if isinstance(args, dict) else {}


@dataclass
class LLMResponse:
    """Response envelope from a chat / stream_chat call.

    Attributes:
        content: Text content (may be empty when tool calls are present).
        tool_calls: List of ToolCall instances.
        has_tool_calls: True when tool_calls is non-empty.
        reasoning_content: Optional reasoning / thinking trace.
        usage_metadata: Optional token usage dict.
    """
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    reasoning_content: Optional[str] = None
    usage_metadata: Optional[Dict[str, Any]] = None

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class GroqChatLLM:
    """Concrete LLM backed by Groq-0 (llama-3.3-70b-versatile).

    Uses the sync Groq SDK. Suitable for running inside a thread pool
    executor (as AgentLoop does).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> None:
        self.api_key = api_key or os.getenv("GROQ_API_KEY0") or os.getenv("GROQ_API_KEY", "")
        self.model = model or os.getenv("GROQ_MODEL0") or os.getenv("GROQ_MODEL", "qwen/qwen3-32b")
        self.max_tokens = max_tokens
        self.temperature = temperature

        if not self.api_key:
            logger.warning("GROQ_API_KEY0 not set — LLM calls will fail")
        self._client = Groq(api_key=self.api_key) if self.api_key else None

    # ── public interface used by AgentLoop ──────────────────────────────

    def stream_chat(
        self,
        messages: list,
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        on_text_chunk: Optional[Callable[[str], None]] = None,
        timeout: Optional[int] = None,
    ) -> LLMResponse:
        """Synchronous streaming chat with tool support.

        Args:
            messages: Conversation history (OpenAI-format list).
            tools: Tool definitions for function calling.
            on_text_chunk: Optional callback receiving each text delta.
            timeout: Optional timeout in seconds.

        Returns:
            LLMResponse with aggregated content and tool calls.
        """
        if not self._client:
            return LLMResponse(content="LLM not configured — check GROQ_API_KEY0")

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            stream = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            logger.error(f"Groq stream_chat error: {exc}")
            return LLMResponse(content=f"Error: {exc}")

        content_chunks: List[str] = []
        tool_call_chunks: Dict[int, Dict[str, Any]] = {}
        reasoning_chunks: List[str] = []

        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue

            # Text content
            if delta.content:
                content_chunks.append(delta.content)
                if on_text_chunk:
                    on_text_chunk(delta.content)

            # Reasoning / thinking
            if getattr(delta, "reasoning_content", None):
                reasoning_chunks.append(delta.reasoning_content)

            # Tool calls
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index if tc.index is not None else 0
                    if idx not in tool_call_chunks:
                        tool_call_chunks[idx] = {
                            "id": tc.id or "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    if tc.id:
                        tool_call_chunks[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_call_chunks[idx]["function"]["name"] += tc.function.name
                        if tc.function.arguments:
                            tool_call_chunks[idx]["function"]["arguments"] += tc.function.arguments

        # Build final response
        content = "".join(content_chunks)
        tool_calls = []
        for tc in tool_call_chunks.values():
            if tc["function"]["name"]:
                try:
                    tc["function"]["arguments"] = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, TypeError):
                    pass
                tool_calls.append(ToolCall(
                    id=tc["id"],
                    type=tc["type"],
                    function=tc["function"],
                ))

        reasoning = "".join(reasoning_chunks) if reasoning_chunks else None

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            reasoning_content=reasoning,
        )

    def chat(
        self,
        messages: list,
        *,
        timeout: Optional[int] = None,
    ) -> LLMResponse:
        """Synchronous non-streaming chat.

        Args:
            messages: Conversation history.
            timeout: Optional timeout.

        Returns:
            LLMResponse with content and usage metadata.
        """
        if not self._client:
            return LLMResponse(content="LLM not configured — check GROQ_API_KEY0")

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                stream=False,
            )
        except Exception as exc:
            logger.error(f"Groq chat error: {exc}")
            return LLMResponse(content=f"Error: {exc}")

        content = response.choices[0].message.content or ""
        usage = response.usage
        usage_meta = None
        if usage:
            usage_meta = {
                "input_tokens": usage.prompt_tokens,
                "output_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            }

        return LLMResponse(
            content=content,
            usage_metadata=usage_meta,
        )


# Alias for backwards compatibility — code importing ChatLLM now gets
# an instantiatable class.
ChatLLM = GroqChatLLM
