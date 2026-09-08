"""测试夹具：在导入 sag_api 之前把配置指向临时目录（settings 为进程级单例）。"""

import os
import sys
import asyncio
import tempfile

import pytest


def _neutralize_dotenv_injection() -> None:
    """Chặn dotenv.load_dotenv() bơm .env thật của máy dev vào os.environ.

    litellm gọi load_dotenv() lúc import (kể cả import gián tiếp qua sag_api),
    nạp mọi SAG_* chưa tồn tại từ .env thật (vd SAG_AGENT_LLM_MODEL=GLM,
    SAG_LLM_TEMPERATURE=0.2) và làm unit test phụ thuộc máy chạy.
    Chặn tại conftest (chạy trước mọi import litellm). pydantic-settings đọc
    file .env bằng dotenv_values nên Settings runtime không bị ảnh hưởng.
    """
    try:
        import dotenv as _dotenv
    except ImportError:  # pragma: no cover - dotenv luôn có trong môi trường test
        return

    def _disabled_load_dotenv(*args: object, **kwargs: object) -> bool:
        return False

    _dotenv.load_dotenv = _disabled_load_dotenv  # type: ignore[method-assign]


_neutralize_dotenv_injection()

_TMP = tempfile.mkdtemp(prefix="sag-test-")
os.environ.setdefault("SAG_DATABASE_URL", f"sqlite+aiosqlite:///{_TMP}/sag.db")
os.environ.setdefault("SAG_ALLOW_SQLITE_RUNTIME", "true")
os.environ.setdefault("SAG_DATA_DIR", f"{_TMP}/sag")
os.environ.setdefault("SAG_UPLOAD_DIR", f"{_TMP}/uploads")
os.environ.setdefault("SAG_DEBUG", "false")
os.environ.setdefault("SAG_SAG_LANGUAGE", "zh")
os.environ["SAG_SAG_VECTOR_PROVIDER"] = "pgvector"
os.environ["SAG_SAG_RELATIONAL_PROVIDER"] = "postgres"
# 强制离线：即使存在带真实 key 的 .env，也保证测试确定性（不发起 LLM 调用）
os.environ["SAG_LLM_API_KEY"] = ""
os.environ["SAG_LLM_BASE_URL"] = ""
# llm_model không còn default trong code — test phải khai báo tường minh.
os.environ["SAG_LLM_MODEL"] = "openai/test-model"
# import litellm (trực tiếp hay gián tiếp qua sag_api) gọi load_dotenv() lúc import,
# bơm toàn bộ .env thật vào os.environ cho các var chưa tồn tại. Khóa tường minh
# các override model riêng để unit test độc lập với .env của máy dev
# (trước đây SAG_AGENT_LLM_MODEL=GLM rò rỉ làm 6 test route sai model).
os.environ["SAG_AGENT_LLM_MODEL"] = ""
os.environ["SAG_AGENT_LLM_BASE_URL"] = ""
os.environ["SAG_AGENT_LLM_API_KEY"] = ""
os.environ["SAG_EXTRACTION_LLM_MODEL"] = ""
os.environ["SAG_EXTRACTION_LLM_BASE_URL"] = ""
os.environ["SAG_EXTRACTION_LLM_API_KEY"] = ""
os.environ["SAG_EMBEDDING_API_KEY"] = ""
os.environ["SAG_MINERU_API_KEY"] = ""
os.environ["SAG_MINERU_BASE_URL"] = ""


@pytest.fixture(autouse=True)
def _isolate_persisted_jobs():
    """A test must not recover queued jobs created by an earlier app lifespan."""
    yield
    if "sag_api.core.db" not in sys.modules:
        return
    asyncio.run(_cleanup_persisted_jobs())


async def _cleanup_persisted_jobs():
    if "sag_api.core.db" not in sys.modules:
        return

    from sqlalchemy import delete, inspect

    from sag_api.core.db import SessionLocal, engine
    from sag_api.db.models import Job

    async with engine.connect() as connection:
        exists = await connection.run_sync(lambda sync: inspect(sync).has_table(Job.__tablename__))
    if not exists:
        return

    async with SessionLocal() as session:
        await session.execute(delete(Job))
        await session.commit()
