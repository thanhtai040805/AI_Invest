"""cheap-ocr: cheap, fast single-GPU PDF OCR.

Co-locates the PP-DocLayoutV3 layout detector and the GLM-OCR recognizer (served
by an in-process vLLM OpenAI server with continuous batching) on a single GPU,
with an async pipeline that keeps the GPU saturated. Runs on any GPU box and
ships a thin launcher for Modal.

The heavy engine objects are imported lazily so ``import cheap_ocr`` (and reading
:class:`OcrConfig`) works without torch/vLLM installed.
"""

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

from cheap_ocr.batches import BatchPolicy
from cheap_ocr.config import EngineConfig, OcrConfig
from cheap_ocr.models import DocumentInput, DocumentResult, OcrError, ProcessedDoc

if TYPE_CHECKING:
    from cheap_ocr.engine import DocumentEngine

try:
    __version__ = version("cheap-ocr")
except PackageNotFoundError:  # pragma: no cover - editable checkout without metadata.
    __version__ = "0.0.0.dev0"

__all__ = [
    "BatchPolicy",
    "DocumentInput",
    "DocumentResult",
    "EngineConfig",
    "OcrConfig",
    "DocumentEngine",
    "OcrError",
    "ProcessedDoc",
    "__version__",
]


def __getattr__(name: str) -> object:
    """Lazily import GPU-heavy objects on first access.

    Importing :class:`DocumentEngine` pulls in the GPU stack (torch/vLLM via
    :mod:`cheap_ocr.engine`), so it is deferred to first access (PEP 562).
    This keeps ``import cheap_ocr`` and reading :class:`OcrConfig` working
    without those heavy, Linux/GPU-only dependencies installed.
    """
    if name == "DocumentEngine":
        from cheap_ocr.engine import DocumentEngine

        return DocumentEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
