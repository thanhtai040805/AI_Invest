"""
State management for brain agents.

Provides lazy imports via __getattr__ to avoid triggering LLM client
initialisation at package-import time.
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_MAP: dict[str, str] = {
    "Reflector": ".reflection",
    "reflector": ".reflection",
    "SignalProcessor": ".signal_processing",
    "signal_processor": ".signal_processing",
    "Checkpointer": ".checkpointer",
    "checkpointer": ".checkpointer",
    "ConcurrencyManager": ".concurrency",
    "concurrency_manager": ".concurrency",
    "AnalystType": ".concurrency",
    "AnalystSpec": ".concurrency",
    "ExecutionPlan": ".concurrency",
}


def __getattr__(name: str) -> Any:
    """Lazy-load module attributes."""
    if name in _LAZY_MAP:
        mod = importlib.import_module(_LAZY_MAP[name], __package__)
        attr = getattr(mod, name)
        globals()[name] = attr
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = list(_LAZY_MAP.keys())
