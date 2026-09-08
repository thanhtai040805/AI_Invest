from types import SimpleNamespace
from uuid import uuid4

import pytest

from sag_api.enums import DocumentStatus
from sag_api.jobs.inproc import _mark_document_waiting_retry


@pytest.mark.asyncio
async def test_retry_marks_document_pending_without_resetting_checkpoint_metrics():
    document = SimpleNamespace(
        status=DocumentStatus.FAILED,
        error="upstream timeout",
        progress=64,
        chunk_count=12,
        event_count=7,
        token_usage=9_000,
        sag_source_id="derived-source",
    )

    class FakeSession:
        async def get(self, _model, document_id):
            assert document_id == "document-1"
            return document

    job = SimpleNamespace(document_id="document-1")

    await _mark_document_waiting_retry(FakeSession(), job)

    assert document.status == DocumentStatus.PENDING
    assert document.error is None
    assert document.progress == 64
    assert document.chunk_count == 12
    assert document.event_count == 7
    assert document.token_usage == 9_000
    assert document.sag_source_id == "derived-source"


@pytest.mark.asyncio
async def test_non_document_retry_does_not_query_for_a_document():
    class FakeSession:
        async def get(self, _model, _document_id):
            raise AssertionError("a non-document job must not load a document")

    await _mark_document_waiting_retry(FakeSession(), SimpleNamespace(document_id=None))
