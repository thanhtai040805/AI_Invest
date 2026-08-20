"""Command-line interface for cheap-ocr.

The default command runs the whole pipeline in-process on the current GPU box
(no provider SDK). SSH into a GPU instance, install the package, and run::

    cheap-ocr --source ./input --target ./output
    cheap-ocr --source s3://bucket/in --target s3://bucket/out --limit 100

The ``modal`` subcommand instead runs the Modal-backed workflow by shelling out
to ``modal run`` (install the ``cheap-ocr[modal]`` extra)::

    cheap-ocr modal --source ./input --target ./output           # batched Modal GPU workflow
    cheap-ocr modal --source s3://in --target s3://out --detach   # cloud run detached in Modal

Local paths and cloud URIs (``s3://``, ``gs://``, ``az://``) both work; install
the matching storage extra (``cheap-ocr[s3]`` etc.) for cloud URIs.

Every settings flag is optional: an unset flag falls back to its ``CHEAP_OCR_*``
environment variable, then to the built-in default (noted in the option help).
Explicit flags always win over the environment.
"""

import asyncio
import json
import os
import shutil
import subprocess
from dataclasses import asdict
from typing import Annotated, Any

import typer

from cheap_ocr.batches import BatchPolicy
from cheap_ocr.config import EngineConfig, LayoutBackend, ModalConfig, OcrConfig, env_overrides
from cheap_ocr.logging import configure_logging

app = typer.Typer(
    add_completion=False,
    help=(
        "cheap, fast single-GPU PDF OCR. The default command runs the pipeline in-process on the local GPU; "
        "the 'modal' subcommand runs the Modal-backed workflow. Source/target may be local paths or cloud URIs. "
        "Unset flags fall back to CHEAP_OCR_* environment variables, then to the built-in defaults."
    ),
)

# The options, declared once and shared by both commands. All settings flags
# default to None (= unset) so the config dataclasses' environment fallback
# stays in charge; built-in defaults are noted in the help text.

SourceOpt = Annotated[str, typer.Option(help="Local folder/file or cloud URI containing PDFs.")]
TargetOpt = Annotated[str, typer.Option(help="Local folder or cloud URI for OCR outputs.")]
LimitOpt = Annotated[int | None, typer.Option(help="Maximum number of incomplete PDFs to process.")]
LogLevelOpt = Annotated[str | None, typer.Option(help="Log level (DEBUG/INFO/WARNING/...). [default: INFO]")]

# Run settings (OcrConfig).
ForceOpt = Annotated[bool | None, typer.Option(help="Reprocess documents whose outputs are already complete.")]
PdfDpiOpt = Annotated[int | None, typer.Option(help="PDF render DPI (100 is throughput-tuned). [default: 100]")]
PdfMaxSideOpt = Annotated[int | None, typer.Option(help="Longest rendered page side in pixels. [default: 3500]")]
PageChunkSizeOpt = Annotated[
    int | None,
    typer.Option(help="Pages streamed through render->layout->crop->OCR as one unit. [default: 8]"),
]
JpegQualityOpt = Annotated[int | None, typer.Option(help="JPEG quality for OCR crops. [default: 90]")]
MaxTokensTextOpt = Annotated[int | None, typer.Option(help="Max generated tokens for text regions. [default: 2048]")]
MaxTokensFormulaOpt = Annotated[int | None, typer.Option(help="Max generated tokens for formulae. [default: 2048]")]
MaxTokensTableOpt = Annotated[int | None, typer.Option(help="Max generated tokens for tables. [default: 4096]")]
TemperatureOpt = Annotated[float | None, typer.Option(help="Generation temperature. [default: 0.0]")]
TopPOpt = Annotated[float | None, typer.Option(help="Generation top_p. [default: 0.00001]")]
MaxInflightOpt = Annotated[int | None, typer.Option(help="Documents rendered/cropped concurrently.")]
OcrConcurrencyOpt = Annotated[
    int | None,
    typer.Option(help="Concurrent OCR requests to the vLLM server (keep near --max-num-seqs). [default: 1024]"),
]
StorageCheckWorkersOpt = Annotated[
    int | None,
    typer.Option(help="Threads reading completion markers when resuming against (cloud) targets. [default: 64]"),
]

# Engine settings (EngineConfig), startup-scoped.
VllmPortOpt = Annotated[int | None, typer.Option(help="Local vLLM server port. [default: 8000]")]
VllmHealthTimeoutOpt = Annotated[
    int | None, typer.Option(help="Seconds to wait for the vLLM server to become healthy. [default: 900]")
]
GpuMemoryUtilizationOpt = Annotated[
    float | None,
    typer.Option(help="vLLM's share of the GPU; the rest is headroom for the layout model. [default: 0.60]"),
]
MaxModelLenOpt = Annotated[int | None, typer.Option(help="vLLM max model length. [default: 8192]")]
MaxNumSeqsOpt = Annotated[int | None, typer.Option(help="vLLM max concurrent sequences. [default: 1024]")]
MaxNumBatchedTokensOpt = Annotated[int | None, typer.Option(help="vLLM batched-token budget. [default: 16384]")]
ApiServerCountOpt = Annotated[
    int | None, typer.Option(help="vLLM API server processes; scale with vCPUs. [default: 4]")
]
SpeculativeConfigOpt = Annotated[
    str | None,
    typer.Option(help="JSON for vLLM --speculative-config; empty string disables. [default: GLM MTP, 3 tokens]"),
]
VllmExtraArgsOpt = Annotated[str | None, typer.Option(help="Extra args appended to `vllm serve`.")]
LayoutBackendOpt = Annotated[
    LayoutBackend | None,
    typer.Option(help="Layout detector: onnx (ONNX Runtime + TensorRT) or transformers (PyTorch). [default: onnx]"),
]
LayoutBatchSizeOpt = Annotated[
    int | None, typer.Option(help="Layout batch size (small batches interleave best). [default: 4]")
]
LayoutThresholdOpt = Annotated[
    float | None,
    typer.Option(help="Layout detection score threshold. [default: backend-calibrated: 0.5 onnx / 0.3 transformers]"),
]
TrtLayoutCacheOpt = Annotated[
    str | None, typer.Option(help="TensorRT layout engine cache dir. [default: /root/.cache/vllm/trt_layout]")
]
TrtBuilderOptLevelOpt = Annotated[int | None, typer.Option(help="TensorRT builder optimization level. [default: 1]")]
CropEncodeWorkersOpt = Annotated[
    int | None, typer.Option(help="Threads for crop/resize/JPEG preparation. [default: 8]")
]
# Batch policy (BatchPolicy).
BatchDocsOpt = Annotated[int | None, typer.Option(help="Maximum PDFs per GPU batch. [default: 64]")]
BatchBytesMbOpt = Annotated[int | None, typer.Option(help="Approximate maximum input MB per GPU batch. [default: 512]")]
BatchPagesOpt = Annotated[int | None, typer.Option(help="Maximum PDF pages per GPU batch. [default: unlimited]")]
BatchRetriesOpt = Annotated[int | None, typer.Option(help="Retries for a failed/preempted GPU batch. [default: 3]")]
BatchRetryBackoffOpt = Annotated[
    float | None, typer.Option(help="Initial retry backoff seconds for failed batches. [default: 10]")
]


def build_config(
    *,
    force: bool | None,
    pdf_dpi: int | None,
    pdf_max_side: int | None,
    page_chunk_size: int | None,
    jpeg_quality: int | None,
    max_tokens_text: int | None,
    max_tokens_formula: int | None,
    max_tokens_table: int | None,
    temperature: float | None,
    top_p: float | None,
    max_inflight_pdfs: int | None,
    ocr_request_concurrency: int | None,
    storage_check_workers: int | None,
) -> OcrConfig:
    """Build the run config shared by both commands; unset flags defer to the environment."""
    return OcrConfig.from_dict(
        {
            "force": force,
            "pdf_dpi": pdf_dpi,
            "pdf_max_side": pdf_max_side,
            "page_chunk_size": page_chunk_size,
            "jpeg_quality": jpeg_quality,
            "max_tokens_text": max_tokens_text,
            "max_tokens_formula": max_tokens_formula,
            "max_tokens_table": max_tokens_table,
            "temperature": temperature,
            "top_p": top_p,
            "max_inflight_pdfs": max_inflight_pdfs,
            "ocr_request_concurrency": ocr_request_concurrency,
            "storage_check_workers": storage_check_workers,
        }
    )


def engine_values(
    *,
    vllm_port: int | None,
    vllm_health_timeout: int | None,
    gpu_memory_utilization: float | None,
    max_model_len: int | None,
    max_num_seqs: int | None,
    max_num_batched_tokens: int | None,
    api_server_count: int | None,
    speculative_config: str | None,
    vllm_extra_args: str | None,
    layout_backend: LayoutBackend | None,
    layout_batch_size: int | None,
    layout_threshold: float | None,
    trt_layout_cache: str | None,
    trt_builder_opt_level: int | None,
    crop_encode_workers: int | None,
) -> dict[str, Any]:
    """Collect the engine flags by :class:`EngineConfig` field name (``None`` = unset)."""
    return {
        "vllm_port": vllm_port,
        "vllm_health_timeout_seconds": vllm_health_timeout,
        "gpu_memory_utilization": gpu_memory_utilization,
        "max_model_len": max_model_len,
        "max_num_seqs": max_num_seqs,
        "max_num_batched_tokens": max_num_batched_tokens,
        "api_server_count": api_server_count,
        "speculative_config": speculative_config,
        "vllm_extra_args": vllm_extra_args,
        "layout_backend": layout_backend,
        "layout_batch_size": layout_batch_size,
        "layout_threshold": layout_threshold,
        "trt_layout_cache": trt_layout_cache,
        "trt_builder_opt_level": trt_builder_opt_level,
        "crop_encode_workers": crop_encode_workers,
    }


def build_policy(
    *,
    batch_docs: int | None,
    batch_bytes_mb: int | None,
    batch_pages: int | None,
    batch_retries: int | None,
    batch_retry_backoff_seconds: float | None,
) -> BatchPolicy:
    """Build the batch policy; unset flags defer to the environment."""
    return BatchPolicy.from_dict(
        {
            "max_docs": batch_docs,
            "max_bytes": batch_bytes_mb * 1024 * 1024 if batch_bytes_mb is not None else None,
            "max_pages": batch_pages,
            "retries": batch_retries,
            "retry_backoff_seconds": batch_retry_backoff_seconds,
        }
    )


@app.callback(invoke_without_command=True)
def run(
    ctx: typer.Context,
    source: SourceOpt = "./input",
    target: TargetOpt = "./output",
    force: ForceOpt = None,
    limit: LimitOpt = None,
    pdf_dpi: PdfDpiOpt = None,
    pdf_max_side: PdfMaxSideOpt = None,
    page_chunk_size: PageChunkSizeOpt = None,
    jpeg_quality: JpegQualityOpt = None,
    max_tokens_text: MaxTokensTextOpt = None,
    max_tokens_formula: MaxTokensFormulaOpt = None,
    max_tokens_table: MaxTokensTableOpt = None,
    temperature: TemperatureOpt = None,
    top_p: TopPOpt = None,
    max_inflight_pdfs: MaxInflightOpt = None,
    ocr_request_concurrency: OcrConcurrencyOpt = None,
    storage_check_workers: StorageCheckWorkersOpt = None,
    vllm_port: VllmPortOpt = None,
    vllm_health_timeout: VllmHealthTimeoutOpt = None,
    gpu_memory_utilization: GpuMemoryUtilizationOpt = None,
    max_model_len: MaxModelLenOpt = None,
    max_num_seqs: MaxNumSeqsOpt = None,
    max_num_batched_tokens: MaxNumBatchedTokensOpt = None,
    api_server_count: ApiServerCountOpt = None,
    speculative_config: SpeculativeConfigOpt = None,
    vllm_extra_args: VllmExtraArgsOpt = None,
    layout_backend: LayoutBackendOpt = None,
    layout_batch_size: LayoutBatchSizeOpt = None,
    layout_threshold: LayoutThresholdOpt = None,
    trt_layout_cache: TrtLayoutCacheOpt = None,
    trt_builder_opt_level: TrtBuilderOptLevelOpt = None,
    crop_encode_workers: CropEncodeWorkersOpt = None,
    batch_docs: BatchDocsOpt = None,
    batch_bytes_mb: BatchBytesMbOpt = None,
    batch_pages: BatchPagesOpt = None,
    batch_retries: BatchRetriesOpt = None,
    batch_retry_backoff_seconds: BatchRetryBackoffOpt = None,
    log_level: LogLevelOpt = None,
) -> None:
    """Run single-GPU (co-located layout + OCR) PDF OCR on the local machine."""
    if ctx.invoked_subcommand is not None:
        return

    configure_logging(log_level)

    config = build_config(
        force=force,
        pdf_dpi=pdf_dpi,
        pdf_max_side=pdf_max_side,
        page_chunk_size=page_chunk_size,
        jpeg_quality=jpeg_quality,
        max_tokens_text=max_tokens_text,
        max_tokens_formula=max_tokens_formula,
        max_tokens_table=max_tokens_table,
        temperature=temperature,
        top_p=top_p,
        max_inflight_pdfs=max_inflight_pdfs,
        ocr_request_concurrency=ocr_request_concurrency,
        storage_check_workers=storage_check_workers,
    )
    engine_config = EngineConfig.from_dict(
        engine_values(
            vllm_port=vllm_port,
            vllm_health_timeout=vllm_health_timeout,
            gpu_memory_utilization=gpu_memory_utilization,
            max_model_len=max_model_len,
            max_num_seqs=max_num_seqs,
            max_num_batched_tokens=max_num_batched_tokens,
            api_server_count=api_server_count,
            speculative_config=speculative_config,
            vllm_extra_args=vllm_extra_args,
            layout_backend=layout_backend,
            layout_batch_size=layout_batch_size,
            layout_threshold=layout_threshold,
            trt_layout_cache=trt_layout_cache,
            trt_builder_opt_level=trt_builder_opt_level,
            crop_encode_workers=crop_encode_workers,
        )
    )
    policy = build_policy(
        batch_docs=batch_docs,
        batch_bytes_mb=batch_bytes_mb,
        batch_pages=batch_pages,
        batch_retries=batch_retries,
        batch_retry_backoff_seconds=batch_retry_backoff_seconds,
    )

    # Imported here (not at module load) so `cheap-ocr --help` works without the
    # GPU stack installed. This loads the layout model and starts the vLLM server
    # on each invocation, so it suits one-shot CLI runs; a long-lived worker
    # should reuse one DocumentEngine instead.
    from cheap_ocr.engine import DocumentEngine

    engine = DocumentEngine(engine_config).start()
    try:
        summary = asyncio.run(engine.run(source, target, config, limit=limit, batch_policy=policy))
    finally:
        engine.stop()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


@app.command()
def modal(
    source: SourceOpt = "./input",
    target: TargetOpt = "./output",
    detach: Annotated[bool, typer.Option(help="Detach the run (cloud source and cloud target only).")] = False,
    force: ForceOpt = None,
    limit: LimitOpt = None,
    pdf_dpi: PdfDpiOpt = None,
    pdf_max_side: PdfMaxSideOpt = None,
    page_chunk_size: PageChunkSizeOpt = None,
    jpeg_quality: JpegQualityOpt = None,
    max_tokens_text: MaxTokensTextOpt = None,
    max_tokens_formula: MaxTokensFormulaOpt = None,
    max_tokens_table: MaxTokensTableOpt = None,
    temperature: TemperatureOpt = None,
    top_p: TopPOpt = None,
    max_inflight_pdfs: Annotated[
        int | None, typer.Option(help="Documents rendered/cropped concurrently. [default on Modal: 32]")
    ] = None,
    ocr_request_concurrency: OcrConcurrencyOpt = None,
    storage_check_workers: StorageCheckWorkersOpt = None,
    vllm_port: VllmPortOpt = None,
    vllm_health_timeout: VllmHealthTimeoutOpt = None,
    gpu_memory_utilization: GpuMemoryUtilizationOpt = None,
    max_model_len: MaxModelLenOpt = None,
    max_num_seqs: MaxNumSeqsOpt = None,
    max_num_batched_tokens: MaxNumBatchedTokensOpt = None,
    api_server_count: ApiServerCountOpt = None,
    speculative_config: SpeculativeConfigOpt = None,
    vllm_extra_args: VllmExtraArgsOpt = None,
    layout_backend: LayoutBackendOpt = None,
    layout_batch_size: LayoutBatchSizeOpt = None,
    layout_threshold: LayoutThresholdOpt = None,
    trt_layout_cache: TrtLayoutCacheOpt = None,
    trt_builder_opt_level: TrtBuilderOptLevelOpt = None,
    crop_encode_workers: CropEncodeWorkersOpt = None,
    batch_docs: BatchDocsOpt = None,
    batch_bytes_mb: BatchBytesMbOpt = None,
    batch_pages: BatchPagesOpt = None,
    batch_retries: BatchRetriesOpt = None,
    batch_retry_backoff_seconds: BatchRetryBackoffOpt = None,
    app_name: Annotated[str | None, typer.Option(help="Modal app name. [default: cheap-ocr]")] = None,
    gpu: Annotated[str | None, typer.Option(help="Modal GPU type. [default: A100-40GB]")] = None,
    cpu_cores: Annotated[float | None, typer.Option(help="Modal CPU request. [default: 16]")] = None,
    cpu_limit: Annotated[float | None, typer.Option(help="Modal CPU limit. [default: none]")] = None,
    scaledown_window: Annotated[
        int | None, typer.Option(help="Modal container scaledown window seconds. [default: 900]")
    ] = None,
    startup_timeout: Annotated[
        int | None, typer.Option(help="Modal container startup timeout seconds. [default: 1200]")
    ] = None,
    timeout: Annotated[int | None, typer.Option(help="Modal call timeout seconds. [default: 21600]")] = None,
    max_containers: Annotated[int | None, typer.Option(help="Maximum GPU containers. [default: 1]")] = None,
    modal_secrets: Annotated[
        str | None, typer.Option(help="Comma-separated Modal secret names mounted into the workers.")
    ] = None,
    log_level: LogLevelOpt = None,
) -> None:
    """Run the Modal-backed batched all-in-worker workflow."""
    if shutil.which("modal") is None:
        raise typer.BadParameter(
            "The `modal` CLI is not installed. Install the Modal extra: pip install 'cheap-ocr[modal]'."
        )

    # The Modal default deliberately differs: batches overlap upload/write
    # round-trips, so more documents in flight keep the remote GPU fed. Flag and
    # environment still win.
    if max_inflight_pdfs is None and not os.environ.get("CHEAP_OCR_MAX_INFLIGHT_PDFS"):
        max_inflight_pdfs = 32

    config = build_config(
        force=force,
        pdf_dpi=pdf_dpi,
        pdf_max_side=pdf_max_side,
        page_chunk_size=page_chunk_size,
        jpeg_quality=jpeg_quality,
        max_tokens_text=max_tokens_text,
        max_tokens_formula=max_tokens_formula,
        max_tokens_table=max_tokens_table,
        temperature=temperature,
        top_p=top_p,
        max_inflight_pdfs=max_inflight_pdfs,
        ocr_request_concurrency=ocr_request_concurrency,
        storage_check_workers=storage_check_workers,
    )
    policy = build_policy(
        batch_docs=batch_docs,
        batch_bytes_mb=batch_bytes_mb,
        batch_pages=batch_pages,
        batch_retries=batch_retries,
        batch_retry_backoff_seconds=batch_retry_backoff_seconds,
    )

    # Engine and Modal-resource flags cross into the `modal run` subprocess as
    # environment variables (Modal consumes them in decorators at import time);
    # only explicitly-set flags are forwarded, so its own env fallback holds.
    env = os.environ.copy()
    env.update(
        env_overrides(
            EngineConfig,
            engine_values(
                vllm_port=vllm_port,
                vllm_health_timeout=vllm_health_timeout,
                gpu_memory_utilization=gpu_memory_utilization,
                max_model_len=max_model_len,
                max_num_seqs=max_num_seqs,
                max_num_batched_tokens=max_num_batched_tokens,
                api_server_count=api_server_count,
                speculative_config=speculative_config,
                vllm_extra_args=vllm_extra_args,
                layout_backend=layout_backend,
                layout_batch_size=layout_batch_size,
                layout_threshold=layout_threshold,
                trt_layout_cache=trt_layout_cache,
                trt_builder_opt_level=trt_builder_opt_level,
                crop_encode_workers=crop_encode_workers,
            ),
        )
    )
    env.update(
        env_overrides(
            ModalConfig,
            {
                "app_name": app_name,
                "gpu": gpu,
                "cpu_cores": cpu_cores,
                "cpu_limit": cpu_limit,
                "scaledown_window_seconds": scaledown_window,
                "startup_timeout_seconds": startup_timeout,
                "timeout_seconds": timeout,
                "max_containers": max_containers,
                "secrets": modal_secrets,
            },
        )
    )
    if log_level is not None:
        env["CHEAP_OCR_LOG_LEVEL"] = log_level

    command = ["modal", "run"]
    if detach:
        # Modal's own detach flag must appear before FUNC_REF; the later
        # `--detach` is the cheap-ocr entrypoint flag that submits the
        # non-preemptible cloud supervisor and returns immediately.
        command.append("--detach")
    command += ["-m", "cheap_ocr.modal.run::run"]
    command += ["--source", source, "--target", target]
    # The whole config crosses as one strict JSON blob so the flag list cannot
    # drift between this CLI and the Modal entrypoint.
    command += ["--config", json.dumps(asdict(config))]
    command += ["--policy", json.dumps(asdict(policy))]
    if limit is not None:
        command += ["--limit", str(limit)]
    if detach:
        command.append("--detach")

    result = subprocess.run(command, check=False, env=env)
    raise typer.Exit(code=result.returncode)


def main() -> None:
    """Run the cheap-ocr CLI (the ``cheap-ocr`` console script)."""
    app()


if __name__ == "__main__":
    main()
