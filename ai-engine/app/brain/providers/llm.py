"""LLM environment helpers — compatibility stub for ``src.providers.llm``."""

import os
import logging

logger = logging.getLogger(__name__)


def _ensure_dotenv() -> None:
    """Ensure .env is loaded.  Relies on ``app.config.settings`` already
    calling ``load_dotenv`` during import."""
    from app.config.settings import get_settings
    get_settings()  # triggers load_dotenv
    logger.debug("_ensure_dotenv: settings loaded")


def _sync_provider_env() -> None:
    """Sync provider environment variables.

    In the original ``src.providers.llm`` this was responsible for reading
    a config file and exporting variables to ``os.environ``.  The current
    codebase reads env vars directly via ``app.config.settings.Settings``,
    so this is a no‑op kept for import compatibility.
    """
    logger.debug("_sync_provider_env: no‑op (env already configured)")
