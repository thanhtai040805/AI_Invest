"""User-facing configuration for cheap-ocr.

Two scopes, two dataclasses (plus :class:`ModalConfig` for the Modal launcher):

* :class:`OcrConfig` — everything that can vary per call; passed to
  :meth:`DocumentEngine.run`/``stream``/``ocr`` (or as the engine's default).
* :class:`EngineConfig` — startup-scoped knobs (what gets loaded onto the GPU
  and how); passed to :class:`~cheap_ocr.engine.DocumentEngine` at construction.

Every field resolves the same way: an explicit value (CLI flag or constructor
argument) wins, else the field's ``CHEAP_OCR_*`` environment variable, else the
built-in default. The environment is read when a config is instantiated, not at
import time.
"""

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from typing import Any, Literal, Self

LayoutBackend = Literal["onnx", "transformers"]


def strict_kwargs(fields: set[str], kind: str, value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate mapping keys against a dataclass's fields, dropping ``None`` values.

    Strictness is deliberate: this parses user-supplied JSON (the Modal
    entrypoint's ``--config``/``--policy``), where a silently dropped typo would
    run a whole batch with the wrong settings.
    """
    unknown = set(value) - fields
    if unknown:
        raise ValueError(f"Unknown {kind} keys: {sorted(unknown)}; valid keys: {sorted(fields)}")
    return {key: item for key, item in value.items() if item is not None}


def parse_bool(raw: str) -> bool:
    """Parse an environment-variable boolean (``1``/``true``/``yes`` are true)."""
    return raw.strip().lower() in {"1", "true", "yes"}


def env_field(env: str, default: Any, parse: Callable[[str], Any], *, empty_is_unset: bool = True) -> Any:
    """A dataclass field that falls back to the ``env`` variable, then ``default``.

    The environment is read when the dataclass is instantiated, so an explicit
    constructor argument always wins over the environment. ``default`` may be a
    callable (evaluated lazily, for dynamic defaults). With ``empty_is_unset``
    (the default) an empty environment value counts as unset; disable it where
    an explicit empty string is meaningful.
    """

    def resolve() -> Any:
        raw = os.environ.get(env)
        if raw is None or (empty_is_unset and raw == ""):
            return default() if callable(default) else default
        return parse(raw)

    return field(default_factory=resolve, metadata={"env": env})


def to_env_value(value: object) -> str:
    """Serialize one config field value to its environment-variable string."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def config_env(config: Any) -> dict[str, str]:
    """Serialize a config's values back to their ``CHEAP_OCR_*`` variables.

    Used by the Modal launcher to bake the caller's resolved settings into the
    container image, so in-container config instances see the caller's values.
    """
    return {str(f.metadata["env"]): to_env_value(getattr(config, f.name)) for f in fields(config)}


def env_overrides(config_cls: type, values: Mapping[str, Any]) -> dict[str, str]:
    """Map explicitly-set field values to their environment variables.

    Used to hand flags to a child process (the ``modal run`` subprocess) without
    a flag list that could drift: ``None`` values (unset flags) are dropped so
    the child's own environment fallback still applies.
    """
    env_by_field = {f.name: str(f.metadata["env"]) for f in fields(config_cls)}  # type: ignore[arg-type]
    unknown = set(values) - set(env_by_field)
    if unknown:
        raise ValueError(f"Unknown {config_cls.__name__} fields: {sorted(unknown)}")
    return {env_by_field[name]: to_env_value(value) for name, value in values.items() if value is not None}


@dataclass(frozen=True, slots=True)
class OcrConfig:
    """Configuration for one cheap-ocr run.

    Carries everything that can vary per call; startup-scoped knobs (vLLM server
    flags, layout backend) live on :class:`EngineConfig`.

    Defaults are tuned for throughput on a single A100-40GB (see the project
    README): 100 DPI rendering and OCR request concurrency matched to vLLM's
    ``max_num_seqs``. Raise ``pdf_dpi`` if your documents need higher OCR
    accuracy than 100 DPI provides.
    """

    # Resumability. ``force`` reprocesses documents whose outputs are already
    # complete (resumable runs skip them by default).
    force: bool = env_field("CHEAP_OCR_FORCE", False, parse_bool)

    # PDF rendering. PDFium renders pages, and ``pdf_max_side`` caps the
    # longest rendered page side in pixels.
    # ``page_chunk_size`` is how many pages flow through render → layout → crop →
    # OCR as one streaming unit; smaller chunks start OCR earlier, larger chunks
    # amortize per-chunk PDF reopening. Keep it a multiple of the layout batch
    # size (``EngineConfig.layout_batch_size``, default 4) so no chunk ends on an
    # under-filled layout forward pass.
    pdf_dpi: int = env_field("CHEAP_OCR_PDF_DPI", 100, int)
    pdf_max_side: int = env_field("CHEAP_OCR_PDF_MAX_SIDE", 3500, int)
    page_chunk_size: int = env_field("CHEAP_OCR_PAGE_CHUNK_SIZE", 8, int)

    # Region crops are JPEG-encoded for vLLM at this quality.
    jpeg_quality: int = env_field("CHEAP_OCR_JPEG_QUALITY", 90, int)

    # Generation behavior.
    max_tokens_text: int = env_field("CHEAP_OCR_MAX_TOKENS_TEXT", 2048, int)
    max_tokens_formula: int = env_field("CHEAP_OCR_MAX_TOKENS_FORMULA", 2048, int)
    max_tokens_table: int = env_field("CHEAP_OCR_MAX_TOKENS_TABLE", 4096, int)
    temperature: float = env_field("CHEAP_OCR_TEMPERATURE", 0.0, float)
    top_p: float = env_field("CHEAP_OCR_TOP_P", 0.00001, float)

    # Concurrency. ``max_inflight_pdfs`` bounds how many documents are rendered,
    # laid out, and cropped at once. ``ocr_request_concurrency`` bounds how many
    # OCR requests are in flight against the local vLLM server at once; this is
    # what keeps the engine's continuous batcher fed. Keep it near
    # ``EngineConfig.max_num_seqs``. ``storage_check_workers`` is the thread pool
    # for reading completion markers when resuming against (cloud) targets.
    max_inflight_pdfs: int = env_field("CHEAP_OCR_MAX_INFLIGHT_PDFS", 16, int)
    ocr_request_concurrency: int = env_field("CHEAP_OCR_OCR_REQUEST_CONCURRENCY", 1024, int)
    storage_check_workers: int = env_field("CHEAP_OCR_STORAGE_CHECK_WORKERS", 64, int)

    def __post_init__(self) -> None:
        """Fail fast on values vLLM would reject per request (as opaque HTTP 400s)."""
        if not 0.0 < self.top_p <= 1.0:
            raise ValueError(f"top_p must be in (0, 1], got {self.top_p}")
        if self.temperature < 0.0:
            raise ValueError(f"temperature must be >= 0, got {self.temperature}")
        if min(self.max_tokens_text, self.max_tokens_formula, self.max_tokens_table) < 1:
            raise ValueError("max_tokens_text/formula/table must all be >= 1")
        if self.pdf_dpi < 1:
            raise ValueError(f"pdf_dpi must be >= 1, got {self.pdf_dpi}")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        """Create a config from a mapping, rejecting unknown keys and dropping ``None`` values."""
        return cls(**strict_kwargs(set(cls.__dataclass_fields__), "OcrConfig", value))


@dataclass(frozen=True, slots=True)
class EngineConfig:
    """Startup-scoped configuration for one :class:`~cheap_ocr.engine.DocumentEngine`.

    Consumed when the engine starts: GPU/runtime knobs and how the in-process
    vLLM server is launched. Changing these requires an engine restart;
    per-call settings live on
    :class:`OcrConfig`.
    """

    # Local vLLM OpenAI-compatible server.
    vllm_port: int = env_field("CHEAP_OCR_VLLM_PORT", 8000, int)
    vllm_health_timeout_seconds: int = env_field("CHEAP_OCR_VLLM_HEALTH_TIMEOUT_SECONDS", 15 * 60, int)

    # vLLM tuning. Defaults target many small OCR crops on one A100-40GB shared
    # with the layout model. The GPU is shared, so vLLM is capped well below 1.0:
    # under concurrent OCR load its vision-encoder activations spike its
    # footprint, and the co-located layout model needs several GiB of headroom
    # for its own forward pass.
    gpu_memory_utilization: float = env_field("CHEAP_OCR_GPU_MEMORY_UTILIZATION", 0.60, float)
    max_model_len: int = env_field("CHEAP_OCR_MAX_MODEL_LEN", 8192, int)
    max_num_seqs: int = env_field("CHEAP_OCR_MAX_NUM_SEQS", 1024, int)
    max_num_batched_tokens: int = env_field("CHEAP_OCR_MAX_NUM_BATCHED_TOKENS", 16384, int)
    # API server scale-out parallelizes the CPU-bound front-end (tokenization +
    # multimodal preprocessing) and only pays off with spare CPU cores. 1 is the
    # default because api_server_count > 1 makes the vLLM EngineCore process die
    # on real multimodal requests (a native abort: the multimodal processor
    # executor is shut down while another ApiServer still schedules work on it),
    # which takes the whole OCR server down. Scale it up only after that vLLM
    # bug is fixed and verified with real OCR traffic.
    api_server_count: int = env_field("CHEAP_OCR_API_SERVER_COUNT", 1, int)
    # Speculative decoding (e.g. GLM MTP): a JSON string passed to vLLM's
    # --speculative-config. Off by default: besides being GLM-specific, MTP
    # caused the same EngineCore crash described above.
    speculative_config: str = env_field(
        "CHEAP_OCR_SPECULATIVE_CONFIG",
        "",
        str,
        empty_is_unset=False,
    )
    vllm_extra_args: str = env_field("CHEAP_OCR_VLLM_EXTRA_ARGS", "", str)

    # Layout backend: "onnx" runs the PaddleX RT-DETR ONNX via ONNX Runtime with
    # the TensorRT execution provider (much faster, FP16); "transformers" runs
    # the original PyTorch PP-DocLayoutV3. The TRT engine is built on first use
    # and cached so later container starts skip the (slow) build.
    layout_backend: LayoutBackend = env_field("CHEAP_OCR_LAYOUT_BACKEND", "onnx", str)
    # Layout detection runs first so vLLM measures the remaining free memory
    # correctly at startup. NOTE: bigger layout batches HURT here — a larger
    # layout forward monopolizes the shared GPU for a longer continuous stretch
    # and stalls OCR decode (measured: batch 16 dropped throughput from ~8.2 to
    # ~5.1 pages/s). Small batches interleave well: 1, 2, and 4 measured
    # near-identical (~8.2), 4 is the default. The TRT engine profile is built
    # to match this batch.
    layout_batch_size: int = env_field("CHEAP_OCR_LAYOUT_BATCH_SIZE", 4, int)
    # Detection score threshold. None means each backend's calibrated default
    # (0.5 for the ONNX RT-DETR export, 0.3 for the transformers detector).
    layout_threshold: float | None = env_field("CHEAP_OCR_LAYOUT_THRESHOLD", None, float)
    trt_layout_cache: str = env_field("CHEAP_OCR_TRT_LAYOUT_CACHE", "/root/.cache/vllm/trt_layout", str)
    trt_builder_opt_level: int = env_field("CHEAP_OCR_TRT_BUILDER_OPT_LEVEL", 1, int)

    # Thread-pool size for crop/resize/JPEG preparation, shared by all documents.
    crop_encode_workers: int = env_field("CHEAP_OCR_CROP_ENCODE_WORKERS", 8, int)

    def __post_init__(self) -> None:
        """Fail fast on values the engine or vLLM would choke on later."""
        if self.layout_backend not in ("onnx", "transformers"):
            raise ValueError(f"layout_backend must be 'onnx' or 'transformers', got {self.layout_backend!r}")
        if not 0.0 < self.gpu_memory_utilization <= 1.0:
            raise ValueError(f"gpu_memory_utilization must be in (0, 1], got {self.gpu_memory_utilization}")
        if self.layout_batch_size < 1:
            raise ValueError(f"layout_batch_size must be >= 1, got {self.layout_batch_size}")
        if self.crop_encode_workers < 1:
            raise ValueError(f"crop_encode_workers must be >= 1, got {self.crop_encode_workers}")

    def to_env(self) -> dict[str, str]:
        """Serialize resolved values back to their ``CHEAP_OCR_*`` variables."""
        return config_env(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        """Create a config from a mapping, rejecting unknown keys and dropping ``None`` values."""
        return cls(**strict_kwargs(set(cls.__dataclass_fields__), "EngineConfig", value))


@dataclass(frozen=True, slots=True)
class ModalConfig:
    """GPU/container resources for the Modal launcher.

    Consumed by the ``@app.cls`` decorators in :mod:`cheap_ocr.modal` at import
    time; bare-metal users choose these at provisioning time instead. The
    ``cheap-ocr modal`` CLI forwards explicit flags to the ``modal run``
    subprocess as environment variables.
    """

    app_name: str = env_field("CHEAP_OCR_APP_NAME", "cheap-ocr", str)
    gpu: str = env_field("CHEAP_OCR_GPU", "A100-40GB", str)
    cpu_cores: float = env_field("CHEAP_OCR_CPU_CORES", 8.0, float)
    cpu_limit: float | None = env_field("CHEAP_OCR_CPU_LIMIT", None, float)
    scaledown_window_seconds: int = env_field("CHEAP_OCR_SCALEDOWN_WINDOW_SECONDS", 5 * 60, int)
    startup_timeout_seconds: int = env_field("CHEAP_OCR_STARTUP_TIMEOUT_SECONDS", 20 * 60, int)
    timeout_seconds: int = env_field("CHEAP_OCR_TIMEOUT_SECONDS", 6 * 60 * 60, int)
    max_containers: int = env_field("CHEAP_OCR_MAX_CONTAINERS", 1, int)
    # Comma-separated Modal secret names mounted into the workers.
    secrets: str = env_field("CHEAP_OCR_MODAL_SECRET_NAMES", "", str)

    @property
    def secret_names(self) -> list[str]:
        """The ``secrets`` field split into individual Modal secret names."""
        return [name.strip() for name in self.secrets.split(",") if name.strip()]

    @property
    def cpu(self) -> float | tuple[float, float]:
        """The CPU request, or ``(request, limit)`` when a limit is set."""
        return (self.cpu_cores, self.cpu_limit) if self.cpu_limit else self.cpu_cores

    def to_env(self) -> dict[str, str]:
        """Serialize resolved values back to their ``CHEAP_OCR_*`` variables."""
        return config_env(self)
