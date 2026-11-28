"""Search outages must survive the runtime's recoverable tool-error boundary."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sag_agent import AgentEvent, CancellationToken, EventType
from sag_agent.tools import ToolContext as RuntimeToolContext
from sag_api.core.errors import ServiceUnavailableError
from sag_api.enums import MessageStatus
from sag_api.services import agent_service
from sag_api.services.agent_domain import AskPlan
from sag_api.tools import Tool, ToolContext, ToolMeta, ToolRegistry, ToolResult

PRIVATE_PROVIDER_DETAIL = "https://index.invalid/?api_key=unit-test-private-detail"
ANSWER = "Frontend dùng Next.js [1]."


class SearchWithTemporaryFailure(Tool):
    meta = ToolMeta(name="search_context", description="Search verified project evidence")

    def __init__(self):
        self.calls = 0

    async def invoke(self, args, ctx):
        self.calls += 1
        if self.calls == 1:
            raise ServiceUnavailableError(f"Provider unavailable: {PRIVATE_PROVIDER_DETAIL}")
        return ToolResult(
            content=ANSWER,
            citations=[
                {
                    "n": 1,
                    "chunk_id": "unit-1",
                    "source_id": "source-1",
                    "document_id": "document-1",
                    "document_version_id": "version-1",
                    "page_from": 1,
                    "page_to": 1,
                    "anchor": "framework",
                }
            ],
            data={"section_count": 1},
        )


def runtime_tool_context():
    return RuntimeToolContext(
        run_id="run-1",
        tool_call_id="call-1",
        data=None,
        cancellation=CancellationToken(),
        _on_progress=AsyncMock(),
    )


class CompletingRuntime:
    """Model the runtime continuing after a failed tool, using real SDK events."""

    def __init__(self, *, retry=False):
        self.retry = retry
        self.host_context = None

    def run(self, definition, *, context, **kwargs):
        self.host_context = context
        search = next(tool for tool in definition.tools if tool.spec.name == "search_context")

        async def events():
            sequence = 0

            def event(kind, payload):
                nonlocal sequence
                sequence += 1
                return AgentEvent(
                    type=kind,
                    run_id="run-1",
                    sequence=sequence,
                    timestamp=datetime.now(UTC),
                    payload=payload,
                )

            yield event(EventType.RUN_STARTED, {})
            for attempt in range(2 if self.retry else 1):
                call_id = f"call-{attempt}"
                yield event(
                    EventType.TOOL_STARTED,
                    {"name": "search_context", "tool_call_id": call_id, "arguments": {"query": "framework"}},
                )
                try:
                    result = await search.execute({"query": "framework"}, runtime_tool_context())
                except ServiceUnavailableError as error:
                    yield event(
                        EventType.TOOL_FAILED,
                        {"name": "search_context", "tool_call_id": call_id, "error": {"message": str(error)}},
                    )
                else:
                    yield event(
                        EventType.TOOL_COMPLETED,
                        {"name": "search_context", "tool_call_id": call_id, "details": result.details},
                    )
            yield event(EventType.RUN_COMPLETED, {"output": ANSWER})

        return events()


@asynccontextmanager
async def session_factory():
    yield None


async def run_host(monkeypatch, *, retry=False):
    source = SimpleNamespace(id="source-1", name="Frontend", sag_source_config_id="config-1")
    monkeypatch.setattr(agent_service, "resolve_sources", AsyncMock(return_value=[source]))
    monkeypatch.setattr(agent_service, "_initial_tool_choice", AsyncMock(return_value="required"))
    persist = AsyncMock(return_value="message-1")
    monkeypatch.setattr(agent_service, "persist_answer", persist)
    registry = ToolRegistry()
    registry.register(SearchWithTemporaryFailure())
    runtime = CompletingRuntime(retry=retry)
    plan = AskPlan(
        query="Frontend dùng framework nào?",
        source_ids=[source.id],
        messages=[{"role": "user", "content": "Frontend dùng framework nào?"}],
    )
    events = [
        event
        async for event in agent_service.generate_stream(
            session_factory,
            plan=plan,
            agent=SimpleNamespace(id="agent-1", name="Test agent", persona={}, is_default=False),
            thread_id="thread-1",
            engine_manager=SimpleNamespace(),
            llm=SimpleNamespace(configured=True),
            tool_registry=registry,
            runtime=runtime,
            knowledge_only=True,
        )
    ]
    return events, persist, runtime


@pytest.mark.asyncio
async def test_search_outage_emits_and_persists_failed_run_instead_of_no_answer(monkeypatch):
    events, persist, _ = await run_host(monkeypatch)

    assert not any(event.type == EventType.RUN_COMPLETED for event in events)
    failures = [event for event in events if event.type == EventType.RUN_FAILED]
    assert len(failures) == 1
    error = failures[0].data["payload"]["error"]
    assert error["code"] == "search_unavailable"
    assert error["retryable"] is True
    assert not any(event.data.get("payload", {}).get("answer_status") == "no_answer" for event in events)
    assert PRIVATE_PROVIDER_DETAIL not in repr([event.data for event in events])
    persist.assert_awaited_once()
    assert persist.await_args.kwargs["status"] == MessageStatus.FAILED
    assert persist.await_args.kwargs["error"]["code"] == "search_unavailable"
    assert persist.await_args.args[2] == ""
    assert PRIVATE_PROVIDER_DETAIL not in repr(persist.await_args)


@pytest.mark.asyncio
async def test_search_adapter_marks_outage_and_sanitizes_runtime_facing_error():
    host_context = ToolContext(engine_manager=SimpleNamespace())
    adapted = agent_service._adapt_tool(SearchWithTemporaryFailure(), host_context, [])

    with pytest.raises(ServiceUnavailableError) as failure:
        await adapted.execute({"query": "framework"}, runtime_tool_context())

    assert PRIVATE_PROVIDER_DETAIL not in str(failure.value)
    assert host_context.search_context_unavailable is True


@pytest.mark.asyncio
async def test_successful_search_retry_clears_outage_and_allows_cited_answer(monkeypatch):
    events, persist, runtime = await run_host(monkeypatch, retry=True)

    assert runtime.host_context.search_context_unavailable is False
    assert not any(event.type == EventType.RUN_FAILED for event in events)
    completed = [event for event in events if event.type == EventType.RUN_COMPLETED]
    assert len(completed) == 1
    payload = completed[0].data["payload"]
    assert payload["answer_status"] == "answered"
    assert payload["output"] == ANSWER
    assert payload["citations"][0]["document_id"] == "document-1"
    assert PRIVATE_PROVIDER_DETAIL not in repr([event.data for event in events])
    assert persist.await_args.kwargs.get("status", MessageStatus.OK) == MessageStatus.OK
