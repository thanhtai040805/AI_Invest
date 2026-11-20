"""Dịch vụ lưu trữ và quản lý khối văn bản chuẩn (Canonical Block Persistence Service).

Triết lý Ponytail: Tái sử dụng model CanonicalBlock và StageRun đã có sẵn trong
sag_api.db.models.routing_rag, lưu trữ giao dịch nguyên tử (transactional staging),
hỗ trợ retry an toàn tuyệt đối.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.error_taxonomy import ErrorLayer, ErrorStage
from sag_api.core.errors import ApiError
from sag_api.core.logging import get_logger
from sag_api.db.models.routing_rag import CanonicalBlock, StageRun
from sag_api.parsing.canonical import (
    ExtractedBlock,
    extract_canonical_blocks,
    generate_canonical_block_id,
)

log = get_logger("canonical_service")


async def persist_canonical_blocks(
    session: AsyncSession,
    document_version_id: str,
    blocks: Sequence[ExtractedBlock],
    *,
    run_id: str | None = None,
) -> list[CanonicalBlock]:
    """Ghi nhận danh sách CanonicalBlock vào cơ sở dữ liệu có transactional staging.
    
    Nếu phiên bản đã có blocks từ trước (trường hợp retry), xóa bỏ sạch sẽ trước khi nạp mới.
    Ghi nhận tiến trình StageRun (stage='PARSE').
    """
    start_time = time.monotonic()

    # 1. Dọn dẹp bản ghi cũ của version này nếu đây là lần retry (Idempotent cleanup)
    # Xóa SearchUnit trước vì SearchUnit có FK trỏ tới CanonicalBlock không cascade
    from sag_api.db.models import DocumentVersion
    from sag_api.db.models.routing_rag import SearchUnit

    # Invalidate before rewriting artifacts; knowledge's short final fence sees one committed revision.
    # Preserve historical evidence without taking enrichment locks in the search lane.
    await session.execute(
        update(DocumentVersion)
        .where(DocumentVersion.id == document_version_id)
        .values(knowledge_status="NOT_STARTED")
    )
    await session.execute(
        delete(SearchUnit).where(SearchUnit.document_version_id == document_version_id)
    )
    await session.execute(
        delete(CanonicalBlock).where(CanonicalBlock.document_version_id == document_version_id)
    )

    # 2. Tạo các thực thể CanonicalBlock với stable UUIDv5
    db_blocks: list[CanonicalBlock] = []
    for b in blocks:
        block_id = generate_canonical_block_id(document_version_id, b.ordinal, b.content_hash)
        db_block = CanonicalBlock(
            id=block_id,
            document_version_id=document_version_id,
            ordinal=b.ordinal,
            block_type=b.block_type,
            page_from=b.page_from,
            page_to=b.page_to,
            section_path=b.section_path,
            source_anchor=b.source_anchor,
            normalized_text=b.normalized_text,
            content_hash=b.content_hash,
        )
        session.add(db_block)
        db_blocks.append(db_block)

    duration_ms = (time.monotonic() - start_time) * 1000.0

    # 3. Ghi nhận StageRun nếu có run_id
    if run_id:
        session.add(
            StageRun(
                id=str(uuid.uuid4()),
                run_id=run_id,
                stage="PARSE",
                status="SUCCESS",
                duration_ms=duration_ms,
                metrics_json={
                    "block_count": len(db_blocks),
                    "types": {t: sum(1 for x in db_blocks if x.block_type == t) for t in set(x.block_type for x in db_blocks)},
                },
            )
        )

    await session.flush()
    return db_blocks


async def parse_and_persist_document_content(
    session: AsyncSession,
    document_version_id: str,
    content: str,
    *,
    run_id: str | None = None,
    page_from: int = 1,
    page_to: int = 1,
    document_title: str | None = None,
) -> list[CanonicalBlock]:
    """Hàm hợp nhất trích xuất canonical blocks từ nội dung và lưu trữ vào database."""
    try:
        resolved_title = document_title
        if not resolved_title:
            from sag_api.db.models import Document, DocumentVersion

            ver = await session.get(DocumentVersion, document_version_id)
            if ver and ver.document_id:
                doc = await session.get(Document, ver.document_id)
                if doc and doc.filename:
                    stem = Path(doc.filename).stem.replace("_", " ").strip()
                    if stem:
                        resolved_title = stem
        blocks = extract_canonical_blocks(
            content,
            version_id=document_version_id,
            page_from=page_from,
            page_to=page_to,
            document_title=resolved_title,
        )
        return await persist_canonical_blocks(
            session,
            document_version_id,
            blocks,
            run_id=run_id,
        )
    except Exception as exc:
        if run_id:
            session.add(
                StageRun(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    stage="PARSE",
                    status="FAILED",
                    duration_ms=0.0,
                    metrics_json={},
                    error_message=str(exc),
                )
            )
            await session.flush()
        raise ApiError(
            message=f"Lỗi trích xuất khối chuẩn hóa (Canonical Extraction): {exc}",
            code="CANONICAL_EXTRACTION_FAILED",
            layer=ErrorLayer.ENGINE,
            stage=ErrorStage.PARSE,
        ) from exc
