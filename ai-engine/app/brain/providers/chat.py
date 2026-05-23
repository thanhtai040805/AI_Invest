"""Chat LLM provider compatibility stub.

In the original src layout, ``ChatLLM`` was the base protocol for LangChain
chat-model instances.  The current codebase builds LangChain chat models
directly (``ChatOpenAI``, ``ChatAnthropic``, etc.) so this module exists
purely as an import-compatibility shim.
"""

from __future__ import annotations

from typing import Any, Protocol

from langchain_core.language_models.chat_models import BaseChatModel


class LLMResponse:
    """Minimal response envelope."""

    def __init__(self, content: str) -> None:
        self.content = content


ChatLLM = BaseChatModel
"""Alias so that ``from app.brain.providers.chat import ChatLLM`` resolves
to the LangChain ``BaseChatModel`` protocol used everywhere downstream."""
