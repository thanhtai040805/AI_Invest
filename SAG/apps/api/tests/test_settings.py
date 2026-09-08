"""Unit tests cấu hình Settings thuần túy (không cần DB/HTTP).

Các test endpoint v1 (model-config/quick-setup) đã xóa cùng kiến trúc API v1.
"""

import pytest

from sag_api.core.config import Settings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://localhost:3000", ["http://localhost:3000"]),
        (
            "http://localhost:3000,https://sag.example.com",
            ["http://localhost:3000", "https://sag.example.com"],
        ),
        ('["http://localhost:3000"]', ["http://localhost:3000"]),
    ],
)
def test_cors_origins_env_formats(monkeypatch, raw, expected):
    monkeypatch.setenv("SAG_CORS_ORIGINS", raw)
    assert Settings(_env_file=None).cors_origins == expected


def test_legacy_atomic_env_strategy_maps_to_precise(monkeypatch):
    monkeypatch.setenv("SAG_SEARCH_STRATEGY", "atomic")
    assert Settings(_env_file=None).search_strategy == "multi"


def test_timezone_defaults_to_ho_chi_minh_and_rejects_invalid(monkeypatch):
    monkeypatch.delenv("SAG_TIMEZONE", raising=False)
    assert Settings(_env_file=None).timezone == "Asia/Ho_Chi_Minh"
    monkeypatch.setenv("SAG_TIMEZONE", "UTC")
    assert Settings(_env_file=None).timezone == "UTC"
    monkeypatch.setenv("SAG_TIMEZONE", "Mars/Olympus")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


def test_provider_base_urls_default_to_documented_endpoints(monkeypatch):
    for name in ("SAG_LLM_BASE_URL", "SAG_EMBEDDING_BASE_URL", "SAG_MINERU_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    configured = Settings(_env_file=None)
    assert configured.llm_provider == "openai"
    assert configured.llm_base_url == "https://api.302ai.cn/v1"
    assert configured.embedding_base_url == "https://api.302ai.cn/v1"
    assert configured.mineru_base_url == "https://mineru.net"


def test_default_model_output_limit_is_20000(monkeypatch):
    monkeypatch.delenv("SAG_LLM_MAX_TOKENS", raising=False)
    assert Settings(_env_file=None).llm_max_tokens == 20_000


def test_database_model_config_overrides_environment_default():
    from sag_api.services.settings_service import apply_overrides

    configured = Settings(_env_file=None, llm_model="environment-model")
    apply_overrides(configured, {"llm_model": "database-model"})

    assert configured.llm_model == "database-model"


def test_explicit_llm_lock_preserves_environment_values():
    from sag_api.services.settings_service import apply_overrides

    configured = Settings(
        _env_file=None,
        llm_model="environment-model",
        lock_llm_config=True,
    )
    apply_overrides(configured, {"llm_model": "database-model"})

    assert configured.llm_model == "environment-model"
