"""MCP 三件事，全程离线：

1. 信源即 MCP —— 经进程内内存客户端列出并调用 search/get_entity/get_chunk（空库 → 结构化结果）。
2. 远端 MCP 工具适配成 sag 的 `Tool`（命名空间前缀 + call_tool 往返）。
3. 绑定与描述端点 —— agent 挂载外部 MCP 的校验、信源的 MCP 连接描述。
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session as connect

from sag_api.tools import mcp as mcp_module
from sag_api.tools import registry
from sag_api.tools.base import Tool, ToolContext, ToolMeta, ToolResult
from sag_api.tools.mcp import (
    MCPTool,
    MCPToolExecutionError,
    _clean_url,
    open_agent_mcp_tools,
    tools_from_session,
)


@pytest.mark.asyncio
async def test_remote_mcp_tool_adapted_as_sag_tool():
    """远端 MCP 工具 → MCPTool：命名空间前缀 + invoke 往返回文本。"""
    stub = FastMCP("stub")

    @stub.tool(description="回显输入")
    async def echo(text: str) -> str:
        return f"echo:{text}"

    async with connect(stub) as client:
        await client.initialize()
        tools = await tools_from_session(client, namespace="stub")
        assert len(tools) == 1
        tool = tools[0]
        assert tool.meta.name == "mcp__stub__echo"
        assert tool.meta.parameters.get("type") == "object"
        result = await tool.invoke({"text": "hi"}, ToolContext(engine_manager=None))
        assert result.content == "echo:hi"
        assert result.data == {"external_references": []}


class _StubCallSession:
    def __init__(self, result):
        self.result = result

    async def call_tool(self, name, args):
        return self.result


def _adapt_stub_result(result) -> MCPTool:
    return MCPTool(
        _StubCallSession(result),
        remote_name="lookup",
        local_name="mcp__stub__lookup",
        description="stub",
        parameters={"type": "object", "properties": {}},
    )


def test_external_reference_url_validation_rejects_unsafe_authority_and_whitespace():
    unsafe_urls = (
        " https://example.com/leading-space",
        "https://example.com/path with space",
        "https://user:secret@example.com/private",
        "https://example.com:not-a-port/result",
        "https:///missing-host",
        "ftp://example.com/file",
    )

    assert all(_clean_url(url) is None for url in unsafe_urls)
    safe_url = "https://example.com:8443/report?q=agent#section"
    assert _clean_url(safe_url) == safe_url


@pytest.mark.asyncio
async def test_remote_mcp_error_result_raises_structured_exception():
    """MCP isError 必须进入运行时失败分支，不能伪装成成功的文本结果。"""
    tool = _adapt_stub_result(
        SimpleNamespace(
            isError=True,
            content=[SimpleNamespace(text="upstream rejected the query")],
        )
    )

    with pytest.raises(MCPToolExecutionError) as raised:
        await tool.invoke({}, ToolContext(engine_manager=None))

    assert raised.value.to_dict() == {
        "code": "mcp_tool_error",
        "tool_name": "lookup",
        "message": "upstream rejected the query",
    }
    assert "lookup" in str(raised.value)


@pytest.mark.asyncio
async def test_remote_mcp_extracts_and_deduplicates_external_references():
    """structured content、文本 JSON 与普通 URL 都能形成可渲染外部来源。"""
    tool = _adapt_stub_result(
        SimpleNamespace(
            isError=False,
            structuredContent={
                "results": [
                    {
                        "url": "https://news.example/a",
                        "title": "Alpha report",
                        "source": "Example News",
                        "snippet": "  Alpha   launch\n details.  ",
                    }
                ]
            },
            structured_content={
                "items": [
                    {
                        "href": "https://docs.example/b",
                        "name": "Beta docs",
                        "publisher": "Example Docs",
                        "description": "Beta documentation summary.",
                    }
                ]
            },
            content=[
                SimpleNamespace(
                    text=(
                        '{"results":[{"link":"https://third.example/c","title":"Gamma",'
                        '"site":"Third","summary":"Gamma release notes."}]}'
                    )
                ),
                SimpleNamespace(text="重复来源 https://news.example/a；忽略 ftp://files.example/x"),
            ],
        )
    )

    result = await tool.invoke({}, ToolContext(engine_manager=None))

    assert result.data["external_references"] == [
        {
            "url": "https://news.example/a",
            "title": "Alpha report",
            "source": "Example News",
            "snippet": "Alpha launch details.",
        },
        {
            "url": "https://docs.example/b",
            "title": "Beta docs",
            "source": "Example Docs",
            "snippet": "Beta documentation summary.",
        },
        {
            "url": "https://third.example/c",
            "title": "Gamma",
            "source": "Third",
            "snippet": "Gamma release notes.",
        },
    ]


@pytest.mark.asyncio
async def test_remote_mcp_reference_snippets_are_bounded_and_merge_richer_duplicates():
    """重复 URL 补全后续元数据；摘要有边界且不会展开 JSON 工具载荷。"""
    long_summary = "  ".join(["detail"] * 100)
    serialized_payload = (
        '{"results":[{"url":"https://nested.example/private",'
        '"content":"complete upstream payload"}]}'
    )
    tool = _adapt_stub_result(
        SimpleNamespace(
            isError=False,
            structuredContent={
                "results": [
                    {"url": "https://merge.example/report"},
                    {
                        "url": "https://safe.example/result",
                        "title": "Safe result",
                        "content": serialized_payload,
                    },
                ]
            },
            structured_content={
                "results": [
                    {
                        "url": "https://merge.example/report",
                        "headline": "Complete report",
                        "provider": "Merge News",
                        "content": long_summary,
                    }
                ]
            },
            content=[
                SimpleNamespace(
                    text=(
                        '{"results":[{"link":"https://text.example/item",'
                        '"title":"Text JSON","description":"  concise\\nsummary  "}]}'
                    )
                )
            ],
        )
    )

    result = await tool.invoke({}, ToolContext(engine_manager=None))
    references = result.data["external_references"]

    assert references[0]["title"] == "Complete report"
    assert references[0]["source"] == "Merge News"
    assert len(references[0]["snippet"]) == 320
    assert references[0]["snippet"].endswith("…")
    assert references[1] == {
        "url": "https://safe.example/result",
        "title": "Safe result",
        "source": "safe.example",
    }
    assert references[2] == {
        "url": "https://text.example/item",
        "title": "Text JSON",
        "source": "text.example",
        "snippet": "concise summary",
    }
    assert all(reference["url"] != "https://nested.example/private" for reference in references)


@pytest.mark.asyncio
async def test_open_agent_mcp_tools_returns_safe_connection_warning(monkeypatch):
    """连接失败会反馈给调用方，但 warning 不泄露配置、凭据或完整异常。"""

    @asynccontextmanager
    async def broken_session(config):
        del config
        raise RuntimeError("Bearer top-secret at https://private.example/mcp")
        yield  # pragma: no cover

    monkeypatch.setattr(mcp_module, "_open_session", broken_session)

    async with open_agent_mcp_tools(
        [("private-search", {"url": "https://private.example/mcp", "token": "top-secret"})]
    ) as bundle:
        assert bundle.tools == []
        assert bundle.warnings == [
            {
                "code": "mcp_connection_failed",
                "server": "private_search",
                "message": "Kết nối dịch vụ MCP thất bại，lượt này đã bỏ qua dịch vụ đó。",
            }
        ]
        warning_text = str(bundle.warnings)
        assert "top-secret" not in warning_text
        assert "private.example" not in warning_text


def test_registry_overlay_does_not_pollute_global():
    """叠加层含内置 + MCP 工具；全局单例不被污染。"""

    class _Stub(Tool):
        meta = ToolMeta(
            name="mcp__ext__ping",
            description="stub",
            parameters={"type": "object", "properties": {}},
        )

        async def invoke(self, args, ctx):
            return ToolResult(content="pong")

    child = registry.overlay([_Stub()])
    assert child.has("mcp__ext__ping")
    assert child.has("search_context")  # 内置工具继承
    assert not registry.has("mcp__ext__ping")  # 全局不受影响
