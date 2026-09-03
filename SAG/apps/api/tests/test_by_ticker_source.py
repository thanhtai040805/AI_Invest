import asyncio
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sag_api.db.base import Base
from sag_api.services.source_service import get_or_create_source_by_ticker
from sag_api.services.document_service import ingest_content
from sag_api.jobs import JobQueue


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_get_or_create_source_by_ticker(tmp_path):
    """Kiểm tra tự động tìm hoặc tạo Nguồn tri thức theo mã cổ phiếu."""
    test_db = tmp_path / "test_sources.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{test_db}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        # Lần 1: Chưa có -> tạo mới
        source1 = await get_or_create_source_by_ticker(session, "HPG")
        assert source1.name == "BCTC_HPG"
        assert source1.id is not None
        id1 = source1.id

        # Lần 2: Đã có -> lấy lại nguồn cũ (id trùng nhau)
        source2 = await get_or_create_source_by_ticker(session, "HPG")
        assert source2.id == id1

        class DummyJobQueue:
            async def enqueue(self, job_id: str) -> None:
                pass

        job_queue = DummyJobQueue()
        upload_dir = str(tmp_path / "uploads")

        doc = await ingest_content(
            session=session,
            source=source2,
            text="# BCTC Hoa Phat Q1/2026",
            title="BCTC Q1/2026",
            upload_dir=upload_dir,
            job_queue=job_queue,
            doc_role="LATEST_QUARTER",
            is_active=True,
            fiscal_year=2026,
            fiscal_quarter=1,
        )

        assert doc.doc_role == "LATEST_QUARTER"
        assert doc.is_active is True
        assert doc.fiscal_year == 2026
        assert doc.fiscal_quarter == 1

    await engine.dispose()
