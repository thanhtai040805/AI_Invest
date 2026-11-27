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
        "SAG_MINERU_OFFICIAL_API_TOKEN_0",
        "SAG_MINERU_OFFICIAL_API_TOKEN_1",
        "SAG_MINERU_OFFICIAL_API_TOKENS",
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
        if env_key.startswith(("SAG_", "R2_", "CLOUDFLARE_API_TOKEN")):
            monkeypatch.delenv(env_key, raising=False)

    api_root = Path(__file__).resolve().parents[1]
    sag_root = Path(__file__).resolve().parents[3]
    repo_root = Path(__file__).resolve().parents[4]

    templates = [
        api_root / ".env.example",
        sag_root / ".env.example",
        repo_root / ".env.example",
        repo_root / ".env.production.example",
    ]
    for template in templates:
        assert template.is_file(), f"Missing env template: {template}"
        seen_keys: set[str] = set()
        for raw_line in template.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key = line.split("=", 1)[0].strip()
            assert key not in seen_keys, f"Duplicate env key {key!r} in {template}"
            seen_keys.add(key)

        if template.name == ".env.production.example":
            # Production template intentionally leaves SAG_SECRET_KEY / DB URLs blank until filled on server
            continue

        loaded = Settings(_env_file=template)
        assert loaded.secret_key.strip(), f"Empty secret_key in {template}"
        assert loaded.sag_qdrant_url.startswith(("http://", "https://")), f"Invalid Qdrant URL in {template}"
        assert not hasattr(loaded, "knowledge_tree_rebuild_enabled")
        assert loaded.user_memory_mode == "on"
        assert loaded.sag_qdrant_shared_collection == "aiinvest_search_units"
        assert loaded.auth_mode == "continuum"
        assert loaded.bff_service_token and len(loaded.bff_service_token) >= 32
        assert loaded.rerank_provider == "deepinfra"
        assert loaded.rerank_model == "Qwen/Qwen3-Reranker-0.6B"
        assert loaded.decision_provider == "disabled"
        assert loaded.decision_model == "Kev-4b"
        assert loaded.r2_bucket_name == "aiinvest-bctc-prod"

    bare = Settings(_env_file=None)
    assert bare.user_memory_mode == "on"
    assert bare.sag_qdrant_shared_collection == "aiinvest_search_units"
    assert bare.rerank_provider == "deepinfra"
    assert bare.decision_provider == "disabled"
    auto_sf = Settings(_env_file=None, siliconflow_api_key="sf-live-key")
    assert auto_sf.decision_provider == "siliconflow"


def test_r2_core_db_deepinfra_rerank_and_siliconflow_fail_open_wiring(tmp_path, monkeypatch) -> None:
    import os

    from sag_api.core import config as config_mod
    from sag_api.research.batch_ingest import resolve_default_core_database_url
    from sag_api.research.financial_metrics_store import resolve_core_database_url
    from sag_api.research.r2_markdown_cache import SagR2MarkdownCache
    from sag_api.services.reranker_client import CrossEncoderRerankerClient

    for env_key in list(os.environ):
        if env_key.startswith(("SAG_", "R2_", "CLOUDFLARE_API_TOKEN")):
            monkeypatch.delenv(env_key, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "SAG_CORE_DATABASE_URL=postgresql://postgres:secret@localhost:5432/aiinvest\n"
        "R2_ACCOUNT_ID=acc999\n"
        "R2_ENDPOINT_URL=https://acc999.r2.cloudflarestorage.com/\n"
        "R2_BUCKET_NAME=aiinvest-bctc-prod\n"
        "R2_ACCESS_KEY_ID=ak_test_123\n"
        "R2_SECRET_ACCESS_KEY=sk_test_456\n"
        "SAG_R2_MD_LOCAL_CACHE_DIR=./.data/custom-r2-md\n"
        "SAG_LLM_API_KEY=deepinfra-shared-key-test\n"
        "SAG_RERANK_PROVIDER=deepinfra\n"
        "SAG_RERANK_MODEL=Qwen/Qwen3-Reranker-0.6B\n"
        "SAG_DECISION_PROVIDER=siliconflow\n"
        "SAG_SILICONFLOW_API_KEY=\n",
        encoding="utf-8",
    )

    loaded = Settings(_env_file=env_file)
    assert loaded.core_database_url == "postgresql://postgres:secret@localhost:5432/aiinvest"
    assert loaded.r2_account_id == "acc999"
    assert loaded.r2_endpoint_url == "https://acc999.r2.cloudflarestorage.com/"
    assert loaded.r2_bucket_name == "aiinvest-bctc-prod"
    assert loaded.r2_access_key_id == "ak_test_123"
    assert loaded.r2_secret_access_key == "sk_test_456"
    assert loaded.r2_md_local_cache_dir == "./.data/custom-r2-md"
    assert loaded.r2_remote_configured is True
    assert loaded.effective_rerank_api_key == "deepinfra-shared-key-test"
    assert loaded.siliconflow_api_key is None

    cache = SagR2MarkdownCache.from_settings(loaded)
    assert cache.remote_mode == "s3"
    assert cache.is_configured is True
    assert cache.endpoint_url == "https://acc999.r2.cloudflarestorage.com"
    assert cache.bucket_name == "aiinvest-bctc-prod"
    assert cache.local_cache_dir == Path("./.data/custom-r2-md")

    # Verify batch_ingest & financial_metrics_store resolve settings.core_database_url and honor os.environ override
    monkeypatch.setattr(config_mod.settings, "core_database_url", loaded.core_database_url)
    assert resolve_default_core_database_url() == "postgresql://postgres:secret@localhost:5432/aiinvest"
    assert resolve_core_database_url() == "postgresql://postgres:secret@localhost:5432/aiinvest"

    monkeypatch.setenv("SAG_CORE_DATABASE_URL", "postgresql://postgres:override@db:5432/aiinvest")
    assert resolve_default_core_database_url() == "postgresql://postgres:override@db:5432/aiinvest"
    assert resolve_core_database_url() == "postgresql://postgres:override@db:5432/aiinvest"

    # DeepInfra Reranker reuses SAG_LLM_API_KEY automatically without needing duplicate SAG_RERANK_API_KEY
    monkeypatch.setattr(config_mod.settings, "llm_api_key", "deepinfra-shared-key-test")
    monkeypatch.setattr(config_mod.settings, "rerank_provider", "deepinfra")
    monkeypatch.setattr(config_mod.settings, "rerank_api_key", None)
    reranker = CrossEncoderRerankerClient()
    assert reranker.is_enabled() is True
    assert reranker.provider == "deepinfra"
    assert reranker.model == "Qwen/Qwen3-Reranker-0.6B"
    assert reranker._resolve_endpoint() == "https://api.deepinfra.com/v1/inference/Qwen/Qwen3-Reranker-0.6B"

    # Guard against common user mistake of pasting OpenAI base URL (/v1/openai) into SAG_RERANK_BASE_URL
    reranker_with_openai_suffix = CrossEncoderRerankerClient(
        provider="deepinfra",
        model="Qwen/Qwen3-Reranker-0.6B",
        base_url="https://api.deepinfra.com/v1/openai/",
        api_key="deepinfra-shared-key-test",
    )
    assert (
        reranker_with_openai_suffix._resolve_endpoint()
        == "https://api.deepinfra.com/v1/inference/Qwen/Qwen3-Reranker-0.6B"
    )


async def test_mineru_multi_token_round_robin_failover_and_parallel_chunking(tmp_path, monkeypatch) -> None:
    """Layer 2 Wire-Boundary Test (`httpx.MockTransport` — zero wrapper mocking):
    1. `Settings` collects `SAG_MINERU_OFFICIAL_API_TOKEN_0` & `_1` in order and scales concurrency.
    2. `MinerUOcrAdapter` splits a 12-page scanned PDF (`chunk_pages=5` -> 3 slices) in parallel
       across `tok-0` and `tok-1` via Round-Robin, pinning each `batch_id` poll to its creator token!
    3. When `tok-0` returns HTTP 401 (`A0211 user token expired`), `OfficialMinerUClient` cools down
       `tok-0` and transparently fails over batch submission to `tok-1` with zero dropped OCR pages!
    """
    import io
    import json
    import zipfile
    from unittest.mock import AsyncMock

    import fitz
    import httpx

    from sag_api.parsing.mineru_official import (
        OfficialMinerUClient,
        reset_mineru_token_pool_state,
    )
    from sag_api.research.ocr_adapter import MinerUOcrAdapter

    reset_mineru_token_pool_state()
    monkeypatch.setattr("sag_api.parsing.mineru._assert_public_host", AsyncMock(return_value=None))

    env_file = tmp_path / ".env"
    env_file.write_text(
        "SAG_MINERU_OFFICIAL_API_TOKEN_0=tok-rr-0\n"
        "SAG_MINERU_OFFICIAL_API_TOKEN_1=tok-rr-1\n"
        "SAG_MINERU_PER_TOKEN_CONCURRENCY=8\n",
        encoding="utf-8",
    )
    for k in (
        "SAG_MINERU_OFFICIAL_API_TOKEN",
        "SAG_MINERU_OFFICIAL_API_TOKEN_0",
        "SAG_MINERU_OFFICIAL_API_TOKEN_1",
        "SAG_MINERU_OFFICIAL_API_TOKENS",
    ):
        monkeypatch.delenv(k, raising=False)

    cfg = Settings(_env_file=env_file, mineru_poll_interval=0.05)
    assert cfg.effective_mineru_official_api_tokens == ("tok-rr-0", "tok-rr-1")
    assert cfg.mineru_configured is True
    assert cfg.effective_mineru_io_concurrency == 16
    assert cfg.effective_per_project_mineru_limit == 6

    def _zip_for_pages(count: int, label: str) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            md_parts = [f"<!-- PAGE {i} -->\nNội dung {label} trang {i}" for i in range(1, count + 1)]
            zf.writestr("full.md", "\n\n".join(md_parts))
        return buf.getvalue()

    batch_owner: dict[str, str] = {}
    batch_slice_name: dict[str, str] = {}
    submissions_by_token: dict[str, int] = {"Bearer tok-rr-0": 0, "Bearer tok-rr-1": 0}
    expire_tok0 = {"active": False}
    seq = {"n": 0}

    def _wire_handler(req: httpx.Request) -> httpx.Response:
        auth = req.headers.get("authorization")
        url_s = str(req.url)
        if req.method == "POST" and url_s == "https://mineru.net/api/v4/file-urls/batch":
            if expire_tok0["active"] and auth == "Bearer tok-rr-0":
                return httpx.Response(
                    401,
                    json={"msgCode": "A0211", "msg": "user token expired", "success": False},
                )
            assert auth in submissions_by_token, f"Unexpected auth: {auth}"
            submissions_by_token[auth] += 1
            seq["n"] += 1
            bid = f"batch-{seq['n']}"
            body = json.loads(req.content.decode("utf-8"))
            fname = body["files"][0]["name"]
            batch_owner[bid] = auth
            batch_slice_name[bid] = fname
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": bid,
                        "file_urls": [f"https://upload-mineru.example.com/{bid}"],
                    },
                },
            )
        if req.method == "PUT" and url_s.startswith("https://upload-mineru.example.com/"):
            assert auth is None
            return httpx.Response(200, text="")
        if req.method == "GET" and url_s.startswith("https://mineru.net/api/v4/extract-results/batch/"):
            bid = url_s.rsplit("/", 1)[-1]
            # Strict Batch-Affinity assertion: poll MUST use the exact token that created `bid`!
            assert auth == batch_owner[bid], f"Batch {bid} created by {batch_owner[bid]} but polled with {auth}!"
            fname = batch_slice_name[bid]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "file_name": fname,
                                "state": "done",
                                "full_zip_url": f"https://cdn-mineru.example.com/{bid}.zip",
                            }
                        ]
                    },
                },
            )
        if req.method == "GET" and url_s.startswith("https://cdn-mineru.example.com/"):
            assert auth is None
            bid = url_s.rsplit("/", 1)[-1].removesuffix(".zip")
            fname = batch_slice_name[bid]
            # Parse slice page span from `hose_slice_p{a}_p{b}.pdf`
            if "hose_slice_p" in fname:
                span = fname.removeprefix("hose_slice_p").removesuffix(".pdf")
                p_from, p_to = (int(x) for x in span.split("_p"))
                n_p = p_to - p_from + 1
            else:
                n_p = 1
            return httpx.Response(
                200,
                headers={"content-type": "application/zip"},
                content=_zip_for_pages(n_p, bid),
            )
        raise AssertionError(f"Unexpected request: {req.method} {url_s}")

    orig_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "sag_api.parsing.mineru_official.httpx.AsyncClient",
        lambda **kw: orig_async_client(transport=httpx.MockTransport(_wire_handler), **kw),
    )
    monkeypatch.setattr(
        "sag_api.parsing.mineru.httpx.AsyncClient",
        lambda **kw: orig_async_client(transport=httpx.MockTransport(_wire_handler), **kw),
    )

    # Build a 12-page scanned PDF -> chunk_pages=5 splits into 3 parallel slices (5 + 5 + 2) across 2 tokens!
    pdf_doc = fitz.open()
    for _ in range(12):
        pdf_doc.new_page()
    pdf_12p = pdf_doc.tobytes()
    pdf_doc.close()

    adapter = MinerUOcrAdapter(cfg, chunk_pages=5)
    page_map = await adapter.ocr_pdf_pages(pdf_12p, list(range(1, 13)), language="vi")
    assert sorted(page_map.keys()) == list(range(1, 13))
    # Both tokens received batches via Round-Robin (total 3 slices -> 2 + 1 across the 2 tokens)!
    assert submissions_by_token["Bearer tok-rr-0"] >= 1
    assert submissions_by_token["Bearer tok-rr-1"] >= 1
    assert sum(submissions_by_token.values()) == 3

    # Now simulate tok-rr-0 expiring (`HTTP 401 A0211`): client transparently fails over to tok-rr-1!
    reset_mineru_token_pool_state()
    expire_tok0["active"] = True
    single_pdf = tmp_path / "single.pdf"
    single_pdf.write_bytes(pdf_12p)
    client = OfficialMinerUClient(cfg, language="vi")
    md_failover = await client.parse(str(single_pdf))
    assert "Nội dung" in md_failover

    reset_mineru_token_pool_state()


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


