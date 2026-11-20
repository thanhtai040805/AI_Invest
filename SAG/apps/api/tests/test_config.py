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


def test_blank_qdrant_url_and_api_key_normalize_to_defaults() -> None:
    loaded = Settings(
        _env_file=None,
        sag_qdrant_url="   ",
        sag_qdrant_api_key="   ",
    )
    assert loaded.sag_qdrant_url == "http://localhost:6333"
    assert loaded.sag_qdrant_api_key is None


def test_production_secret_key_rejects_empty_short_padded_and_default_secrets() -> None:
    import pytest

    from sag_api.main import _INSECURE_SECRETS, _validate_production_secret_key

    for bad_secret in (
        "",
        "   ",
        "short-secret-under-32-bytes",
        " " + ("a" * 40),
        ("a" * 40) + " ",
        *sorted(_INSECURE_SECRETS),
    ):
        with pytest.raises(RuntimeError):
            _validate_production_secret_key("prod", bad_secret)

    # Strong 32+ byte secret passes in prod; dev allows known placeholders.
    _validate_production_secret_key("prod", "a1b2c3d4e5f60718293a4b5c6d7e8f90")
    _validate_production_secret_key("dev", "")
    _validate_production_secret_key("dev", "dev-insecure-secret-change-me-in-production-0123456789")


def test_env_example_templates_validate_cleanly(monkeypatch) -> None:
    import os

    for env_key in list(os.environ):
        if env_key.startswith("SAG_"):
            monkeypatch.delenv(env_key, raising=False)

    api_root = Path(__file__).resolve().parents[1]
    sag_root = Path(__file__).resolve().parents[3]
    repo_root = Path(__file__).resolve().parents[4]

    templates = [
        api_root / ".env.example",
        sag_root / ".env.example",
        repo_root / ".env.example",
    ]
    for template in templates:
        assert template.is_file(), f"Missing env template: {template}"
        loaded = Settings(_env_file=template)
        assert loaded.secret_key.strip(), f"Empty secret_key in {template}"
        assert loaded.sag_qdrant_url.startswith(("http://", "https://")), f"Invalid Qdrant URL in {template}"
        assert loaded.document_event_extraction_enabled is False
        assert loaded.auth_mode == "continuum"
        assert loaded.bff_service_token and len(loaded.bff_service_token) >= 32


async def test_standalone_bff_scoped_auth_login_upload_and_sources(tmp_path, monkeypatch) -> None:
    import httpx
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from sag_api.core import config as config_mod
    from sag_api.core import db as db_mod
    from sag_api.core import deps as deps_mod
    from sag_api.db.base import Base
    from sag_api.db.models import Document, Source, SourceProjectMapping
    from sag_api.main import create_app

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_session():
        async with factory() as session:
            yield session

    class _NoopQueue:
        async def enqueue_durably(self, job_id: str) -> None:
            return None

        async def enqueue(self, job_id: str) -> None:
            return None

    bff_token = "aiinvest-sag-bff-standalone-test-token-32bytes"
    monkeypatch.setattr(config_mod.settings, "auth_mode", "continuum")
    monkeypatch.setattr(config_mod.settings, "bff_service_token", bff_token)
    monkeypatch.setattr(config_mod.settings, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(config_mod.settings, "upload_dir", str(tmp_path / "uploads"))

    app = create_app()
    app.dependency_overrides[db_mod.get_session] = _override_session
    app.dependency_overrides[deps_mod.get_job_queue] = lambda: _NoopQueue()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://sag.test") as client:
            login_resp = await client.post(
                "/api/v1/auth/continuum-login",
                headers={
                    "X-SAG-BFF-Service-Token": bff_token,
                    "X-Continuum-User-Id": "65f1a2b3c4d5e6f7a8b9c0d1",
                    "X-Continuum-Name": "AIInvest Research",
                    "X-Continuum-Organization-Id": "aiinvest",
                    "X-Continuum-Project-Id": "FPT",
                },
            )
            assert login_resp.status_code == 200, login_resp.text
            access_token = login_resp.json()["access_token"]
            bearer = {"Authorization": f"Bearer {access_token}"}

            before_sources = await client.get("/api/v1/sources", headers=bearer)
            assert before_sources.status_code == 200, before_sources.text
            assert before_sources.json() == []

            upload_resp = await client.post(
                "/api/v1/projects/FPT/documents/upload",
                headers={
                    "X-SAG-BFF-Service-Token": bff_token,
                    "X-Continuum-User-Id": "65f1a2b3c4d5e6f7a8b9c0d1",
                    "X-Continuum-Organization-Id": "aiinvest",
                    "X-Continuum-Project-Id": "FPT",
                    "X-Continuum-Security-Partition": "FPT",
                    "Idempotency-Key": "fpt-bctn-2025-test",
                },
                files={
                    "file": (
                        "fpt_bctn_2025.md",
                        b"# FPT Annual Report 2025\n\nGlobal IT Services revenue grew 25%.\n",
                        "text/markdown",
                    )
                },
            )
            assert upload_resp.status_code == 201, upload_resp.text
            body = upload_resp.json()
            assert body["document_id"]
            assert body["version_id"]

            after_sources = await client.get("/api/v1/sources", headers=bearer)
            assert after_sources.status_code == 200, after_sources.text
            assert after_sources.json() == []

            async with factory() as verify_session:
                sources = (await verify_session.execute(select(Source))).scalars().all()
                assert len(sources) == 1
                provisioned_source = sources[0]
                assert provisioned_source.config.get("is_project_source") is True
                assert provisioned_source.config.get("project_id") == "FPT"
                assert provisioned_source.config.get("tenant_id") == "aiinvest"

                mappings = (await verify_session.execute(select(SourceProjectMapping))).scalars().all()
                assert len(mappings) == 1
                mapping = mappings[0]
                assert mapping.source_id == provisioned_source.id
                assert mapping.organization_id == "aiinvest"
                assert mapping.project_id == "FPT"
                assert mapping.state == "CONFIRMED"
                assert mapping.confirmed_at is not None

                uploaded_doc = (
                    await verify_session.execute(select(Document).where(Document.id == body["document_id"]))
                ).scalar_one()
                assert uploaded_doc.source_id == provisioned_source.id
                assert uploaded_doc.project_id == "FPT"

            status_resp = await client.get(
                f"/api/v1/projects/FPT/documents/{body['document_id']}/versions/{body['version_no']}/status",
                headers={
                    "X-SAG-BFF-Service-Token": bff_token,
                    "X-Continuum-User-Id": "65f1a2b3c4d5e6f7a8b9c0d1",
                    "X-Continuum-Organization-Id": "aiinvest",
                    "X-Continuum-Project-Id": "FPT",
                    "X-Continuum-Security-Partition": "FPT",
                },
            )
            assert status_resp.status_code == 200, status_resp.text
            assert status_resp.json()["document_id"] == body["document_id"]
            assert status_resp.json()["version_no"] == body["version_no"]
    finally:
        await engine.dispose()


