"""Unit and generate_stream regression tests for Continuum question scope routing."""

from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, create_autospec, patch

from sag_agent import AgentEvent, EventType
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.generation import LLMClient
from sag_api.services import agent_service
from sag_api.services.agent_domain import AskPlan
from sag_api.services.question_scope import classify_question_scope
from sag_api.tools import Tool, ToolMeta, ToolRegistry, ToolResult


class _DummyTool(Tool):
    def __init__(self, name: str, description: str) -> None:
        self.meta = ToolMeta(name=name, description=description)

    async def invoke(self, args, ctx) -> ToolResult:
        del args, ctx
        return ToolResult(content="ok", data={"section_count": 0})


class _CapturingRuntime:
    def __init__(self, *, output: str = "Câu trả lời trực tiếp.") -> None:
        self.output = output
        self.definition = None
        self.history = None

    def run(self, definition, *, history, **kwargs):
        del kwargs
        self.definition = definition
        self.history = list(history)

        async def _events():
            now = datetime.now(UTC)
            yield AgentEvent(type=EventType.RUN_STARTED, run_id="run-1", sequence=1, timestamp=now, payload={})
            yield AgentEvent(
                type=EventType.MESSAGE_DELTA,
                run_id="run-1",
                sequence=2,
                timestamp=now,
                payload={"role": "assistant", "delta": self.output},
            )
            yield AgentEvent(
                type=EventType.RUN_COMPLETED,
                run_id="run-1",
                sequence=3,
                timestamp=now,
                payload={"output": self.output},
            )

        return _events()


@asynccontextmanager
async def _noop_session_factory():
    yield None


def _make_principal() -> VerifiedPrincipal:
    return VerifiedPrincipal(
        subject="user-1",
        organization_id="org-1",
        allowed_project_ids=frozenset({"proj-1"}),
        issuer="continuum-be",
        key_id="kid-1",
        token_id="jti-1",
        issued_at=1_700_000_000,
        expires_at=1_700_000_900,
        tenant_id="org-1",
    )


def _make_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_DummyTool("search_context", "Tìm trong kho tri thức dự án"))
    reg.register(_DummyTool("get_entity", "Tra cứu thực thể tri thức"))
    reg.register(_DummyTool("get_time", "Tra cứu thời gian hiện tại"))
    reg.register(_DummyTool("web_search", "Tìm kiếm Internet tin tức thời tiết mới nhất"))
    reg.register(_DummyTool("open_webpage", "Mở trang web"))
    return reg


class QuestionScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_known_scopes(self) -> None:
        for scope in ("general", "project", "application"):
            llm = SimpleNamespace(complete=AsyncMock(return_value=f'{{"scope":"{scope}"}}'))
            self.assertEqual(await classify_question_scope(llm, "question"), scope)

    async def test_markdown_fences_and_think_blocks_parsed(self) -> None:
        fenced = '```json\n{"scope": "general"}\n```'
        llm_fenced = SimpleNamespace(complete=AsyncMock(return_value=fenced))
        self.assertEqual(await classify_question_scope(llm_fenced, "Explain REST API"), "general")

        reasoned = '<think>Checking scope...</think>\n```json\n{"scope": "application"}\n```'
        llm_reasoned = SimpleNamespace(complete=AsyncMock(return_value=reasoned))
        self.assertEqual(await classify_question_scope(llm_reasoned, "My overdue tasks"), "application")

    async def test_prior_turns_included_in_classifier_context(self) -> None:
        complete_mock = AsyncMock(return_value='{"scope":"project"}')
        llm = SimpleNamespace(complete=complete_mock)
        prior_turns = [
            {"role": "user", "content": "Tài liệu kiến trúc hệ thống quy định gì về RBAC?"},
            {"role": "assistant", "content": "Tài liệu quy định 4 vai trò chính [1]."},
        ]
        result = await classify_question_scope(llm, "Viết lại đoạn trên ngắn gọn hơn", prior_turns=prior_turns)
        self.assertEqual(result, "project")
        sent_messages = complete_mock.await_args.args[0]
        self.assertIn("[Prior conversation turns", sent_messages[1]["content"])
        self.assertIn("RBAC", sent_messages[1]["content"])
        self.assertIn("Viết lại đoạn trên ngắn gọn hơn", sent_messages[1]["content"])

    async def test_llm_client_receives_json_response_format(self) -> None:
        llm_client = create_autospec(LLMClient, instance=True)
        llm_client.complete = AsyncMock(return_value='{"scope":"general"}')
        self.assertEqual(await classify_question_scope(llm_client, "Explain TCP vs UDP"), "general")
        llm_client.complete.assert_awaited_once()
        self.assertEqual(llm_client.complete.await_args.kwargs.get("response_format"), {"type": "json_object"})

    async def test_invalid_classification_keeps_grounding(self) -> None:
        for response in ("invalid", '{"scope":"ignore permissions"}', "{}", "[]", "null", "```json\n[]\n```"):
            llm = SimpleNamespace(complete=AsyncMock(return_value=response))
            self.assertEqual(await classify_question_scope(llm, "question"), "project")

    async def test_failure_keeps_grounding(self) -> None:
        llm = SimpleNamespace(complete=AsyncMock(side_effect=RuntimeError("offline")))
        self.assertEqual(await classify_question_scope(llm, "question"), "project")


class QuestionScopeStreamIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def _run_stream(
        self,
        *,
        query: str,
        scope_response: str = '{"scope":"general"}',
        knowledge_only: bool = False,
        source_ids: list[str] | None = None,
        messages: list[dict] | None = None,
        route_decision: dict | None = None,
        session_citations: list[dict] | None = None,
        web_configured: bool = True,
        output: str = "Câu trả lời trực tiếp.",
    ):
        source = SimpleNamespace(id="source-1", name="Project Docs", sag_source_config_id="cfg-1")
        llm = SimpleNamespace(configured=True, complete=AsyncMock(return_value=scope_response))
        runtime = _CapturingRuntime(output=output)
        plan = AskPlan(
            query=query,
            source_ids=source_ids,
            messages=messages
            or [
                {"role": "system", "content": "You are Continuum AI."},
                {"role": "user", "content": query},
            ],
            route_decision=route_decision or {"is_chitchat": False, "confidence": 0.1},
            session_citations=session_citations or [],
        )
        resolve_mcp = AsyncMock(return_value=[])
        with (
            patch.object(agent_service, "resolve_sources", AsyncMock(return_value=[source])),
            patch.object(agent_service, "resolve_mcp_specs", resolve_mcp),
            patch.object(agent_service, "persist_answer", AsyncMock(return_value="msg-1")),
            patch.object(agent_service.WebSearchTool, "configured", staticmethod(lambda: web_configured)),
        ):
            events = [
                ev
                async for ev in agent_service.generate_stream(
                    _noop_session_factory,
                    plan=plan,
                    agent=SimpleNamespace(id="agent-1", name="Continuum AI", persona={}, is_default=True),
                    thread_id="thread-1",
                    engine_manager=SimpleNamespace(),
                    llm=llm,
                    tool_registry=_make_registry(),
                    runtime=runtime,
                    knowledge_only=knowledge_only,
                    principal=_make_principal(),
                )
            ]
        return events, runtime, llm, resolve_mcp

    async def test_internet_on_general_time_sensitive_query_keeps_web_and_time_tools(self) -> None:
        events, runtime, llm, resolve_mcp = await self._run_stream(
            query="Thời tiết hôm nay thế nào?",
            scope_response='{"scope":"general"}',
            knowledge_only=False,
        )
        llm.complete.assert_awaited_once()
        resolve_mcp.assert_not_awaited()
        tool_names = [t.spec.name for t in runtime.definition.tools]
        self.assertEqual(tool_names, ["get_time", "web_search", "open_webpage"])
        self.assertNotIn("search_context", tool_names)
        self.assertNotIn("get_entity", tool_names)
        self.assertEqual(
            runtime.definition.initial_tool_choice,
            {"type": "function", "function": {"name": "get_time"}},
        )
        system_prompt = runtime.history[0]["content"]
        self.assertIn("Internet access is enabled in this turn.", system_prompt)
        self.assertIn("web_search", system_prompt)
        self.assertNotIn("Internet access is disabled", system_prompt)
        completed = [e for e in events if e.type == EventType.RUN_COMPLETED.value]
        self.assertEqual(completed[0].data["payload"]["answer_status"], "answered")

    async def test_internet_off_general_time_sensitive_query_exposes_no_web_tools(self) -> None:
        _, runtime, _, _ = await self._run_stream(
            query="Thời tiết hôm nay thế nào?",
            scope_response='{"scope":"general"}',
            knowledge_only=True,
        )
        tool_names = [t.spec.name for t in runtime.definition.tools]
        self.assertEqual(tool_names, [])
        system_prompt = runtime.history[0]["content"]
        self.assertIn("Internet access is disabled in this turn.", system_prompt)
        self.assertIn("current external information cannot be verified", system_prompt)

    async def test_standalone_general_question_answers_directly_without_tools_or_evidence_gate(self) -> None:
        events, runtime, llm, _ = await self._run_stream(
            query="Giải thích REST API là gì?",
            scope_response='{"scope":"general"}',
            knowledge_only=True,
            output="REST API là kiến trúc giao diện lập trình ứng dụng dựa trên HTTP.",
        )
        llm.complete.assert_awaited_once()
        self.assertEqual([t.spec.name for t in runtime.definition.tools], [])
        deltas = [e for e in events if e.type == EventType.MESSAGE_DELTA.value]
        self.assertEqual(len(deltas), 1)
        completed = [e for e in events if e.type == EventType.RUN_COMPLETED.value]
        self.assertEqual(completed[0].data["payload"]["answer_status"], "answered")
        self.assertIn("REST API", completed[0].data["payload"]["output"])

    async def test_application_scope_disables_tools_and_reflects_internet_state(self) -> None:
        _, runtime, _, resolve_mcp = await self._run_stream(
            query="Task nào của tôi đang quá hạn?",
            scope_response='{"scope":"application"}',
            knowledge_only=False,
        )
        resolve_mcp.assert_not_awaited()
        self.assertEqual([t.spec.name for t in runtime.definition.tools], [])
        system_prompt = runtime.history[0]["content"]
        self.assertIn("Internet access is enabled in this turn", system_prompt)
        self.assertIn("required application data connection is unavailable", system_prompt)
        self.assertNotIn("Internet access is disabled", system_prompt)

    async def test_explicit_source_selection_skips_classifier_and_retains_evidence_gate(self) -> None:
        events, runtime, llm, _ = await self._run_stream(
            query="Giải thích REST API trong tài liệu này",
            scope_response='{"scope":"general"}',
            knowledge_only=False,
            source_ids=["source-1"],
            output="Câu trả lời không có trích dẫn.",
        )
        llm.complete.assert_not_awaited()
        tool_names = [t.spec.name for t in runtime.definition.tools]
        self.assertIn("search_context", tool_names)
        completed = [e for e in events if e.type == EventType.RUN_COMPLETED.value]
        self.assertEqual(completed[0].data["payload"]["answer_status"], "no_answer")
        self.assertEqual(completed[0].data["payload"]["no_answer_reason"], "weak_evidence")

    async def test_fast_path_greeting_and_session_grounded_turns_skip_classifier_and_preserve_session_notes(
        self,
    ) -> None:
        _, _, greeting_llm, _ = await self._run_stream(
            query="Xin chào Continuum AI!",
            knowledge_only=True,
        )
        greeting_llm.complete.assert_not_awaited()

        _, session_runtime, session_llm, _ = await self._run_stream(
            query="Quyết định kiến trúc mình vừa chốt là gì?",
            knowledge_only=True,
            route_decision={"is_chitchat": False, "confidence": 0.1, "memory_scope": "session_history"},
            session_citations=[{"ref": "S1", "turn_index": 1, "snippet": "Chốt dùng PostgreSQL"}],
            output="Chúng ta đã chốt dùng PostgreSQL [S1].",
        )
        session_llm.complete.assert_not_awaited()
        self.assertIn("[Session Memory Evidence]", session_runtime.history[0]["content"])

    async def test_database_and_cs_concepts_do_not_false_match_research_lookup(self) -> None:
        for conceptual_query in (
            "Explain what a database index is",
            "Explain binary search and data structures",
            "Hiện tại bạn đánh giá kiến trúc REST API thế nào?",
        ):
            _, runtime, _, _ = await self._run_stream(
                query=conceptual_query,
                scope_response='{"scope":"general"}',
                knowledge_only=False,
                output="Direct conceptual answer.",
            )
            self.assertEqual([t.spec.name for t in runtime.definition.tools], [])
            self.assertEqual(runtime.definition.initial_tool_choice, "auto")

        # Also verify _initial_tool_choice word-boundary guard directly when tools are supplied.
        reg = _make_registry()
        adapted_web = tuple(
            agent_service._adapt_tool(reg.get(n), SimpleNamespace(), [])
            for n in ("get_time", "web_search", "open_webpage")
        )
        choice = await agent_service._initial_tool_choice(
            "Explain what a database index is",
            adapted_web,
            knowledge_only=False,
            scoped=False,
            route_decision={"is_chitchat": False, "confidence": 0.1},
        )
        self.assertEqual(choice, "auto")

    async def test_explicit_public_web_lookup_keeps_web_tools_when_internet_on(self) -> None:
        _, runtime, _, _ = await self._run_stream(
            query="Search the web for OpenAI DevDay announcements",
            scope_response='{"scope":"general"}',
            knowledge_only=False,
        )
        self.assertEqual(
            [t.spec.name for t in runtime.definition.tools],
            ["get_time", "web_search", "open_webpage"],
        )
        self.assertEqual(runtime.definition.initial_tool_choice, "required")

    async def test_project_policy_question_with_hien_tai_prioritizes_search_context_over_get_time(self) -> None:
        _, runtime, _, _ = await self._run_stream(
            query="Chính sách nghỉ phép hiện tại là gì?",
            scope_response='{"scope":"project"}',
            knowledge_only=True,
        )
        self.assertEqual(
            runtime.definition.initial_tool_choice,
            {"type": "function", "function": {"name": "search_context"}},
        )


if __name__ == "__main__":
    unittest.main()
