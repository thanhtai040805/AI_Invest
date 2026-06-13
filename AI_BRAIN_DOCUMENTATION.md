# AI Engine — Toàn Bộ Bộ Não AI

> **Dự án:** AIInvest AI Engine v2.0  
> **Mục tiêu:** Hệ thống AI phân tích và giao dịch chứng khoán (tập trung thị trường Việt Nam)  
> **Kiến trúc:** FastAPI + Multi-Agent LangGraph + Swarm DAG + Quant Engine + 7-Layer Risk

---

# Mục Lục

1. [Tổng Quan Kiến Trúc](#1-tổng-quan-kiến-trúc)
2. [Application Layer](#2-application-layer)
3. [State Machine & Orchestration](#3-state-machine--orchestration)
4. [ReAct AgentLoop (5-Layer Context)](#4-react-agentloop-5-layer-context)
5. [Multi-Agent System](#5-multi-agent-system)
6. [Tool Registry (20+ Tools)](#6-tool-registry-20-tools)
7. [Multi-Model Router & LLM Providers](#7-multi-model-router--llm-providers)
8. [Swarm Presets & DAG Engine](#8-swarm-presets--dag-engine)
9. [Quant Engine](#9-quant-engine)
10. [7-Layer Risk System](#10-7-layer-risk-system)
11. [Market Data Pipeline](#11-market-data-pipeline)
12. [Cross-Session Memory](#12-cross-session-memory)
13. [Evaluation System](#13-evaluation-system)
14. [Dataflow Routing](#14-dataflow-routing)
15. [Machine Learning & Deep Learning](#15-machine-learning--deep-learning)
16. [DNSE Open API Integration](#16-dnse-open-api-integration)
17. [Configuration & Environment](#17-configuration--environment)
18. [Tổng Kết](#18-tổng-kết)

---

# 1. Tổng Quan Kiến Trúc

```
┌──────────────────────────────────────────────────────────────────┐
│                       FastAPI App                                 │
│  ┌────────────┐  ┌──────────────────────┐  ┌──────────────────┐  │
│  │  Routers   │  │      Services        │  │      Brain       │  │
│  │  (24 API)  │  │  (DNSE, Backtest,    │  │    (Core AI)     │  │
│  │            │  │   ML, ETL, Risk)     │  │                  │  │
│  └────────────┘  └──────────────────────┘  └──────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

## 1.1 Luồng Xử Lý Chính

```
User Request
    │
    ▼
IntentRouter (CHAT / RESEARCH / SIGNAL)
    │
    ├── CHAT → Groq-0 (llama-3.3-70b) → Response trực tiếp
    │
    ├── RESEARCH → LangGraph StateGraph
    │   ├── Market Analyst ──────────────┐
    │   ├── Fundamental Analyst ─────────┤
    │   ├── Sentiment Analyst ───────────┤
    │   ├── News Analyst ────────────────┤
    │   ├── Bull Researcher ─────────────┤
    │   ├── Bear Researcher ─────────────┤
    │   ├── Aggressive Debater ──────────┤
    │   ├── Conservative Debater ────────┤
    │   ├── Neutral Debater ─────────────┤
    │   ├── Research Manager → 5-tier Rating ──┐
    │   └── Portfolio Manager → BUY/SELL/HOLD ─┤
    │                                          ▼
    │                                  Trader → Execution
    │
    └── SIGNAL → SwarmRuntime (DAG Engine)
        ├── Layer 0: Data Analysts (parallel)
        ├── Layer 1: Researchers (parallel)
        ├── Layer 2: Strategists (parallel)
        ├── Layer 3: Risk Reviewers (serial)
        └── Layer 4: Decision Maker (final)
```

## 1.2 Models (3-Tier)

| Provider | Model | Priority | Vai Trò |
|----------|-------|----------|---------|
| **Groq-0** | `llama-3.3-70b-versatile` | 1 | Reasoning, chat, synthesis, real-time signal |
| **Groq-1** | `qwen/qwen3-32b` | 2 | Structured output, classification, cross-check |
| **NVIDIA** | `minimaxai/minimax-m2.7` | 3 | Document/news analysis, deep research |

## 1.3 Thành Phần Chính

| Component | Files | Chức Năng |
|-----------|-------|-----------|
| **State Machine** | 23 files | Trạng thái session, LangGraph graph, orchestration |
| **Agents** | 19 files | ReAct loop, analysts, researchers, debaters, managers |
| **Tools** | 20+ tools | Stock data, indicators, web, files, swarm |
| **Providers** | 8 files | LLM clients, multi-model routing, prompts |
| **Config** | 3 files | Agent config schema, loader, paths |
| **Memory** | 1 file | Cross-session persistent memory |
| **Eval** | 2 files | Signal tracking, LLM judge |
| **Quant** | 20+ files | Factor IC testing, hypothesis research |
| **Risk** | 12 files | 7-layer risk scoring |
| **Dataflows** | 13 files | Vendor routing (yfinance, VN vendors) |

---

# 2. Application Layer

## 2.1 `app/main.py` — Entry Point

- FastAPI app: `title="AIInvest AI Engine"`, `version="2.0.0"`
- CORS: mở rộng `*`
- Routes: import 24 routers từ `app/routers/`
- Health endpoints:
  - `GET /health` — kiểm tra DNSE + Redis status
  - `GET /health/detailed` — stream hub, rate limiter, market session state

## 2.2 `app/lifespan.py` — App Lifespan

- Gọi `pg_migrate()` để migrate PostgreSQL schema (signal_log, nhật ký đánh giá)
- Delegate sang `brain_lifespan` cho AI-specific lifecycle (khởi tạo LLM clients, load presets)

## 2.3 `app/config/settings.py` — Settings Dataclass

Đọc từ `.env` với các nhóm:

### DNSE Settings
| Field | Mô tả |
|-------|-------|
| `dnse_api_key` | API key DNSE |
| `dnse_api_secret` | API secret DNSE |
| `dnse_account_no` | Số tài khoản giao dịch |
| `dnse_base_url` | REST endpoint |
| `dnse_ws_url` | WebSocket endpoint |
| `dnse_board_id` | Board ID (mặc định G1) |

### Redis Settings
| Field | Mô tả |
|-------|-------|
| `redis_url` | Redis connection URL |
| `dnse_redis_channel_prefix` | Prefix cho channel (mặc định `dnse:`) |

### LLM Multi-Model Settings
| Field | Mô tả |
|-------|-------|
| `llm_nvidia_key`, `llm_nvidia_model` | NVIDIA API key & model |
| `llm_groq_key0`, `llm_groq_model0` | Groq-0 API key & model |
| `llm_groq_key1`, `llm_groq_model1` | Groq-1 API key & model |
| `llm_routing_mode` | Routing mode (auto/manual) |
| `enable_fallback` | Bật fallback giữa các models |
| `confidence_threshold` | Ngưỡng confidence (mặc định 0.6) |
| `max_parallel_calls` | Số parallel calls tối đa (mặc định 2) |

### Feature Flags
| Field | Mô tả |
|-------|-------|
| `dnse_enabled` | Bật DNSE integration |
| `llm_routing_mode` | Routing mode |

## 2.4 Database Models

### `PaperTrade` (SQLite)
| Field | Mô tả |
|-------|-------|
| `ticker`, `action` | Mã CK, hành động |
| `price`, `confidence`, `thesis` | Giá, độ tin cậy, luận điểm |
| `pnl_t2`, `pnl_t5` | P&L T+2 và T+5 |

### `SessionLog` (SQLite)
| Field | Mô tả |
|-------|-------|
| `session_id`, `intent`, `pipeline` | ID phiên, ý định, pipeline |
| `model_used`, `duration_ms`, `status` | Model, thời gian, trạng thái |
| `ticker`, `result` | Mã CK, kết quả |

---

# 3. State Machine & Orchestration

**23 files** trong `app/brain/state/` — quản lý vòng đời session, agent graph, swarm runtime.

## 3.1 Data Models (`models.py`)

### Enums
| Enum | Values |
|------|--------|
| `RunStatus` | `pending` → `running` → `completed` / `failed` / `cancelled` |
| `TaskStatus` | `pending` → `blocked` → `in_progress` → `completed` / `failed` / `cancelled` |
| `WorkerStatus` | `completed`, `failed`, `timeout`, `token_limit`, `incomplete` |

### Core Models
| Model | Fields | Vai Trò |
|-------|--------|---------|
| `SwarmAgentSpec` | id, role, system_prompt, tools[], skills[], max_iterations, timeout_seconds, max_retries | Định nghĩa agent role từ YAML |
| `SwarmTask` | id, agent_id, prompt_template, depends_on (immutable DAG edges), blocked_by (runtime), input_from, status, summary, artifacts | Node trong DAG |
| `SwarmRun` | id, preset_name, status, user_vars, agents[], tasks[], final_report, tokens | Full run state |
| `WorkerResult` | status (WorkerStatus), summary, artifact_paths, iterations, tokens | Kết quả worker |
| `Session` | id, status (active/archived), messages[], attempts[] | Chat session |
| `Attempt` | id, status (pending→running→completed/failed), result | Agent execution attempt |

## 3.2 SessionService (`service.py`)

### Vòng Đời Session
```
1. create_session()         → tạo Session mới
2. append_message()         → thêm user message
3. create_attempt()         → tạo Attempt (pending)
4. run_agent_loop()         → execute AgentLoop (background thread)
   - Thread pool: _AGENT_EXECUTOR (max 4 workers)
   - _active_loops: dict quản lý các AgentLoop đang chạy
   - _session_locks: asyncio locks tránh race condition
5. SSE event streaming qua event_bus
6. Response trả về client
```

## 3.3 LangGraph Trading Graph (`graph.py`)

### Nodes (12 nodes)
```
1. market_analyst          → technical analysis
2. sentiment_analyst       → sentiment scoring
3. fundamentals_analyst    → fundamental analysis
4. news_analyst            → news analysis
5. bull_researcher         → bullish thesis
6. bear_researcher         → bearish thesis
7. aggressive_debater      → risk-seeking debate
8. conservative_debater    → risk-averse debate
9. neutral_debater         → balanced debate
10. research_manager       → tổng hợp → 5-tier rating
11. portfolio_manager      → BUY/SELL/HOLD + confidence
12. trader                 → execution proposal
```

### Conditional Edges
| Edge Function | Decision |
|---------------|----------|
| `should_continue_debate()` | bull ↔ bear multi-round |
| `should_auto_trade()` | auto trade vs research-only |
| `should_run_risk_gate()` | có chạy risk assessment không |
| `route_to_analyst()` | chọn analyst theo intent |

## 3.4 GraphState (`state.py`)

```python
GraphState = TypedDict {
    symbol: str
    user_query: str
    intent: str
    analysis_results: Dict
    decision: str           # BUY/SELL/HOLD
    confidence: float
    risk_level: str
    debate_round: int
    bull_history: List
    bear_history: List
    risk_analysis_history: List
    reflection: Dict
    auto_trade: bool
    decision_type: DecisionType  # NORMAL / AUTO_TRADE / RESEARCH_ONLY
}
```

## 3.5 EventBus (`events.py`)

### `SSEEvent`
| Field | Mô tả |
|-------|-------|
| `event_id` | UUID hex 16 chars — dùng cho `last_event_id` recovery |
| `event_type` | SSE event field |
| `data` | Payload dict |
| `session_id` | Owning session |
| `timestamp` | Unix time |

### `EventBus`
| Method | Mô tả |
|--------|-------|
| `publish(event)` | Thread-safe publish: append buffer + push to asyncio Queue |
| `emit(session_id, event_type, data)` | One-step build + publish |
| `replay(session_id, last_event_id)` | Replay buffered events cho reconnect recovery |
| `subscribe(session_id, last_event_id)` | Async generator → SSE frames, heartbeat 30s |

### Thread Safety
```
Backing thread → EventBus.publish(event)
  → with lock: buffer.append(event)
  → loop.call_soon_threadsafe(queue.put)  # Thread-safe asyncio
```

## 3.6 Session Persistence

### SessionStore (file-based)
```
runs/.sessions/{session_id}/
├── session.json        # Session metadata
├── messages.jsonl      # Append-only messages
├── attempts/           # Attempt files
```

### SessionSearchIndex (SQLite FTS5)
- Index mọi message → search cross-session
- `last_event_id` recovery

## 3.7 Concurrency Manager (`concurrency.py`)

### Parallel Analyst Execution Plan
```python
AnalystType: MARKET, FUNDAMENTAL, NEWS, SOCIAL
ExecutionPlan: {
    specs: [analyst specs],
    concurrency_limit: int
}
```

## 3.8 Confidence Scorer (`confidence_scorer.py`)

### Scoring Algorithm
```
1. Hard flags → confidence = 0 (DO_NOT_TRADE)
   - CANH_BAO_TC, DEBT_DANGER, CAR_DANGER, ...
2. Soft flags → multiply ×0.5
   - M_SCORE_FLAG, FLOOR_TRAP, SHARP_DROP, ...
3. Factor composite → base score 0.0–1.0
4. Technical confirmation → bonus +0.1 if aligned
```

## 3.9 Market Data Grounding (`grounding.py`)

- Pre-fetch OHLCV cho symbols trong user_vars trước khi chạy swarm
- Pattern matching: `NVDA.US`, `700.HK`, `600519.SH`, `BTC-USDT`
- Inject markdown block vào worker prompt

---

# 4. ReAct AgentLoop (5-Layer Context)

**7 files** trong `app/brain/agents/core/`

## 4.1 AgentLoop (`loop.py`) — ReAct Core

### Thuật Toán Vòng Lặp ReAct

```
while iteration < max_iterations (50):
  1. Check cancellation flag
  2. Check timeout (nếu configured)
  3. Inject background notifications
  4. Layer 1: Microcompact (mọi iteration)
  5. Layer 2: Context Collapse (nếu tokens > 28K)
  6. Layer 3: Auto Compact (nếu tokens > 40K)
  7. Call LLM.stream_chat() với tool definitions
  8. Retry 1 lần nếu API error
  9. Track token usage
  10. Collect thinking text (reasoning_content)
  11. Nếu không có tool calls → final answer → break
  12. Append assistant message + tool calls
  13. Execute tools (read batching)
  14. Nếu compact tool được gọi → auto compact
```

### 5-Layer Context Management

| Layer | Tên | Trigger | Cơ Chế | Chi Phí |
|-------|-----|---------|--------|---------|
| **1** | Microcompact | Mọi iteration | Thay content tool result cũ (>100 chars) bằng `[cleared]`, giữ `KEEP_RECENT=3` | Free |
| **2** | Context Collapse | `tokens > 28.000` (70% của 40K) | String truncation: head 900 + tail 500 chars, giữ `COLLAPSE_PRESERVE_RECENT=6` | Free |
| **3** | Auto Compact | `tokens > 40.000` | LLM structured summary với `TAIL_TOKEN_BUDGET=20.000` token tail protection | LLM call |
| **4** | Compact Tool | Model gọi `compact()` tool | Trigger Layer 3 với optional `focus_topic` | LLM call |
| **5** | Iterative Update | Nth compaction (có `_previous_summary`) | Update summary cũ với turns mới, zero info decay | LLM call |

### Token Thresholds
```python
TOKEN_THRESHOLD = 40000        # env TOKEN_THRESHOLD
COLLAPSE_THRESHOLD = 28000     # 40K * 0.7
KEEP_RECENT = 3                # microcompact
COLLAPSE_PRESERVE_RECENT = 6
COLLAPSE_TEXT_MIN = 2400       # chars
COLLAPSE_HEAD = 900            # chars preserved from head
COLLAPSE_TAIL = 500            # chars preserved from tail
TAIL_TOKEN_BUDGET = 20000      # tail protection
TOOL_RESULT_LIMIT = 10000      # chars
```

### Read Batching (Parallel Readonly Tools)

```
_batch_execute():
  Scan tool_calls:
    consecutive is_readonly → "parallel" batch
    write tools → "serial" batch each
  
  parallel mode:
    ThreadPoolExecutor(max_workers=min(len, 8))
    Mỗi worker có heartbeat + progress emitter riêng
  
  serial mode:
    Execute từng cái một
```

### Tool Deduplication
```
1. Block duplicate: nếu tool đã success (trong _called_ok) và không repeatable
2. Fingerprint dedup: (tool_name + JSON args) trong cùng batch
```

### AgentLoop Output
```python
{
    "status": "success" | "failed" | "cancelled",
    "run_dir": str,
    "run_id": str,
    "content": str,          # final answer
    "react_trace": List,     # trace các tool calls
    "iterations": int,
    "total_input_tokens": int,
    "total_output_tokens": int,
    "reason": str | None     # error reason nếu failed
}
```

## 4.2 WorkerAgentLoop (`worker_loop.py`) — Swarm Worker

Kế thừa từ `AgentLoop`, thêm:

| Feature | Mô tả |
|---------|-------|
| `data_tool_calls` | Đếm tool calls không generic (deliverable classification) |
| `write_artifacts()` | Ghi `summary.md` + `messages.json` |
| `resolve_summary()` | Ưu tiên `report.md` content |
| `classify_deliverable()` | Kiểm tra chất lượng output |
| `collect_artifacts()` | Gom output files |
| `report_written()` | Kiểm tra `report.md` tồn tại |
| `is_data_agent()` | Tool set có non-generic tools không |

### Deliverable Classification
Phát hiện các vấn đề:
- `"empty deliverable"` → summary rỗng
- `"unparsed tool-call markup"` → markup chưa parse
- `"explicitly fabricated / mock data"` → dữ liệu giả
- `"raw tool-result envelope"` → chưa phân tích
- `"plan-only stub"` → chỉ có plan không có execution
- `"data agent produced no tool calls and no report.md"` → data agent không làm gì

## 4.3 ContextBuilder (`context.py`)

### System Prompt Assembly
```
System prompt động:
  1. Skill count, tool count (progressive disclosure)
  2. 5 data sources, 29 swarm teams
  3. Tool descriptions + skill descriptions
  4. Memory summary injection
  5. Task routing instructions:
     - VN stock → vn_stock_analyze(symbol) mandatory
     - Graph pipeline → debate flow
     - Swarm → DAG executor
     - Research → research pipeline
     - General → simple chat
```

## 4.4 ToolRegistry (`tools.py`)

### BaseTool (ABC)
| Method | Mô tả |
|--------|-------|
| `check_available()` | Dependency check |
| `execute(**kwargs)` → JSON string | Execute tool |
| `to_openai_schema()` | OpenAI function calling format |
| `is_readonly` | Cho phép parallel execution |
| `repeatable` | Cho phép gọi nhiều lần |

### ToolRegistry
| Method | Mô tả |
|--------|-------|
| `register(tool)` | Register tool instance |
| `execute(name, args)` | Execute by name |
| `get_openai_schemas()` | All schemas for LLM |
| `build_filtered_registry(names)` | Filter by allowed names |
| `check_available()` | Auto-exclude unavailable |

## 4.5 SkillsLoader (`skills.py`)

### Skill Loading
```
skills/ directory:
  - skill-name/
    - SKILL.md (full content)
    - support files

Progressive disclosure:
  1. Summary: names + descriptions → system prompt
  2. Full content: load_skill tool on demand
```

## 4.6 WorkspaceMemory (`memory.py`)

### Runtime State trong một AgentLoop.run()
- Counters (số lần gọi tool)
- `run_dir` path
- `to_summary()` → inject vào context

## 4.7 Progress & Heartbeat (`progress.py`)

### HeartbeatTimer
- Emit keepalive events mỗi 3 giây
- Thread-safe via `threading.local` emitter
- Tránh false-positive stale-run detection

### ProgressEvent
```python
ProgressEvent {
    tool: str,
    stage: str,
    current: int,
    total: int,
    message: str
}
```

---

# 5. Multi-Agent System

**19 files** trong `app/brain/agents/`

## 5.1 Cấu Trúc Agents

```
agents/
├── core/           # AgentLoop, WorkerAgentLoop, ContextBuilder, Tools, Skills
├── analysts/
│   ├── market_analyst.py       # Technical analysis
│   ├── sentiment_analyst.py    # Social sentiment
│   ├── fundamentals_analyst.py # Fundamental analysis
│   └── news_analyst.py         # News analysis
├── researchers/
│   ├── bull_researcher.py      # Bullish thesis
│   └── bear_researcher.py      # Bearish thesis
├── debaters/
│   ├── aggressive_debater.py   # Risk-seeking
│   ├── conservative_debater.py # Risk-averse
│   └── neutral_debater.py      # Balanced
├── managers/
│   ├── research_manager.py     # Research tổng hợp → rating
│   └── portfolio_manager.py    # BUY/SELL/HOLD decision
├── trader/
│   └── trader.py               # Execution proposal
└── utils/                      # Utilities
```

## 5.2 Agent Roles & Prompts

### Analysts
| Role | Input | Output |
|------|-------|--------|
| Market Analyst | OHLCV, indicators | Technical analysis (RSI, MACD, SMA, Bollinger) |
| Sentiment Analyst | News, social data | Sentiment score, social sentiment |
| Fundamentals Analyst | Financial ratios | PE/PB/ROE, financial health |
| News Analyst | News events | News analysis, catalysts |

### Researchers
| Role | Input | Output |
|------|-------|--------|
| Bull Researcher | Analyst outputs | Long thesis, upside catalysts, price targets |
| Bear Researcher | Analyst outputs | Short thesis, downside risks, red flags |

### Debaters
| Role | Perspective | Scoring |
|------|-------------|---------|
| Aggressive Debater | Risk-seeking | +1 (pro-trade) |
| Conservative Debater | Risk-averse | -1 (anti-trade) |
| Neutral Debater | Balanced | Weighted average |

### Managers
| Role | Input | Output |
|------|-------|--------|
| Research Manager | All analyst + debater outputs | 5-tier rating: Buy/Overweight/Hold/Underweight/Sell |
| Portfolio Manager | Rating + risk | BUY/SELL/HOLD + confidence + position size |

### Pydantic Output Schemas (`schemas.py`)
```python
PortfolioRating: Buy | Overweight | Hold | Underweight | Sell
TraderAction: BUY | SELL | HOLD
ResearchReport: company_info, investment_thesis, financial_analysis, valuation,
                risks, catalysts, rating
TradingDecision: action, entry_price_range, target_price, stop_loss,
                 position_size, confidence, timeframe
```

---

# 6. Tool Registry (20+ Tools)

## 6.1 Auto-Discovery

Tự động phát hiện tất cả `BaseTool.__subclasses__()`:
- `check_available()` → silent exclusion nếu dependency thiếu
- `build_filtered_registry()` → filter theo agent spec
- Blacklist: background tools excluded từ swarm

## 6.2 Danh Sách Tools

| Tool | File | Chức Năng | Readonly |
|------|------|-----------|----------|
| `vn_stock_analyze` | `vn_stock_analyze_tool.py` | Phân tích toàn diện cổ phiếu VN | Yes |
| `vn_qualitative_rag` | `vn_qualitative_rag_tool.py` | RAG định tính VN (báo cáo, tin tức) | Yes |
| `vn_index` | `vn_index_tool.py` | Dữ liệu chỉ số VN (VN30, HNX...) | Yes |
| `vn_funds` | `vn_funds_tool.py` | Phân tích quỹ VN | Yes |
| `vn_factor_data` | `vn_factor_data_tool.py` | Factor data cho VN | Yes |
| `trading_rules` | `trading_rules_tool.py` | Rule giao dịch, T+2, room ngoại | Yes |
| `trade_journal` | `trade_journal_tool.py` | Nhật ký giao dịch + P&L tracking | Yes |
| `swarm` | `swarm_tool.py` | Chạy swarm workflow | No |
| `skill_writer` | `skill_writer_tool.py` | Viết skill markdown mới | No |
| `shadow_account` | `shadow_account_tool.py` | Theo dõi shadow account | Yes |
| `risk_flags` | `risk_flags_tool.py` | Risk flags query | Yes |
| `web_search` | `web_search_tool.py` | Tìm kiếm web | Yes |
| `web_reader` | `web_reader_tool.py` | Đọc nội dung web | Yes |
| `write_file` | `write_file_tool.py` | Ghi file | No |
| `bash` | built-in | Shell command | No |
| `background_run` | built-in | Background process | No |
| `load_skill` | built-in | Load skill document | Yes |
| `session_search` | built-in | Search session history | Yes |
| `memorize` | built-in | Ghi nhớ thông tin | No |
| `recall` | built-in | Nhớ lại thông tin | Yes |
| `factor_analysis` | built-in | Phân tích factor | Yes |

---

# 7. Multi-Model Router & LLM Providers

**8 files** trong `app/brain/providers/`

## 7.1 GraphOrchestrator (`orchestrator.py`)

### 9 Task Types & Model Mapping

| Task Type | Mô tả | Provider | Model | Priority |
|-----------|-------|----------|-------|----------|
| `REALTIME_SIGNAL` | Tín hiệu real-time | Groq-0 | llama-3.3-70b | 1 |
| `QUICK_ANALYSIS` | Phân tích nhanh | Groq-0 | llama-3.3-70b | 1 |
| `CHATBOT` | Chat thông thường | Groq-0 | llama-3.3-70b | 1 |
| `HEADLINE_CLASSIFICATION` | Phân loại headline | Groq-1 | qwen/qwen3-32b | 2 |
| `STRUCTURED_OUTPUT` | Output có cấu trúc | Groq-1 | qwen/qwen3-32b | 2 |
| `CROSS_CHECK` | Kiểm tra chéo | Groq-1 | qwen/qwen3-32b | 2 |
| `DEEP_RESEARCH` | Nghiên cứu sâu | NVIDIA | minimax-m2.7 | 3 |
| `LONG_FORM_ANALYSIS` | Phân tích dài | NVIDIA | minimax-m2.7 | 3 |
| `FALLBACK` | Fallback | → groq1 → groq0 | - | - |

### Fallback Chain
```
GROQ0 → GROQ1 → GROQ0 → GROQ0
NVIDIA → GROQ0 → GROQ1
```

Kích hoạt khi `confidence < 0.6` AND `enable_fallback = True`

### Parallel Execution
Khi `decision_type == "auto_trade"` AND `max_parallel_calls >= 2`:
- Chạy Groq-0 và Groq-1 song song
- `_consensus_vote()`: chọn kết quả có confidence cao nhất

### Confidence Scoring
```python
confidence = 0.75  # base
if contains JSON chars: += 0.10
if content length > 100: += 0.05
cap at 1.0
```

## 7.2 IntentRouter (`router.py`)

### 3 Intent Types
```python
IntentType.CHAT          # → SIMPLE pipeline, GROQ0, non-stream
IntentType.RESEARCH      # → GRAPH pipeline, GROQ0, stream
IntentType.SIGNAL        # → GRAPH pipeline, GROQ1, stream
```

### Regex Patterns (Vietnamese + English)

**CHAT patterns:**
| Pattern | Ý nghĩa |
|---------|---------|
| `^(hi|hello|hey|xin chao|chao)` | Greetings |
| `lam sao|lam gi|how to|how do` | How-to questions |
| `giai thich|cho toi biet|explain` | Giải thích |
| `ban la ai|who are you` | Identity |
| `giup toi|help me` | Help |

**RESEARCH patterns:**
| Pattern | Ý nghĩa |
|---------|---------|
| `phan tich|danh gia|analyze|analysis` | Phân tích |
| `nen mua|nen ban|should i buy|should i sell` | Advice |
| `trien vong|tang truong|outlook|growth` | Triển vọng |
| `bao cao|report|financial` | Báo cáo |
| `ket qua kinh doanh|earnings` | Kết quả KD |

**SIGNAL patterns:**
| Pattern | Ý nghĩa |
|---------|---------|
| `tin hieu|signal` | Tín hiệu |
| `mua vao|ban ra|buy|sell` | Mua/bán |
| `[A-Z]{4,6}` | Mã cổ phiếu |
| `gia|price` | Giá |
| `khuyen nghi|recommendation` | Khuyến nghị |

## 7.3 ChatLLM (`chat.py`) — Groq Implementation

### GroqChatLLM
| Method | Streaming | Tools | Callback | Usage |
|--------|-----------|-------|----------|-------|
| `stream_chat()` | ✅ `stream=True` | ✅ `tools`, `tool_choice="auto"` | `on_text_chunk` | AgentLoop ReAct |
| `chat()` | ❌ `stream=False` | ❌ | ❌ | Context compression |

### Tool Call Parsing (Streaming)
```
1. Accumulate tool_call chunks by index
2. Parse function arguments JSON
3. Error: log warning, set empty dict
```

### Reasoning Content
- Captured from `delta.reasoning_content`
- Emitted as `thinking_done` event
- Logged prefix 300 chars

## 7.4 BaseAgent (`base.py`) — Abstract Base

### Retry Strategy
```python
@retry(
    stop=stop_after_attempt(3),              # 3 attempts max
    wait=wait_exponential(multiplier=1, min=2, max=10),  # 2s, 4s, 8s
    retry=retry_if_exception_type((RateLimitError, APIError))
)
```

### Error Classification
| Exception | Condition |
|-----------|-----------|
| `RateLimitError` | Contains "rate limit" |
| `APIError` | Contains "api" or "http" |
| `LLMError` | Timeout hoặc không classify được |

### Statistics Tracking
```python
{
    "model": str,
    "call_count": int,
    "success_rate": "XX.XX%",
    "total_cost": "$0.000000",
    "avg_cost_per_call": "$0.000000"
}
```

## 7.5 GroqAgent (`groq_client.py`)

- Cost: `$0.05/1M input`, `$0.08/1M output`
- Default params: `temperature=0.7`, `max_tokens=2048`
- Single-turn: `_call_llm(prompt)`
- Multi-turn: `chat(messages)` — maps role/user/assistant/system
- Specialized: `realtime_signal(symbol, data)` — `temperature=0.3`

## 7.6 VN Prompts (`prompts/vn_prompts.py`)

System prompts tiếng Việt cho từng agent:

- **MARKET_ANALYST**: Phân tích kỹ thuật chuyên sâu (RSI, MACD, SMA, Bollinger Bands)
- **FUND_ANALYST**: Phân tích cơ bản (PE/PB, ROE/ROA, tăng trưởng doanh thu/lợi nhuận)
- **BULL_RESEARCHER**: Xây dựng luận điểm tăng giá
- **BEAR_RESEARCHER**: Nhận diện rủi ro giảm giá
- **RESEARCH_MANAGER**: Tổng hợp → 5-tier rating
- **PORTFOLIO_MANAGER**: Quyết định cuối cùng
- **Debaters**: Aggressive/Conservative/Neutral

---

# 8. Swarm Presets & DAG Engine

**7 files** + **28 YAML presets** trong `app/brain/state/`

## 8.1 Preset Loader (`presets.py`)

### YAML Preset Structure
```yaml
name: <preset_name>
title: "..." description: "..." agents:
  - id: <agent_id>
    role: <role_description>
    system_prompt: |
      <full system prompt with {template_vars}>
    tools: [bash, read_file, write_file, ...]
    skills: [skill-1, skill-2, ...]
    max_iterations: 50
    timeout_seconds: 1800
    max_retries: 1
tasks:
  - id: <task_id>
    agent_id: <ref to agent.id>
    prompt_template: "<prompt with {template_vars}>"
    depends_on: [<upstream_task_ids>]
    input_from:
      <context_key>: <upstream_task_id>
variables:
  - name: <var_name>
    description: "..."
    required: true|false
```

### Functions
| Function | Mô tả |
|----------|-------|
| `load_preset(name)` | Load YAML file từ `presets/{name}.yaml` |
| `list_presets()` | Summary info cho tất cả presets (name, title, agents, vars) |
| `inspect_preset(name)` | Dry-run validation: duplicate IDs, cycle detection, dependency chain |
| `build_run_from_preset(preset_name, user_vars)` | Core builder → SwarmRun |

### Template Variable Injection
- `_FallbackDict`: nếu thiếu variable → `"(determine the appropriate {key} based on the objective)"` — LLM tự suy luận
- `upstream_context`: reserved variable — runtime thay bằng upstream task summaries

## 8.2 SwarmRuntime (`runtime.py`) — DAG Engine

### Core Algorithm
```
1. build_run_from_preset() → SwarmRun
2. validate_dag()          → cycle detection (DFS 3-color)
3. _prefetch_grounding_data() → async OHLCV fetch
4. Compute topological_layers() → Kahn's algorithm
5. FOR EACH LAYER:
   a. Check cancel_event
   b. _execute_layer():
      - ThreadPoolExecutor(max_workers=4)
      - Per task: build upstream_summaries from input_from
      - Submit _run_worker_with_retries()
      - Layer deadline = max(timeout * retries) + 60s
   c. resolve_dependencies() → unblock downstream tasks
   d. Sync tasks snapshot
6. Determine final status (completed/failed/cancelled)
7. Final report = last task output
```

### Parallelism
```
Layer 0: [A, B, C]  ← all parallel (ThreadPoolExecutor, max 4 workers)
Layer 1: [D (depends_on: [A,B])]
Layer 2: [E (depends_on: [D])]
```

### Stale-Run Recovery
- `reap_stale_running_runs()`: sweep tất cả run directories
- `is_run_stale()`: `last_event_age > max(60s, min(heartbeat_floor, retry_ceiling))`
- Reconciliation: hydrate task files → recover terminal → reap stale

## 8.3 TaskStore (`task_store.py`) — DAG Algorithms

### Task File Structure
```
tasks/task-{id}.json  # Per-task state (atomic write via .tmp + rename)
```

### DAG Algorithms
| Function | Algorithm | Mô tả |
|----------|-----------|-------|
| `resolve_dependencies()` | Dependency resolution | Remove completed task from `blocked_by`. If empty → status `pending` |
| `validate_dag()` | DFS 3-color (WHITE/GRAY/BLACK) | Cycle detection + validates all depends_on refs |
| `topological_layers()` | Kahn's algorithm | In-degree based layering for parallel execution |

## 8.4 Worker Execution (`worker.py`)

### Worker Lifecycle
```
1. Build filtered ToolRegistry
2. Create ChatLLM
3. SkillsLoader → filter skills
4. build_worker_prompt():
   - Role + System Prompt với {upstream_context}
   - Available Skills
   - Ground Truth (OHLCV data)
   - Data Citation Discipline (HARD RULE)
   - Execution Rules (20 tool limit, 3-phase plan/execute/summarize)
5. Resolve prompt_template (str.format_map with _FallbackDict)
6. Create artifact_dir
7. WorkerAgentLoop.run(user_message, history)
8. resolve_summary() → extract từ report.md
9. classify_deliverable()
10. collect_artifacts()
```

## 8.5 29 Swarm Presets

| Preset | Agents | DAG Pattern | Mô Tả |
|--------|--------|-------------|-------|
| `vn_equity_desk` | 3 | fan-in (2→1) | VN equity: factor IC → sector rotation → stock selection → risk gate |
| `investment_committee` | 4 | fan-in (2→1→1) | Bull+Bear → Risk → PM final call |
| `risk_committee` | 3 | fan-in (3→1) | Drawdown + Tail Risk + Regime → Head of Risk |
| `quant_strategy_desk` | 3 | fan-in (2→1) | Screener + Factor → Backtest → Risk |
| `pairs_research_lab` | 4 | fan-in (2→1→1) | Correlation + Cointegration → Pair Strategist → Microstructure |
| `fundamental_research_team` | 4 | fan-in (3→1) | Financial + Valuation + Quality → Editor |
| `earnings_research_desk` | 4 | fan-in (3→1) | Fundamental + Revision + Options → Strategist |
| `sector_rotation_team` | 4 | fan-in (3→1) | Cycle + Prosperity + Flows → Strategist |
| `technical_analysis_panel` | 6 | fan-in (5→1) | Classic+Ichomoku+Harmonic+Wave+SMC → Aggregator |
| `statistical_arbitrage_desk` | 4 | fan-in (3→1) | Mean-reversion + Momentum + Relative-value → Trader |
| `social_alpha_team` | 4 | fan-in (3→1) | Reddit + StockTwits + News → Alpha |
| `sentiment_intelligence_team` | 4 | fan-in (3→1) | Fear/Greed + Flow + Options → Strategist |
| `macro_strategy_forum` | 4 | fan-in (3→1) | Global + Domestic + Policy → Strategist |
| `macro_rates_fx_desk` | 3 | pipeline (1→1→1) | Rates + FX → Macro Trader |
| `global_equities_desk` | 3 | fan-in (2→1) | Fundamental + Technical → Strategist |
| `global_allocation_committee` | 5 | fan-in (4→1) | 4 Regional → Allocation Committee |
| `geopolitical_war_room` | 3 | fan-in (2→1) | Geopolitical risk analysis |
| `fund_selection_panel` | 4 | fan-in (3→1) | Screening + Performance + Risk → Selector |
| `factor_research_committee` | 4 | hybrid (2→1→1) | Mine + Validate → Combine → Review |
| `event_driven_task_force` | 3 | fan-in (2→1) | Event ID + Impact → Strategist |
| `etf_allocation_desk` | 3 | fan-in (2→1) | Screening + Flow → Strategist |
| `equity_research_team` | 4 | pipeline (1→1→1→1) | Macro → Sector → Stock → Aggregator |
| `derivatives_strategy_desk` | 3 | fan-in (2→1) | Options + Vol → Strategist |
| `credit_research_team` | 3 | fan-in (2→1) | Credit Spread + Issuer → Strategist |
| `convertible_bond_team` | 3 | fan-in (2→1) | CB Valuation + Equity-linked → Strategist |
| `portfolio_review_board` | 3 | fan-in (2→1) | Position + Risk → Rebalance |
| `ml_quant_lab` | 3 | fan-in (2→1) | Features + Model → Backtest |
| `commodity_research_team` | 3 | fan-in (2→1) | Supply + Demand → Cycle Strategist |
| `oil_gas_analyst` | 3 | pipeline (1→1→1) | E&P + Refining → Synthesis |

---

# 9. Quant Engine

**20+ files** trong `app/brain/quant/`

## 9.1 Data Pipeline (`pipeline.py`)

3-step preprocessing:

### Step 1: Imputation
| Method | Strategy |
|--------|----------|
| `ffill_bfill` | Forward fill → Backward fill (axis=0 time) |
| `ffill` | Forward fill only |
| `bfill` | Backward fill only |
| `zero` | Fill NaN với 0 |

### Step 2: Winsorization
- Row-wise clipping per date
- Default: cap at 1st and 99th percentile
- Công thức: `clip(lower=quantile(0.01), upper=quantile(0.99))`

### Step 3: Z-Score Normalization
- Cross-sectional: `(x - mu) / sigma` per date
- `min_std = 1e-12` (tránh division by zero)
- Replaces inf với NaN

## 9.2 VN IC Tester (`factors/vn_ic_tester.py`) — 1421 lines

### VN-Specific Constraints
```python
VN_CONSTRAINTS = {
    "settlement_lag": 2,        # T+2 settlement
    "entry_offset": 1,          # T+1 first tradable
    "min_stocks": 30,           # Minimum universe
    "min_value_bn": 5.0,        # 5 tỷ VND daily avg
    "price_limit": 0.07,        # HOSE ±7%
    "holding_periods": [5, 10, 20],
    "min_dates": 20,
}
```

### HOSE Price Steps
```python
HOSE_PRICE_STEPS = [
    (10_000, 10),      # ≤10K → step 10
    (50_000, 50),      # ≤50K → step 50
    (100_000, 100),    # ≤100K → step 100
    (200_000, 500),    # ≤200K → step 500
    (inf, 1_000),      # >200K → step 1000
]
```

### 10 Core VN Factors

| Factor | Group | Direction | Công Thức |
|--------|-------|-----------|-----------|
| `SIZE` | risk | -1 (large cap premium) | `ln(market_cap)` |
| `VOL_20D_ORTHO` | risk | -1 | Orthogonalized 20d vol vs 60d vol |
| `EVEBITDA_INV` | value | +1 | `1 / EV/EBITDA` |
| `HML_REAL` | value | +1 | `1 / PB` (book-to-market) |
| `ROE_NORM` | quality | +1 | ROE (TTM normalized) |
| `NM` | quality | +1 | Net Margin (TTM) |
| `GM` | quality | +1 | Gross Margin (TTM) |
| `YOY_REV` | quality | +1 | YoY Revenue Growth |
| `PIOTROSKI_F` | quality | +1 | 2-point F-score |
| `FOREIGN_NET_5D` | flow | +1 | 5d net foreign / mcap |

### Event-Study Factors
| Factor | Group | Direction | Mô tả |
|--------|-------|-----------|-------|
| `CEILING_STREAK` | behavioral | -1 | Consecutive days hitting ceiling |
| `TET_WINDOW` | behavioral | +1 | Tết holiday effect |
| `FORCED_SELLING` | behavioral | +1 | Floor hits + volume spike |

### Factor Computation Flow
```
1. Load OHLCV (365 days)
2. Price steps + ceiling computation
3. Load fundamentals (financial_ratios table)
4. Load foreign flow (foreign_flow table)
5. Load insider trades
6. Load financial statements (BS/IS/CF)
7. Per-symbol factor computation:
   - Market data: VOL_20D, VOL_60D, SIZE, TET_WINDOW
   - Fundamental: PB_INV, HML_REAL, GM, NM, ROE_NORM, YOY_REV
   - Flow: FOREIGN_NET_5D
   - Event: CEILING_STREAK, FORCED_SELLING
8. VOL_20D orthogonalization: linear regression vs VOL_60D → residual
9. TTM override: trailing 4 quarters net_income/equity
10. Sector-neutral percentile rank (prepare_factor_for_ic)
```

### Forward Returns (T+2 Adjusted)
```
Entry: close at T+1 (first tradable after T+2 settlement)
Exit: close at T+1+H
Return = P(T+1+H) / P(T+1) - 1
```

### IC Testing Methodology
1. **Spearman rank correlation** (robust to fat tails / price limits)
2. **Benjamini-Hochberg** multiple testing correction
3. **Walk-forward validation**: 5-split expanding window
4. **Liquidity filter**: min 5B VND daily average value (20d)
5. **Return winsorization**: ±7% HOSE price limit
6. **Survivorship bias-free**: uses all symbols present at each date

### Cache Preloading
- `_preload_all_static()`: load tất cả data 1 lần trước loop IC benchmark
- Tránh 1000+ SQL round trips per date
- Caches: meta, fundamentals, foreign flow, insider trades, financial statements

## 9.3 Factor Analysis Core (`factors/factor_analysis_core.py`)

### Pure IC/IR Math
| Function | Công Thức |
|----------|-----------|
| `compute_ic_series(factor, returns)` | Spearman rank correlation per period |
| `compute_mean_ic(ic_series)` | Mean IC + ICIR (IC / std(IC)) |
| `compute_factor_return(factor, returns)` | Long-short factor return |
| `compute_quantile_returns(factor, returns, n_quantiles=5)` | Quintile returns |

## 9.4 Sector Neutralizer (`factors/sector_neutralizer.py`)

### Sector-Neutral Z-Score
1. Group symbols by sector (ICB classification)
2. Within each sector: Z-score normalization
3. Sectors with < 5 stocks: use global mean/std
4. Config: `KNOWN_FACTOR_CONFIGS` — direction per factor

## 9.5 Factor Orthogonalization (`factors/factor_orthogonalization.py`)

### Methods
| Method | Mô tả |
|--------|-------|
| Gram-Schmidt | Sequential orthogonalization |
| PCA | Principal Component Analysis — decorrelate all factors |

## 9.6 Hypothesis Registry (`hypotheses/registry.py`)

### Status Lifecycle
```
exploring → testing → validated | rejected | monitoring
```

### Features
- User voting, comments, evidence tracking
- Tag-based search
- File: `~/.vibe-trading/hypotheses.json`

### Hypotheses Tests
| Test | File | Mô tả |
|------|------|-------|
| Base Test | `test_base.py` | Abstract base class |
| Tết Effect | `test_tet.py` | Lunar New Year seasonality |
| Insider Trading | `test_insider.py` | Insider trading alpha |
| Foreign Flow | `test_foreign_flow.py` | Foreign flow momentum |

## 9.7 Skills Data (`skills_data/`) — 40+ Skills

Mỗi skill = thư mục với `SKILL.md` + optional files:

| Category | Skills |
|----------|--------|
| **Technical** | technical-basic, candlestick, ichimoku, elliott-wave, harmonic, chanlun, smc |
| **Quantitative** | multi-factor, volatility, minute-analysis |
| **Fundamental** | fundamental-filter, dividend-analysis, financial-statement, earnings-forecast, earnings-revision |
| **Risk** | credit-analysis, convertible-bond, hedging-strategy |
| **Portfolio** | asset-allocation, execution-model |
| **Research** | behavioral-finance, correlation-analysis, cross-market-strategy, commodity-analysis, etf-analysis |
| **Tools** | data-routing, doc-reader, backtest-diagnose, report-generate, corporate-events, vnpy-export |

---

# 10. 7-Layer Risk System

**12 files** trong `app/brain/risk/`

## 10.1 VNCompositeRiskScorer (`composite_scorer.py`)

### 7-Layer Weighted Scoring

| Layer | Weight | Tên | Flag Prefixes |
|-------|--------|-----|--------------|
| 1. Quant | 20% | CVaR, volatility, drawdown | CVAR_, VOLATILITY_, MOMENTUM_, LIQUIDITY_ |
| 2. Fundamental | 20% | PE/PB extremes, Piotroski, Beneish | HIGH_ACCRUAL, ALTMAN_Z_, HIGH_LEVERAGE, WEAK_FSCORE, M_SCORE_ |
| 3. Market VN | 20% | Pledge, margin call, floor trap | PRICE_LIMIT_, VOLUME_SPIKE_, MARGIN_CASCADE_, FLOOR_TRAP, PLEDGE_NEWS_ |
| 4. Macro VN | 15% | CPI, PMI, credit growth, SBV | RATE_RISING, VND_WEAKENING, CREDIT_OVERHEAT, SYSTEM_NPL_ |
| 5. Global | 10% | Fed, USD, oil, EM flows | VIX_ELEVATED, DXY_STRONG, OIL_HIGH, CHINA_SELLOFF |
| 6. Regulatory | 10% | Audit, investigation, delist | UNDER_INVESTIGATION, CRITICAL_REGULATORY_, TAX_DISPUTE, REGULATORY_, GOVERNANCE_ |
| 7. Behavioral | 5% | Sentiment extreme, pump pattern | FOMO_PATTERN, FUD_PATTERN, PUMP_PATTERN, DUMP_PATTERN, TET_APPROACHING |

### Sector Overrides

| Sector | Weight Changes |
|--------|---------------|
| BANKS | fundamental: 0.25 (+0.05), regulatory: 0.15 (+0.05) |
| FINANCIAL_SERVICES | fundamental: 0.25 (+0.05) |
| REAL_ESTATE | regulatory: 0.20 (+0.10), market: 0.25 (+0.05) |
| CONSTRUCTION | regulatory: 0.15 (+0.05), market: 0.22 (+0.02) |
| BASIC_RESOURCES | global: 0.18 (+0.08), macro: 0.12 (+0.02) |
| EXPORT | global: 0.18 (+0.08), macro: 0.12 (+0.02) |

### Hard Block Flags (DO_NOT_TRADE)
```
CRITICAL_REGULATORY_ACTION
UNDER_INVESTIGATION
ADVERSE_AUDIT_OPINION
DELIST_CONFIRMED
TRADING_SUSPENDED
```

### Soft Block Flags (REQUIRE_HUMAN_REVIEW)
```
QUALIFIED_AUDIT_OPINION
EXTREME_PLEDGE_RATIO
PUMP_PATTERN_DETECTED
NEAR_MARGIN_CALL
FLOOR_TRAP
```

### Recommendation Ladder
| CRS Score | Recommendation |
|-----------|---------------|
| hard_blocked | DO_NOT_TRADE |
| > 0.80 | DO_NOT_TRADE |
| > 0.55 | REQUIRE_HUMAN_REVIEW |
| > 0.40 | REDUCE_SIZE_50PCT |
| > 0.25 | REDUCE_SIZE_25PCT |
| ≤ 0.25 | NORMAL_SIZING |

### Risk Level Ladder
| CRS Score | Risk Level |
|-----------|------------|
| hard_blocked | BLOCKED |
| > 0.55 | HIGH |
| > 0.40 | MEDIUM_HIGH |
| > 0.25 | MEDIUM |
| > 0.15 | LOW |
| ≤ 0.15 | VERY_LOW |

## 10.2 Layer Implementations

### Tier 1 — Quant Risk
| Component | Metric | Threshold | Score | Flag |
|-----------|--------|-----------|-------|------|
| CVaR 95% (tail risk) | abs(cvar) > 0.04 | > 4% | +0.30 | CVAR_HIGH |
| CVaR 95% | > 0.03 | > 3% | +0.20 | CVAR_MEDIUM |
| CVaR 95% | > 0.02 | > 2% | +0.10 | - |
| 60d Volatility | vol > 0.035 | > 3.5% | +0.25 | VOLATILITY_HIGH |
| 60d Volatility | vol > 0.025 | > 2.5% | +0.15 | VOLATILITY_MEDIUM |
| Max Drawdown 20d | mdd > 0.15 | > 15% | +0.25 | MOMENTUM_CRASH |
| Max Drawdown 20d | mdd > 0.10 | > 10% | +0.15 | - |
| Amihud Illiquidity | amihud > 0.01 | > 0.01 | +0.20 | LIQUIDITY_RISK |
| Amihud Illiquidity | amihud > 0.005 | > 0.005 | +0.10 | - |

### Tier 2 — Fundamental Risk
| Component | Metric | Threshold | Score | Flag |
|-----------|--------|-----------|-------|------|
| Accrual Ratio | (NI-CFO)/TA > 0.20 | > 20% | +0.35 | HIGH_ACCRUAL |
| Accrual Ratio | > 0.10 | > 10% | +0.15 | - |
| Altman Z' (emerging) | Z' < 1.1 | distress | +0.30 | ALTMAN_Z_DISTRESS |
| Altman Z' | Z' < 2.6 | grey zone | +0.15 | ALTMAN_Z_GREY |
| Debt/Equity | D/E > 3.0 | > 3.0 | +0.25 | HIGH_LEVERAGE |
| Debt/Equity | D/E > 2.0 | > 2.0 | +0.10 | - |
| Piotroski F-Score | F < 4 | weak | +0.20 | WEAK_FSCORE |
| Beneish M-Score | M > -2.22 | manipulation | +0.25 | M_SCORE_RISK |

Altman Z' Formula (emerging market):
```
Z' = 3.25 + 6.56*X1 + 3.26*X2 + 6.72*X3 + 1.05*X4
X1 = Working Capital / Total Assets
X2 = Retained Earnings / Total Assets
X3 = EBIT / Total Assets
X4 = Market Cap / Total Liabilities
```

### Tier 3 — Market Structure Risk
| Component | Metric | Threshold | Score | Flag |
|-----------|--------|-----------|-------|------|
| Price limit hit | mom_1d ≤ -6.9% | floor hit | +0.35 | PRICE_LIMIT_HIT |
| Near floor | mom_1d ≤ -5% | near floor | +0.20 | NEAR_FLOOR |
| Near floor | mom_1d ≤ -3% | mild | +0.10 | - |
| Volume anomaly | vol_ratio > 5.0x | extreme | +0.25 | VOLUME_SPIKE_EXTREME |
| Volume anomaly | vol_ratio > 3.0x | high | +0.15 | VOLUME_SPIKE_HIGH |
| Margin cascade | mom_1d < -6% AND vol > 3x | combined | +0.30 | MARGIN_CASCADE_PROXY |
| Floor trap | 2+ consecutive floor hits | streak | +0.20 | FLOOR_TRAP |
| Pledge news | contains pledge keywords | any | +0.25 | PLEDGE_NEWS_PROXY |

Pledge keywords (Vietnamese): `cầm cố`, `giải chấp`, `call margin`, `bán giải chấp`

### Tier 4 — Macro VN Risk

Sector sensitivities:
| Sector | Rate | FX | Credit |
|--------|:----:|:--:|:------:|
| BANKS | 0.8 | 0.4 | 1.3 |
| REAL_ESTATE | 0.9 | 0.5 | 1.3 |
| CONSTRUCTION | 0.7 | 0.3 | 1.3 |
| FINANCIAL_SERVICES | 0.3 | - | - |
| UTILITIES | 0.7 | - | - |
| EXPORT | 0.2 | 0.6 | - |
| BASIC_RESOURCES | - | 0.7 | - |
| RETAIL_TRADE | - | 0.8 | - |
| Default | 0.3 | 0.1 | 1.0 |

| Component | Score Formula | Flag |
|-----------|---------------|------|
| Rate rising (RE/Utils) | 0.6 * rate_mag | RATE_RISING |
| Rate rising (Banks) | 0.3 * rate_mag | - |
| Rate rising (others) | 0.15 * rate_mag | - |
| VND weakening | 0.5 * fx_mag | VND_WEAKENING |
| VND strengthening | 0.1 * fx_mag | - |
| Credit growth > 20% | 0.30 * credit_mult | CREDIT_OVERHEAT |
| System NPL > 4% | 0.35 * credit_mult | SYSTEM_NPL_HIGH |

### Tier 5 — Global Risk

| Sector | Global Sensitivity |
|--------|:-----------------:|
| EXPORT | 0.8 |
| BASIC_RESOURCES | 0.7 |
| OIL_GAS | 0.6 |
| TECHNOLOGY | 0.4 |
| BANKS | 0.3 |
| FOOD_BEVERAGE | 0.2 |
| Default | 0.2 |

| Component | Score Formula | Flag |
|-----------|---------------|------|
| VIX > 25 | ((VIX-15)/30) * 0.6 * mag | VIX_ELEVATED |
| DXY > 108 | ((DXY-100)/15) * 0.4 * mag | DXY_STRONG |
| Oil > 90 | 0.10 * mag | OIL_HIGH |
| Oil < 50 | 0.05 * mag | - |
| CSI300 < -5% | 0.15 * mag | CHINA_SELLOFF |

### Tier 6 — Regulatory Risk

Sector base risk: REAL_ESTATE(0.40), EDUCATION(0.40), PHARMA(0.35), BANKS(0.30), OIL_GAS(0.25)

Legal flag mapping (VN keywords → CRS flags):
| Vietnamese Keyword | Flag | Score Add |
|-------------------|------|:---------:|
| bị khởi tố / tạm giam | UNDER_INVESTIGATION | +0.35 |
| hủy niêm yết / đình chỉ giao dịch | CRITICAL_REGULATORY_ACTION | +0.50 |
| truy thu thuế | TAX_DISPUTE | +0.25 |
| thao túng thị trường | UNDER_INVESTIGATION | +0.35 |
| vi phạm công bố thông tin | DISCLOSURE_VIOLATION | +0.10 |
| thanh tra ủy ban | REGULATORY_PROBE | +0.10 |
| xử phạt | REGULATORY_FINE | +0.15 |

Governance shock keywords: `từ nhiệm`, `miễn nhiệm`, `thay ceo`, `thay chủ tịch`
- ≥ 3 events: +0.25 + GOVERNANCE_SHOCK
- ≥ 1 event: +0.10

### Tier 7 — Behavioral Risk

| Component | Condition | Score | Flag |
|-----------|-----------|:-----:|------|
| FOMO | 5d return > 15% AND vol > 3x | +0.40 | FOMO_PATTERN |
| FOMO (mild) | 5d return > 10% AND vol > 2.5x | +0.20 | - |
| FUD | 5d return < -10% AND sentiment < -0.3 | +0.30 | FUD_PATTERN |
| Pump | 5d return > 8% AND vol > 4x | +0.25 | PUMP_PATTERN |
| Dump | 5d return < -8% AND vol > 4x | +0.25 | DUMP_PATTERN |
| Tết approaching | 0-14 days before Tet | +0.15 | TET_APPROACHING |
| Tết during | -7 to 0 days | +0.10 | - |

Tết calendar: 2024(Feb 10-16), 2025(Jan 29-Feb 2), 2026(Feb 17-23), 2027(Feb 6-12)

## 10.3 Query Helpers (`queries.py`)

### Functions
| Function | Mô tả |
|----------|-------|
| `get_active_flags(symbol, cur)` | Merge flags từ `risk_flags` + `risk_assessments` tables |
| `get_hard_blocked(symbol)` | Quick boolean check từ latest assessment |
| `get_soft_flag_count(symbol)` | Count soft flags từ latest assessment |

### Table Schemas
- `risk_flags`: `flag_type`, `effective_date`, `description`, `symbol`, `is_active`
- `risk_assessments`: `symbol`, `assessment_date`, `all_flags`(text[]), `hard_flags`(text[]), `soft_flags`(text[]), `risk_level`, `crs_score`, `hard_blocked`, `soft_blocked`

## 10.4 CafeF Proxy (`layers/cafef_proxy.py`)

Web scraper cho CafeF (cafef.vn):
- Search URL: `https://cafef.vn/tim-kiem.chn`
- Keywords T3: `cầm cố`, `giải chấp`, `call margin`, `bán giải chấp`
- Keywords T6: `bị khởi tố`, `tạm giam`, `hủy niêm yết`, ... (9 keywords)
- Symbol extraction: regex `\b([A-Z]{2,4})\b` với `SKIP_WORDS` filter
- BeautifulSoup HTML parsing

---

# 11. Market Data Pipeline

**~30 files** trong `app/services/dnse/` + `app/brain/dataflows/`

## 11.1 DNSE Stream Hub (`stream_hub.py`) — 719 lines

### DnseStreamHub — Singleton Orchestrator

| Method | Mô tả |
|--------|-------|
| `start()` | Start background WebSocket loop thread |
| `stop()` | Flag loop to stop |
| `mode` | `"live"` / `"connecting"` / `"mock"` |
| `status` | Comprehensive status dict |
| `get_quote(symbol)` | Thread-safe cached quote |
| `get_orderbook(symbol)` | Cached orderbook |
| `get_snapshot()` | All cached quotes |
| `get_trade_history(symbol, limit)` | Redis List |
| `get_ohlc_history(symbol, resolution, from, to, limit)` | Redis Sorted Set |
| `get_ohlc_live(symbol)` | Live (in-progress) candle |
| `subscribe_symbols(symbols)` | Add to subscription set |

### Data Flow
```
WebSocket → TradingClient → handlers
  → In-memory caches (dicts)
  → Redis Pub/Sub (real-time)
  → Redis Streams (durable replay)
  → Redis Lists (trade history)
  → Redis Sorted Sets (OHLC history)
```

### Broadcast Channels
| Channel | Frequency | Data |
|---------|-----------|------|
| `{prefix}:snapshot` | Periodic | Full market snapshot |
| `{prefix}:breadth` | Periodic | Advancers/decliners/unchanged |
| `{prefix}:liquidity` | 5s | Total trading value + top 10 |
| `{prefix}:heatmap` | 10s | Sector-based heatmap |
| `{prefix}:indices` | On update | All market indices |

### Reconnection
- Exponential backoff: 1s, 2s, 4s, ... up to 20 retries
- `_replay_missed_streams()`: replay từ Redis Streams sau reconnect

## 11.2 Market Session (`market_session.py`)

### MarketState
| State | HOSE Time | HNX Time |
|-------|-----------|----------|
| PRE_OPEN | 08:30-09:00 | 08:30-09:00 |
| OPENING_AUCTION (ATO) | 09:00-09:15 | 09:00-09:15 |
| CONTINUOUS_MORNING | 09:15-11:30 | 09:15-11:30 |
| LUNCH_BREAK | 11:30-13:00 | 11:30-13:00 |
| CONTINUOUS_AFTERNOON | 13:00-14:30 | 13:00-14:15 |
| CLOSING_AUCTION (ATC) | 14:30-14:45 | 14:15-14:30 |
| CLOSED | 14:45+ | 14:30+ |

## 11.3 WebSocket Client (`websocket/client.py`) — 809 lines

### TradingClient

**Connection:** `wss://ws-openapi.dnse.com.vn/v1/stream?encoding={json|msgpack}`

**Auth:** HMAC-SHA256 signature
```
signature = HMAC-SHA256("{api_key}:{timestamp}:{nonce}", secret)
```

**Message Type Map:**
| Code | Event | Model |
|------|-------|-------|
| `t` | trade | `Trade` |
| `te` | trade_extra | `TradeExtra` |
| `e` | expected_price | `ExpectedPrice` |
| `sd` | security_definition | `SecurityDefinition` |
| `q` | quote | `Quote` |
| `b` | ohlc | `Ohlc` |
| `bc` | ohlc_closed | `Ohlc` |
| `mi` | market_index | `MarketIndex` |
| `f` | foreign | `ForeignInvestor` |
| `do/eo` | order_event | `Order` |
| `dp/ep` | position_event | `Position` |
| `a` | account | `AccountUpdate` |

**Internal Architecture:**
```
[_message_handler] → [dispatch queues ×6] → [_dispatch_worker ×6]
  (reads, decodes)    (per-symbol hash)     (typed models → callbacks)
```

**Subscription Channels:**
| Channel | Method | Format |
|---------|--------|--------|
| `tick.{board}.{enc}` | `subscribe_trades()` | Per-board |
| `top_price.{board}.{enc}` | `subscribe_quotes()` | Boards G1-G7 |
| `ohlc.{resolution}.{enc}` | `subscribe_ohlc()` | All resolutions |
| `ohlc_closed.{resolution}.{enc}` | `subscribe_ohlc_closed()` | Closed candles |
| `market_index.{index}.{enc}` | `subscribe_market_index()` | HOSE, HNX, VN30... |
| `foreign.{board}.{enc}` | `subscribe_foreign_trading()` | Foreign flow |
| `security_definition.{board}.{enc}` | `subscribe_sec_def()` | Security master |
| `expected_price.{board}.{enc}` | `subscribe_expected_price()` | Expected price |
| `order.{market_type}.{enc}` | `subscribe_order_event()` | Order events |
| `position.{market_type}.{enc}` | `subscribe_position_event()` | Position events |
| `account` | `subscribe_account()` | Account updates |

## 11.4 REST API Client (`api/client.py`) — 411 lines

### DNSEClient
- Base URL: `https://openapi.dnse.com.vn`
- HTTP: `urllib3.PoolManager` (10 pools, 10 max, 30s connect / 60s read)
- HMAC signature per request

### Endpoints

**Account:**
| Method | Endpoint |
|--------|----------|
| `get_accounts()` | `GET /accounts` |
| `get_balances(account_no)` | `GET /accounts/{no}/balances` |
| `get_positions(account_no)` | `GET /accounts/{no}/positions` |
| `get_orders(account_no)` | `GET /accounts/{no}/orders` |

**Trading:**
| Method | Endpoint |
|--------|----------|
| `post_order(market_type, payload, token)` | `POST /accounts/orders` |
| `put_order(account_no, order_id, ...)` | `PUT /accounts/{no}/orders/{id}` |
| `cancel_order(account_no, order_id, ...)` | `DELETE /accounts/{no}/orders/{id}` |
| `create_trading_token(otp_type, passcode)` | `POST /registration/trading-token` |

**Price:**
| Method | Endpoint |
|--------|----------|
| `get_security_definition(symbol)` | `GET /price/{symbol}/secdef` |
| `get_ohlc(bar_type, query)` | `GET /price/ohlc` |
| `get_trades(symbol, ...)` | `GET /price/{symbol}/trades` |
| `get_latest_trade(symbol)` | `GET /price/{symbol}/trades/latest` |
| `get_close_price(symbol)` | `GET /price/{symbol}/close` |

## 11.5 Rate Limiter (`rate_limiter.py`)

### TokenBucket
- Standard algorithm: `rate` tokens/s, `capacity` burst
- Thread-safe via `threading.Lock()`

### RateLimitedPublisher
- 2 tiers: high-freq (10/s, cap 20), low-freq (2/s, cap 5)
- Per-channel bucket
- Tracks: published/dropped stats

## 11.6 Validation Models (`models.py`)

Pydantic validation cho stream data:
```
ValidatedTrade, ValidatedOrderBook, ValidatedMarketIndex,
ValidatedForeignTrading, ValidatedOhlc, ValidatedExpectedPrice,
ValidatedSecurityDef, ValidatedTradeExtra
```

## 11.7 Composite Pipeline (`dataflows/vendors/vn/composite_pipeline.py`) — 633 lines

### Full Pipeline
```
1. Load factor_details from PostgreSQL
2. Load sector map (FINANCIALS / REAL_ESTATE / OTHERS)
3. Compute sector-neutral Z-scores (per factor)
4. IC-weighted composite:
   composite = sum(w_i * z_i * direction_i) / sum(w_i)
5. Load risk flags + CRS scores + foreign flow
6. Apply risk gate (confidence scorer)
7. Build portfolio: top 15 stocks, score-weighted, max 5% per stock
8. Write to PostgreSQL (factor_scores + portfolio_weights tables)
```

### IC Weights (8 Factors)
| Factor | Weight |
|--------|:------:|
| ROE_NORM | 0.077 |
| HML_REAL | 0.075 |
| NM | 0.051 |
| SIZE | 0.040 |
| YOY_REV | 0.032 |
| PIOTROSKI_F | 0.026 |
| VOL_20D_ORTHO | 0.023 |
| GM | 0.023 |

### Portfolio Construction
1. Filter blocked stocks (confidence = 0 → composite = -99)
2. Top-decile selection (n_top = 15)
3. Score-weighting: `weight_i = score_i / sum(scores)`
4. Max weight cap: 5% per stock
5. Residual redistribution

---

# 12. Cross-Session Memory

**1 file** trong `app/brain/memory/`

## PersistentMemory (`memory/persistent.py`)

### File Structure
```
~/.vibe-trading/memory/
├── MEMORY.md         # Index file (< 200 lines)
├── user_prefs.md     # User preferences
├── project_btc.md    # Project-specific
└── ...
```

### Entry Format
```yaml
---
title: <title>
type: user | feedback | project | reference
tags: [tag1, tag2]
created: <ISO date>
updated: <ISO date>
---
<content (max 8000 chars)>
```

### Operations
| Method | Mô tả |
|--------|-------|
| `save(title, type, content, tags)` | Create/update entry |
| `read(title)` | Retrieve entry |
| `search(query)` | TF-IDF weighted search (metadata ×2) |
| `update(title, content, tags)` | Update existing |
| `delete(title)` | Remove entry |

### Tokenization
- CJK + Latin + Arabic + Hebrew support
- Returns max 5 results

---

# 13. Evaluation System

**2 files** trong `app/brain/eval/`

## SignalTracker (`eval/signal_tracker.py`)

### PostgreSQL Table: `signal_log`
| Field | Mô tả |
|-------|-------|
| `symbol`, `direction`, `confidence` | Signal metadata |
| `entry_price`, `target_price`, `stop_loss` | Price levels |
| `source` | factor / hypothesis / agent / composite |
| `factors` | JSON factor contributions |
| `actual_return`, `hit` | Actual outcome |
| `max_favorable`, `max_adverse` | Max favorable/adverse excursion |

### Methods
| Method | Mô tả |
|--------|-------|
| `log_signal(signal)` | Log signal to DB |
| `evaluate_signal(signal_id, actual_return)` | Evaluate outcome |
| `get_performance_stats(source, start_date, end_date)` | Performance stats |

## LLM Judge (`eval/llm_judge.py`)

### 6 Criteria (score 1-10)
| Criterion | Mô tả |
|-----------|-------|
| `factual_accuracy` | Độ chính xác thông tin |
| `reasoning_quality` | Chất lượng suy luận |
| `risk_awareness` | Nhận thức rủi ro |
| `vn_context` | Bối cảnh VN |
| `actionability` | Tính khả thi |
| `overall` | Tổng thể |

### Verdict
- `good`: average ≥ 7
- `average`: 4-7
- `poor`: < 4

---

# 14. Dataflow Routing

**~13 files** trong `app/brain/dataflows/`

## Routing Interface (`interface.py`)

### Tool Categories
```python
TOOLS_CATEGORIES = {
    "core_stock_apis":      ["get_stock_data"],
    "technical_indicators": ["get_indicators"],
    "fundamental_data":     ["get_fundamentals", "get_balance_sheet", ...],
    "news_data":            ["get_news", "get_global_news", ...],
}
```

### Functions
| Function | Mô tả |
|----------|-------|
| `route_to_vendor(method, *args)` | Route method → vendor implementation |
| `get_vendor(category, method)` | Get configured vendor |
| `get_category_for_method(method)` | Get category for method |

## yFinance Integration (`y_finance.py`)

### Functions
| Function | Mô tả |
|----------|-------|
| `get_YFin_data_online(symbol, start, end)` | OHLCV via yfinance |
| `get_stock_stats_indicators_window(symbol, indicator, curr_date, lookback)` | Technical indicators |
| `get_fundamentals(ticker, curr_date)` | 29 fundamental fields |
| `get_balance_sheet(ticker, freq)` | Balance sheet CSV |
| `get_cashflow(ticker, freq)` | Cash flow CSV |
| `get_income_statement(ticker, freq)` | Income statement CSV |
| `get_insider_transactions(ticker)` | Insider transactions CSV |

### Supported Indicators (13)
```
close_50_sma, close_200_sma, close_10_ema, macd, macds, macdh,
rsi, boll, boll_ub, boll_lb, atr, vwma, mfi
```

## VN-Specific Data Vendors

### OHLCVTool (`vendors/vn/ohlcv_tool.py`)
| Method | Mô tả |
|--------|-------|
| `get_ohlcv(symbol, start, end, timeframe)` | OHLCV từ MarketDataService |
| `get_latest_price(symbol)` | Real-time price |
| `get_price_range(symbol, days)` | N-day high/low/avg/current |

### IndicatorsTool (`vendors/vn/indicators_tool.py`)
| Method | Công Thức |
|--------|-----------|
| `calculate_rsi(prices, period=14)` | RSI = 100 - 100/(1 + avg_gain/avg_loss) |
| `calculate_macd(prices, fast=12, slow=26, signal=9)` | MACD line, signal, histogram |
| `calculate_sma(prices, period=20)` | Simple Moving Average |
| `calculate_ema(prices, period=20)` | Exponential Moving Average |
| `calculate_bollinger_bands(prices, period=20, std=2.0)` | Upper/middle/lower bands |
| `get_all_indicators(ohlcv)` | All indicators at once |

### FundamentalsTool (`vendors/vn/fundamentals_tool.py`)
| Method | Mô tả |
|--------|-------|
| `get_fundamentals(symbol)` | P/E, P/B, EPS, ROE, ROA, D/E, Market Cap... |
| `get_financial_ratios(symbol)` | Extended ratios + valuation score 0-100 |
| `_classify_valuation(pe, pb)` | VN-specific: P/E<10 & P/B<1.0 → UNDervalued |
| `_classify_profitability(roe)` | ROE>20 → EXCELLENT, >15 → GOOD |

---

# 15. Machine Learning & Deep Learning

**5 files** — Classical ML (XGBoost/RF), Deep Learning (LSTM/MLP), Parameter Optimization, News RAG

## 15.1 ML Alpha Predictor (`services/ml_alpha_predictor.py`) — 376 lines

XGBoost/Random Forest regression trên factor zoo để dự đoán forward return 5 ngày cho cổ phiếu VN.

### Pipeline
```
1. Fetch OHLCV 365 ngày (market_data_service)
2. Compute feature panel (48 features)
3. Impute NaN (median) + train/test split (80/20)
4. Train model (XGBoost hoặc Random Forest)
5. Predict forward return
6. Return prediction + feature importance
```

### Features (48 features từ factor zoo)

| Nhóm | Features | Số lượng |
|------|----------|:--------:|
| Momentum | ret_5d, ret_10d, ret_20d, ret_60d | 4 |
| Volatility | vol_5d, vol_10d, vol_20d, vol_60d | 4 |
| Volume | volume_ma_5/10/20/60d, volume_ratio_5/10/20/60d | 8 |
| RSI | rsi_14 | 1 |
| MACD | macd, macd_signal | 2 |
| Bollinger | bb_position, bb_width | 2 |
| ATR | atr_14 | 1 |
| Price/SMA | price_sma_10, price_sma_20, price_sma_50 | 3 |
| VPT | volume_price_trend | 1 |
| Alpha zoo | alpha_001..alpha_054, carhart_mom, beta5, correlation10, std20, roc20, rsv_kd | 22 |

### Models

**XGBoost** (default):
```python
XGBRegressor(
    n_estimators=200, max_depth=6, learning_rate=0.05,
    subsample=0.8, colsample_bytree=0.8, random_state=42
)
```

**Random Forest**:
```python
RandomForestRegressor(
    n_estimators=200, max_depth=8, random_state=42, n_jobs=-1
)
```

### Key Functions

| Function | Mô tả |
|----------|-------|
| `train_model(symbol, model_type, force_retrain)` | Fetch data → engineer features → train → save pickle → metrics |
| `predict_alpha(symbol, model_type)` | Load model → fetch latest data → predict → direction + confidence |
| `_fetch_factor_panel(symbol)` | OHLCV → 48 features + target (5d forward return) |
| `_prepare_data(panel)` | Drop NaN target → impute feature NaN → X, y |

### Output

```python
train_model() → {
    "symbol": "ACB", "model_type": "xgboost", "status": "trained",
    "training_samples": 210, "test_samples": 53, "feature_count": 48,
    "metrics": {
        "train_mae": 0.012, "test_mae": 0.018,
        "train_rmse": 0.015, "test_rmse": 0.022,
        "train_r2": 0.45, "test_r2": 0.22,
    },
    "top_features": [{"name": "ret_20d", "importance": 0.12}, ...]
}

predict_alpha() → {
    "symbol": "ACB", "model": "xgboost",
    "predictionDate": "2025-06-10",
    "predicted5dReturn": 2.35,  # %
    "direction": "BUY", "confidence": 0.72,
    "topFactors": [{"factor": "ret_20d", "importance": 0.12, "currentValue": 0.05}, ...]
}
```

### Model Persistence
```
Model dir: $ML_MODEL_DIR/ml_alpha_models/ (default: tempdir)
File: {symbol}_{model_type}.pkl    # Pickle model
File: {symbol}_{model_type}_features.json  # Feature column names
```

## 15.2 Deep Learning Alpha (`services/deep_learning_alpha.py`) — 265 lines

LSTM sequence model (30-day lookback, 20 features) với fallback sklearn MLP.

### Architecture

**TensorFlow LSTM** (primary):
```python
Input(shape=(30, 20))
  → LSTM(64, return_sequences=True) → Dropout(0.2)
  → LSTM(32, return_sequences=False) → Dropout(0.2)
  → Dense(16, relu) → Dense(1)
Optimizer: Adam(lr=0.001), Loss: MSE
EarlyStopping(patience=5)
```

**Fallback sklearn MLP** (khi TF không available):
```python
MLPRegressor(
    hidden_layer_sizes=(64, 32, 16),
    activation="relu", max_iter=500, random_state=42
)
```

### Training Pipeline
```
1. _build_sequence_features(symbol):
   - Fetch factor panel (48 features từ ml_alpha_predictor)
   - Normalize (z-score across time)
   - Create sequences: X shape (n_samples, 30, 20)
   - Target: forward 5-day return
2. Train/val/test split: 80/10/10
3. Train with EarlyStopping (50 epochs max)
4. Save model (.keras cho TF, .pkl cho sklearn)
```

### Key Functions

| Function | Mô tả |
|----------|-------|
| `train_lstm(symbol, force_retrain)` | Build features → train → save → metrics |
| `predict_lstm(symbol)` | Load model → latest sequence → predict → direction |
| `_build_sequence_features(symbol)` | Factor panel → normalized sequences (30×20) |

### Output
```python
train_lstm() → {
    "symbol": "ACB", "backend": "tensorflow_lstm",
    "seq_length": 30, "n_features": 20,
    "train_samples": 170, "test_samples": 20,
    "train_loss": 0.0012, "val_loss": 0.0015,
    "train_mae": 0.015, "test_mae": 0.021,
    "status": "trained"
}

predict_lstm() → {
    "symbol": "ACB", "backend": "tensorflow_lstm",
    "predicted5dReturn": 1.85,  # %
    "direction": "BUY", "confidence": 0.65
}
```

### Agent-Facing Tool (`brain/tools/deep_learning_tool.py`)

```python
class DeepLearningTool(BaseTool):
    name = "deep_learning"
    actions: "train" | "predict"
    repeatable = True
```

## 15.3 Parameter Optimizer (`services/param_optimizer.py`) — 199 lines

Grid search + sensitivity analysis cho backtest strategy parameters.

### Core Algorithm
```
1. Define param_grid: {param_name: [values...]}
2. Generate all combinations (itertools.product)
3. Sample nếu > max_combinations (50 default) — uniform sampling
4. For each combo:
   - Run backtest (asyncio.run -> run_backtest_route)
   - Extract metric (sharpe, total_return, sortino, calmar)
5. Sort results (maximize/minimize)
6. Sensitivity analysis: vary 1 param at a time around best
```

### Data Classes
```python
ParamGrid:           name, values[]
StrategyTemplate:    type, param_template
OptimizationResult:  params, metrics, sort_key
```

### Output
```python
grid_search() → {
    "symbol": "ACB", "strategy": "sma_cross", "metric": "sharpe",
    "combinations_tried": 50,
    "best_params": {"fast": 10, "slow": 30},
    "best_metric": 1.25,
    "best_metrics": {"sharpe": 1.25, "total_return": 0.35, ...},
    "all_results": [{"params": {...}, "metric": 1.25}, ...],
    "sensitivity": {
        "fast": [{"value": 5, "avg_metric": 0.95, "count": 10}, ...],
        ...
    }
}
```

## 15.4 News RAG (`services/news_rag.py`) — 103 lines

Lightweight semantic news retrieval dùng TF-IDF + Cosine Similarity.

### Class: `NewsRAGService` (Singleton)

| Method | Mô tả |
|--------|-------|
| `add_articles(news_list)` | Add articles → retrain TF-IDF (max 1000 features) |
| `query(query_text, symbol, top_k=5)` | Cosine similarity search |
| `clear_database()` | Reset toàn bộ in-memory storage |
| `has_article(news_id)` | Check duplicate |
| `get_all_articles()` | Get all stored articles |

### Search Algorithm
```python
1. Filter articles by symbol (optional)
2. Vectorize query: vectorizer.transform([query_text])
3. Cosine similarity: cosine_similarity(query_vec, filtered_matrix)
4. Sort descending → top_k results
```

## 15.5 ML + DL Integration Flow

```
Agent/User Request
    │
    ├── ML Alpha Predictor (XGBoost/RF)
    │   → Feature engineering (48 factors)
    │   → Train/predict forward return
    │   → Direction (BUY/SELL/HOLD) + confidence
    │   → Feature importance explanation
    │
    ├── Deep Learning (LSTM/MLP)
    │   → Sequence features (30×20)
    │   → Train/predict
    │   → Direction + confidence
    │
    ├── Parameter Optimizer
    │   → Grid search strategy params
    │   → Sensitivity analysis
    │   → Best params + metrics
    │
    └── News RAG (TF-IDF)
        → Semantic news retrieval
        → Context enrichment for LLM
```

---

# 16. DNSE Open API Integration

## Authentication

HMAC-SHA256 signature:
```
Canonical string: "(request-target): {method} {path}\n{date_header}: {date_value}"
Signature: base64(HMAC-SHA256(canonical_string, api_secret))
Headers: Signature keyId="{api_key}",algorithm="sha256",headers="(request-target) date",signature="{sig}"
```

## WebSocket Handshake
1. Connect to `wss://ws-openapi.dnse.com.vn/v1/stream?encoding=json`
2. Send: `{"action": "auth", "api_key": "...", "signature": "...", "timestamp": "...", "nonce": "..."}`
3. Receive welcome + auth_success
4. Subscribe to channels
5. Heartbeat: `ping` every 25s, expect `pong`

## OHLC Data Flow
```
WebSocket "ohlc.{res}.json" (live) + "ohlc_closed.{res}.json" (closed)
  → stream_hub._on_ohlc() / _on_ohlc_closed()
  → Redis Sorted Set (ohlc_closed:{symbol}:{resolution}, scored by timestamp)
  → Redis Stream (dnse:stream:ohlc_closed:{symbol}, maxlen 10000)
  → Redis Pub/Sub ({prefix}:ohlc:{symbol})
```

## Trade Data Flow
```
WebSocket "tick.{board}.json"
  → stream_hub._on_trade()
  → In-memory cache
  → Redis List (trade:{symbol}, max 100, TTL 300s)
  → Redis Stream (dnse:stream:trade:{symbol}, maxlen ~5000)
  → Redis Pub/Sub ({prefix}:trade:{symbol})
  → Snapshot + breadth + liquidity + heatmap broadcasts
```

## Key Redis Data Structures

| Structure | Pattern | TTL | Usage |
|-----------|---------|:---:|-------|
| Pub/Sub | `{prefix}:trade:{sym}` | - | Real-time trades |
| Pub/Sub | `{prefix}:quote:{sym}` | - | Order book |
| Pub/Sub | `{prefix}:ohlc:{sym}` | - | OHLC updates |
| Pub/Sub | `{prefix}:index:{name}` | - | Market indices |
| Pub/Sub | `{prefix}:snapshot` | - | Full snapshot |
| Pub/Sub | `{prefix}:liquidity` | - | Liquidity summary |
| Pub/Sub | `{prefix}:heatmap` | - | Sector heatmap |
| List | `trade:{sym}` | 300s | Last 100 trades |
| Sorted Set | `ohlc_closed:{sym}:{res}` | 86400s | OHLC history |
| Stream | `dnse:stream:trade:{sym}` | - | Durable replay |
| String | `stock:{sym}:quote` | 2s | Latest quote |
| String | `market:indices` | 3s | Indices cache |
| String | `stock:{sym}:sec_def` | 3600s | Security definitions |

---

# 16. Configuration & Environment

## `.env` File — 40+ Variables

### DNSE
```
DNSE_API_KEY=
DNSE_API_SECRET=
DNSE_ACCOUNT_NO=
DNSE_BASE_URL=https://openapi.dnse.com.vn
DNSE_WS_URL=wss://ws-openapi.dnse.com.vn
DNSE_BOARD_ID=G1
```

### Redis
```
REDIS_URL=redis://localhost:6379/0
DNSE_REDIS_CHANNEL_PREFIX=dnse:
```

### LLM Providers
```
GROQ_API_KEY0=
GROQ_MODEL0=llama-3.3-70b-versatile
GROQ_API_KEY1=
GROQ_MODEL1=qwen/qwen3-32b
NVIDIA_API_KEY=
NVIDIA_MODEL=minimaxai/minimax-m2.7
OPENROUTER_API_KEY=
OPENROUTER_MODEL=
```

### VN Data
```
VNSTOCK_API_KEY=
VIMO_API_KEY=
HUGGING_FACE=
```

### Routing & Feature Flags
```
LLM_ROUTING_MODE=auto
ENABLE_FALLBACK=true
CONFIDENCE_THRESHOLD=0.6
MAX_PARALLEL_CALLS=2
DNSE_ENABLED=true
```

## Docker
```
FROM python:3.11-slim
RUN apt-get install -y gcc
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

# 18. Tổng Kết

## Thống Kê

| Thành phần | Số lượng |
|-----------|:--------:|
| Files Python | 100+ |
| YAML Presets | 29 |
| Tools | 20+ |
| Skills | 40+ |
| LLM Providers | 4 (Groq×2, NVIDIA, OpenRouter) |
| Agent Roles | 100+ (across all presets) |
| Risk Layers | 7 |
| VN Factors | 10 core + 3 event |
| Router Modules | 24 |
| Services | 25+ |
| Database Tables | 2 (SQLite) + 3 (PostgreSQL) |

## Kiến Trúc Tổng Thể

Bộ não AI của AIInvest là một hệ thống **multi-agent hybrid architecture** kết hợp:

1. **LangGraph StateGraph** — debate flow (analysts → researchers → debaters → managers → trader)
2. **Swarm DAG** — complex workflows (29+ preset teams với topological layering)
3. **ReAct AgentLoop** — single-agent tasks với 5-layer context management
4. **Multi-Model Routing** — 3 providers × 9 task types, fallback chain, parallel consensus
5. **Quant Engine** — VN-specific factor IC testing, walk-forward validation, Benjamini-Hochberg
6. **7-Layer Risk System** — weighted composite scoring, sector overrides, hard/soft blocks
7. **DNSE Real-Time Data** — WebSocket streaming, REST API, Redis pub/sub, market session
8. **40+ Specialist Skills** — từ technical analysis đến behavioral finance

## Luồng Xử Lý Hoàn Chỉnh

```
User Input
  → IntentRouter (CHAT / RESEARCH / SIGNAL)
    → CHAT: Groq-0 → Response
    → RESEARCH: LangGraph StateGraph
      → 4 Analysts (market, fundamental, sentiment, news)
      → Bull ↔ Bear Debate (multi-round)
      → Risk Debate (3 perspectives)
      → Research Manager → 5-tier rating
      → Portfolio Manager → BUY/SELL/HOLD
      → Trader → execution proposal
    → SIGNAL: SwarmRuntime DAG
      → Load YAML preset
      → Validate DAG (cycle detection)
      → Parallel layer execution (ThreadPoolExecutor)
      → WorkerAgentLoop per task (5-layer context)
      → Tool execution (read batching)
      → Risk gate (7-layer CRS)
      → Final decision

Throughout: EventBus SSE streaming, Redis pub/sub, memory persistence
```
