# sag-api

sag 的后端服务：FastAPI + `zleap-sag`。

## 分层

| 层 | 目录 | 职责 |
|---|---|---|
| 适配层 | `sag_api/sag/` | **唯一** import `zleap-sag` 之处；信源 ↔ `DataEngine` |
| 连接器 | `sag_api/connectors/` | 采集抽象 + 注册表（文件上传 → 动态同步） |
| 文档解析 | `sag_api/parsing/` | Markdown 直通；PDF 优先 MinerU、失败自动回退；其余由 MarkItDown 转换 |
| 任务队列 | `sag_api/jobs/` | 后台处理编排（ingest → extract 状态机） |
| 生成层 | `sag_api/generation/` | 检索结果 → LLM 流式答案 + 引用 |
| 工具层 | `sag_api/tools/` | Agent 工具：内置检索/实体 + 远端 MCP 适配（统一 `Tool` 接口） |
| Agent Core | `sag_agent/` | 独立编排核心：生命周期、事件、工具、审批、取消、存储端口 |
| Agent 适配 | `sag_api/services/agent_service.py` | 将 SAG 模型、工具、会话接入 Agent Core |
| MCP | `sag_api/mcp/` | 信源即 MCP：FastMCP server + Streamable-HTTP 挂载（`/mcp/`）+ stdio 入口 |
| 领域服务 | `sag_api/services/` | 纯业务逻辑，不依赖 FastAPI |
| 接口 | `sag_api/api/v1/` | HTTP 路由，仅做 IO / 校验 / 序列化 |

## 运行

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,postgres]"
cp .env.example .env
docker compose up -d db qdrant
uvicorn sag_api.main:app --reload --host 0.0.0.0 --port 8000
```

文档 UI：http://localhost:8000/docs

## Shared model configuration authorization

In `SAG_AUTH_MODE=password` or `SAG_AUTH_MODE=continuum`, shared model settings
are writable only by SAG users whose IDs appear in the comma-separated
`SAG_MODEL_CONFIG_OPERATOR_IDS` environment variable. An empty value denies
model connection tests, updates, and shared model setup. An operator can read
their SAG user ID from the authenticated `GET /api/v1/auth/me` response. Keep
this allowlist in trusted deployment configuration; organization and Project
scope do not grant model-configuration authority. `SAG_AUTH_MODE=local` keeps
the existing single-user local setup behavior.

## Evidence authorization (ACL rollout)

Search, source/document reads, graph-backed source-scoped operations, agents,
Dify, MCP HTTP and OCTX evidence paths require a server-verified Continuum
principal assertion in `X-SAG-Principal-Assertion`. Configure the exact issuer
and fixed HTTPS JWKS URL with `SAG_PRINCIPAL_ASSERTION_ISSUER` and
`SAG_PRINCIPAL_ASSERTION_JWKS_URL`; the default audience is `sag-api`. Missing
configuration or an invalid/missing assertion fails closed. The assertion is a
server-to-server credential: the trusted BE/BFF must overwrite or strip any
client-supplied header and must not expose the assertion to browser storage.

SAG maps `allowedProjectIds` and `orgId` from the verified assertion to
confirmed rows in `source_project_mappings`. New, pending, unmapped, ambiguous,
or revoked Source mappings are not searchable/readable. `source_ids` can only
narrow the resulting scope. Use the reviewed DDL and
`scripts/source_project_acl.py` workflow for the mapping table and
owner-approved backfill; do not use package metadata or client input to confirm
a Project mapping. Production trust configuration, key rotation, mapping writer,
and legacy backfill still require their respective owners' approval; the local
contract tests are not production ACL acceptance.

`POST /sources` may receive `project_id` as a requested assignment. The API
checks that it is in the verified principal's readable Project scope and stores
the mapping as `PENDING`; the response includes `mapping_state: "PENDING"`.
When `project_id` is omitted, the API may use the sole readable Project; callers
with multiple readable Projects must specify one. Pending Sources remain
inaccessible until an owner confirms the mapping through the approved workflow.

Frontend integration must treat `201` with `mapping_state: "PENDING"` as a
request awaiting approval. Show **Chờ phê duyệt Project Mapping** and retain the
returned Source ID; do not redirect to the upload/detail screen. Pending Sources
are omitted from `GET /sources`, and their detail, document list and upload
routes return `404`, including for the creator. A `404` at this stage does not
mean creation failed. After owner confirmation, refresh the authorized Source
list before enabling uploads. There is currently no pending-mapping status/list
API; adding that UI/workflow is a separate frontend deliverable.

Agent history preserves assistant turns whose evidence is no longer authorized
as placeholders with evidence fields cleared. Historical assistant
`prompt_preview` is always returned as an empty string because frozen prompts
can contain earlier revoked evidence. Stored messages remain unchanged; the
current run's completion event still includes its prompt preview.

Global P4 retrieval uses dense/lexical RRF and emits no graph fields. Source-
scoped P3 graph/event recall runs only after Source authorization. Universe
global manifest/rebuild and MCP stdio remain disabled until their data paths can
prove Project-scoped authorization.

## Retrieval score contract

Search section scores are normalized reciprocal-rank fusion (RRF) rank scores
in `[0, 1]`; they are not cosine similarities or relevance probabilities.
When both dense and lexical retrieval return candidates, a top-ranked result
found by only one retriever can score `0.5`. Dify's `score_threshold` filters
this normalized RRF score, so configure it for rank-fusion semantics rather
than as a cosine-similarity cutoff.

也可以在仓库根目录运行 `make api`。开发服务器默认监听全部本机网卡，便于从局域网地址访问 Web；生产环境请通过反向代理与访问控制暴露服务。

## Docker LLM/MCP compatibility

The Docker build takes the `litellm` and `mcp` versions from `uv.lock` rather
than independently resolving their latest releases. LiteLLM releases that
import MCP v2 gateway types during ordinary function-tool calls cannot run
with this application's MCP v1 API. Keep the SDK pair aligned when updating
the lockfile.

From the `SAG` directory, check the installed image without provider keys or
external AI requests:

```bash
docker compose exec -T api python - < apps/api/tests/test_litellm_mcp_compatibility.py
```

This starts a loopback-only OpenAI-compatible stub, supplies ordinary function
tools and verifies that a streamed answer completes. An HTTP 200 from the real
`ask` route only opens the SSE stream; a later `run.failed` event still means
generation failed. Check `docker compose logs api` for the underlying cause.

### Continuum question scope

Authenticated Continuum asks without explicit source selection are classified into
public knowledge, project evidence, or live application records. Standalone public
knowledge is answered directly without document search even when Internet is
disabled, while time-sensitive or explicit research public questions retain allowed
`get_time` / `web_search` / `open_webpage` tools when Internet is enabled (never
exposing private project or MCP tools). Scene notes reflect the turn's actual
Internet status and preserve session-memory citations when present. Explicit source
selection, session-grounded turns, and uncertain/failed classification retain the
evidence gate. Application-record questions currently explain that the required
data connection is unavailable: this change does not add task queries or
browsing-history storage. The classifier is not an authorization mechanism and
grants no backend access.

Run the offline regression tests with `python -m unittest discover -s tests -p test_question_scope.py`.
