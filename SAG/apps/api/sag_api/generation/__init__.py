"""Lazy public exports for generation helpers.

Importing prompt helpers pulls the zleap-backed graph DTOs. Keep package import
light so pure LLM/extraction code and offline tests can import ``LLMClient``
without requiring the full graph engine dependency.
"""

from __future__ import annotations

from typing import Any

from sag_api.generation.llm import LLMClient

__all__ = [
    "LLMClient",
    "build_agent_messages",
    "build_citations",
    "build_messages",
    "build_prompt_preview",
]


def __getattr__(name: str) -> Any:
    if name in {"build_agent_messages", "build_citations", "build_messages", "build_prompt_preview"}:
        from sag_api.generation import prompt

        return getattr(prompt, name)
    raise AttributeError(name)
