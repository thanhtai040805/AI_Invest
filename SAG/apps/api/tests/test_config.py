from pathlib import Path

from sag_api.core.config import _DEFAULT_ENV_FILE, Settings


def test_default_dotenv_is_resolved_from_repository_root() -> None:
    project_root = Path(__file__).resolve().parents[3]

    assert _DEFAULT_ENV_FILE == project_root / ".env"
    assert _DEFAULT_ENV_FILE.is_absolute()
    assert Settings.model_config["env_file"] == _DEFAULT_ENV_FILE


def test_dotenv_is_loaded_and_process_environment_takes_precedence(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SAG_LLM_PROVIDER=anthropic\n"
        "SAG_LLM_MODEL=claude-test-model\n"
        "SAG_SILICONFLOW_BASE_URL=https://api.siliconflow.com/v1\n"
        "SAG_SILICONFLOW_API_KEY=unit-test-key\n",
        encoding="utf-8",
    )
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()

    for name in ("SAG_LLM_PROVIDER", "SAG_LLM_MODEL", "SAG_SILICONFLOW_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    class DotenvSettings(Settings):
        model_config = {**Settings.model_config, "env_file": env_file}

    monkeypatch.chdir(runtime_dir)
    loaded = DotenvSettings()
    assert loaded.llm_provider == "anthropic"
    assert loaded.llm_model == "claude-test-model"
    assert loaded.siliconflow_base_url == "https://api.siliconflow.com/v1"
    assert loaded.siliconflow_api_key == "unit-test-key"

    monkeypatch.setenv("SAG_LLM_MODEL", "process-env-model")
    assert DotenvSettings().llm_model == "process-env-model"


def test_mineru_official_api_token_is_sole_mineru_configuration(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SAG_MINERU_PROVIDER=302\n"
        "SAG_MINERU_BASE_URL=https://api.302ai.cn\n"
        "SAG_MINERU_OFFICIAL_API_TOKEN=official-token-v4-test\n"
        "SAG_MINERU_OFFICIAL_MODEL=vlm\n",
        encoding="utf-8",
    )
    for name in (
        "SAG_MINERU_PROVIDER",
        "SAG_MINERU_BASE_URL",
        "SAG_MINERU_OFFICIAL_API_TOKEN",
        "SAG_MINERU_OFFICIAL_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    class DotenvSettings(Settings):
        model_config = {**Settings.model_config, "env_file": env_file}

    loaded = DotenvSettings()
    assert loaded.mineru_provider == "official"
    assert loaded.mineru_base_url == "https://mineru.net"
    assert loaded.mineru_official_api_token == "official-token-v4-test"
    assert loaded.mineru_configured is True
    assert loaded.effective_document_parser == "mineru"

    unconfigured = Settings(
        _env_file=None,
        mineru_official_api_token="",
    )
    assert unconfigured.mineru_official_api_token is None
    assert unconfigured.mineru_configured is False
    assert unconfigured.effective_document_parser == "markitdown"
