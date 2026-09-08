import pytest
from pydantic import ValidationError


def test_settings_rejects_sqlite_runtime_by_default():
    from sag_api.core.config import Settings

    with pytest.raises(ValidationError, match="no longer supports SQLite"):
        Settings(database_url="sqlite+aiosqlite:///./.data/sag.db", allow_sqlite_runtime=False)


def test_settings_allows_sqlite_only_with_explicit_escape_hatch():
    from sag_api.core.config import Settings

    settings = Settings(database_url="sqlite+aiosqlite:///:memory:", allow_sqlite_runtime=True)

    assert settings.database_url.startswith("sqlite")


def test_prod_requires_postgres_and_strong_service_token():
    from sag_api.core.config import Settings

    with pytest.raises(ValidationError, match="production requires PostgreSQL"):
        Settings(
            environment="prod",
            database_url="sqlite+aiosqlite:///./.data/sag.db",
            allow_sqlite_runtime=True,
            service_token="x" * 32,
        )

    with pytest.raises(ValidationError, match="strong SAG_SERVICE_TOKEN"):
        Settings(environment="prod", database_url="postgresql+asyncpg://sag:sag@db:5432/sag", service_token="weak")

    settings = Settings(
        environment="prod",
        database_url="postgresql+asyncpg://sag:sag@db:5432/sag",
        service_token="x" * 32,
    )

    assert settings.database_url.startswith("postgresql+asyncpg://")
