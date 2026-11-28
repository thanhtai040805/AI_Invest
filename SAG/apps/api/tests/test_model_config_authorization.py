from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from sag_api.api.v1 import system
from sag_api.core.config import settings
from sag_api.core.deps import get_current_user, get_session, require_model_config_operator
from sag_api.core.errors import ApiError


def _app_for_user(user_id: str) -> FastAPI:
    app = FastAPI()
    app.include_router(system.router, prefix="/api/v1")

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(status_code=error.status_code, content=error.to_envelope())

    async def current_user() -> SimpleNamespace:
        return SimpleNamespace(id=user_id)

    async def session() -> Any:
        yield None

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_session] = session
    return app


@pytest.mark.parametrize(
    ("auth_mode", "method", "path", "body"),
    [
        ("continuum", "PUT", "/api/v1/system/model-config", {"llm_model": "attacker-model"}),
        (
            "continuum",
            "POST",
            "/api/v1/system/model-config/test",
            {"llm_base_url": "https://attacker.example/v1"},
        ),
        ("continuum", "POST", "/api/v1/system/model-setup/302", {"api_key": "test-only-key"}),
        ("continuum", "POST", "/api/v1/system/model-config/mineru/302", None),
        ("password", "PUT", "/api/v1/system/model-config", {"llm_model": "attacker-model"}),
        ("password", "POST", "/api/v1/system/model-config/test", {"llm_base_url": "https://attacker.example/v1"}),
    ],
)
async def test_non_operator_cannot_access_shared_model_settings_in_multi_user_modes(
    monkeypatch: pytest.MonkeyPatch,
    auth_mode: str,
    method: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    monkeypatch.setattr(settings, "auth_mode", auth_mode)
    monkeypatch.setattr(settings, "model_config_operator_ids", "operator-id")
    app = _app_for_user("ordinary-user-id")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, path, json=body)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_allowlisted_user_is_a_model_configuration_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_mode", "continuum")
    monkeypatch.setattr(settings, "model_config_operator_ids", "other-id, operator-id")

    user = await require_model_config_operator(SimpleNamespace(id="operator-id"))

    assert user.id == "operator-id"


async def test_standalone_auth_modes_keep_existing_model_setup_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_mode", "local")
    monkeypatch.setattr(settings, "model_config_operator_ids", "")

    user = await require_model_config_operator(SimpleNamespace(id="local-user-id"))

    assert user.id == "local-user-id"
