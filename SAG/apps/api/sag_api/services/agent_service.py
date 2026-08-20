"""Host adapters connecting SAG Agent Core to the knowledge application."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Awaitable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import async_sessionmaker

from sag_agent import (
    Agent as RuntimeAgent,
)
from sag_agent import (
    AgentEvent as RuntimeEvent,
)
from sag_agent import (
    AgentRuntime,
    AgentTool,
    EventType,
    ToolExecutionMode,
    ToolRisk,
    ToolSpec,
)
from sag_agent import (
    ToolResult as RuntimeToolResult,
)
from sag_api.generation import LLMClient, build_prompt_preview
from sag_api.sag import EngineManager, SourceGraphInfo
from sag_api.services.agent_domain import (
    AskPlan,
    persist_answer,
    resolve_mcp_specs,
    resolve_sources,
)
from sag_api.tools import ToolContext as HostToolContext
from sag_api.tools import ToolRegistry
from sag_api.tools.builtin import WebSearchTool
from sag_api.tools.mcp import open_agent_mcp_tools

AGENT_MAX_STEPS = 4

_KNOWLEDGE_TOOLS = ["search_context", "get_entity"]
_WEB_TOOLS = ["web_search", "open_webpage"]
_ALWAYS_TOOLS = ["get_time"]
_TOOL_LABELS = {
    "search_context": "Tra cứu kho tri thức",
    "get_entity": "Truy vấn thực thể",
    "get_time": "Truy vấn thời gian",
    "web_search": "Tìm kiếm trực tuyến",
    "open_webpage": "Mở trang web",
}

_DIRECT_INTENT = re.compile(
    r"^(?:(?:xin )?chào(?: bạn)?|alô|hi|hello|hey|"
    r"chào buổi sáng|chào buổi chiều|chào buổi tối|(?:bạn )?có đó không|bạn có ở đó không|cảm ơn(?: bạn)?|cám ơn(?: bạn)?|"
    r"tạm biệt|bye bye|bạn là ai|bạn tên gì|tên bạn là gì|who are you|"
    r"what(?:'|’)s your name)[!?.,\s]*$",
    re.IGNORECASE,
)
_DIRECT_RESPONSE_HINT = re.compile(
    r"(?:dịch|viết lại|trau chuốt|viết tiếp|sửa lỗi|đặt tên|viết một bài|viết một đoạn|tạo bài viết|brainstorm)"
    r"|(?:tóm tắt|tổng kết)(?:sau|đoạn này|bài này|tôi cung cấp)",
    re.IGNORECASE,
)
_CLARIFICATION_FIRST_INTENT = re.compile(
    r"^(?:(?:xin|giúp tôi|làm ơn|có thể|please)\s*)?"
    r"(?:phân tích|tóm tắt|giới thiệu|đề xuất|so sánh|lập kế hoạch|xem thử|nói về|tra cứu|analyse|analyze|summarize|"
    r"recommend|compare|plan)(?:một chút)?[!?.,\s]*$"
    r"|^(?:(?:gần đây|bây giờ|hiện tại|cái này|cái kia|hôm qua|tuần trước|tháng trước|năm ngoái|thời gian qua(?:một khoảng thời gian)?)?"
    r"(?:thế nào|làm sao|làm thế nào|như thế nào|như nào)[!?.,\s]*)$",
    re.IGNORECASE,
)
_SIMPLE_ARITHMETIC = re.compile(r"^[\d\s+\-*/().=?]+$")
_TIME_INTENT = re.compile(
    r"(?:bây giờ|hôm nay|hiện tại).{0,6}(?:mấy giờ|thời gian|ngày|thứ mấy)|(?:múi giờ|timezone|what time|current time)",
    re.IGNORECASE,
)
_RELATIVE_TIME_INTENT = re.compile(
    r"(?:trong|gần)\s*(?:\d+|vài)\s*(?:ngày|tuần|tháng|quý|năm)"
    r"|(?:hôm qua|hôm trước|ngày mai|ngày kia|tuần trước|tuần này|tuần sau|tháng trước|tháng này|tháng sau|năm ngoái|năm nay|năm sau)"
    r"|\b(?:yesterday|tomorrow)\b"
    r"|\b(?:last|past|next)\s+(?:(?:\d+|one|two|three|several|few)\s+)?"
    r"(?:days?|weeks?|months?|quarters?|years?)\b",
    re.IGNORECASE,
)
_TIME_SENSITIVE_INTENT = re.compile(
    r"(?:gần đây|mới nhất|thời gian gần đây|hiện tại|đang áp dụng|trực tiếp|đến thời điểm|hôm nay|tuần này|tháng này|năm nay|vừa rồi|"
    r"latest|recent|current|today|this (?:week|month|year)|as of)"
    r".{0,40}(?:tin tức|cập nhật|phiên bản|phát hành|giá|thị trường|thời tiết|xu hướng|phát triển|chính sách|quy định|lịch thi đấu|lịch trình|trạng thái|thay đổi|"
    r"news|updates?|versions?|releases?|price|weather|trends?|policy|schedule|status|changes?)",
    re.IGNORECASE,
)
_EXPLICIT_RESEARCH_INTENT = re.compile(
    r"(?:tài liệu|kho tri thức|văn bản|nguồn|trích dẫn|bằng chứng|dữ liệu|thống kê|nghiên cứu|báo cáo|khảo sát|tìm|tìm kiếm|truy vấn|xác minh|"
    r"sources?|citations?|evidence|data|statistics|research|report|find|search|look up|verify)",
    re.IGNORECASE,
)
_RESEARCH_TOOL = re.compile(
    r"(?:search|find|query|browse|web|news|weather|price|knowledge|document|lookup|read|"
    r"truy vấn|tìm kiếm|tra cứu|tin tức|thời tiết|thị trường|tri thức|tài liệu|trang web)",
    re.IGNORECASE,
)
# Numeric knowledge markers are plain ``[n]``. A numeric Markdown link label
# (``[n](https://...)``) is an external link and must not be stripped.
_CITATION_REFERENCE = re.compile(r"\[(\d+)](?!\()")
_ANSWER_URL = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
_MAX_EXTERNAL_CITATIONS = 12
_URL_TRAILING_PUNCTUATION = ".,;:!?)]}"


def _tool_supports_research(tool: AgentTool) -> bool:
    if tool.spec.name in {"search_context", "get_entity"}:
        return True
    return bool(_RESEARCH_TOOL.search(f"{tool.spec.name} {tool.spec.description}"))


def _named_tool_choice(name: str) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name}}


def _initial_tool_choice(
    query: str,
    tools: tuple[AgentTool, ...],
    *,
    knowledge_only: bool,
    scoped: bool,
) -> str | dict[str, Any]:
    """Apply only high-confidence first-turn routing.

    High-confidence social requests, complete arithmetic, and requests that
    require clarification cannot use tools on the first turn. Explicit temporal
    requests establish a dynamic time anchor; knowledge scopes and research
    requests still receive deterministic grounding.
    """

    if not tools:
        return "auto"
    names = {tool.spec.name for tool in tools}
    normalized = " ".join(query.strip().split())
    if _DIRECT_INTENT.fullmatch(normalized):
        return "none"
    if _CLARIFICATION_FIRST_INTENT.fullmatch(normalized) or _SIMPLE_ARITHMETIC.fullmatch(normalized):
        return "none"
    if not normalized or _DIRECT_RESPONSE_HINT.search(normalized):
        return "auto"

    temporal = bool(
        _TIME_INTENT.search(normalized)
        or _RELATIVE_TIME_INTENT.search(normalized)
        or _TIME_SENSITIVE_INTENT.search(normalized)
    )
    if temporal and "get_time" in names:
        return _named_tool_choice("get_time")
    if (knowledge_only or scoped) and "search_context" in names:
        return _named_tool_choice("search_context")
    if _EXPLICIT_RESEARCH_INTENT.search(normalized) and any(_tool_supports_research(tool) for tool in tools):
        return "required"
    if temporal and any(_tool_supports_research(tool) for tool in tools):
        return "required"
    return "auto"


def _append_current_scene(
    messages: list[dict[str, Any]],
    notes: list[str],
) -> list[dict[str, Any]]:
    """Keep dynamic run context inside the single system role."""

    if not notes:
        return list(messages)
    result = [dict(message) for message in messages]
    scene = "### Bối cảnh hiện tại\n" + "\n".join(f"- {note}" for note in notes)
    for index, message in enumerate(result):
        if message.get("role") == "system":
            result[index] = {**message, "content": f"{message.get('content', '')}\n\n{scene}"}
            break
    else:
        result.insert(0, {"role": "system", "content": scene})
    return result


def _finalize_answer_citations(
    answer: str,
    citations: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Return one canonical answer whose numeric citations are all clickable.

    Model-invented or non-traceable numbers are removed. When grounded search
    evidence exists but the model forgot claim markers, return a small set of
    explicitly non-claim-mapped structured sources without modifying the answer.
    """

    traceable: dict[int, dict[str, Any]] = {}
    for citation in citations:
        number = citation.get("n")
        if (
            isinstance(number, int)
            and not isinstance(number, bool)
            and citation.get("chunk_id")
            and citation.get("source_id")
        ):
            traceable[number] = citation

    used: list[int] = []

    def replace_reference(match: re.Match[str]) -> str:
        number = int(match.group(1))
        if number not in traceable:
            return ""
        if number not in used:
            used.append(number)
        return match.group(0)

    canonical = _CITATION_REFERENCE.sub(replace_reference, answer).strip()
    canonical = re.sub(r"[ \t]+([，。！？；：,.!?;:])", r"\1", canonical)
    explicitly_mapped = set(used)
    if canonical and traceable and not used:
        used = list(traceable)[:3]
    normalized = []
    for number in used:
        citation = dict(traceable[number])
        mapped = number in explicitly_mapped
        citation.update(
            {
                "kind": "internal",
                "mapped": mapped,
                "claim_level": "claim" if mapped else "run",
            }
        )
        normalized.append(citation)
    return canonical, normalized


def _normalize_external_url(value: Any) -> str | None:
    """Return a safe, canonical HTTP(S) URL, or ``None``.

    External citations are persisted as clickable links, so accepting a broad
    string prefix is not sufficient. Credentials, whitespace, malformed ports,
    and non-web schemes are rejected before anything reaches the API payload.
    Fragments are dropped for stable de-duplication.
    """

    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw or any(character.isspace() for character in raw):
        return None
    try:
        parsed = urlsplit(raw)
        _ = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    netloc = parsed.hostname.lower()
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "", parsed.query, ""))


def _build_external_citations(
    answer: str,
    references: list[dict[str, Any]],
    *,
    start_n: int = 1,
) -> list[dict[str, Any]]:
    """Build bounded, de-duplicated citations from this run's tool artifacts.

    A URL is claim-mapped only when the model placed that observed URL in its
    answer. Other observed sources remain explicitly run-level so the citation
    UI can present them without pretending they support a particular claim.
    """

    answer_urls = {
        normalized
        for match in _ANSWER_URL.findall(answer)
        if (normalized := _normalize_external_url(match.rstrip(_URL_TRAILING_PUNCTUATION)))
    }
    citations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for reference in references:
        url = _normalize_external_url(reference.get("url"))
        if url is None or url in seen:
            continue
        seen.add(url)
        raw_title = reference.get("title")
        title = " ".join(str(raw_title or "").split())[:200]
        raw_source = reference.get("source")
        source = " ".join(str(raw_source or "").split())[:120]
        raw_snippet = next(
            (
                reference.get(key)
                for key in ("snippet", "summary", "description", "content")
                if isinstance(reference.get(key), str) and str(reference.get(key)).strip()
            ),
            "",
        )
        snippet = " ".join(str(raw_snippet or "").split())[:720]
        summary = snippet[:160].strip()
        if len(snippet) > 160:
            sentence_end = next(
                (index + 1 for index, character in enumerate(summary) if index >= 5 and character in "。！？.!?"),
                0,
            )
            if sentence_end:
                summary = summary[:sentence_end].strip()
            elif not summary.endswith(("。", "！", "？", ".", "!", "?")):
                summary = summary.rstrip("…") + "…"
        hostname = urlsplit(url).hostname or ""
        mapped = url in answer_urls
        citation = {
            "kind": "external",
            "n": start_n + len(citations),
            "url": url,
            "title": title or hostname or url,
            "source": source or hostname,
            "mapped": mapped,
            "claim_level": "claim" if mapped else "run",
        }
        if snippet:
            citation["summary"] = summary
            citation["snippet"] = snippet
        citations.append(citation)
        if len(citations) >= _MAX_EXTERNAL_CITATIONS:
            break
    return citations


@dataclass(frozen=True, slots=True)
class AgentStreamEvent:
    """Versioned runtime event ready for an HTTP/WebSocket transport."""

    type: str
    data: dict[str, Any]


def _enabled_tool_names(agent, *, has_sources: bool = False, knowledge_only: bool = False) -> list[str]:
    persona = agent.persona or {}
    names = persona.get("tools")
    configured = [name for name in names if isinstance(name, str)] if isinstance(names, list) else []
    # Truy vấn là khả năng cơ bản do việc gắn nguồn mang lại, không nên phụ thuộc vào việc persona có tình cờ lưu trường tools hay không.
    knowledge_tools = _KNOWLEDGE_TOOLS if has_sources or getattr(agent, "is_default", False) else []
    if knowledge_only:
        # Keep local, read-only system utilities available while excluding
        # configured/MCP tools and model-knowledge fallbacks.
        return list(dict.fromkeys([*_ALWAYS_TOOLS, *knowledge_tools]))
    return list(dict.fromkeys([*_ALWAYS_TOOLS, *knowledge_tools, *_WEB_TOOLS, *configured]))


def _adapt_tool(host_tool, host_context: HostToolContext, citations: list[dict]) -> AgentTool:
    async def execute(
        arguments: Mapping[str, Any],
        context,
    ) -> RuntimeToolResult:
        context.cancellation.raise_if_cancelled()
        host_context.citation_offset = len(citations)
        result = await host_tool.invoke(dict(arguments), host_context)
        citations.extend(result.citations)
        count = int(result.data.get("section_count") or len(result.citations) or 0)
        raw_external_references = result.data.get("external_references")
        external_references = (
            [dict(reference) for reference in raw_external_references if isinstance(reference, Mapping)]
            if isinstance(raw_external_references, list)
            else []
        )
        matches = [
            {
                key: citation.get(key)
                for key in (
                    "n",
                    "chunk_id",
                    "heading",
                    "snippet",
                    "score",
                    "source_id",
                    "source_name",
                    "event_refs",
                )
            }
            for citation in result.citations[:6]
        ]
        details: dict[str, Any] = {"count": count}
        if host_tool.meta.name in _KNOWLEDGE_TOOLS:
            details["scope"] = "knowledge"
            details["sources"] = [
                {"id": source.id, "name": source.name} for source in host_context.sources
            ]
        elif host_tool.meta.name in _WEB_TOOLS:
            details["scope"] = "internet"
        if matches:
            details["matches"] = matches
        elif result.content:
            details["output_preview"] = result.content[:800]
        artifacts: dict[str, Any] = {"citations": result.citations}
        if external_references:
            details["external_references"] = external_references[:6]
            artifacts["external_references"] = external_references
        sections = result.data.get("sections")
        if host_tool.meta.name == "search_context" and isinstance(sections, list) and sections:
            sources_by_config = {source.sag_source_config_id: source for source in host_context.sources}
            graph_resolved = "_graph" in result.data
            graph = result.data.get("_graph")
            if not graph_resolved:
                graph_fn: Any = getattr(host_context.engine_manager, "graph_for_sections", None)
                if callable(graph_fn):
                    res = graph_fn(
                        sections,
                        sources_by_config,
                        event_limit=max(12, len(sections)),
                        entity_limit=36,
                    )
                    if isinstance(res, Awaitable):
                        graph = await res
                    else:
                        graph = res
            graph = graph or SourceGraphInfo()
            event_nodes = []
            for item in graph.events:
                source = sources_by_config.get(item.source_config_id)
                event_nodes.append(
                    {
                        "id": item.id,
                        "kind": "event",
                        "source_id": source.id if source else None,
                        "label": item.title,
                        "description": item.summary,
                        "category": item.category,
                        "chunk_id": item.chunk_id,
                        "importance": item.score,
                        "citation_numbers": [
                            citation.get("n")
                            for citation in result.citations
                            if citation.get("chunk_id") == item.chunk_id
                            and citation.get("source_id") == (source.id if source else None)
                        ],
                    }
                )
            event_source_by_id = {str(event["id"]): str(event["source_id"] or "") for event in event_nodes}
            artifacts["universe_activation"] = {
                "query": str(arguments.get("query") or ""),
                "nodes": [
                    *event_nodes,
                    *[
                        {
                            "id": item.id,
                            "kind": "entity",
                            "source_id": next(
                                (
                                    event["source_id"]
                                    for event in event_nodes
                                    if any(
                                        association.event_id == event["id"] and association.entity_id == item.id
                                        for association in graph.associations
                                    )
                                ),
                                None,
                            ),
                            "label": item.name,
                            "description": item.description,
                            "category": item.type,
                            "importance": min(1.0, 0.2 + item.heat / 10.0),
                        }
                        for item in graph.entities
                    ],
                ],
                "relations": [
                    {
                        "source_id": event_source_by_id.get(item.event_id, ""),
                        "from_id": item.event_id,
                        "to_id": item.entity_id,
                        "kind": "mentions",
                        "weight": item.weight,
                        "description": item.description,
                    }
                    for item in graph.associations
                ],
            }
        return RuntimeToolResult(
            content=result.content,
            details=details,
            artifacts=artifacts,
        )

    name = host_tool.meta.name
    return AgentTool(
        spec=ToolSpec(
            name=name,
            label=_TOOL_LABELS.get(name, name),
            description=host_tool.meta.description,
            parameters=host_tool.meta.parameters,
            risk=ToolRisk.READ_ONLY,
            # Citation numbering currently depends on source-order execution.
            execution_mode=ToolExecutionMode.SEQUENTIAL,
        ),
        executor=execute,
    )


def _stream_event(event: RuntimeEvent, *, payload: Mapping[str, Any] | None = None) -> AgentStreamEvent:
    data = event.to_dict()
    if payload is not None:
        data["payload"] = dict(payload)
    return AgentStreamEvent(type=event.type.value, data=data)


async def generate_stream(
    session_factory: async_sessionmaker,
    *,
    plan: AskPlan,
    agent,
    thread_id: str | None,
    engine_manager: EngineManager,
    llm: LLMClient,
    tool_registry: ToolRegistry,
    runtime: AgentRuntime | None = None,
    knowledge_only: bool = False,
) -> AsyncIterator[AgentStreamEvent]:
    """Run one request and expose the SDK event contract to the host transport."""

    from sag_api.core.config import settings

    owns_runtime = runtime is None
    active_runtime = runtime or AgentRuntime()
    if owns_runtime:
        await active_runtime.start()

    citations = list(plan.citations)
    external_references: list[dict[str, Any]] = []
    external_reference_urls: set[str] = set()
    trace: list[dict] = []
    tool_inputs: dict[str, dict[str, Any]] = {}
    handle = None
    terminal = False

    async with session_factory() as session:
        sources = await resolve_sources(session, agent, plan.source_ids)
        mcp_specs = [] if knowledge_only else await resolve_mcp_specs(session, agent)
    host_context = HostToolContext(
        engine_manager=engine_manager,
        sources=sources,
        persona=agent.persona or {},
        agent=agent,
    )

    try:
        async with open_agent_mcp_tools(mcp_specs) as mcp_bundle:
            names = _enabled_tool_names(
                agent,
                has_sources=bool(sources),
                knowledge_only=knowledge_only,
            )
            host_tools = [tool_registry.get(name) for name in names if tool_registry.has(name)]
            host_tools.extend(mcp_bundle.tools)
            tools = tuple(_adapt_tool(tool, host_context, citations) for tool in host_tools)
            scene_notes: list[str] = []
            if mcp_bundle.warnings:
                unavailable = ", ".join(warning.get("server", "MCP") for warning in mcp_bundle.warnings)
                scene_notes.append(
                    f"Một số công cụ được gắn không khả dụng ở lượt này: {unavailable}. Nếu nhiệm vụ hiện tại phụ thuộc vào các khả năng này, "
                    "phải nói rõ là tạm thời không thể xác minh, không được dùng trí nhớ của model thay cho dữ kiện thời gian thực hoặc bên ngoài."
                )
            if knowledge_only:
                offline_rule = (
                    "Lượt này đã tắt kết nối mạng, chỉ được dùng kho tri thức cục bộ đã gắn và các công cụ hệ thống cần thiết; "
                    "không được gọi hoặc tuyên bố dùng trang web, MCP hoặc tìm kiếm ngoài khác. Tắt mạng không có nghĩa mỗi lượt đều phải truy vấn; "
                    "chỉ khi câu trả lời phụ thuộc vào dữ kiện tri thức, phải gọi search_context trước, chỉ dựa vào bằng chứng nguyên văn công cụ trả về "
                    "để trả lời và giữ lại trích dẫn; "
                    "khi bằng chứng không đủ phải nói rõ kho tri thức không có đủ căn cứ, không được dùng kiến thức riêng của model để bổ sung."
                )
                if "search_context" not in names:
                    offline_rule = (
                        "Lượt này đã tắt kết nối mạng, và Agent hiện tại không có kho tri thức cục bộ nào để truy vấn; chỉ được dùng các công cụ hệ thống cần thiết. "
                        "Không được gọi hoặc tuyên bố dùng trang web, MCP, tìm kiếm ngoài khác hoặc kiến thức riêng của model để bổ sung dữ kiện tri thức; "
                        "khi câu hỏi phụ thuộc vào tài liệu bên ngoài hoặc kho tri thức, phải nói rõ hiện tại không có căn cứ nào khả dụng."
                    )
                scene_notes.append(offline_rule)
            elif WebSearchTool.configured():
                scene_notes.append(
                    "Lượt này đã bật kết nối mạng. Mọi câu trả lời phụ thuộc dữ kiện thời gian thực, mới nhất hoặc bên ngoài phải gọi web_search để lấy bằng chứng trang web, "
                    "và giữ lại liên kết nguồn Markdown có thể bấm gần kết luận; search_context chỉ dùng cho kho tri thức cục bộ của người dùng, "
                    "không được dùng nó thay cho tìm kiếm internet. Khi cần thông tin mới nhất, truy vấn phải chứa ngày tuyệt đối và đặt time_range là "
                    "day hoặc week; khi tóm tắt tìm kiếm không đủ để xác minh kết luận chính xác, phải dùng open_webpage mở nguồn đáng tin cậy liên quan nhất. "
                    "Sau khi web_search hoặc open_webpage trả về kết quả thành công, không được nói không thể kết nối mạng, không truy cập được thông tin thời gian thực hoặc trang web; "
                    "nếu kết quả không đủ mới hoặc không đủ hỗ trợ kết luận, chỉ được nói rõ lần tìm kiếm này không tìm thấy kết quả đủ để xác minh và nêu ngày của bằng chứng, "
                    "không được mô tả thiếu bằng chứng như thiếu năng lực của hệ thống."
                )
            if plan.source_ids and sources:
                scene_notes.append(
                    "Người dùng đã dùng @ để giới hạn phạm vi tri thức lượt này là: "
                    + ", ".join(source.name for source in sources)
                    + ". Khi câu hỏi liên quan tài liệu phải gọi search_context trước và chỉ trả lời dựa trên bằng chứng trả về."
                )
            run_messages = _append_current_scene(list(plan.messages), scene_notes)
            # Freeze the actual initial input before the runtime appends model
            # output and tool-result messages. This is the only content the UI
            # may describe as the model's starting context.
            frozen_prompt_preview = build_prompt_preview(run_messages)
            initial_tool_choice = _initial_tool_choice(
                plan.query,
                tools,
                knowledge_only=knowledge_only,
                scoped=bool(plan.source_ids),
            )
            max_turns = max(1, int(getattr(settings, "agent_max_steps", AGENT_MAX_STEPS)))
            definition = RuntimeAgent(
                name=agent.name,
                model=llm,
                tools=tools,
                initial_tool_choice=initial_tool_choice,
                max_turns=max_turns,
                finalize_on_max_turns=True,
                metadata={
                    "agent_id": agent.id,
                    "initial_tool_choice": initial_tool_choice,
                    "web_enabled": not knowledge_only,
                    "knowledge_only": knowledge_only,
                },
            )
            handle = active_runtime.run(
                definition,
                history=run_messages,
                context=host_context,
                metadata={
                    "thread_id": thread_id,
                    "source_ids": [source.id for source in sources],
                    "source_names": [source.name for source in sources],
                    "web_enabled": not knowledge_only,
                    "knowledge_only": knowledge_only,
                },
            )

            async for event in handle:
                payload = event.payload
                output_payload: Mapping[str, Any] = payload

                if event.type == EventType.RUN_STARTED:
                    output_payload = {
                        **payload,
                        "user_message_id": plan.user_message_id,
                        "citations": citations,
                        "sources": [{"id": source.id, "name": source.name} for source in sources],
                        "tools": [tool.spec.name for tool in tools],
                        "tool_warnings": mcp_bundle.warnings,
                        "web_enabled": not knowledge_only,
                        "knowledge_only": knowledge_only,
                    }
                elif event.type in (
                    EventType.TOOL_APPROVAL_REQUIRED,
                    EventType.TOOL_STARTED,
                ):
                    tool_inputs[str(payload.get("tool_call_id") or "")] = {
                        "label": payload.get("label") or payload.get("name"),
                        "arguments": dict(payload.get("arguments") or {}),
                    }
                elif event.type == EventType.TOOL_COMPLETED:
                    details = payload.get("details") or {}
                    artifacts = payload.get("artifacts") or {}
                    observed_references = artifacts.get("external_references")
                    if isinstance(observed_references, list):
                        for reference in observed_references:
                            if not isinstance(reference, Mapping):
                                continue
                            url = reference.get("url")
                            if not isinstance(url, str) or url in external_reference_urls:
                                continue
                            external_reference_urls.add(url)
                            external_references.append(dict(reference))
                    activation = artifacts.get("universe_activation")
                    if isinstance(activation, Mapping):
                        activation_event = event.to_dict()
                        activation_event["type"] = "universe.activation"
                        activation_event["payload"] = dict(activation)
                        yield AgentStreamEvent(
                            type="universe.activation",
                            data=activation_event,
                        )
                    tool_call_id = str(payload.get("tool_call_id") or "")
                    started = tool_inputs.pop(tool_call_id, {})
                    trace.append(
                        {
                            "kind": "tool",
                            "step": event.turn,
                            "name": payload["name"],
                            "label": started.get("label") or payload.get("name"),
                            "arguments": started.get("arguments") or {},
                            "ms": payload.get("duration_ms", 0),
                            "count": details.get("count", 0),
                            "details": details,
                        }
                    )
                elif event.type == EventType.TOOL_FAILED:
                    error = payload.get("error") or {}
                    tool_call_id = str(payload.get("tool_call_id") or "")
                    started = tool_inputs.pop(tool_call_id, {})
                    trace.append(
                        {
                            "kind": "tool",
                            "step": event.turn,
                            "name": payload["name"],
                            "label": started.get("label") or payload.get("label") or payload.get("name"),
                            "arguments": started.get("arguments") or {},
                            "ms": payload.get("duration_ms", 0),
                            "count": 0,
                            "error": error.get("message", "Thực thi công cụ thất bại"),
                        }
                    )
                elif (
                    event.type == EventType.MESSAGE_COMPLETED and payload.get("message", {}).get("role") == "assistant"
                ):
                    duration = int(payload.get("duration_ms") or 0)
                    if payload.get("has_tool_calls"):
                        trace.append({"kind": "thinking", "step": event.turn, "ms": duration})
                    else:
                        trace.append({"kind": "answer", "step": event.turn, "ms": duration})
                elif event.type == EventType.RUN_COMPLETED:
                    canonical_answer, internal_citations = _finalize_answer_citations(
                        str(payload.get("output") or ""),
                        citations,
                    )
                    external_start = (
                        max(
                            (
                                citation["n"]
                                for citation in internal_citations
                                if isinstance(citation.get("n"), int) and not isinstance(citation.get("n"), bool)
                            ),
                            default=0,
                        )
                        + 1
                    )
                    external_citations = _build_external_citations(
                        canonical_answer,
                        external_references,
                        start_n=external_start,
                    )
                    canonical_citations = [*internal_citations, *external_citations]
                    message_id = None
                    if thread_id is not None:
                        message_id = await persist_answer(
                            session_factory,
                            thread_id,
                            canonical_answer,
                            canonical_citations,
                            steps=trace,
                            prompt_preview=frozen_prompt_preview,
                        )
                    output_payload = {
                        **payload,
                        "output": canonical_answer,
                        "message_id": message_id,
                        "citations": canonical_citations,
                        "prompt_preview": frozen_prompt_preview,
                    }
                    terminal = True
                elif event.type in (EventType.RUN_FAILED, EventType.RUN_CANCELLED):
                    terminal = True

                yield _stream_event(event, payload=output_payload)
    finally:
        if handle is not None and not terminal and not handle.done:
            handle.cancel()
            await handle.result()
        if owns_runtime:
            await active_runtime.stop()
