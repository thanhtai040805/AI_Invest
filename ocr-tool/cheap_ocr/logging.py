"""Logging setup for cheap-ocr entrypoints.

Library modules obtain a logger with ``logging.getLogger(__name__)`` and never
configure logging at import time. The actual entrypoints (the bare-metal CLI and
the Modal GPU worker) call :func:`configure_logging` once at startup to attach a
stderr handler to the ``cheap_ocr`` logger.

The handler is attached to the package logger (not the root logger) so cheap-ocr
controls its own namespace without clobbering host-application or third-party
logging configuration.
"""

import logging
import os

_PACKAGE_LOGGER = "cheap_ocr"
_DEFAULT_LEVEL = "INFO"
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(level: str | None = None) -> None:
    """Attach a stderr handler to the ``cheap_ocr`` logger.

    Idempotent: safe to call from every entrypoint. The level defaults to the
    ``CHEAP_OCR_LOG_LEVEL`` environment variable, then ``INFO``.

    Args:
        level: Explicit level name (e.g. ``"DEBUG"``) overriding the environment.
    """
    resolved = (level or os.environ.get("CHEAP_OCR_LOG_LEVEL") or _DEFAULT_LEVEL).upper()
    logger = logging.getLogger(_PACKAGE_LOGGER)
    logger.setLevel(resolved)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        logger.addHandler(handler)
        # Handle records here rather than letting them bubble to the root logger,
        # which avoids duplicate output if the host application also configured one.
        logger.propagate = False
