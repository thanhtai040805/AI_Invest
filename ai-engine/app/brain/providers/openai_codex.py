"""OpenAI Codex login helper — compatibility stub."""

import os
import logging

logger = logging.getLogger(__name__)


def get_openai_codex_login_status() -> str | None:
    """Return a cached OpenAI Codex token, or ``None`` if not configured.

    The original ``src.providers.openai_codex`` maintained a persistent
    login session.  For now we read the API key from the environment so
    dependent code (e.g. the preflight scanner) doesn't raise.
    """
    token = os.getenv("OPENAI_API_KEY")
    if not token:
        logger.warning("OPENAI_API_KEY not set — Codex features unavailable")
        return None
    return token
