import asyncio

import pytest


def test_financial_v2_dedupes_and_does_not_activate_without_extraction(tmp_path, monkeypatch):
    async def _test():
        pytest.importorskip("aiosqlite")
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from sag_api.db.base import Base
        from sag_api.schemas.v2 import DocumentCreateIn
        from sag_api.services import financial_v2_service as service

        monkeypatch.setattr(service.settings, "upload_dir", str(tmp_path / "uploads"))
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        body = DocumentCreateIn(
            title="HPG Q1",
            markdown="# HPG\n## Thuyet minh\nNoi dung",
            doc_role="LATEST_QUARTER",
            fiscal_year=2026,
            fiscal_quarter=1,
            period_end="2026-03-31",
        )
        async with session_factory() as session:
            first, first_dedup = await service.create_financial_document(session, "HPG", body)
            second, second_dedup = await service.create_financial_document(session, "HPG", body)

            assert first.id == second.id
            assert first_dedup is False
            assert second_dedup is True
            assert first.structure_status == "COMPLETE"
            assert first.embedding_status == "INCOMPLETE"
            assert first.extraction_status == "INCOMPLETE"
            assert first.is_active is False

        await engine.dispose()

    asyncio.run(_test())


def test_extraction_manifest_rejects_runtime_taxonomy_values():
    from pydantic import ValidationError

    from sag_api.services.extraction_v2_service import ExtractionManifestIn

    with pytest.raises(ValidationError):
        ExtractionManifestIn.model_validate(
            {
                "taxonomy_version": "financial-evidence-taxonomy-v2",
                "node_annotations": [{"node_id": "n1", "relevance": "NONE"}],
                "entity_mentions": [
                    {
                        "raw_text": "Công ty X",
                        "canonical_name": "Công ty X",
                        "entity_type": "steel_distributor",
                        "evidence": {"node_id": "n1", "quote": "Công ty X"},
                    }
                ],
            }
        )


def test_extraction_manifest_requires_all_node_annotations():
    from sag_api.db.models import DocumentTreeNode
    from sag_api.services.extraction_v2_service import ExtractionManifestIn, validate_and_persist_manifest

    async def _test():
        manifest = ExtractionManifestIn.model_validate(
            {
                "taxonomy_version": "financial-evidence-taxonomy-v2",
                "node_annotations": [{"node_id": "n1", "relevance": "NONE"}],
            }
        )
        nodes = [
            DocumentTreeNode(document_id="d1", node_id="n1", start_line=1, end_line=1),
            DocumentTreeNode(document_id="d1", node_id="n2", start_line=2, end_line=2),
        ]

        with pytest.raises(ValueError, match="thiếu node_id"):
            await validate_and_persist_manifest(None, None, None, "a\nb\n", nodes, manifest)  # type: ignore[arg-type]

    asyncio.run(_test())


def test_extraction_quote_must_be_unique_inside_node():
    from sag_api.db.models import DocumentTreeNode
    from sag_api.services.extraction_v2_service import _locate_quote

    node = DocumentTreeNode(document_id="d1", node_id="n1", start_line=1, end_line=3)
    with pytest.raises(ValueError, match="xuất hiện duy nhất"):
        _locate_quote("abc\nabc\nxyz\n", node, "abc")


def test_extraction_quote_line_hint_disambiguates_repeated_quote():
    from sag_api.db.models import DocumentTreeNode
    from sag_api.services.extraction_v2_service import _locate_quote

    node = DocumentTreeNode(document_id="d1", node_id="n1", start_line=10, end_line=12)

    location = _locate_quote("ignore\n" * 9 + "abc\nabc\nxyz\n", node, "abc", line_hint=11)

    assert location["start_line"] == 11
    assert location["end_line"] == 11


def test_extraction_quote_fuzzy_line_match_hydrates_markdown_line():
    from sag_api.db.models import DocumentTreeNode
    from sag_api.services.extraction_v2_service import _locate_quote

    markdown = "\n".join(
        [
            "# Root",
            "| Chỉ tiêu | 30/06/2026 |",
            "| --- | ---: |",
            "| Tại ngày 30/06/2026 | 84.429.645.200.000 | 14.376.628.220.213 | 1.388.437.800.829 |",
        ]
    )
    node = DocumentTreeNode(document_id="d1", node_id="n1", start_line=1, end_line=4)

    location = _locate_quote(
        markdown,
        node,
        "Tai ngày 30/06/2026 | 84.429.645.200.000 | 14.376.628.220.213 | 1.388.437.800.829",
    )

    assert location["start_line"] == 4
    assert location["resolution"] == "unique_line_fuzzy"
    assert location["resolved_quote"].startswith("| Tại ngày")


def test_read_markdown_from_local_object_uri_accepts_raw_or_canonical_hash(tmp_path):
    import hashlib

    from sag_api.schemas.v2 import DocumentCreateIn
    from sag_api.services.financial_v2_service import _read_markdown_from_body

    async def _test():
        source = tmp_path / "doc.md"
        raw = b"\xef\xbb\xbf# HPG\r\nNoi dung"
        source.write_bytes(raw)
        raw_hash = hashlib.sha256(raw).hexdigest()
        body = DocumentCreateIn(
            title="HPG annual",
            object_uri=str(source),
            content_sha256=raw_hash,
            doc_role="ANNUAL_BACKBONE",
            fiscal_year=2026,
            period_end="2026-12-31",
        )

        canonical, returned_raw_hash, canonical_hash, size_bytes, uri = await _read_markdown_from_body(body)

        assert canonical == "# HPG\nNoi dung\n"
        assert returned_raw_hash == raw_hash
        assert canonical_hash == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert size_bytes == len(canonical.encode("utf-8"))
        assert uri == str(source)

    asyncio.run(_test())


def test_pdf_upload_requires_mineru_configuration(monkeypatch):
    from sag_api.services.financial_v2_service import parse_pdf_bytes_to_markdown
    from sag_api.core.errors import ConfigurationError
    from sag_api.core.config import settings

    async def _test():
        monkeypatch.setattr(settings, "mineru_api_key", None)
        with pytest.raises(ConfigurationError):
            await parse_pdf_bytes_to_markdown(b"%PDF-1.7\n", "sample.pdf")

    asyncio.run(_test())


def test_financial_v2_role_conflict_on_same_hash(tmp_path, monkeypatch):
    async def _test():
        pytest.importorskip("aiosqlite")
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from sag_api.core.errors import ConflictError
        from sag_api.db.base import Base
        from sag_api.schemas.v2 import DocumentCreateIn
        from sag_api.services import financial_v2_service as service

        monkeypatch.setattr(service.settings, "upload_dir", str(tmp_path / "uploads"))
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        markdown = "# HPG\n## Thuyet minh\nNoi dung"
        async with session_factory() as session:
            await service.create_financial_document(
                session,
                "HPG",
                DocumentCreateIn(
                    title="HPG Q1",
                    markdown=markdown,
                    doc_role="LATEST_QUARTER",
                    fiscal_year=2026,
                    fiscal_quarter=1,
                    period_end="2026-03-31",
                ),
            )
            with pytest.raises(ConflictError):
                await service.create_financial_document(
                    session,
                    "HPG",
                    DocumentCreateIn(
                        title="HPG Annual",
                        markdown=markdown,
                        doc_role="ANNUAL_BACKBONE",
                        fiscal_year=2026,
                        period_end="2026-12-31",
                    ),
                )

        await engine.dispose()

    asyncio.run(_test())


def test_financial_v2_assessments_are_insufficient_without_active_complete_documents(tmp_path, monkeypatch):
    async def _test():
        pytest.importorskip("aiosqlite")
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from sag_api.db.base import Base
        from sag_api.schemas.v2 import DocumentCreateIn
        from sag_api.services import financial_v2_service as service

        monkeypatch.setattr(service.settings, "upload_dir", str(tmp_path / "uploads"))
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            await service.create_financial_document(
                session,
                "HPG",
                DocumentCreateIn(
                    title="HPG Q1",
                    markdown="# HPG\n## Thuyet minh\nNoi dung",
                    doc_role="LATEST_QUARTER",
                    fiscal_year=2026,
                    fiscal_quarter=1,
                    period_end="2026-03-31",
                ),
            )
            moat = await service.assess_moat(session, "HPG")
            gil = await service.assess_gil(session, "HPG")

            assert moat["assessment_status"] == "INSUFFICIENT"
            assert moat["moat_score"] is None
            assert gil["analysis_status"] == "DATA_INSUFFICIENT"
            assert gil["gil_flag"] == "DATA_INSUFFICIENT"

        await engine.dispose()

    asyncio.run(_test())


def test_financial_v2_prod_create_enqueues_without_inline_processing(tmp_path, monkeypatch):
    async def _test():
        pytest.importorskip("aiosqlite")
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from sag_api.db.base import Base
        from sag_api.db.models import ProcessingRun
        from sag_api.schemas.v2 import DocumentCreateIn
        from sag_api.services import financial_v2_service as service

        monkeypatch.setattr(service.settings, "upload_dir", str(tmp_path / "uploads"))
        monkeypatch.setattr(service.settings, "environment", "prod")
        monkeypatch.setattr(service.settings, "process_documents_inline", True)
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            document, dedup = await service.create_financial_document(
                session,
                "HPG",
                DocumentCreateIn(
                    title="HPG Q1",
                    markdown="# HPG\n## Thuyet minh\nNoi dung",
                    doc_role="LATEST_QUARTER",
                    fiscal_year=2026,
                    fiscal_quarter=1,
                    period_end="2026-03-31",
                ),
            )
            runs = (await session.execute(select(ProcessingRun).where(ProcessingRun.document_id == document.id))).scalars().all()

            assert dedup is False
            assert document.status.value == "QUEUED"
            assert document.structure_status == "PENDING"
            assert [run.stage for run in runs] == ["document"]

        await engine.dispose()

    asyncio.run(_test())


def test_embedding_chunk_has_vector_and_json_fallback():
    from sag_api.db.models import EmbeddingChunk

    chunk = EmbeddingChunk(
        document_id="doc",
        node_id="node",
        chunk_id="chunk",
        start_line=1,
        end_line=1,
        content_hash="c",
        embedding_text_hash="e",
        embedding_vector=[0.1, 0.2],
        embedding_json=[0.1, 0.2],
    )

    assert chunk.embedding_vector == [0.1, 0.2]
    assert chunk.embedding_json == [0.1, 0.2]


def test_hydrate_markdown_falls_back_to_object_uri(tmp_path, monkeypatch):
    async def _test():
        from sag_api.db.models import Document, DocumentAsset
        from sag_api.services import financial_v2_service as service

        source = tmp_path / "canonical.md"
        source.write_bytes(b"# HPG\r\nNoi dung")
        canonical = "# HPG\nNoi dung\n"
        content_hash = service._hash_bytes(canonical.encode("utf-8"))
        monkeypatch.setattr(service.settings, "upload_dir", str(tmp_path / "uploads"))

        asset = DocumentAsset(
            object_uri=str(source),
            content_sha256=content_hash,
            canonical_markdown_sha256=content_hash,
            metadata_json={"local_cache_path": str(tmp_path / "missing.md")},
        )
        doc = Document(
            issuer_id="issuer",
            filename="HPG",
            storage_path=str(tmp_path / "missing.md"),
            doc_role="LATEST_QUARTER",
            content_sha256=content_hash,
        )

        hydrated = await service.hydrate_markdown(doc, asset)

        assert hydrated == canonical
        assert "local_cache_path" in asset.metadata_json
        assert doc.storage_path.endswith(".md")

    asyncio.run(_test())
