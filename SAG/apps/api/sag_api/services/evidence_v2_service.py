from __future__ import annotations

from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.db.models import EmbeddingChunk
from sag_api.services.document_structure_service import ACTIVE_DOCUMENT_ROLES

def heading_path_list(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(">") if part.strip()]


async def search_evidence(session: AsyncSession, ticker: str, query: str, *, top_k: int, include_historical: bool) -> list[dict[str, Any]]:
    issuer = await get_or_create_issuer(session, ticker)
    # pgvector is optional.  Isolate a failed vector query in a savepoint so
    # lexical fallback can still use the same request transaction.
    try:
        async with session.begin_nested():
            dense_hits = await _search_evidence_pgvector(session, issuer.id, query, top_k=top_k, include_historical=include_historical)
    except Exception:
        dense_hits = []
    if dense_hits:
        return dense_hits
    return await _search_evidence_lexical(session, issuer.id, query, top_k=top_k, include_historical=include_historical)


async def _search_evidence_pgvector(
    session: AsyncSession,
    issuer_id: str,
    query: str,
    *,
    top_k: int,
    include_historical: bool,
) -> list[dict[str, Any]]:
    dialect = session.bind.dialect.name if session.bind is not None else ""
    if (
        not settings.embedding_enabled
        or dialect != "postgresql"
        or not settings.effective_embedding_api_key
    ):
        return []
    try:
        from sag_api.services.embedding_v2_service import _embed_texts

        query_vector = (await _embed_texts([query]))[0]
        vector_literal = "[" + ",".join(f"{float(value):.9g}" for value in query_vector) + "]"
        active_filter = "" if include_historical else "AND active_version = true"
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT
                        document_id,
                        doc_role,
                        node_id,
                        start_line,
                        end_line,
                        content_hash,
                        metadata_json,
                        1 - (embedding_vector <=> CAST(:query_vector AS vector)) AS dense_score
                    FROM sag.embedding_chunks
                    WHERE issuer_id = :issuer_id
                      AND embedding_vector IS NOT NULL
                      {active_filter}
                    ORDER BY embedding_vector <=> CAST(:query_vector AS vector)
                    LIMIT :limit
                    """
                ),
                {"issuer_id": issuer_id, "query_vector": vector_literal, "limit": max(top_k * 4, top_k)},
            )
        ).mappings().all()
    except Exception:
        # Let search_evidence() roll back the surrounding savepoint before
        # attempting lexical fallback.  Returning [] here would leave the
        # PostgreSQL transaction aborted.
        raise

    folded_terms = [term for term in query.casefold().split() if term]
    ranked: list[tuple[float, Any]] = []
    for row in rows:
        metadata = row.get("metadata_json") or {}
        text_value = str(metadata.get("text") or "")
        lexical = _lexical_score(text_value, folded_terms)
        dense = float(row.get("dense_score") or 0.0)
        role_bonus = 1.0 if row.get("doc_role") in ACTIVE_DOCUMENT_ROLES else 0.0
        ranked.append((0.55 * dense + 0.30 * lexical + 0.15 * role_bonus, row))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "document_id": row["document_id"],
            "doc_role": row["doc_role"],
            "node_id": row["node_id"],
            "heading_path": heading_path_list((row.get("metadata_json") or {}).get("heading_path")),
            "line_span": (int(row["start_line"]), int(row["end_line"])),
            "score": score,
            "content": str((row.get("metadata_json") or {}).get("text") or ""),
            "quote_hash": row["content_hash"],
        }
        for score, row in ranked[:top_k]
    ]


async def _search_evidence_lexical(
    session: AsyncSession,
    issuer_id: str,
    query: str,
    *,
    top_k: int,
    include_historical: bool,
) -> list[dict[str, Any]]:
    folded_terms = [term for term in query.casefold().split() if term]
    stmt = select(EmbeddingChunk).where(EmbeddingChunk.issuer_id == issuer_id)
    if not include_historical:
        stmt = stmt.where(EmbeddingChunk.active_version.is_(True))
    chunks = (await session.execute(stmt)).scalars().all()
    ranked = []
    for chunk in chunks:
        text = str((chunk.metadata_json or {}).get("text") or "")
        folded = text.casefold()
        score = _lexical_score(text, folded_terms)
        if score:
            ranked.append((float(score), chunk, text))
    ranked.sort(key=lambda item: item[0], reverse=True)
    hits = []
    for score, chunk, text in ranked[:top_k]:
        hits.append(
            {
                "document_id": chunk.document_id,
                "doc_role": chunk.doc_role,
                "node_id": chunk.node_id,
                "heading_path": heading_path_list((chunk.metadata_json or {}).get("heading_path")),
                "line_span": (chunk.start_line, chunk.end_line),
                "score": score,
                "content": text,
                "quote_hash": chunk.content_hash,
            }
        )
    return hits


def _lexical_score(text_value: str, folded_terms: list[str]) -> float:
    if not folded_terms:
        return 0.0
    folded = text_value.casefold()
    raw = sum(folded.count(term) for term in folded_terms)
    return min(float(raw) / max(len(folded_terms), 1), 1.0)


