"""
Verify LLM Model IDs — Kiểm tra tất cả model ID đang dùng có hoạt động không.

Usage:
    python scripts/verify_llm_models.py
    python scripts/verify_llm_models.py --verbose

Exit code 0: tất cả OK
Exit code 1: có model failed
"""
import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class ModelCheckResult:
    provider: str
    model_id: str
    status: str  # "OK" | "FAIL" | "SKIP"
    status_code: Optional[int] = None
    error: str = ""


VERIFIED_MODEL_IDS = {
    "groq_0": "llama-3.3-70b-versatile",
    "groq_1": "qwen/qwen3-32b",
    "nvidia": "minimaxai/minimax-m2.7",
}

CONFIG_MODEL_MAP = {
    "groq0": ("groq", "llama-3.3-70b-versatile"),
    "groq1": ("groq", "qwen/qwen3-32b"),
    "nvidia": ("nvidia", "minimaxai/minimax-m2.7"),
}


def check_groq_model(api_key: str, model_id: str) -> ModelCheckResult:
    """Check Groq model availability via chat completion endpoint."""
    import httpx

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": 5,
    }
    try:
        with httpx.Client(timeout=15) as client:
            r = client.post(url, json=payload, headers=headers)
            return ModelCheckResult(
                provider="Groq",
                model_id=model_id,
                status="OK" if r.status_code == 200 else "FAIL",
                status_code=r.status_code,
                error="" if r.status_code == 200 else r.text[:200],
            )
    except Exception as e:
        return ModelCheckResult(
            provider="Groq",
            model_id=model_id,
            status="FAIL",
            error=str(e),
        )


def check_nvidia_model(api_key: str, model_id: str) -> ModelCheckResult:
    """Check NVIDIA model availability."""
    import httpx

    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": 5,
    }
    try:
        with httpx.Client(timeout=15) as client:
            r = client.post(url, json=payload, headers=headers)
            return ModelCheckResult(
                provider="NVIDIA",
                model_id=model_id,
                status="OK" if r.status_code == 200 else "FAIL",
                status_code=r.status_code,
                error="" if r.status_code == 200 else r.text[:200],
            )
    except Exception as e:
        return ModelCheckResult(
            provider="NVIDIA",
            model_id=model_id,
            status="FAIL",
            error=str(e),
        )


def main():
    parser = argparse.ArgumentParser(description="Verify LLM model endpoints")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output")
    args = parser.parse_args()

    groq_api_key = os.getenv("GROQ_API_KEY", "")
    nvidia_api_key = os.getenv("NVIDIA_API_KEY", "")

    results: list[ModelCheckResult] = []

    if groq_api_key:
        results.append(check_groq_model(groq_api_key, VERIFIED_MODEL_IDS["groq_0"]))
        results.append(check_groq_model(groq_api_key, VERIFIED_MODEL_IDS["groq_1"]))
    else:
        results.append(ModelCheckResult("Groq", VERIFIED_MODEL_IDS["groq_0"], "SKIP", error="No GROQ_API_KEY"))
        results.append(ModelCheckResult("Groq", VERIFIED_MODEL_IDS["groq_1"], "SKIP", error="No GROQ_API_KEY"))

    if nvidia_api_key:
        results.append(check_nvidia_model(nvidia_api_key, VERIFIED_MODEL_IDS["nvidia"]))
    else:
        results.append(ModelCheckResult("NVIDIA", VERIFIED_MODEL_IDS["nvidia"], "SKIP", error="No NVIDIA_API_KEY"))

    all_ok = True
    print(f"\n{'Model ID Verification':^60}")
    print(f"{'─' * 60}")
    print(f"{'Provider':<12} {'Model ID':<32} {'Status':<8} {'Detail'}")
    print(f"{'─' * 60}")

    for r in results:
        detail = r.error if r.status == "FAIL" else (str(r.status_code) if r.status_code else "")
        print(f"{r.provider:<12} {r.model_id:<32} {r.status:<8} {detail[:40]}")
        if r.status == "FAIL":
            all_ok = False

    print(f"{'─' * 60}")
    passed = sum(1 for r in results if r.status == "OK")
    skipped = sum(1 for r in results if r.status == "SKIP")
    failed = sum(1 for r in results if r.status == "FAIL")
    print(f"Passed: {passed} | Skipped: {skipped} | Failed: {failed}")

    if args.verbose:
        for r in results:
            if r.status == "FAIL":
                print(f"\nError details for {r.provider}/{r.model_id}:")
                print(f"  {r.error}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
