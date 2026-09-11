from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.db.models import Document, DocumentTreeNode, EmbeddingChunk, Fact, Issuer
from sag_api.enums import ProcessingStageStatus
from sag_api.services.document_structure_service import sha256_text

EMBEDDING_VERSION = "embedding-v2"
CHUNKING_VERSION = "chunking-v2"
PROCESSING_VERSION = 2


@dataclass(frozen=True)
class EmbeddingBuildResult:
    status: str
    chunk_count: int
    embedded_count: int = 0
    error: str | None = None
    metadata: dict[str, Any] | None = None


async def build_embeddings_for_document(
    session: AsyncSession,
    issuer: Issuer,
    document: Document,
    markdown: str,
) -> EmbeddingBuildResult:
    await session.execute(delete(EmbeddingChunk).where(EmbeddingChunk.document_id == document.id))
    node_rows = (
        await session.execute(
            select(DocumentTreeNode)
            .where(DocumentTreeNode.document_id == document.id)
            .order_by(DocumentTreeNode.order_index.asc())
        )
    ).scalars().all()
    fact_rows = (await session.execute(select(Fact).where(Fact.document_id == document.id))).scalars().all()
    labels_by_node: dict[str, list[str]] = {}
    for fact in fact_rows:
        labels_by_node.setdefault(fact.node_id, []).append(fact.label)

    pending: list[tuple[EmbeddingChunk, str]] = []
    lines = markdown.splitlines()
    for node in node_rows:
        excluded = bool((node.metadata_json or {}).get("excluded_from_analysis"))
        if excluded:
            continue
        labels = labels_by_node.get(node.node_id, [])[:16]
        node_text = "\n".join(
            part
            for part in [
                node.heading_path,
                node.summary or "",
                " | ".join(labels),
            ]
            if part
        )
        if node_text.strip():
            pending.append(
                (
                    _chunk_row(
                        issuer,
                        document,
                        node,
                        "node",
                        node_text,
                        node.start_line,
                        node.end_line,
                        {"retrieval_unit": "node", "heading_path": node.heading_path, "fact_labels": labels},
                    ),
                    node_text,
                )
            )

        if node.level == 0 and len(node_rows) > 1:
            continue
        node_lines = lines[int(node.start_line) - 1 : int(node.end_line)]
        for idx, chunk in enumerate(_split_node_markdown(node_lines, int(node.start_line))):
            span = "\n".join(lines[chunk["start_line"] - 1 : chunk["end_line"]])
            evidence_text = f"{node.heading_path}\n{span}".strip()
            if not evidence_text:
                continue
            metadata = {
                "retrieval_unit": "evidence",
                "heading_path": node.heading_path,
                "text": span,
            }
            if chunk.get("table_header"):
                metadata["table_header"] = chunk["table_header"]
                metadata["table_policy"] = "repeat_header"
            pending.append(
                (
                    _chunk_row(
                        issuer,
                        document,
                        node,
                        str(chunk["kind"]),
                        evidence_text,
                        int(chunk["start_line"]),
                        int(chunk["end_line"]),
                        metadata,
                        ordinal=idx,
                    ),
                    evidence_text,
                )
            )

    if not pending:
        return EmbeddingBuildResult(
            status=ProcessingStageStatus.COMPLETE.value,
            chunk_count=0,
            metadata={"mode": "empty_document", "embedding_model": settings.embedding_model},
        )

    if not settings.effective_embedding_api_key:
        for row, _text in pending:
            row.metadata_json = {**(row.metadata_json or {}), "embedding": "not_configured"}
            session.add(row)
        await session.flush()
        return EmbeddingBuildResult(
            status=ProcessingStageStatus.INCOMPLETE.value,
            chunk_count=len(pending),
            error="embedding_api_key_not_configured",
            metadata={"mode": "metadata_only", "embedding_model": settings.embedding_model},
        )

    try:
        embedded = 0
        # SiliconFlow embeddings API caps input at 32 items per request.
        for start in range(0, len(pending), 32):
            batch = pending[start : start + 32]
            vectors = await _embed_texts([text for _row, text in batch])
            for (row, _text), vector in zip(batch, vectors, strict=True):
                row.embedding_vector = vector
                row.embedding_json = vector
                row.embedding_dimensions = len(vector)
                row.metadata_json = {**(row.metadata_json or {}), "embedding": "complete"}
                session.add(row)
                embedded += 1
        await session.flush()
        return EmbeddingBuildResult(
            status=ProcessingStageStatus.COMPLETE.value,
            chunk_count=len(pending),
            embedded_count=embedded,
            metadata={
                "mode": "litellm_embedding",
                "embedding_model": settings.embedding_model,
                "embedded_count": embedded,
            },
        )
    except Exception as exc:  # noqa: BLE001
        for row, _text in pending:
            row.metadata_json = {**(row.metadata_json or {}), "embedding": "failed", "embedding_error": str(exc)[:1000]}
            session.add(row)
        await session.flush()
        return EmbeddingBuildResult(
            status=ProcessingStageStatus.FAILED.value,
            chunk_count=len(pending),
            error=str(exc)[:2000],
            metadata={"mode": "litellm_embedding", "embedding_model": settings.embedding_model, "error": str(exc)[:2000]},
        )


def _chunk_row(
    issuer: Issuer,
    document: Document,
    node: DocumentTreeNode,
    kind: str,
    embedding_text: str,
    start_line: int,
    end_line: int,
    metadata: dict[str, Any],
    *,
    ordinal: int = 0,
) -> EmbeddingChunk:
    content_hash = sha256_text(str(metadata.get("text") or embedding_text))
    text_hash = sha256_text(embedding_text)
    return EmbeddingChunk(
        issuer_id=issuer.id,
        document_id=document.id,
        node_id=node.node_id,
        chunk_id=f"ec_{sha256_text(f'{document.id}:{node.node_id}:{kind}:{ordinal}:{text_hash}')[:24]}",
        chunk_kind=kind,
        ticker=issuer.ticker,
        doc_role=document.doc_role,
        period=_period_label(document),
        start_line=start_line,
        end_line=end_line,
        content_hash=content_hash,
        embedding_text_hash=text_hash,
        embedding_vector=None,
        embedding_json=None,
        embedding_model=settings.embedding_model,
        embedding_dimensions=settings.embedding_dimensions,
        embedding_version=EMBEDDING_VERSION,
        chunking_version=CHUNKING_VERSION,
        active_version=bool(document.is_active),
        processing_version=PROCESSING_VERSION,
        metadata_json={**metadata, "embedding_version": EMBEDDING_VERSION, "chunking_version": CHUNKING_VERSION},
    )


async def _embed_texts(texts: list[str]) -> list[list[float]]:
    from litellm import aembedding

    request: dict[str, Any] = {
        "model": settings.routed_embedding_model,
        "api_key": settings.effective_embedding_api_key,
        "input": texts,
        "timeout": settings.llm_timeout_ms / 1000,
    }
    if settings.effective_embedding_base_url:
        request["api_base"] = settings.effective_embedding_base_url
    if settings.embedding_dimensions:
        request["dimensions"] = settings.embedding_dimensions
        request["allowed_openai_params"] = ["dimensions"]

    response = await aembedding(**request)
    data = response.get("data") if isinstance(response, dict) else getattr(response, "data", None)
    if not data or len(data) != len(texts):
        raise RuntimeError("embedding_response_size_mismatch")

    vectors: list[list[float]] = []
    expected_dim: int | None = settings.embedding_dimensions
    for item in data:
        embedding = item.get("embedding") if isinstance(item, dict) else getattr(item, "embedding", None)
        if not isinstance(embedding, list) or not embedding:
            raise RuntimeError("embedding_vector_missing")
        vector = [float(value) for value in embedding]
        if expected_dim is not None and len(vector) != int(expected_dim):
            raise RuntimeError(f"embedding_dimension_mismatch expected={expected_dim} actual={len(vector)}")
        vectors.append(vector)
    return vectors


def _split_node_markdown(node_lines: list[str], absolute_start_line: int) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    text_buffer: list[tuple[int, str]] = []
    table_header: str | None = None
    table_rows: list[tuple[int, str]] = []

    def flush_text() -> None:
        nonlocal text_buffer
        if not text_buffer:
            return
        for segment in _split_line_items(text_buffer, max_chars=2800, overlap=320):
            chunks.append({"kind": "evidence", "start_line": segment[0][0], "end_line": segment[-1][0]})
        text_buffer = []

    def flush_table() -> None:
        nonlocal table_header, table_rows
        if not table_rows:
            table_header = None
            return
        current: list[tuple[int, str]] = []
        current_chars = len(table_header or "")
        for item in table_rows:
            row_len = len(item[1]) + 1
            if current and current_chars + row_len > 2800:
                chunks.append(
                    {
                        "kind": "table",
                        "start_line": current[0][0],
                        "end_line": current[-1][0],
                        "table_header": table_header,
                    }
                )
                current = []
                current_chars = len(table_header or "")
            current.append(item)
            current_chars += row_len
        if current:
            chunks.append(
                {
                    "kind": "table",
                    "start_line": current[0][0],
                    "end_line": current[-1][0],
                    "table_header": table_header,
                }
            )
        table_header = None
        table_rows = []

    for offset, line in enumerate(node_lines):
        absolute = absolute_start_line + offset
        stripped = line.strip()
        is_table = stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2
        if is_table:
            flush_text()
            if table_header is None:
                table_header = stripped
            table_rows.append((absolute, line))
        else:
            flush_table()
            if stripped:
                text_buffer.append((absolute, line))
            elif text_buffer:
                flush_text()
    flush_table()
    flush_text()
    return chunks


def _split_line_items(items: list[tuple[int, str]], *, max_chars: int, overlap: int) -> list[list[tuple[int, str]]]:
    if not items:
        return []
    segments: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    current_chars = 0
    for item in items:
        line_len = len(item[1]) + 1
        if current and current_chars + line_len > max_chars:
            segments.append(current)
            carry: list[tuple[int, str]] = []
            carry_chars = 0
            for old in reversed(current):
                carry.insert(0, old)
                carry_chars += len(old[1]) + 1
                if carry_chars >= overlap:
                    break
            current = carry
            current_chars = carry_chars
        current.append(item)
        current_chars += line_len
    if current:
        segments.append(current)
    return segments


def _period_label(document: Document) -> str | None:
    if document.period_start or document.period_end:
        return f"{document.period_start or ''}:{document.period_end or ''}"
    if document.fiscal_year and document.fiscal_quarter:
        return f"{document.fiscal_year}Q{document.fiscal_quarter}"
    if document.fiscal_year:
        return str(document.fiscal_year)
    return None
