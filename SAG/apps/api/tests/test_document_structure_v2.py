import pytest

from sag_api.services.document_structure_service import (
    ExtractionReference,
    _node_line_ranges,
    _reference_node,
    hydrate_node_content,
    parse_markdown_tree,
    sha256_text,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_parse_markdown_tree_covers_lines_and_filters_repeated_ocr_heading():
    markdown = "\n".join(
        [
            "# HPG Q2 2026",
            "HỒY PHẠT HỒA HỢP CỦNG PHẠT TRIỂN",
            "HỒY PHẠT HỒA HỢP CỦNG PHẠT TRIỂN",
            "## I. Thuyết minh",
            "1. Tiền và tương đương tiền",
            "| Chỉ tiêu | 30/06/2026 |",
            "| --- | ---: |",
            "| Tiền | 1.000.000.000 VND |",
            "2. Vốn chủ sở hữu",
            "Vốn chủ sở hữu 100.000.000.000 VND",
        ]
    )

    nodes, coverage = parse_markdown_tree(markdown, document_id="doc", source_id="src")

    assert coverage["coverage_ratio"] == 1.0
    assert coverage["total_lines"] == 10
    assert not any("HỒY PHẠT" in node.heading for node in nodes)
    money_node = next(node for node in nodes if "Tiền" in node.heading)
    assert money_node.start_line == 5
    assert money_node.end_line == 8


def test_hydrate_node_content_uses_exact_line_span():
    markdown = "a\nb\nc\nd"
    node = type("Node", (), {"start_line": 2, "end_line": 3})()

    assert hydrate_node_content(markdown, node) == "b\nc"
    assert sha256_text("b\nc")


def test_evidence_ranges_do_not_cross_node_and_split_table_rows():
    markdown = "\n".join(
        [
            "# Root",
            "## Note",
            "intro",
            "| C | V |",
            "| --- | ---: |",
            "| A | 1 |",
            "| B | 2 |",
            "tail",
        ]
    )
    nodes, _coverage = parse_markdown_tree(markdown, document_id="doc", source_id="src")
    note = next(node for node in nodes if node.heading == "Note")
    chunks = _node_line_ranges(markdown, note)

    assert chunks
    assert all(note.start_line <= chunk["start_line"] <= chunk["end_line"] <= note.end_line for chunk in chunks)
    table_rows = [chunk for chunk in chunks if chunk["kind"] == "table_row"]
    assert [(chunk["start_line"], chunk["end_line"]) for chunk in table_rows] == [(6, 6), (7, 7)]
    assert all(chunk["table_header_hash"] for chunk in table_rows)


def test_reference_validation_rejects_span_outside_node():
    markdown = "# Root\n## Note\ninside\n## Other\noutside"
    nodes, _coverage = parse_markdown_tree(markdown, document_id="doc", source_id="src")
    note = next(node for node in nodes if node.heading == "Note")

    with pytest.raises(ValueError):
        _reference_node(
            ExtractionReference(node_id=note.node_id, start_line=note.start_line, end_line=note.end_line + 1),
            {node.node_id: node for node in nodes},
        )


@pytest.mark.anyio
async def test_create_document_deduplicates_same_source_role_hash(tmp_path):
    pytest.importorskip("aiosqlite")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from sag_api.db.base import Base
    from sag_api.db.models import Document
    from sag_api.services.document_service import create_document_from_upload
    from sag_api.services.source_service import get_or_create_source_by_ticker

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    class DummyJobQueue:
        def __init__(self):
            self.enqueued = []

        async def enqueue(self, job_id: str) -> None:
            self.enqueued.append(job_id)

    async with session_factory() as session:
        source = await get_or_create_source_by_ticker(session, "HPG")
        queue = DummyJobQueue()
        first, first_job = await create_document_from_upload(
            session,
            source,
            filename="hpg.md",
            content_type="text/markdown",
            data=b"# HPG",
            upload_dir=str(tmp_path / "uploads"),
            job_queue=queue,
            doc_role="LATEST_QUARTER",
        )
        second, second_job = await create_document_from_upload(
            session,
            source,
            filename="hpg-copy.md",
            content_type="text/markdown",
            data=b"# HPG",
            upload_dir=str(tmp_path / "uploads"),
            job_queue=queue,
            doc_role="LATEST_QUARTER",
        )

        assert first.id == second.id
        assert first_job is not None
        assert second_job is None
        assert len(queue.enqueued) == 1
        docs = (await session.execute(Document.__table__.select())).all()
        assert len(docs) == 1

    await engine.dispose()
