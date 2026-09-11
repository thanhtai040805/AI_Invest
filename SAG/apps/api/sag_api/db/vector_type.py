from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy.types import TypeDecorator, UserDefinedType


class PgVectorDDL(UserDefinedType):
    """DDL-only pgvector type for Alembic migrations.

    We intentionally keep this local instead of depending on the optional
    pgvector Python adapter. PostgreSQL's pgvector extension accepts vectors
    through its text input form, so the ORM type below can bind safely while
    SQLite/local tests still work without the extension.
    """

    cache_ok = True

    def __init__(self, dimensions: int | None = None) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **_kw: Any) -> str:
        return "TEXT"


class PgVector(TypeDecorator[list[float]]):
    """Portable pgvector binding with SQLite-safe fallback.

    PostgreSQL stores this as ``vector`` when the migration creates the column;
    SQLite sees a text payload and can still run local tests. The canonical
    vector payload remains a Python list at the service boundary.
    """

    impl = PgVectorDDL
    cache_ok = True

    def __init__(self, dimensions: int | None = None) -> None:
        super().__init__(dimensions)
        self.dimensions = dimensions

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PgVectorDDL(self.dimensions))
        from sqlalchemy import Text

        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: Sequence[float] | str | None, _dialect) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return "[" + ",".join(_format_float(float(item)) for item in value) + "]"

    def process_result_value(self, value: Any, _dialect) -> list[float] | None:
        if value is None:
            return None
        if isinstance(value, list):
            return [float(item) for item in value]
        text = str(value).strip()
        if not text:
            return None
        if text.startswith("[") and text.endswith("]"):
            return [float(item) for item in text[1:-1].split(",") if item]
        try:
            loaded = json.loads(text)
        except Exception:
            return None
        if isinstance(loaded, list):
            return [float(item) for item in loaded]
        return None


def _format_float(value: float) -> str:
    # pgvector accepts ordinary decimal/scientific notation. 9 significant
    # digits are enough for embedding retrieval while keeping rows smaller.
    return f"{value:.9g}"
