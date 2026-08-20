"""Phơi bày khả năng truy vấn, thực thể và văn bản gốc của kho kiến thức SAG thành MCP server chuẩn.

Một instance SAG chỉ xây dựng một FastMCP server. Mỗi lần gọi có thể tác động lên tất cả nguồn, hoặc có thể
thu hẹp xuống một nguồn duy nhất qua ``source_id``: lớp bọc HTTP, Agent trong tiến trình và lối vào stdio đều
tiêm nguồn hiện có thể thấy qua ``MCPScope``, tool bản thân không phụ thuộc vào phương thức truyền tải.
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

from sag_api.services.retrieval_service import retrieve_relevant_sections

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
        "label": "Xem nguồn",
        "description": "Xem các nguồn kiến thức hiện có thể truy cập, số tài liệu và số chunk, đồng thời lấy source_id.",
    },
    {
        "name": "search",
        "label": "Tìm kiếm ngữ nghĩa",
        "description": "Tìm tài liệu liên quan theo ý nghĩa, phù hợp cho câu hỏi ngôn ngữ tự nhiên, khái niệm và diễn đạt mơ hồ; trả về đoạn bằng chứng và chunk_id.",
    },
    {
        "name": "get_entity",
        "label": "Truy vấn thực thể",
        "description": "Tìm người, tổ chức hoặc khái niệm, đồng thời tổng hợp ngữ cảnh liên quan của nó trong tài liệu.",
    },
    {
        "name": "list_documents",
        "label": "Xem tài liệu",
        "description": "Liệt kê tài liệu, trạng thái xử lý và số lượng chunk, đồng thời lấy document_id.",
    },
    {
        "name": "outline",
        "label": "Đề cương tài liệu",
        "description": "Xem cấu trúc chương và chunk của tài liệu chỉ định, lấy chunk_id để nhanh chóng định vị nội dung.",
    },
    {
        "name": "grep",
        "label": "Tìm chính xác",
        "description": "Tìm theo nội dung nguyên văn, phù hợp cho tên riêng, số hiệu, cụm từ cố định và mã; trả về ngữ cảnh khớp và chunk_id.",
    },
    {
        "name": "read",
        "label": "Đọc văn bản gốc theo dòng",
        "description": "Đọc văn bản gốc của tài liệu chỉ định theo trang dòng, phù hợp để xem ngữ cảnh liên tục.",
    },
    {
        "name": "get_chunk",
        "label": "Đọc chunk",
        "description": "Đọc toàn văn bản gốc của một chunk qua chunk_id, dùng để đối chiếu và trích dẫn bằng chứng.",
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
    Field(description="Tùy chọn. Lấy từ list_sources; để trống để truy vấn tất cả nguồn có thể thấy."),
]
DocumentId = Annotated[str, Field(description="ID tài liệu lấy từ list_documents.")]
ChunkId = Annotated[
    str,
    Field(description="ID chunk lấy từ kết quả search, outline hoặc grep."),
]


@dataclass(frozen=True)
class MCPScope:
    """Nguồn có thể thấy trong một lần gọi MCP và warm engine của chúng."""

    engine_manager: EngineManager
    sources: tuple[Source, ...]


_scope: contextvars.ContextVar[MCPScope | None] = contextvars.ContextVar(
    "sag_mcp_scope", default=None
)


def _require_scope() -> MCPScope:
    scope = _scope.get()
    if scope is None:
        raise RuntimeError("Lời gọi MCP thiếu phạm vi kho kiến thức")
    return scope


@contextlib.contextmanager
def use_scope(engine_manager: EngineManager, sources: Source | Iterable[Source]):
    """Ràng buộc một nguồn hoặc một nhóm nguồn trong ngữ cảnh."""
    if hasattr(sources, "sag_source_config_id"):
        selected = (sources,)
    else:
        selected = tuple(sources)
    token = _scope.set(MCPScope(engine_manager=engine_manager, sources=selected))
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
        return "（Không có tài liệu liên quan）"
    by_config = {source.sag_source_config_id: source for source in sources}
    by_id = {source.id: source for source in sources}
    blocks = []
    for index, section in enumerate(sections, start=1):
        heading = getattr(section, "heading", None) or "đoạn"
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
    from sag_api.db.models import Document

    async with SessionLocal() as session:
        document = await session.get(Document, (document_id or "").strip())
    if document is None:
        return None
    source = next((item for item in scope.sources if item.id == document.source_id), None)
    return (document, source) if source is not None else None


def build_source_mcp(
    *,
    stateless_http: bool = False,
    transport_security: TransportSecuritySettings | None = None,
) -> FastMCP:
    """Tạo MCP server cho kho kiến thức, phạm vi cụ thể được contextvar tiêm trước mỗi yêu cầu."""
    mcp = FastMCP(
        "sag-knowledge",
        instructions=(
            "MCP kho kiến thức SAG: mặc định tìm kiếm tất cả nguồn, cũng có thể truyền source_id "
            "cho tool để giới hạn phạm vi. Trước tiên dùng list_sources/list_documents để nắm phạm vi "
            "tài liệu, rồi dùng search, grep, outline, read và get_chunk để lấy bằng chứng trích dẫn "
            "được. Trả lời hãy dựa trên bằng chứng có số thứ tự mà search trả về."
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
            return "（Kho kiến thức chưa có nguồn nào）"
        return "\n".join(
            f"- {_source_title(source)} · {source.document_count} tài liệu · {source.chunk_count} chunk"
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
            int,
            Field(description="Số bằng chứng tối đa trả về; mặc định 8, máy chủ giới hạn trong 1–50."),
        ] = 8,
        source_id: SourceId = "",
    ) -> str:
        scope = _require_scope()
        selected = _selected_sources(scope, source_id)
        if not selected:
            return "（Không có nguồn nào để tìm kiếm）" if not source_id else "（Nguồn không tồn tại hoặc không nằm trong phạm vi hiện tại）"
        normalized = (query or "").strip()
        if not normalized:
            return "（Truy vấn rỗng）"
        outcome = await retrieve_relevant_sections(
            scope.engine_manager,
            selected,
            normalized,
            top_k=max(1, min(top_k, 50)),
        )
        return _sections_to_text(outcome.sections, selected)

    @mcp.tool(
        title=MCP_TOOL_LABELS["get_entity"],
        description=MCP_TOOL_DESCRIPTIONS["get_entity"],
        annotations=READ_ONLY_TOOL_ANNOTATIONS,
    )
    async def get_entity(
        name: Annotated[
            str,
            Field(description="Tên thực thể như người, tổ chức, khái niệm; hỗ trợ tên đầy đủ hoặc tên một phần."),
        ],
        source_id: SourceId = "",
    ) -> str:
        scope = _require_scope()
        selected = _selected_sources(scope, source_id)
        if not selected:
            return "（Không có nguồn nào để truy vấn）" if not source_id else "（Nguồn không tồn tại hoặc không nằm trong phạm vi hiện tại）"
        target = (name or "").strip()
        if not target:
            return "（Không tìm thấy thực thể này）"

        async def _one(source: Source) -> str | None:
            try:
                scid = source.sag_source_config_id
                entities = await scope.engine_manager.list_entities(
                    scid, source=source, limit=200
                )
                lowered = target.lower()
                match = next(
                    (entity for entity in entities if (entity.name or "").lower() == lowered),
                    None,
                )
                if match is None:
                    match = next(
                        (
                            entity
                            for entity in entities
                            if lowered in (entity.name or "").lower()
                        ),
                        None,
                    )
                if match is None:
                    return None
                snippets = await scope.engine_manager.entity_context(
                    scid, match.id, source=source, limit=6
                )
                body = "\n\n".join(snippets) if snippets else (match.description or "")
                prefix = f"Nguồn: {_source_title(source)}\n" if len(selected) > 1 else ""
                return f"{prefix}Thực thể「{match.name}」（{match.type}）:\n{body}".strip()
            except Exception:
                return None

        results = await asyncio.gather(*(_one(source) for source in selected))
        matches = [result for result in results if result]
        return "\n\n".join(matches) if matches else "（Không tìm thấy thực thể này）"

    @mcp.tool(
        title=MCP_TOOL_LABELS["list_documents"],
        description=MCP_TOOL_DESCRIPTIONS["list_documents"],
        annotations=READ_ONLY_TOOL_ANNOTATIONS,
    )
    async def list_documents(source_id: SourceId = "") -> str:
        scope = _require_scope()
        selected = _selected_sources(scope, source_id)
        if not selected:
            return "（Kho kiến thức chưa có nguồn nào）" if not source_id else "（Nguồn không tồn tại hoặc không nằm trong phạm vi hiện tại）"
        from sqlalchemy import select

        from sag_api.core.db import SessionLocal
        from sag_api.db.models import Document

        source_ids = [source.id for source in selected]
        async with SessionLocal() as session:
            documents = list(
                (
                    await session.execute(
                        select(Document)
                        .where(Document.source_id.in_(source_ids))
                        .order_by(Document.created_at, Document.id)
                    )
                )
                .scalars()
                .all()
            )
        if not documents:
            return "（Kho kiến thức chưa có tài liệu nào）"
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
                    f"{document.chunk_count} chunk"
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
            return "（Không tìm thấy tài liệu này）"
        document, source = match
        if not document.sag_source_id:
            return "（Chưa có đề cương: tài liệu có thể vẫn đang xử lý）"
        rows = await scope.engine_manager.list_chunk_headings(
            source.sag_source_config_id,
            source=source,
            doc_sag_id=document.sag_source_id,
        )
        if not rows:
            return "（Chưa có đề cương: tài liệu có thể vẫn đang xử lý）"
        return "\n".join(
            f"{row['rank']:>3}. {row['heading'] or '（chunk không có tiêu đề）'}"
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
            Field(description="Văn bản cần tìm chính xác trong bản gốc; phù hợp cho tên riêng, số hiệu, cụm từ cố định và mã."),
        ],
        limit: Annotated[
            int,
            Field(description="Số kết quả khớp tối đa trả về; mặc định 20, máy chủ giới hạn trong 1–100."),
        ] = 20,
        source_id: SourceId = "",
    ) -> str:
        scope = _require_scope()
        selected = _selected_sources(scope, source_id)
        if not selected:
            return "（Không có nguồn nào để tìm kiếm）" if not source_id else "（Nguồn không tồn tại hoặc không nằm trong phạm vi hiện tại）"
        needle = (pattern or "").strip()
        if not needle:
            return "（Chuỗi khớp rỗng）"
        bounded_limit = max(1, min(limit, 100))

        async def _one(source: Source) -> list[dict]:
            try:
                return await scope.engine_manager.grep_chunks(
                    source.sag_source_config_id,
                    needle,
                    source=source,
                    limit=bounded_limit,
                )
            except Exception:
                return []

        results = await asyncio.gather(*(_one(source) for source in selected))
        blocks = []
        for source, rows in zip(selected, results, strict=True):
            for row in rows:
                source_line = (
                    f"Nguồn: {_source_title(source)}\n" if len(selected) > 1 else ""
                )
                blocks.append(
                    f"{row['heading'] or 'đoạn'}（chunk_id={row['chunk_id']}）\n"
                    f"{source_line}{row['snippet']}"
                )
                if len(blocks) >= bounded_limit:
                    break
            if len(blocks) >= bounded_limit:
                break
        if not blocks:
            return "（Không có nội dung khớp）"
        return "\n\n".join(f"[{index}] {block}" for index, block in enumerate(blocks, 1))

    @mcp.tool(
        title=MCP_TOOL_LABELS["read"],
        description=MCP_TOOL_DESCRIPTIONS["read"],
        annotations=READ_ONLY_TOOL_ANNOTATIONS,
    )
    async def read(
        document_id: DocumentId,
        offset: Annotated[
            int,
            Field(description="Đọc từ dòng thứ mấy; dòng đầu là 1, mặc định 1."),
        ] = 1,
        limit: Annotated[
            int,
            Field(description="Đọc bao nhiêu dòng trong lần này; mặc định 120, máy chủ trả về tối đa 500 dòng."),
        ] = 120,
    ) -> str:
        scope = _require_scope()
        match = await _document_in_scope(scope, document_id)
        if match is None:
            return "（Không tìm thấy tài liệu này）"
        document, source = match
        import os

        if not document.storage_path or not os.path.isfile(document.storage_path):
            return "（File gốc không tồn tại hoặc đã được dọn）"
        try:
            with open(document.storage_path, encoding="utf-8", errors="replace") as file:
                lines = file.readlines()
        except OSError:
            return "（Đọc file thất bại）"
        start = max(0, offset - 1)
        page = lines[start : start + max(1, min(limit, 500))]
        if not page:
            return f"（Ngoài phạm vi: toàn văn có {len(lines)} dòng）"
        body = "".join(f"{start + index + 1:>5}\t{line}" for index, line in enumerate(page))
        source_line = f"Nguồn: {_source_title(source)}\n" if len(scope.sources) > 1 else ""
        return (
            f"{document.filename} · Dòng {start + 1}-{start + len(page)} / "
            f"tổng {len(lines)} dòng\n{source_line}{body}"
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
            return "（Không có nguồn nào để tìm kiếm）" if not source_id else "（Nguồn không tồn tại hoặc không nằm trong phạm vi hiện tại）"
        cid = (chunk_id or "").strip()
        if not cid:
            return "（Thiếu chunk_id）"

        async def _one(source: Source):
            try:
                chunk = await scope.engine_manager.get_chunk(
                    source.sag_source_config_id, cid, source=source
                )
                return source, chunk
            except Exception:
                return source, None

        results = await asyncio.gather(*(_one(source) for source in selected))
        found = next(((source, chunk) for source, chunk in results if chunk is not None), None)
        if found is None:
            return "（Không tìm thấy chunk này）"
        source, chunk = found
        heading = (chunk.heading or "").strip()
        body = f"{heading}\n\n{chunk.content}".strip() if heading else chunk.content
        return f"Nguồn: {_source_title(source)}\n\n{body}" if len(selected) > 1 else body

    return mcp


_singleton: FastMCP | None = None


def get_source_mcp() -> FastMCP:
    """Trả về MCP server dùng chung cho stdio/gọi trong tiến trình."""
    global _singleton
    if _singleton is None:
        _singleton = build_source_mcp()
    return _singleton


async def serve_stdio(source_id: str | None = None) -> None:
    """Chạy stdio server; khi không cung cấp source_id sẽ mở tất cả nguồn."""
    from sqlalchemy import select

    from sag_api.core.config import settings
    from sag_api.core.db import SessionLocal, init_db
    from sag_api.db.models import Source
    from sag_api.sag import EngineManager

    await init_db()
    engine_manager = EngineManager(settings)
    async with SessionLocal() as session:
        statement = select(Source).order_by(Source.created_at, Source.id)
        if source_id:
            statement = statement.where(Source.id == source_id)
        sources = tuple((await session.execute(statement)).scalars().all())
    if source_id and not sources:
        raise SystemExit(f"Nguồn không tồn tại: {source_id}")

    mcp = get_source_mcp()
    try:
        with use_scope(engine_manager, sources):
            await mcp.run_stdio_async()
    finally:
        await engine_manager.aclose_all()


def _main() -> None:
    import os

    source_id = os.environ.get("SAG_MCP_SOURCE_ID", "").strip() or None
    asyncio.run(serve_stdio(source_id))


if __name__ == "__main__":
    _main()
