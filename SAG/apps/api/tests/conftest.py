"""Keep offline unit tests hermetic against local untracked `.env` files.

`litellm` invokes `dotenv.load_dotenv()` at import time, which otherwise leaks
developer-local `apps/api/.env` keys into `os.environ` for subsequent tests.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_sag_env_from_third_party_dotenv():
    before = {k: v for k, v in os.environ.items() if k.startswith("SAG_")}
    yield
    for key in list(os.environ):
        if key.startswith("SAG_") and key not in before:
            os.environ.pop(key, None)
    for key, value in before.items():
        os.environ[key] = value
