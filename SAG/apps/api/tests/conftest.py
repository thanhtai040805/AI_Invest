"""Keep offline unit tests hermetic against local untracked `.env` files.

`litellm` invokes `dotenv.load_dotenv()` at import time, which otherwise leaks
developer-local `apps/api/.env` keys into `os.environ` for subsequent tests.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_sag_env_from_third_party_dotenv():
    tracked_prefixes = ("SAG_", "R2_", "CLOUDFLARE_API_TOKEN")
    before = {
        k: v
        for k, v in os.environ.items()
        if k.startswith(tracked_prefixes)
    }
    yield
    for key in list(os.environ):
        if key.startswith(tracked_prefixes) and key not in before:
            os.environ.pop(key, None)
    for key, value in before.items():
        os.environ[key] = value


@pytest.fixture(autouse=True)
def _isolate_sag_local_storage_dirs(tmp_path, monkeypatch):
    """Redirect default local data/upload dirs and external live backends so offline tests stay hermetic."""
    from sag_api.core import config as config_mod

    monkeypatch.setattr(config_mod.settings, "_active_data_dir", None)
    monkeypatch.setattr(config_mod.settings, "data_dir", str(tmp_path / "engine"))
    monkeypatch.setattr(config_mod.settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(config_mod.settings, "sag_qdrant_shared_collection", None)
    monkeypatch.setattr(config_mod.settings, "core_database_url", None)
    monkeypatch.setattr(config_mod.settings, "rerank_provider", "disabled")
    monkeypatch.setattr(config_mod.settings, "rerank_api_key", None)
    monkeypatch.setattr(config_mod.settings, "r2_account_id", None)
    monkeypatch.setattr(config_mod.settings, "r2_endpoint_url", None)
    monkeypatch.setattr(config_mod.settings, "r2_token", None)
    monkeypatch.setattr(config_mod.settings, "r2_access_key_id", None)
    monkeypatch.setattr(config_mod.settings, "r2_secret_access_key", None)
    monkeypatch.setattr(config_mod.settings, "r2_md_local_cache_dir", None)
    monkeypatch.setattr(config_mod.settings, "mineru_official_api_token_0", None)
    monkeypatch.setattr(config_mod.settings, "mineru_official_api_token_1", None)
    monkeypatch.setattr(config_mod.settings, "mineru_official_api_token_2", None)
    monkeypatch.setattr(config_mod.settings, "mineru_official_api_token_3", None)
    monkeypatch.setattr(config_mod.settings, "mineru_official_api_tokens", [])

    from sag_api.parsing.mineru_official import reset_mineru_token_pool_state

    reset_mineru_token_pool_state()
