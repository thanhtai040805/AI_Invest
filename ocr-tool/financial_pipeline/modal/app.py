# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
"""Modal images and app for the financial-ocr orchestrator.

The cheap-ocr app owns the GPU OCR workers; this app owns the CPU side of the
financial pipeline:

* ``classify_one`` — download + page-classify/prune one BCTC PDF on cheap Modal
  CPU, so the local machine never downloads or touches PDFs in a batch.
* ``BatchSupervisor`` — a preemptible CPU orchestrator that runs the whole
  batch: classify in parallel, group into GPU batches, farm them to the
  already-deployed cheap-ocr ``GpuWorker`` (cross-app), apply the region filter,
  and checkpoint completed documents on a shared volume for resumability.

The classifier image carries ``tesseract-ocr`` + the ``vie`` language pack for
the hybrid-PDF Tesseract fallback, plus PyMuPDF for pruning.
"""

from pathlib import Path

import modal

FINANCIAL_PACKAGE_DIR = Path(__file__).resolve().parents[1]  # .../financial_pipeline
REPO_ROOT = FINANCIAL_PACKAGE_DIR.parent  # .../TestDNSE
CHEAP_PACKAGE_DIR = REPO_ROOT / "cheap_ocr"
PROFILE_PATH = REPO_ROOT / "financial_profile.yaml"

REMOTE_ROOT = "/root/app"
OUTPUT_VOLUME_PATH = "/root/financial_outputs"

_CLASSIFIER_PACKAGES = [
    "pymupdf>=1.24.0",
    "pytesseract>=0.3.10",
    "pillow>=10.0.0",
    "pyyaml>=6.0.0",
    "aiohttp>=3.13.0",
]

classifier_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("tesseract-ocr", "tesseract-ocr-vie")
    .uv_pip_install(*_CLASSIFIER_PACKAGES)
    .env({"PYTHONPATH": REMOTE_ROOT})
    .add_local_dir(str(FINANCIAL_PACKAGE_DIR), remote_path=f"{REMOTE_ROOT}/financial_pipeline")
    .add_local_dir(str(CHEAP_PACKAGE_DIR), remote_path=f"{REMOTE_ROOT}/cheap_ocr")
    .add_local_file(str(PROFILE_PATH), remote_path=f"{REMOTE_ROOT}/financial_profile.yaml")
)

# Shared checkpoint volume so a re-invoked supervisor skips completed docs
# instead of re-paying GPU time (unless force=True).
outputs_volume = modal.Volume.from_name("financial-ocr-outputs", create_if_missing=True)

app = modal.App("financial-ocr")


def default_ocr_config() -> dict:
    """Standard financial-pipeline OcrConfig overrides (cheap + accurate)."""
    return {"force": True, "pdf_dpi": 200}


def default_batch_policy() -> dict:
    """Batch grouping for ~1k-page BCTCs: bound docs/bytes/pages per GPU call.

    A batch is one ``GpuWorker.process_batch`` call on one warm GPU container;
    smaller batches keep retry granularity reasonable while amortizing the
    container's vLLM warm-up across many documents.
    """
    return {"max_docs": 16, "max_bytes_mb": 512, "max_pages": 1200}
