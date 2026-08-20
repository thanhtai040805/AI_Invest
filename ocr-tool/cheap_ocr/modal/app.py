# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
"""Modal app, image, and volumes for the cheap-ocr launcher."""

import os
from pathlib import Path

import modal

from cheap_ocr.config import EngineConfig, ModalConfig

PACKAGE_DIR = Path(__file__).resolve().parents[1]  # .../cheap_ocr
REMOTE_PACKAGE_ROOT = "/root/app/cheap_ocr"

FINANCIAL_DIR = PACKAGE_DIR.parent / "financial_pipeline"
PROFILE_FILE = PACKAGE_DIR.parent / "financial_profile.yaml"

MODAL = ModalConfig()
CPU_CORES: float | tuple[float, float] = MODAL.cpu
MODAL_SECRETS = [modal.Secret.from_name(name) for name in MODAL.secret_names]

# Shared volumes
hf_cache_vol = modal.Volume.from_name("huggingface-cache", create_if_missing=True)
vllm_cache_vol = modal.Volume.from_name("vllm-cache", create_if_missing=True)

PACKAGES = [
    "vllm==0.21.0",
    "transformers>=5.3.0",
    "accelerate>=1.13.0",
    "sentencepiece>=0.2.0",
    "pillow>=10.0.0",
    "numpy>=1.26.0",
    "opencv-python-headless>=4.10.0",
    "pypdfium2>=4.30.0",
    "aiohttp>=3.13.0",
    "huggingface-hub>=0.27.0",
    "onnxruntime-gpu>=1.20.0",
    "tensorrt>=10.0.0,<11",
    "onnx>=1.17.0",
    "fsspec>=2026.6.0",
    "adlfs>=2026.5.0",
    "azure-identity>=1.25.3",
    "azure-storage-blob>=12.30.0",
    "s3fs>=2026.6.0",
    "gcsfs>=2026.6.0",
    "pyyaml>=6.0",
]
STORAGE_PACKAGES = [
    "aiohttp>=3.13.0",
    "pillow>=10.0.0",
    "pypdfium2>=4.30.0",
    "fsspec>=2026.6.0",
    "adlfs>=2026.5.0",
    "azure-identity>=1.25.3",
    "azure-storage-blob>=12.30.0",
    "s3fs>=2026.6.0",
    "gcsfs>=2026.6.0",
]

_COMMON_ENV = {
    "PYTHONPATH": "/root/app",
    **MODAL.to_env(),
}
_RUNTIME_ENV = {
    **EngineConfig().to_env(),
    "VLLM_MEDIA_LOADING_THREAD_COUNT": os.environ.get("VLLM_MEDIA_LOADING_THREAD_COUNT", "8"),
}

image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .apt_install("libgl1", "libglib2.0-0")
    .uv_pip_install(*PACKAGES)
    .run_commands(
        "for d in /usr/local/lib/python3.12/site-packages/nvidia/*/lib "
        '/usr/local/lib/python3.12/site-packages/tensorrt_libs; do echo "$d"; done '
        "> /etc/ld.so.conf.d/onnx-trt.conf && ldconfig"
    )
    .env(
        {
            **_COMMON_ENV,
            "HF_XET_HIGH_PERFORMANCE": "1",
            "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
            **_RUNTIME_ENV,
        }
    )
    .add_local_dir(str(PACKAGE_DIR), remote_path=REMOTE_PACKAGE_ROOT)
    .add_local_dir(str(FINANCIAL_DIR), remote_path="/root/app/financial_pipeline")
    .add_local_file(str(PROFILE_FILE), remote_path="/root/app/financial_profile.yaml")
)

storage_image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install(*STORAGE_PACKAGES)
    .env(_COMMON_ENV)
    .add_local_dir(str(PACKAGE_DIR), remote_path=REMOTE_PACKAGE_ROOT)
)

app = modal.App(MODAL.app_name)
