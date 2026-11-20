"""Execution trace stream contract tests for Continuum AI Chat."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sag_agent import AgentEvent, CancellationToken, EventType
from sag_agent.tools import ToolContext as RuntimeToolContext
from sag_api.services import agent_service
from sag_api.services.agent_domain import AskPlan
from sag_api.tools import Tool, ToolMeta, ToolRegistry, ToolResult


class GroundedSearchTool(Tool):
    meta = ToolMeta(name="search_context", description="Search verified project evidence")

    async def invoke(self, args, ctx):
        return ToolResult(
            content="Quy trình bàn giao yêu cầu checklist [1].",
            citations=[
                {
                    "n": 1,
                    "chunk_id": "unit-1",
                    "source_id": "source-1",
                    "document_id": "document-1",
                    "document_version_id": "version-1",
                    "page_from": 2,
                    "page_to": 2,
                    "anchor": "handover-checklist",
                }
            ],
            data={"section_count": 1},
        )


def _runtime_tool_context() -> RuntimeToolContext:
    return RuntimeToolContext(
        run_id="run-trace-1",
        tool_call_id="call-trace-1",
        data=None,
        cancellation=CancellationToken(),
        _on_progress=AsyncMock(),
    )


class TraceSimulatingRuntime:
    """Emits the exact SDK event sequence produced by sag_agent.AgentRuntime."""

    def run(self, definition, *, context, **kwargs):
        search = next(tool for tool in definition.tools if tool.spec.name == "search_context")

        async def events():
            sequence = 0

            def emit(kind: EventType, payload: dict, *, turn: int = 0) -> AgentEvent:
                nonlocal sequence
                sequence += 1
                return AgentEvent(
                    type=kind,
                    run_id="run-trace-1",
                    sequence=sequence,
                    timestamp=datetime.now(UTC),
                    turn=turn,
                    payload=payload,
                )

            yield emit(EventType.RUN_STARTED, {"agent": definition.name})
            yield emit(EventType.TURN_STARTED, {"message_count": 2, "tool_count": 1}, turn=1)
            yield emit(
                EventType.MESSAGE_COMPLETED,
                {
                    "message": {"role": "assistant", "content": ""},
                    "duration_ms": 185,
                    "has_tool_calls": True,
                },
                turn=1,
            )
            # Runtime emits label on TOOL_STARTED, and omits label on TOOL_COMPLETED.
            yield emit(
                EventType.TOOL_STARTED,
                {
                    "tool_call_id": "call-trace-1",
                    "name": "search_context",
                    "label": search.spec.label,
                    "arguments": {"query": "handover checklist"},
                },
                turn=1,
            )
            result = await search.execute({"query": "handover checklist"}, _runtime_tool_context())
            yield emit(
                EventType.TOOL_COMPLETED,
                {
                    "tool_call_id": "call-trace-1",
                    "name": "search_context",
                    "duration_ms": 420,
                    "details": result.details,
                    "artifacts": result.artifacts,
                    "terminate": False,
                },
                turn=1,
            )
            yield emit(EventType.TURN_STARTED, {"message_count": 4, "tool_count": 1}, turn=2)
            # Unverified delta during grounded synthesis must stay held until citation gate finishes.
            yield emit(
                EventType.MESSAGE_DELTA,
                {"role": "assistant", "delta": "Quy trình bàn giao yêu cầu checklist [1] và [S1]."},
                turn=2,
            )
            yield emit(
                EventType.MESSAGE_COMPLETED,
                {
                    "message": {
                        "role": "assistant",
                        "content": "Quy trình bàn giao yêu cầu checklist [1] và [S1].",
                    },
                    "duration_ms": 960,
                    "has_tool_calls": False,
                },
                turn=2,
            )
            yield emit(
                EventType.RUN_COMPLETED,
                {"output": "Quy trình bàn giao yêu cầu checklist [1] và [S1]."},
                turn=2,
            )

        return events()


@asynccontextmanager
async def _session_factory():
    yield None


@pytest.mark.asyncio
async def test_generate_stream_emits_live_execution_trace_labels_session_context_and_completed_steps(
    monkeypatch,
):
    source = SimpleNamespace(id="source-1", name="Handover Docs", sag_source_config_id="cfg-1")
    monkeypatch.setattr(agent_service, "resolve_sources", AsyncMock(return_value=[source]))
    monkeypatch.setattr(agent_service, "_initial_tool_choice", AsyncMock(return_value="required"))
    persist = AsyncMock(return_value="msg-assistant-1")
    monkeypatch.setattr(agent_service, "persist_answer", persist)

    registry = ToolRegistry()
    registry.register(GroundedSearchTool())

    plan = AskPlan(
        query="Quy trình bàn giao gồm gì?",
        source_ids=[source.id],
        messages=[{"role": "user", "content": "Quy trình bàn giao gồm gì?"}],
        user_message_id="msg-user-1",
        session_audit_trace={
            "mode": "on",
            "latency_ms": 37,
            "selected_groups": [{"citation_tag": "S1"}],
        },
        session_citations=[
            {
                "ref": "S1",
                "source": "selected_group",
                "group_id": "grp-1",
                "turn_ids": ["msg-prev-1"],
                "summary_excerpt": "Đã thống nhất checklist bàn giao ở lượt trước.",
            }
        ],
    )

    events = [
        event
        async for event in agent_service.generate_stream(
            _session_factory,
            plan=plan,
            agent=SimpleNamespace(id="agent-1", name="Continuum Agent", persona={}, is_default=True),
            thread_id="thread-1",
            engine_manager=SimpleNamespace(),
            llm=SimpleNamespace(configured=True),
            tool_registry=registry,
            runtime=TraceSimulatingRuntime(),
            knowledge_only=True,
        )
    ]

    started = next(ev for ev in events if ev.type == EventType.RUN_STARTED.value)
    assert started.data["payload"]["session_context"] == {"mode": "on", "ms": 37}

    tool_started = next(ev for ev in events if ev.type == EventType.TOOL_STARTED.value)
    assert tool_started.data["payload"]["label"] == "Tìm trong kho tri thức"

    tool_completed = next(ev for ev in events if ev.type == EventType.TOOL_COMPLETED.value)
    assert tool_completed.data["payload"]["label"] == "Tìm trong kho tri thức"
    assert tool_completed.data["payload"]["duration_ms"] == 420
    assert tool_completed.data["payload"]["details"]["count"] == 1

    # Grounded synthesis still emits only the single validated delta right before run.completed.
    deltas = [ev for ev in events if ev.type == EventType.MESSAGE_DELTA.value]
    assert len(deltas) == 1

    completed = next(ev for ev in events if ev.type == EventType.RUN_COMPLETED.value)
    completed_steps = completed.data["payload"]["steps"]
    assert [step["kind"] for step in completed_steps] == [
        "session_context",
        "thinking",
        "tool",
        "answer",
    ]
    assert completed_steps[0]["session_citations"][0]["ref"] == "S1"
    assert completed_steps[0]["session_citations"][0]["mapped"] is True
    assert persist.await_args.kwargs["steps"] == completed_steps


class ToolFailureSimulatingRuntime:
    """Emits TOOL_STARTED without explicit label followed by TOOL_FAILED."""

    def run(self, definition, *, context, **kwargs):
        async def events():
            yield AgentEvent(
                type=EventType.RUN_STARTED,
                run_id="run-fail-1",
                sequence=1,
                timestamp=datetime.now(UTC),
                payload={"agent": definition.name},
            )
            yield AgentEvent(
                type=EventType.TOOL_STARTED,
                run_id="run-fail-1",
                sequence=2,
                timestamp=datetime.now(UTC),
                turn=1,
                payload={
                    "tool_call_id": "call-fail-1",
                    "name": "get_entity",
                    "arguments": {"name": "API"},
                },
            )
            yield AgentEvent(
                type=EventType.TOOL_FAILED,
                run_id="run-fail-1",
                sequence=3,
                timestamp=datetime.now(UTC),
                turn=1,
                payload={
                    "tool_call_id": "call-fail-1",
                    "name": "get_entity",
                    "duration_ms": 52,
                    "error": {"message": "Entity lookup timeout"},
                },
            )
            yield AgentEvent(
                type=EventType.RUN_COMPLETED,
                run_id="run-fail-1",
                sequence=4,
                timestamp=datetime.now(UTC),
                turn=1,
                payload={"output": "Chưa thể tra cứu thực thể."},
            )

        return events()


@pytest.mark.asyncio
async def test_tool_failed_propagates_canonical_label_and_omits_session_context_when_absent(
    monkeypatch,
):
    monkeypatch.setattr(agent_service, "resolve_sources", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent_service, "resolve_mcp_specs", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent_service, "_initial_tool_choice", AsyncMock(return_value="auto"))
    persist = AsyncMock(return_value="msg-assistant-2")
    monkeypatch.setattr(agent_service, "persist_answer", persist)

    plan = AskPlan(
        query="Tra cứu thực thể API",
        source_ids=[],
        messages=[{"role": "user", "content": "Tra cứu thực thể API"}],
        user_message_id="msg-user-2",
        session_audit_trace=None,
    )

    events = [
        event
        async for event in agent_service.generate_stream(
            _session_factory,
            plan=plan,
            agent=SimpleNamespace(id="agent-1", name="Continuum Agent", persona={}, is_default=True),
            thread_id="thread-2",
            engine_manager=SimpleNamespace(),
            llm=SimpleNamespace(configured=True),
            tool_registry=ToolRegistry(),
            runtime=ToolFailureSimulatingRuntime(),
            knowledge_only=False,
        )
    ]

    started = next(ev for ev in events if ev.type == EventType.RUN_STARTED.value)
    assert "session_context" not in started.data["payload"]

    tool_started = next(ev for ev in events if ev.type == EventType.TOOL_STARTED.value)
    assert tool_started.data["payload"]["label"] == "Tra cứu thực thể"

    tool_failed = next(ev for ev in events if ev.type == EventType.TOOL_FAILED.value)
    assert tool_failed.data["payload"]["label"] == "Tra cứu thực thể"
    assert tool_failed.data["payload"]["duration_ms"] == 52
