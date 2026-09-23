"""Unit tests for UnifiedLLMClient multi-model rotation, round-robin, rate-limiting, and EvoMap fallback."""

import asyncio
import time
from unittest.mock import patch, MagicMock

from app.infrastructure.llm.client import UnifiedLLMClient


def test_round_robin_rotation_order():
    """Verify that multiple keys and models are interleaved and rotated via round-robin."""
    async def _run():
        mock_settings = MagicMock()
        mock_settings.llm_groq_key0 = "test_key_0"
        mock_settings.llm_groq_key1 = "test_key_1"
        mock_settings.llm_groq_model0 = "openai/gpt-oss-120b"
        mock_settings.evomap_api_key = "sk-evomap-test"

        with patch("app.infrastructure.llm.client.get_settings", return_value=mock_settings):
            client = UnifiedLLMClient()
            assert client.is_configured
            # 6 Groq slots + 1 EvoMap slot = 7 providers
            assert len(client.providers) == 7

            # Check models present
            models = [p["model"] for p in client.providers]
            assert "openai/gpt-oss-120b" in models
            assert "openai/gpt-oss-20b" in models
            assert "qwen/qwen3.8-27b" in models
            assert "evomap-deepseek-v4-flash" in models

            # Check round robin rotation produces different starting candidates on successive calls
            cand1 = await client._get_ordered_candidates()
            cand2 = await client._get_ordered_candidates()
            assert cand1[0]["name"] != cand2[0]["name"]

    asyncio.run(_run())


def test_rate_limiting_and_cooldown():
    """Verify that slot exceeds MAX_RPM_PER_SLOT or is cooling down is marked unavailable."""
    async def _run():
        mock_settings = MagicMock()
        mock_settings.llm_groq_key0 = "test_key_0"
        mock_settings.llm_groq_key1 = ""
        mock_settings.llm_groq_model0 = "openai/gpt-oss-120b"
        mock_settings.evomap_api_key = ""

        with patch("app.infrastructure.llm.client.get_settings", return_value=mock_settings):
            client = UnifiedLLMClient()
            p_name = client.providers[0]["name"]

            # Initial: slot is available
            assert client._is_slot_available(p_name, is_groq=True) is True

            # Simulate 25 calls within 60s
            now = time.monotonic()
            for _ in range(client.MAX_RPM_PER_SLOT):
                client._call_history[p_name].append(now)

            # Slot should now be unavailable to avoid 429
            assert client._is_slot_available(p_name, is_groq=True) is False

            # Simulate 429 cooldown
            client._call_history[p_name].clear()
            client._cooldown_until[p_name] = now + 60.0
            assert client._is_slot_available(p_name, is_groq=True) is False

    asyncio.run(_run())


def test_evomap_configured_as_fallback():
    """Verify that EvoMap DeepSeek V4 Flash is loaded and present in providers."""
    mock_settings = MagicMock()
    mock_settings.llm_groq_key0 = "test_key_0"
    mock_settings.llm_groq_key1 = ""
    mock_settings.llm_groq_model0 = "openai/gpt-oss-120b"
    mock_settings.evomap_api_key = "sk-evomap-12345"

    with patch("app.infrastructure.llm.client.get_settings", return_value=mock_settings):
        client = UnifiedLLMClient()
        evo_provider = [p for p in client.providers if p["name"] == "EVOMAP_DEEPSEEK"]
        assert len(evo_provider) == 1
        assert evo_provider[0]["model"] == "evomap-deepseek-v4-flash"
        assert evo_provider[0]["base_url"] == "https://api.evomap.ai/v1"
