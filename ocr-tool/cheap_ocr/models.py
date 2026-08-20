"""Data models for cheap-ocr: pages, regions, documents, and results."""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PIL import Image


class Task(StrEnum):
    """OCR task for a detected region; ``SKIP`` regions are never sent to OCR."""

    TEXT = "text"
    TABLE = "table"
    FORMULA = "formula"
    SKIP = "skip"


def output_stem_for(relative_path: str) -> str:
    """Map an input's relative path to its output directory stem (drops a .pdf suffix)."""
    path = PurePosixPath(relative_path)
    if path.suffix.lower() == ".pdf":
        return str(path.with_suffix(""))
    return str(path)


class OcrError(RuntimeError):
    """Raised by :meth:`DocumentEngine.ocr` when a document fails to process."""

    def __init__(self, phase: str, message: str) -> None:
        """Record the pipeline phase and error message."""
        super().__init__(f"OCR failed during {phase}: {message}")
        self.phase = phase
        self.message = message


@dataclass(slots=True)
class Page:
    """Rendered PDF page with its image dimensions.

    ``width``/``height`` duplicate ``image.size`` on purpose: they must survive
    ``image.close()``, which the pipeline calls as soon as crops are prepared.
    """

    page_index: int
    width: int
    height: int
    image: "Image.Image"


@dataclass(slots=True, frozen=True)
class Region:
    """Detected document region to OCR, skip, or format."""

    page_index: int
    region_index: int
    label: str
    task_type: Task
    bbox_2d: tuple[int, int, int, int]  # normalised (x1, y1, x2, y2), 0..1000
    score: float = 1.0


@dataclass(slots=True, frozen=True)
class PreparedRegion:
    """Region crop prepared for one vLLM request as a base64 ``data:`` URL."""

    region: Region
    image_url: str
    image_size_bytes: int


@dataclass(slots=True, frozen=True)
class TokenUsage:
    """Token counts, named to match the API convention."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(slots=True, frozen=True)
class RecognizedRegion:
    """OCR result for one detected region."""

    region: Region
    content: str | None
    usage: TokenUsage | None = None
    status_code: int | None = None
    latency_ms: int | None = None


@dataclass(slots=True, frozen=True)
class StageTiming:
    """Queue/execute split of one call to a serialized worker-thread stage."""

    queue_seconds: float = 0.0
    exec_seconds: float = 0.0
    wall_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class DocumentInput:
    """One PDF to OCR: a storage reference, or in-memory bytes via ``data``."""

    uri: str
    relative_path: str
    input_id: str
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])
    data: bytes | None = None

    @property
    def filename(self) -> str:
        """Return the input PDF file name."""
        return PurePosixPath(self.relative_path).name or PurePosixPath(self.uri).name

    @property
    def output_stem(self) -> str:
        """Return the relative output stem without the PDF suffix."""
        return output_stem_for(self.relative_path)


@dataclass(frozen=True, slots=True)
class OutputPaths:
    """Output paths for one OCR document."""

    markdown: str
    json: str
    stats: str


@dataclass(slots=True)
class DocumentTimings:
    """Per-phase wall-clock timings (seconds) for one document.

    Only ``total_seconds`` is always set; the per-phase fields are ``None`` for
    documents that skipped the pipeline (e.g. an empty PDF). ``render_seconds``
    and ``layout_seconds`` are end-to-end service-call timings summed across page
    chunks; the queue/exec fields split those calls between waiting for the
    serialized worker thread and executing on it.
    """

    total_seconds: float
    wait_seconds: float | None = None
    read_seconds: float | None = None
    render_seconds: float | None = None
    render_queue_seconds: float | None = None
    render_exec_seconds: float | None = None
    layout_seconds: float | None = None
    layout_queue_seconds: float | None = None
    layout_exec_seconds: float | None = None
    crop_seconds: float | None = None
    ocr_seconds: float | None = None
    format_seconds: float | None = None


@dataclass(slots=True)
class ResultSettings:
    """The effective settings one document was processed with."""

    pdf_dpi: int
    layout_model: str
    layout_backend: str
    ocr_backend: str
    ocr_request_concurrency: int


@dataclass(slots=True)
class DocumentResult:
    """One document's OCR output produced by the pipeline."""

    filename: str
    page_count: int
    region_count: int
    ocr_region_count: int
    skipped_region_count: int
    json: list[list[dict[str, Any]]]
    markdown: str
    tokens: TokenUsage
    timings: DocumentTimings
    settings: ResultSettings
    # Keyed by page index (as a string); dynamic, so it stays a mapping.
    region_counts_by_page: dict[str, int] = field(default_factory=dict[str, int])


@dataclass(slots=True)
class ProcessedDoc:
    """One document's result and stats after the pipeline finishes or fails."""

    document: DocumentInput
    stats: dict[str, Any]
    result: DocumentResult | None
