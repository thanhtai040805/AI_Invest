"""Manage the in-container vLLM OpenAI-compatible server for GLM-OCR.

The server provides continuous (dynamic) batching: the orchestrator streams many
small OCR requests concurrently and vLLM keeps the GPU saturated by scheduling
them together. The PyTorch layout model must be loaded before this server starts
so vLLM measures the remaining free GPU memory correctly.
"""

import logging
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.request

from cheap_ocr.config import EngineConfig
from cheap_ocr.ocr.constants import OCR_MODEL, SERVED_MODEL_NAME

logger = logging.getLogger(__name__)

# Where the vLLM server subprocess's stdout/stderr are captured, so startup
# failures can be diagnosed after the fact (placed on a shared volume).
VLLM_SERVER_LOG = os.environ.get("CHEAP_OCR_VLLM_LOG", "/root/.cache/vllm/vllm_server.log")


def _server_log_tail(lines: int = 40) -> str:
    """Return the tail of the captured vLLM server log, or a placeholder."""
    try:
        with open(VLLM_SERVER_LOG, "r", errors="replace") as handle:
            tail = handle.readlines()[-lines:]
        return "".join(tail).strip()
    except FileNotFoundError:
        return "<no vllm server log yet>"


def base_url(port: int) -> str:
    """Return the OpenAI-compatible base URL for the local vLLM server."""
    return f"http://127.0.0.1:{port}/v1"


def build_serve_command(config: EngineConfig) -> list[str]:
    """Build the ``vllm serve`` command for the OCR model."""
    command = [
        "vllm",
        "serve",
        OCR_MODEL,
        "--served-model-name",
        SERVED_MODEL_NAME,
        OCR_MODEL,
        "--host",
        "127.0.0.1",
        "--port",
        str(config.vllm_port),
        "--trust-remote-code",
        "--gpu-memory-utilization",
        str(config.gpu_memory_utilization),
        "--max-model-len",
        str(config.max_model_len),
        "--max-num-seqs",
        str(config.max_num_seqs),
        "--max-num-batched-tokens",
        str(config.max_num_batched_tokens),
        "--limit-mm-per-prompt",
        '{"image": 1}',
        "--mm-processor-cache-gb",
        "0",
        "--api-server-count",
        str(config.api_server_count),
        "--uvicorn-log-level",
        "warning",
    ]
    # OCR crops are unique, so prefix caching only wastes CPU on hashing (~0.2%
    # hit rate); always off. Async scheduling removes inter-step GPU idle gaps and
    # is always wanted for this throughput-bound workload.
    command.append("--no-enable-prefix-caching")
    command.append("--async-scheduling")
    # GLM MTP speculative decoding is deliberately disabled: with it enabled the
    # vLLM EngineCore process dies on real multimodal requests (a native abort
    # with no Python traceback), which takes the whole server down. Re-enable
    # only after confirming the underlying vLLM bug is resolved.
    # if config.speculative_config:
    #     command += ["--speculative-config", config.speculative_config]
    if config.vllm_extra_args:
        command += shlex.split(config.vllm_extra_args)
    return command


def start_vllm_server(config: EngineConfig) -> subprocess.Popen[bytes]:
    """Launch the vLLM server as a subprocess and return the process handle."""
    command = build_serve_command(config)
    logger.info("starting_vllm_server command=%s", " ".join(command))
    os.makedirs(os.path.dirname(VLLM_SERVER_LOG), exist_ok=True)
    log_handle = open(VLLM_SERVER_LOG, "a", buffering=1, encoding="utf-8", errors="replace")
    return subprocess.Popen(command, stdout=log_handle, stderr=log_handle)


def wait_for_health(process: subprocess.Popen[bytes], *, port: int, timeout: float) -> None:
    """Block until the vLLM server reports healthy or the timeout elapses.

    Raises:
        RuntimeError: If the server exits early or fails to become healthy.
    """
    health_url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            tail = _server_log_tail()
            logger.warning("vllm_server_exited code=%s log_tail=%s", exit_code, tail)
            raise RuntimeError(f"vLLM server exited during startup with code {exit_code}: {tail}")
        try:
            with urllib.request.urlopen(health_url, timeout=5) as response:
                if 200 <= response.status < 300:
                    logger.info("vllm_server_healthy port=%d", port)
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            pass
        time.sleep(2.0)
    tail = _server_log_tail()
    logger.warning("vllm_server_health_timeout log_tail=%s", tail)
    raise RuntimeError(f"vLLM server did not become healthy within {timeout:.0f}s: {tail}")


def stop_vllm_server(process: subprocess.Popen[bytes] | None) -> None:
    """Terminate the vLLM server subprocess if it is running."""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
