"""Expose SAG evidence tools through an authorization-scoped MCP request context.

HTTP scopes are constructed from verified principals and confirmed Source
mappings. Process-wide stdio is disabled because it cannot refresh that scope
for every tool call.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, TypedDict

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field

from sag_api.core.config import FIXED_SEARCH_TOP_K
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.services.query_strategy_planner import plan_query
from sag_api.services.retrieval_service import retrieve_relevant_sections
from sag_api.services.search_unit_retrieval_service import retrieve_search_unit_sections
from sag_api.services.source_service import versioned_source_ids

if TYPE_CHECKING:
    from sag_api.db.models import Document, Source
    from sag_api.sag import EngineManager


class MCPToolDetail(TypedDict):
    name: str
    label: str
    description: str


MCP_TOOL_DETAILS: tuple[MCPToolDetail, ...] = (
    {
        "name": "list_sources",
        "label": "Liệt kê nguồn dữ liệu",
        "description": "Liệt kê các nguồn tri thức hiện có thể truy cập, số tài liệu, số đoạn và source_id.",
    },
    {
        "name": "search",
        "label": "Tìm kiếm ngữ nghĩa",
        "description": "Tìm tài liệu liên quan theo ý nghĩa, phù hợp với câu hỏi ngôn ngữ tự nhiên, khái niệm và cách diễn đạt chưa rõ; trả về bằng chứng cùng chunk_id.",
    },
    {
        "name": "get_entity",
        "label": "Tra cứu thực thể",
        "description": "Tìm người, tổ chức hoặc khái niệm và tổng hợp ngữ cảnh liên quan trong tài liệu.",
    },
    {
        "name": "list_documents",
        "label": "Liệt kê tài liệu",
        "description": "Liệt kê tài liệu, trạng thái xử lý, số đoạn và document_id.",
    },
    {
        "name": "outline",
        "label": "Mục lục tài liệu",
        "description": "Xem các chương mục và cấu trúc đoạn của tài liệu được chỉ định; lấy chunk_id để định vị nội dung nhanh.",
    },
    {
        "name": "grep",
        "label": "Tìm chính xác",
        "description": "Tìm theo nội dung nguyên văn, phù hợp với tên riêng, mã số, cụm từ cố định và mã nguồn; trả về ngữ cảnh khớp cùng chunk_id.",
    },
    {
        "name": "read",
        "label": "Đọc nguyên văn theo dòng",
        "description": "Đọc văn bản gốc của tài liệu theo từng trang và dòng, phù hợp để xem ngữ cảnh liên tục.",
    },
    {
        "name": "get_chunk",
        "label": "Đọc đoạn văn bản",
        "description": "Đọc toàn văn một đoạn bằng chunk_id để đối chiếu và trích dẫn bằng chứng.",
    },
)
MCP_TOOL_NAMES = tuple(tool["name"] for tool in MCP_TOOL_DETAILS)
MCP_TOOL_LABELS = {tool["name"]: tool["label"] for tool in MCP_TOOL_DETAILS}
MCP_TOOL_DESCRIPTIONS = {tool["name"]: tool["description"] for tool in MCP_TOOL_DETAILS}
READ_ONLY_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

SourceId = Annotated[
    str,
    Field(description="Không bắt buộc. Lấy từ list_sources; để trống để truy vấn tất cả nguồn dữ liệu có thể xem."),
]
DocumentId = Annotated[str, Field(description="ID tài liệu lấy từ list_documents.")]
ChunkId = Annotated[
    str,
    Field(description="ID đoạn lấy từ kết quả search, outline hoặc grep."),
]


@dataclass(frozen=True)
class MCPScope:
    """Nguồn dữ liệu và engine đã khởi tạo sẵn mà một lần gọi MCP có thể truy cập."""

    engine_manager: EngineManager
    sources: tuple[Source, ...]
    principal: VerifiedPrincipal


_scope: contextvars.ContextVar[MCPScope | None] = contextvars.ContextVar(
    "sag_mcp_scope", default=None
)


def _require_scope() -> MCPScope:
    scope = _scope.get()
    if scope is None:
        raise RuntimeError("Lần gọi MCP thiếu phạm vi kho tri thức")
    return scope


@contextlib.contextmanager
def use_scope(
    engine_manager: EngineManager,
    sources: Source | Iterable[Source],
    principal: VerifiedPrincipal,
):
    """Gắn một hoặc nhiều nguồn dữ liệu vào ngữ cảnh hiện tại."""
    if hasattr(sources, "sag_source_config_id"):
        selected = (sources,)
    else:
        selected = tuple(sources)
    token = _scope.set(MCPScope(engine_manager=engine_manager, sources=selected, principal=principal))
    try:
        yield
    finally:
        _scope.reset(token)


def _selected_sources(scope: MCPScope, source_id: str = "") -> tuple[Source, ...]:
    target = (source_id or "").strip()
    if not target:
        return scope.sources
    return tuple(source for source in scope.sources if source.id == target)


def _source_title(source: Source) -> str:
    return f"{source.name}（source_id={source.id}）"


def _sections_to_text(sections: list, sources: tuple[Source, ...]) -> str:
    if not sections:
        return "(Không tìm thấy tài liệu liên quan.)"
    by_config = {source.sag_source_config_id: source for source in sources}
    by_id = {source.id: source for source in sources}
    blocks = []
    for index, section in enumerate(sections, start=1):
        heading = getattr(section, "heading", None) or "Đoạn"
        chunk_id = getattr(section, "chunk_id", None) or ""
        tag = f"（chunk_id={chunk_id}）" if chunk_id else ""
        source = by_config.get(getattr(section, "source_config_id", None))
        source = source or by_id.get(getattr(section, "source_id", None))
        source_line = f"Nguồn: {_source_title(source)}\n" if source and len(sources) > 1 else ""
        blocks.append(
            f"[{index}] {heading}{tag}\n{source_line}{getattr(section, 'content', '')}"
        )
    return "\n\n".join(blocks)


async def _document_in_scope(
    scope: MCPScope, document_id: str
) -> tuple[Document, Source] | None:
    from sag_api.core.db import SessionLocal
    from sag_api.services.source_service import get_authorized_document

    async with SessionLocal() as session:
        document = await get_authorized_document(
            session,
            principal=scope.principal,
            source_ids=[source.id for source in scope.sources],
            document_id=(document_id or "").strip(),
        )
    if document is None:
        return None
    source = next((item for item in scope.sources if item.id == document.source_id), None)
    return (document, source) if source is not None else None


def build_source_mcp(
    *,
    stateless_http: bool = False,
    transport_security: TransportSecuritySettings | None = None,
) -> FastMCP:
    """Tạo MCP server cho kho tri thức; contextvar sẽ thiết lập phạm vi cụ thể trước mỗi yêu cầu."""
    mcp = FastMCP(
        "sag-knowledge",
        instructions=(
            "MCP kho tri thức SAG: tìm kiếm trong các nguồn thuộc phạm vi đã được cấp quyền cho lần gọi này; có thể truyền source_id vào công cụ để giới hạn thêm. "
            "Trước tiên dùng list_sources/list_documents để nắm phạm vi tài liệu, sau đó dùng search, grep, outline, "
            "read và get_chunk để lấy bằng chứng có thể trích dẫn. Khi trả lời, hãy dựa trên bằng chứng được đánh số từ search."
        ),
        stateless_http=stateless_http,
        transport_security=transport_security,
    )

    @mcp.tool(
        title=MCP_TOOL_LABELS["list_sources"],
        description=MCP_TOOL_DESCRIPTIONS["list_sources"],
        annotations=READ_ONLY_TOOL_ANNOTATIONS,
    )
    async def list_sources() -> str:
        scope = _require_scope()
        if not scope.sources:
            return "(Kho tri thức chưa có nguồn dữ liệu.)"
        return "\n".join(
            f"- {_source_title(source)}"
            for source in scope.sources
        )

    @mcp.tool(
        title=MCP_TOOL_LABELS["search"],
        description=MCP_TOOL_DESCRIPTIONS["search"],
        annotations=READ_ONLY_TOOL_ANNOTATIONS,
    )
    async def search(
        query: Annotated[str, Field(description="Câu hỏi, chủ đề hoặc từ khóa cần tìm.")],
        top_k: Annotated[
            int | None,
            Field(
                description="Số bằng chứng hoặc tài liệu cần lấy; tự động điều chỉnh theo câu hỏi (mặc định 5 cho câu hỏi chuẩn, tối đa 50 khi liệt kê tài liệu).",
                ge=1,
            ),
        ] = None,
        source_id: SourceId = "",
    ) -> str:
        scope = _require_scope()
        selected = _selected_sources(scope, source_id)
        if not selected:
            return "(Không có nguồn dữ liệu để tìm kiếm.)" if not source_id else "(Nguồn dữ liệu không tồn tại hoặc nằm ngoài phạm vi hiện tại.)"
        normalized = (query or "").strip()
        if not normalized:
            return "(Truy vấn trống.)"
        from sag_api.core.db import SessionLocal

        async with SessionLocal() as session:
            partitioned_ids = await versioned_source_ids(session, [source.id for source in selected])
        strategy_plan = plan_query(normalized, requested_top_k=top_k)
        bounded_top_k = strategy_plan.retrieval_plan.cardinality.effective_n
        sections = []
        canonical_sources = [source for source in selected if source.id in partitioned_ids]
        legacy_sources = [source for source in selected if source.id not in partitioned_ids]
        if canonical_sources:
            canonical = await retrieve_search_unit_sections(
                scope.engine_manager,
                canonical_sources,
                normalized,
                principal=scope.principal,
                top_k=bounded_top_k,
                query_strategy_plan=strategy_plan,
            )
            sections.extend(canonical.sections)
        if legacy_sources:
            legacy = await retrieve_relevant_sections(
                scope.engine_manager,
                legacy_sources,
                normalized,
                top_k=bounded_top_k,
            )
            sections.extend(legacy.sections)
        sections.sort(key=lambda item: item.score, reverse=True)
        return _sections_to_text(sections[:bounded_top_k], selected)

    @mcp.tool(
        title=MCP_TOOL_LABELS["get_entity"],
        description=MCP_TOOL_DESCRIPTIONS["get_entity"],
        annotations=READ_ONLY_TOOL_ANNOTATIONS,
    )
    async def get_entity(
        name: Annotated[
            str,
            Field(description="Tên thực thể như người, tổ chức hoặc khái niệm; hỗ trợ tên đầy đủ hoặc một phần."),
        ],
        source_id: SourceId = "",
    ) -> str:
        scope = _require_scope()
        selected = _selected_sources(scope, source_id)
        if not selected:
            return "(Không có nguồn dữ liệu để tra cứu.)" if not source_id else "(Nguồn dữ liệu không tồn tại hoặc nằm ngoài phạm vi hiện tại.)"
        target = (name or "").strip()
        if not target:
            return "(Không tìm thấy thực thể này.)"

        from sag_api.core.db import SessionLocal

        async with SessionLocal() as session:
            partitioned_ids = await versioned_source_ids(session, [source.id for source in selected])
        matches = []
        canonical_sources = [source for source in selected if source.id in partitioned_ids]
        legacy_sources = [source for source in selected if source.id not in partitioned_ids]
        if canonical_sources:
            outcome = await retrieve_search_unit_sections(
                scope.engine_manager,
                canonical_sources,
                target,
                principal=scope.principal,
                query_strategy_plan=plan_query(target),
            )
            if outcome.sections:
                matches.append(_sections_to_text(outcome.sections, canonical_sources))
        async def _legacy_entity(source: Source) -> str | None:
            try:
                entities = await scope.engine_manager.list_entities(
                    source.sag_source_config_id, source=source, limit=200
                )
                lowered = target.lower()
                entity = next((item for item in entities if (item.name or "").lower() == lowered), None)
                entity = entity or next((item for item in entities if lowered in (item.name or "").lower()), None)
                if entity is None:
                    return None
                snippets = await scope.engine_manager.entity_context(
                    source.sag_source_config_id, entity.id, source=source, limit=6
                )
                body = "\n\n".join(snippets) if snippets else (entity.description or "")
                prefix = f"Nguồn: {_source_title(source)}\n" if len(selected) > 1 else ""
                return f"{prefix}Thực thể “{entity.name}” ({entity.type}):\n{body}".strip()
            except Exception:
                return None

        legacy_matches = await asyncio.gather(*(_legacy_entity(source) for source in legacy_sources))
        matches.extend(item for item in legacy_matches if item)
        return "\n\n".join(matches) if matches else "(Không tìm thấy thực thể này.)"

    @mcp.tool(
        title=MCP_TOOL_LABELS["list_documents"],
        description=MCP_TOOL_DESCRIPTIONS["list_documents"],
        annotations=READ_ONLY_TOOL_ANNOTATIONS,
    )
    async def list_documents(source_id: SourceId = "") -> str:
        scope = _require_scope()
        selected = _selected_sources(scope, source_id)
        if not selected:
            return "(Kho tri thức chưa có nguồn dữ liệu.)" if not source_id else "(Nguồn dữ liệu không tồn tại hoặc nằm ngoài phạm vi hiện tại.)"
        from sag_api.core.db import SessionLocal
        from sag_api.services.source_service import list_authorized_documents

        source_ids = [source.id for source in selected]
        async with SessionLocal() as session:
            documents = await list_authorized_documents(
                session, principal=scope.principal, source_ids=source_ids
            )
        documents.sort(key=lambda document: (document.created_at, document.id))
        if not documents:
            return "(Kho tri thức chưa có tài liệu.)"
        by_source: dict[str, list[Document]] = {item.id: [] for item in selected}
        for document in documents:
            by_source.setdefault(document.source_id, []).append(document)
        blocks = []
        for source in selected:
            rows = by_source.get(source.id) or []
            if not rows:
                continue
            lines = []
            for document in rows:
                status = getattr(document.status, "value", document.status)
                lines.append(
                    f"- {document.filename} · id={document.id} · {status} · "
                    f"{document.chunk_count} đoạn"
                )
            header = f"## {_source_title(source)}\n" if len(selected) > 1 else ""
            blocks.append(header + "\n".join(lines))
        return "\n\n".join(blocks)

    @mcp.tool(
        title=MCP_TOOL_LABELS["outline"],
        description=MCP_TOOL_DESCRIPTIONS["outline"],
        annotations=READ_ONLY_TOOL_ANNOTATIONS,
    )
    async def outline(document_id: DocumentId) -> str:
        scope = _require_scope()
        match = await _document_in_scope(scope, document_id)
        if match is None:
            return "(Không tìm thấy tài liệu này.)"
        document, source = match
        if not document.sag_source_id:
            return "(Chưa có mục lục; tài liệu có thể vẫn đang được xử lý.)"
        rows = await scope.engine_manager.list_chunk_headings(
            source.sag_source_config_id,
            source=source,
            doc_sag_id=document.sag_source_id,
        )
        if not rows:
            return "(Chưa có mục lục; tài liệu có thể vẫn đang được xử lý.)"
        return "\n".join(
            f"{row['rank']:>3}. {row['heading'] or '(Đoạn không có tiêu đề)'}"
            f"（chunk_id={row['chunk_id']}）"
            for row in rows
        )

    @mcp.tool(
        title=MCP_TOOL_LABELS["grep"],
        description=MCP_TOOL_DESCRIPTIONS["grep"],
        annotations=READ_ONLY_TOOL_ANNOTATIONS,
    )
    async def grep(
        pattern: Annotated[
            str,
            Field(description="Văn bản cần tìm chính xác trong nội dung gốc; phù hợp với tên riêng, mã số, cụm từ cố định và mã nguồn."),
        ],
        limit: Annotated[
            int,
            Field(description="Số kết quả khớp tối đa; mặc định 20, máy chủ giới hạn trong khoảng 1–100."),
        ] = 20,
        source_id: SourceId = "",
    ) -> str:
        scope = _require_scope()
        selected = _selected_sources(scope, source_id)
        if not selected:
            return "(Không có nguồn dữ liệu để tra cứu.)" if not source_id else "(Nguồn dữ liệu không tồn tại hoặc nằm ngoài phạm vi hiện tại.)"
        needle = (pattern or "").strip()
        if not needle:
            return "(Chuỗi tìm kiếm trống.)"
        bounded_limit = max(1, min(limit, 100))

        from sag_api.core.db import SessionLocal
        from sag_api.services.search_unit_retrieval_service import grep_search_unit_blocks

        async with SessionLocal() as session:
            partitioned_ids = await versioned_source_ids(session, [source.id for source in selected])
        blocks_by_source: list[list[str]] = []
        canonical_sources = [source for source in selected if source.id in partitioned_ids]
        legacy_sources = [source for source in selected if source.id not in partitioned_ids]
        for source in canonical_sources:
            async with SessionLocal() as session:
                matches = await grep_search_unit_blocks(
                    session,
                    [source],
                    needle,
                    principal=scope.principal,
                    limit=bounded_limit,
                )
            source_line = f"Nguồn: {_source_title(source)}\n" if len(selected) > 1 else ""
            blocks_by_source.append([
                f"{match.section_path or 'Đoạn không có tiêu đề'} (chunk_id={match.id})\n"
                f"{source_line}{match.normalized_text}"
                for match in matches
            ])

        async def _legacy_grep(source: Source) -> list[dict]:
            try:
                return await scope.engine_manager.grep_chunks(
                    source.sag_source_config_id, needle, source=source, limit=bounded_limit
                )
            except Exception:
                return []

        results = await asyncio.gather(*(_legacy_grep(source) for source in legacy_sources))
        for source, rows in zip(legacy_sources, results, strict=True):
            source_line = f"Nguồn: {_source_title(source)}\n" if len(selected) > 1 else ""
            blocks_by_source.append([
                    f"{row['heading'] or 'Đoạn không có tiêu đề'} (chunk_id={row['chunk_id']})\n"
                    f"{source_line}{row['snippet']}"
                for row in rows
            ])
        blocks = []
        for rank in range(max((len(items) for items in blocks_by_source), default=0)):
            for source_blocks in blocks_by_source:
                if rank < len(source_blocks):
                    blocks.append(source_blocks[rank])
                    if len(blocks) == bounded_limit:
                        break
            if len(blocks) == bounded_limit:
                break
        if not blocks:
            return "(Không tìm thấy nội dung khớp.)"
        return "\n\n".join(f"[{index}] {block}" for index, block in enumerate(blocks[:bounded_limit], 1))

    @mcp.tool(
        title=MCP_TOOL_LABELS["read"],
        description=MCP_TOOL_DESCRIPTIONS["read"],
        annotations=READ_ONLY_TOOL_ANNOTATIONS,
    )
    async def read(
        document_id: DocumentId,
        offset: Annotated[
            int,
            Field(description="Bắt đầu đọc từ dòng nào; dòng đầu tiên là 1, mặc định 1."),
        ] = 1,
        limit: Annotated[
            int,
            Field(description="Số dòng cần đọc trong lần này; mặc định 120, máy chủ trả về tối đa 500 dòng."),
        ] = 120,
    ) -> str:
        scope = _require_scope()
        match = await _document_in_scope(scope, document_id)
        if match is None:
            return "(Không tìm thấy tài liệu này.)"
        document, source = match
        import os

        if not document.storage_path or not os.path.isfile(document.storage_path):
            return "(Tệp gốc không tồn tại hoặc đã được dọn dẹp.)"
        try:
            with open(document.storage_path, encoding="utf-8", errors="replace") as file:
                lines = file.readlines()
        except OSError:
            return "(Đọc tệp thất bại.)"
        start = max(0, offset - 1)
        page = lines[start : start + max(1, min(limit, 500))]
        if not page:
            return f"(Vượt quá phạm vi: toàn văn có {len(lines)} dòng.)"
        body = "".join(f"{start + index + 1:>5}\t{line}" for index, line in enumerate(page))
        source_line = f"Nguồn: {_source_title(source)}\n" if len(scope.sources) > 1 else ""
        return (
            f"{document.filename} · dòng {start + 1}-{start + len(page)} / "
            f"tổng cộng {len(lines)} dòng\n{source_line}{body}"
        )

    @mcp.tool(
        title=MCP_TOOL_LABELS["get_chunk"],
        description=MCP_TOOL_DESCRIPTIONS["get_chunk"],
        annotations=READ_ONLY_TOOL_ANNOTATIONS,
    )
    async def get_chunk(chunk_id: ChunkId, source_id: SourceId = "") -> str:
        scope = _require_scope()
        selected = _selected_sources(scope, source_id)
        if not selected:
            return "(Không có nguồn dữ liệu để tra cứu.)" if not source_id else "(Nguồn dữ liệu không tồn tại hoặc nằm ngoài phạm vi hiện tại.)"
        cid = (chunk_id or "").strip()
        if not cid:
            return "(Thiếu chunk_id.)"

        from sag_api.core.db import SessionLocal
        from sag_api.services.search_unit_retrieval_service import get_search_unit_citation

        partitioned_ids: set[str] = set()
        found = None
        async with SessionLocal() as session:
            partitioned_ids = await versioned_source_ids(session, [source.id for source in selected])
            for source in selected:
                citation = await get_search_unit_citation(
                    session,
                    source=source,
                    principal=scope.principal,
                    search_unit_id=cid,
                    engine_manager=scope.engine_manager,
                )
                if citation is not None:
                    found = (source, citation)
                    break
        if found is None:
            legacy_sources = [source for source in selected if source.id not in partitioned_ids]
            async def _one(source: Source):
                try:
                    chunk = await scope.engine_manager.get_chunk(
                        source.sag_source_config_id, cid, source=source
                    )
                    return source, chunk
                except Exception:
                    return source, None

            results = await asyncio.gather(*(_one(source) for source in legacy_sources))
            legacy = next(((source, chunk) for source, chunk in results if chunk is not None), None)
            found = legacy
        if found is None:
            return "(Không tìm thấy đoạn này.)"
        source, chunk = found
        if isinstance(chunk, dict):
            heading = str(chunk.get("heading") or "").strip()
            content = str(chunk.get("content") or "")
            body = f"{heading}\n\n{content}".strip() if heading else content
            return f"Nguồn: {_source_title(source)}\n\n{body}" if len(selected) > 1 else body
        heading = (chunk.heading or "").strip()
        body = f"{heading}\n\n{chunk.content}".strip() if heading else chunk.content
        return f"Nguồn: {_source_title(source)}\n\n{body}" if len(selected) > 1 else body

    return mcp


_singleton: FastMCP | None = None


def get_source_mcp() -> FastMCP:
    """Trả về MCP server dùng chung cho lời gọi stdio hoặc trong tiến trình."""
    global _singleton
    if _singleton is None:
        _singleton = build_source_mcp()
    return _singleton


async def serve_stdio(source_id: str | None = None) -> None:
    """Disable stdio until each MCP call can carry a current signed principal."""
    del source_id
    raise SystemExit(
        "SAG MCP stdio is disabled: a process-wide Source ID is not an authorization grant; "
        "use the authenticated HTTP transport with a current Continuum principal assertion."
    )


def _main() -> None:
    import os

    source_id = os.environ.get("SAG_MCP_SOURCE_ID", "").strip() or None
    asyncio.run(serve_stdio(source_id))


if __name__ == "__main__":
    _main()
