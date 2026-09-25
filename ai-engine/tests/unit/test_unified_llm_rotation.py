"""Unit tests for the configured UnifiedLLMClient provider and cooldown behavior."""

import asyncio
import logging
import time
from unittest.mock import patch, MagicMock

from app.infrastructure.llm.client import UnifiedLLMClient


def test_xkiro_and_evomap_are_configured_in_priority_order():
    async def _run():
        mock_settings = MagicMock()
        mock_settings.xkiro_api_key = "xkiro_test"
        mock_settings.evomap_api_key = "evomap_test"

        with patch("app.infrastructure.llm.client.get_settings", return_value=mock_settings):
            client = UnifiedLLMClient()
            assert client.is_configured
            assert [provider["name"] for provider in client.providers] == ["XKIRO_QWEN", "EVOMAP_DEEPSEEK"]
            assert client.providers[0]["base_url"] == "https://api.xkiro.com/v1"
            assert client.providers[0]["model"] == "qwen/qwen3.7-plus:free"
            assert client.providers[1]["base_url"] == "https://api.evomap.ai/v1"
            assert client.providers[1]["model"] == "evomap-deepseek-v4-flash"
            assert client.providers[1]["api_key"] == "sk-evomap-evomap_test"
            cand1 = await client._get_ordered_candidates()
            cand2 = await client._get_ordered_candidates()
            assert cand1 == cand2 == client.providers

    asyncio.run(_run())


def test_provider_cooldown_marks_provider_unavailable():
    async def _run():
        mock_settings = MagicMock()
        mock_settings.xkiro_api_key = "xkiro_test"
        mock_settings.evomap_api_key = ""

        with patch("app.infrastructure.llm.client.get_settings", return_value=mock_settings):
            client = UnifiedLLMClient()
            p_name = client.providers[0]["name"]

            now = time.monotonic()
            assert client._is_slot_available(p_name) is True
            client._cooldown_until[p_name] = now + 60.0
            assert client._is_slot_available(p_name) is False

    asyncio.run(_run())


def test_complete_json_does_not_repeat_full_provider_sweep_on_failure():
    """A failed structured request is surfaced once instead of replaying every provider."""
    from unittest.mock import AsyncMock
    import pytest

    async def _run():
        client = UnifiedLLMClient(api_key="test", base_url="https://example.invalid/v1", model="test-model")
        client.chat = AsyncMock(side_effect=RuntimeError("provider unavailable"))
        with pytest.raises(RuntimeError, match="provider unavailable"):
            await client.complete_json("return json")
        assert client.chat.await_count == 1

    asyncio.run(_run())


def test_provider_failure_log_includes_model_status_and_request_id(caplog):
    import pytest

    class Response:
        status_code = 503
        headers = {"x-evomap-request-id": "request-123"}
        text = "upstream unavailable"

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, *_args, **_kwargs):
            return Response()

    async def _run():
        client = UnifiedLLMClient(api_key="test", base_url="https://example.invalid/v1", model="test-model")
        with patch("app.infrastructure.llm.client.httpx.AsyncClient", return_value=Client()):
            with caplog.at_level(logging.WARNING, logger="app.infrastructure.llm.client"):
                with pytest.raises(RuntimeError, match="All configured LLM providers failed"):
                    await client.chat("hello")

        assert "model=test-model" in caplog.text
        assert "HTTP 503" in caplog.text
        assert "request_id=request-123" in caplog.text
        assert "upstream unavailable" in caplog.text

    asyncio.run(_run())
