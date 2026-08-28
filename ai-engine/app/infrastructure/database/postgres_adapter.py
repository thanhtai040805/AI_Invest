"""PostgreSQL Adapter Alias for Infrastructure Layer.
Re-exports PostgresAdapter from app.adapters.postgres_adapter to maintain compatibility.
"""
from app.adapters.postgres_adapter import PostgresAdapter

__all__ = ["PostgresAdapter"]
