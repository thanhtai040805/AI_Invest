"""Opt-in live smoke test for SiliconFlow Decision and the configured LLM provider.

Run with SAG_RUN_AI_SMOKE=1 to make one small request to each configured service.
"""

import asyncio
import os

import pytest

pytestmark = [
    pytest.mark.ai_smoke,
    pytest.mark.skipif(
        os.getenv("SAG_RUN_AI_SMOKE") != "1",
        reason="set SAG_RUN_AI_SMOKE=1 to call live AI services",
    ),
]


def test_ai_services_and_configured_provider_smoke() -> None:
    from sag_api.core.config import Settings
    from sag_api.core.model_providers import get_model_provider
    from sag_api.generation.llm import LLMClient
    from sag_api.services.decision_router import route_query

    config = Settings()
    assert config.siliconflow_api_key, "SAG_SILICONFLOW_API_KEY is not configured"
    assert config.llm_configured, "SAG_LLM_API_KEY is not configured"

    provider = get_model_provider(config.llm_provider)
    assert config.routed_llm_model == provider.route_model(config.llm_model)

    failures = []
    decision = route_query("Xin chào, hãy phân loại câu hỏi này.")
    if decision["fallback_used"]:
        failures.append("SiliconFlow Decision request fell back")

    # Keep the live generation probe small and avoid retries in a smoke test.
    config.llm_max_tokens = min(config.llm_max_tokens, 32)
    config.llm_timeout_ms = min(config.llm_timeout_ms, 20_000)
    config.llm_max_retries = 0
    try:
        answer = asyncio.run(
            LLMClient(config).complete([{"role": "user", "content": "Reply with only: OK"}])
        )
    except Exception as exc:
        failures.append(f"Configured {config.llm_provider} LLM provider request failed ({type(exc).__name__})")
    else:
        if not answer.strip():
            failures.append(f"Configured {config.llm_provider} LLM provider returned an empty answer")

    assert not failures, "; ".join(failures)
