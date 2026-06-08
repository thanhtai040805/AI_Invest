# AIInvest — Phân Tích Toàn Diện Cho AI Agent

## 1. TỔNG QUAN (MỤC ĐÍCH)

**AIInvest** là nền tảng phân tích chứng khoán Việt Nam toàn diện tích hợp AI, kết hợp:

- **Dữ liệu thị trường real-time** qua WebSocket DNSE (HOSE/HNX/UPCOM)
- **Phân tích AI đa tác tử** (LangGraph multi-agent: Bull/Bear debate, 11+ agents)
- **Phân tích định lượng** (453 alpha factors, XGBoost/LSTM, backtesting, factor research)
- **Paper trading** (danh mục ảo, đặt lệnh giả lập, T+2/T+5 tracking)
- **RAG** (news TF-IDF + PDF annual reports)
- **Shadow account** (journal → profile → codegen → backtest pipeline)
- **Social/Community** (posts, comments, reactions, insights)
- **50+ skills** progressive disclosure cho AI agent
- **Swarm orchestration** (DAG-based multi-agent workflows)

### Mục tiêu cốt lõi
> "Mang phân tích chứng khoán chuyên nghiệp + AI đến nhà đầu tư cá nhân Việt Nam, thay thế Bloomberg/Reuters với chi phí 0."

---

## 2. KIẾN TRÚC TỔNG THỂ

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           DOCKER COMPOSE                                     │
│                                                                              │
│  ┌──────────────┐   ┌──────────────┐   ┌───────────────┐   ┌──────────────┐ │
│  │  PostgreSQL   │   │    Redis     │   │  ai-engine    │   │  back-end    │ │
│  │ (TimescaleDB) │   │ (Cache/Que)  │   │  (FastAPI)    │   │ (Express)    │ │
│  │    :5432      │   │   :6379      │   │   :8000       │   │   :3001      │ │
│  └──────┬───────┘   └──────┬───────┘   └───────┬───────┘   └───┬──┬───────┘ │
│         │                  │                    │               │  │         │
└─────────┼──────────────────┼────────────────────┼───────────────┼──┼─────────┘
          │                  │                    │               │  │
    ┌─────┴──────┐     ┌────┴────┐         ┌─────┴─────┐    ┌───┴──┴────┐
    │  Prisma    │     │ Cache / │         │  30 API    │    │ Socket.IO │
    │  ORM       │     │ Pub/Sub │         │  Routers   │    │  Relay    │
    └────────────┘     └─────────┘         └─────┬──────┘    └───┬───────┘
                                                  │               │
                                            ┌─────▼───────────────▼──────┐
                                            │       Frontend Next.js     │
                                            │          :3000             │
                                            └────────────────────────────┘
```

### 3 Layer:

| Layer | Công nghệ | Port | Nhiệm vụ |
|-------|-----------|------|----------|
| **ai-engine** | Python FastAPI v2.0.0 | `:8000` | Brain: data ingestion, compute, AI agents, ML, factors, skills |
| **back-end** | Node.js Express | `:3001` | Gateway: REST API, Socket.IO relay, auth, Prisma DB |
| **front-end** | Next.js React | `:3000` | UI: TradingView charts, dashboards, AI chat, community |

### Data Layer:

| Component | Công nghệ | Port | Nhiệm vụ |
|-----------|-----------|------|----------|
| PostgreSQL | TimescaleDB + Prisma | `:5432` | 17 tables: OHLCV, users, news, community |
| Redis | Cache + Pub/Sub + Streams | `:6379` | Real-time data relay, durable replay, rate limiting |

---

## 3. DATA SOURCES (8 NGUỒN)

| # | Source | Loại | Cung cấp | Method |
|---|--------|------|----------|--------|
| 1 | **DNSE REST** | Broker API | OHLCV lịch sử, stock list, indices, fundamentals, foreign flow, orderbook | `DnseRestClient` |
| 2 | **DNSE WS** | WebSocket real-time | Quote, trade, orderbook L2, foreign, index, security definition | `DnseWebSocketClient` |
| 3 | **vnstock** | Python lib (v4.0.4 deprecated) | Financial statements, profile, dividends, officers, sector, ratio | `Finance` + `Company` |
| 4 | **CafeF** | Website crawl | News, annual report PDF, insider trading text | `NewsIngestionService` + `ScraperInsider` |
| 5 | **yfinance** | Python lib | Global macro (USD/VND, gold, oil, DXY, VIX, bonds), VN dividends/splits | `yfinance` |
| 6 | **VietFin** | Python lib (DNSE data) | VNINDEX history, ETF data, derivatives | `vf.index` |
| 7 | **Vimo** ❌ | REST API (defunct) | ~~Lending rates SBV: Big4 + Commercial~~ ⚠️ **Shut down Feb 2026** (SBV revoked license) | Replaced by: vi.money (CPI), SBV web scrape (policy rates), FiinGroup (paid) |
| 8 | **UBCKNN** | Web scrape | Regulatory: sanction, warning, delisting, investigation, suspension | `ScraperUBCKNN` |

### Brain Dataflows (data routing layer):

| Adapter | File | Cung cấp |
|---------|------|----------|
| **yfinance** | `brain/dataflows/y_finance.py` | OHLCV, fundamentals, indicators cho US stocks |
| **yfinance_news** | `brain/dataflows/yfinance_news.py` | Yahoo Finance news fetcher |
| **StockTwits** | `brain/dataflows/stocktwits.py` | Social sentiment cho sentiment analyst |
| **Reddit** | `brain/dataflows/reddit.py` | Reddit scraping cho sentiment analyst |
| **VN vendors** | `brain/dataflows/vendors/vn/` | OHLCVTool, IndicatorsTool, FundamentalsTool, NewsTool, VNCalendar |
| **Route** | `brain/dataflows/interface.py` | `route_to_vendor()` dispatch theo tool name |
| **Config** | `brain/dataflows/config.py` | Dataflow configuration |
| **Default Config** | `brain/dataflows/default_config.py` | Default dataflow config |
| **StockStats Utils** | `brain/dataflows/stockstats_utils.py` | Stock statistics utilities |

---

## 4. DATABASE

### 17 Tables hiện tại (Prisma ORM + TimescaleDB):

| Table | Type | Mục đích | Key Fields |
|-------|------|----------|------------|
| `ohlcv` | TimescaleDB hypertable | OHLCV time series | PK: (symbol, time) |
| `stocks` | Regular | Stock master | symbol PK, exchange, industry, ceiling/floor |
| `users` | Regular | Users | email, password_hash, cash_balance |
| `positions` | Regular | Paper trading holdings | user_id FK, symbol FK, qty, avg_price |
| `orders` | Regular | Paper trading orders | side, type, status, filled_qty |
| `news` | Regular | CafeF news | content, sentiment_score, symbol |
| `posts` | Regular | Community | content, tagged_symbols[], likes |
| `comments` | Regular | Nested comments | parent_comment_id self-ref |
| `reactions` | Regular | Likes | target: POST/COMMENT |
| `screener_presets` | Regular | Saved filters | filters JSON |
| `chat_sessions` | Regular | AI history | title, user FK |
| `chat_messages` | Regular | AI messages | role, content, type, metadata JSON |
| `alerts` | Regular | Price alerts | symbol, condition, target_value |
| `chart_drawings` | Regular | Chart annotations | drawings JSON |
| `watchlists` | Regular | Symbol lists | symbols[] array |
| `refresh_tokens` | Regular | JWT | token_id, is_revoked |
| `market_session_logs` | Regular | Backfill tracking | session_date unique |

### SQLAlchemy models (ai-engine internal):
| Model | Table | Mục đích |
|-------|-------|----------|
| `PaperTrade` | `paper_trades` | T+2/T+5 P&L tracking |
| `SessionLog` | `session_logs` | Audit trail cho session ops |

### 9 Tables cần tạo thêm:

| Table | Nội dung | Priority | Lý do |
|-------|----------|----------|-------|
| `financial_statements` | IS/BS/CF | 🔴 CAO | Persist thay on-demand |
| `technical_indicators` | MA, RSI, MACD,... | 🟡 TB | Cache hypertable |
| `financial_ratios` | PE, PB, ROE,... | 🟡 TB | Persist ratios |
| `risk_metrics` | Sharpe, Beta, VaR | 🟢 THẤP | Lịch sử metrics |
| `factor_scores` | Value, Momentum,... | 🟢 THẤP | Cross-stack ranking |
| `corporate_actions` | Dividends, splits, rights | 🔴 CAO | Cần cho adj_close |
| `macro_indicators` | CPI, GDP, lending rates | 🔴 CAO | Time-series vĩ mô |
| `insider_trades` | Quantity, price, value | 🟡 TB | Structured từ news |
| `risk_flags` | DELAYED, AUDITOR,... | 🟢 THẤP | Lịch sử flag |

---

## 5. AI-ENGINE: 31 ROUTERS

Tất cả routers được import batch trong `app/main.py` (line 12).

### Market & Stock Data:

| Router | Prefix | Endpoints |
|--------|--------|-----------|
| `market_data.py` | `/api/market` | indices, breadth, snapshot, stocks, liquidity, sector_performance |
| `stock_data.py` | `/api/stock/:symbol` | profile, ohlcv, quote, orderbook, trades, fundamentals, intraday |
| `stream.py` | `/api/stream` | subscribe, status (WebSocket channel management) |

### AI & Analysis:

| Router | Prefix | Key Endpoints |
|--------|--------|---------------|
| `agent.py` | `/api/agent` | POST /run, GET /status, POST /cancel, POST /upload |
| `swarm.py` | `/api/swarm` | POST /run (DAG SwarmRuntime) |
| `graph.py` | `/api/graph` | GET /list (11 nodes), POST /execute (LangGraph) |
| `trading_agents.py` | `/api/trading-agents` | POST /analyze/{symbol} (11-agent registry) |
| `session.py` | `/api/session` | CRUD sessions + messages |
| `memory.py` | `/api/memory` | POST /store, GET /search, DELETE |
| `skills.py` | `/api/skills` | GET /list (12 VN skills), POST /execute |
| `shadow_account.py` | `/api/shadow-account` | POST /create, POST /trade, GET /portfolio |
| `hypotheses.py` | `/api/hypotheses` | GET /list, POST /test |
| `core.py` | `/api/core` | POST /execute (backtest, get_state) |

### Quant & Data:

| Router | Prefix | Key Endpoints |
|--------|--------|---------------|
| `factors.py` | `/api/factors` | GET /list, POST /compute, GET /meta/{alpha_id} |
| `backtest.py` | `/api/backtest` | POST /run, GET /history, GET /status, GET /results |
| `dataflows.py` | `/api/dataflows` | GET /tools (4 categories), POST /execute |
| `tools.py` | `/api/tools` | GET /list, POST /execute |

### LLM & Providers:

| Router | Prefix | Key Endpoints |
|--------|--------|---------------|
| `providers.py` | `/api/providers` | GET /list, POST /configure, POST /test |
| `llm_clients.py` | `/api/llm-clients` | GET /list, POST /chat |
| `config.py` | `/api/config` | GET / (live), POST /update |
| `preflight.py` | `/api/preflight` | POST /check, GET /status |

### Admin & Others:

| Router | Prefix | Key Endpoints |
|--------|--------|---------------|
| `admin.py` | `/api/admin` | GET /jobs, POST /backfill/trigger |
| `security.py` | `/api/security` | POST /validate, GET /risk-flags/{symbol} |
| `runs.py` | `/api/runs` | GET /{run_id}, GET /list, GET /code, GET /pine |
| `screener.py` | `/api/screener` | GET /presets, POST /filter |
| `ui_services.py` | `/api/ui-services` | POST /run, GET /list |
| `ai_routes.py` | `/api/ai` | Bridge cho Node.js backend |
| `vibe_routes.py` | `/api/vibe-api` | Vibe-Trading API compatibility |
| `vibe_api/alpha_routes.py` | `/api/alpha` | GET /list, POST /bench + SSE streaming |

---

## 6. SERVICES (20+ FILES)

### Data Ingestion & Streaming:

| Service | File | Nhiệm vụ |
|---------|------|----------|
| **DnseStreamHub** | `services/dnse/stream_hub.py` | WS background thread, auto connect/disconnect theo VN trading hours |
| **DnseWebSocketClient** | `services/dnse/websocket/client.py` | WS connection, auth, reconnection backoff, 10+ channels |
| **DnseRestClient** | `services/dnse/rest_client.py` | HTTP client cho DNSE REST API |
| **MarketSessionManager** | `services/dnse/market_session.py` | VN trading hours: pre-ATO → ATO1/2 → morning → break → afternoon → ATC1/2 → closed |
| **RedisPublisher** | `services/dnse/redis_pub.py` | Dual-write: Redis Pub/Sub (real-time) + Streams (durable replay), token-bucket rate limit |
| **ChannelHealthTracker** | `services/dnse/health.py` | WebSocket health monitoring |
| **RateLimiter** | `services/dnse/rate_limiter.py` | Token bucket |
| **DnseIntradayTool** | `services/dnse/intraday_tool.py` | Intraday OHLCV (resolutions: 1,5,15,30,1H,1D) |

### Market Data:

| Service | File | Nhiệm vụ |
|---------|------|----------|
| **MarketDataService** | `services/market_data_service.py` | Unified facade: PG(history) → Redis(recent) → hub(live) → REST(fallback) |
| **DataEnricher** | `services/data_enricher.py` | **Central compute**: 40+ technical, 30+ risk, 30+ ratios, 25+ macro, 7 factors, flags, sentiment |
| **OHLCVBackfill** | `services/ohlcv_backfill.py` | Fetch OHLCV từ DNSE REST, upsert TimescaleDB |
| **BackfillService** | `services/backfill_service.py` | Daily orchestration: check session, sync stocks + OHLCV |

### News & Sentiment:

| Service | File | Nhiệm vụ |
|---------|------|----------|
| **NewsIngestionService** | `services/news_ingestion.py` | Crawl CafeF categories (Playwright), scrape content, gửi backend |
| **NewsRAGService** | `services/news_rag.py` | In-memory TF-IDF vector search, query by text + symbol |
| **SentimentScorer** | `services/sentiment_scorer.py` | Lexicon-based: 39 positive + 24 negative từ |

### Risk & Screening:

| Service | File | Nhiệm vụ |
|---------|------|----------|
| **RiskFlags** | `services/risk_flags.py` | 15+ signals: legal, insider, losses, drop, spike, foreign, debt, ROE, regulatory |
| **ScreenerService** | `services/screener_service.py` | Multi-criteria stock filtering |

### ML & Quant:

| Service | File | Nhiệm vụ |
|---------|------|----------|
| **MLAlphaPredictor** | `services/ml_alpha_predictor.py` | XGBoost/RF: features → forward 5d return |
| **DeepLearningAlpha** | `services/deep_learning_alpha.py` | LSTM/Transformer: 30-day window, 20 features |
| **TimeSeriesForecast** | `services/time_series_forecast.py` | Auto ARIMA/SARIMA + confidence intervals |
| **ParamOptimizer** | `services/param_optimizer.py` | Grid search backtest params |
| **TradingRules** | `services/trading_rules.py` | Stop-loss, take-profit, Kelly sizing, rebalance |

### Other:

| Service | File | Nhiệm vụ |
|---------|------|----------|
| **AIService** | `services/ai_service.py` | Legacy: chat streaming, consensus, backtest |
| **Seasonality** | `services/seasonality.py` | Day-of-week, month, Tet holiday effects |
| **MonitoringService** | `services/monitoring.py` | Alerts (Slack), health checks, ML freshness |
| **PGPool** | `services/pg_pool.py` | PostgreSQL connection pool + migrations |
| **JobStateService** | `services/job_state_service.py` | Job tracking: in-progress, completed, failed |
| **UIServices** | `services/ui_services.py` | UI aggregation endpoints |

---

## 7. BRAIN: AGENT SYSTEM (LONGCONTEXT MULTI-AGENT)

### 7a. Architecture: Single Graph, One-Way Dependency

**KHÔNG phải 2 hệ thống song song.** Dependency là 1 chiều:

```
brain/state/ (orchestration)
    ↓ imports agent factories
brain/agents/ (agent implementations)
    ↓ no reverse imports
brain/providers/ (LLM abstraction)
```

- **`brain/state/graph.py`** là graph DUY NHẤT — nó import agent factories từ `brain/agents/`
- **`brain/agents/`** KHÔNG có `graph.py` riêng, không import từ `brain/state/`
- **`brain/state/`** = orchestration layer (graph, nodes, edges, swarm, persistence, events, session)
- **`brain/agents/`** = agent implementations (analysts, researchers, debators, managers, trader) + ReAct loop
- **`brain/providers/`** = LLM abstraction (Groq, NVIDIA, routing, intent classification)

Tuy nhiên có **2 ReAct loop implementations** (cố tình, không overlap):

| Loop | File | Dùng cho | Ghi chú |
|------|------|----------|---------|
| **AgentLoop** | `agents/core/loop.py` | Individual agents | 5-layer context management (microcompact → auto_compact → iterative_update) |
| **SwarmWorker** | `state/worker.py` | Swarm tasks | Self-contained, heartbeat, không dùng AgentLoop |

### 7b. Agent Roles (12 factories + 1 schema)

| Role | Factory | File | Công cụ |
|------|---------|------|---------|
| **Analyst** — Market | `create_market_analyst` | `analysts/market_analyst.py` | 8 technical indicators |
| **Analyst** — Sentiment | `create_sentiment_analyst` | `analysts/sentiment_analyst.py` | News + StockTwits + Reddit |
| **Analyst** — News | `create_news_analyst` | `analysts/news_analyst.py` | get_news, get_global_news |
| **Analyst** — Fundamentals | `create_fundamentals_analyst` | `analysts/fundamentals_analyst.py` | BS/CF/IS tools |
| **Analyst** — Social Media | `create_social_media_analyst` | `analysts/social_media_analyst.py` | Social sentiment |
| **Researcher** — Bull | `create_bull_researcher` | `researchers/bull_researcher.py` | Bull thesis |
| **Researcher** — Bear | `create_bear_researcher` | `researchers/bear_researcher.py` | Bear thesis |
| **Debator** — Aggressive | `create_aggressive_debator` | `debaters/aggressive_debator.py` | High-risk |
| **Debator** — Conservative | `create_conservative_debator` | `debaters/conservative_debator.py` | Low-risk |
| **Debator** — Neutral | `create_neutral_debator` | `debaters/neutral_debator.py` | Balanced |
| **Manager** — Research | `create_research_manager` | `managers/research_manager.py` | `ResearchPlan` |
| **Manager** — Portfolio | `create_portfolio_manager` | `managers/portfolio_manager.py` | `PortfolioDecision` |
| **Trader** | `create_trader` | `trader/trader.py` | `TraderProposal` |

**Structured Output Schemas** (`agents/schemas.py`): `ResearchPlan`, `TraderProposal`, `PortfolioDecision` — Pydantic models với field descriptions làm output instructions cho LLM, render helpers convert → markdown. Provider adapters: OpenAI json_schema, Gemini response_schema, Anthropic tool-use.

### 7c. Agent Core Components (`agents/core/`)

| File | Lines | Vai trò |
|------|-------|---------|
| `loop.py` | ~300 | AgentLoop: 5-layer context management |
| `context.py` | — | ContextBuilder: build agent context |
| `frontmatter.py` | — | Front matter metadata management |
| `memory.py` | — | WorkspaceMemory: volatile per-run |
| `progress.py` | — | HeartbeatTimer: progress tracking cho worker |
| `skills.py` | — | SkillsLoader: load on-demand skills |
| `tools.py` | — | ToolRegistry: register/discover tools |
| `trace.py` | — | TraceWriter: agent execution trace |

### 7d. Agent Utilities (`agents/utils/`)

| File | Vai trò |
|------|---------|
| `agent_states.py` | AgentState, InvestDebateState, RiskDebateState |
| `agent_utils.py` | State management helpers |
| `core_stock_tools.py` | Core stock data tools cho agents |
| `fundamental_data_tools.py` | Fundamental analysis tools |
| `memory.py` | Agent memory utilities |
| `news_data_tools.py` | News tools cho agents |
| `rating.py` | Rating/review helpers |
| `structured.py` | Structured output formatting |
| `technical_indicators_tools.py` | Technical analysis tools |

### 7e. Agent Loop (5-layer context management)

`brain/agents/core/loop.py` — `AgentLoop`:

| Layer | Mechanism | Mục đích |
|-------|-----------|----------|
| 1. microcompact | Prune old tool results silently | Keep recent 3-5 results |
| 2. context_collapse | Fold long text | Summarize tool output |
| 3. auto_compact | LLM summary | Đầy nén khi budget thấp |
| 4. compact_tool | Model-triggered | Agent tự quyết định nén |
| 5. iterative_update | Nth compression | Compression history |

### 7f. Skills (57 progressive disclosure)

`brain/quant/skills_data/` (57 directories) — AI agents load skills on-demand. Mỗi skill là `SKILL.md` + optional `example_signal_engine.py` + `references/`.

Skills có `example_signal_engine.py`: candlestick, chanlun, cross-market-strategy, elliott-wave, fundamental-filter, harmonic, ichimoku, minute-analysis, multi-factor, pair-trading, seasonal, smc, technical-basic, volatility  
Skills có `references/`: chanlun, elliott-wave, harmonic, ichimoku, smc

### 7g. Swarm (DAG-based orchestration)

`brain/state/runtime.py` — `SwarmRuntime`:

- Topological layering của tasks
- Parallel worker execution (via `state/worker.py` SwarmWorker)
- Persistence: `.swarm/runs/{run_id}/`
- Cancellation support
- Token/rate limiting per worker

### 7h. Providers (5 files, 4-model routing + 2 ReAct loops)

`brain/providers/`:

| File | Vai trò |
|------|---------|
| `orchestrator.py` | `GraphOrchestrator` — 3-model routing coordinator |
| `router.py` | `IntentRouter` — classify CHAT/RESEARCH/SIGNAL, route pipeline + model |
| `base.py` | `BaseAgent` abstract class: tenacity retry, cost tracking, error classification |
| `chat.py` | `ChatLLM` — Groq llama-3.3-70b với streaming + tool calls |
| `groq_client.py` | Groq agent implementation |
| `llm.py` | Environment helpers, `.env` sync |
| `prompts/vn_prompts.py` | Vietnamese-specific prompts |

Routing flow:
```
IntentRouter → classify (CHAT/RESEARCH/SIGNAL)
  ↓
CHAT → Groq0 (llama-3.3-70b) — simple pipeline
SIGNAL → Groq1 (qwen3-32b) — graph pipeline, stream=True
RESEARCH → Groq0 — graph pipeline, stream=True
  ↓
NVIDIA (minimax-m2.7) — deep document analysis
OpenAI (GPT-4o-mini) — fallback
```

**2 ReAct loop implementations** (cố tình tách biệt):
| Loop | File | Dùng cho |
|------|------|----------|
| AgentLoop | `agents/core/loop.py` | Individual agents (5-layer context) |
| SwarmWorker | `state/worker.py` | Swarm tasks (heartbeat, self-contained) |

### 7i. Memory System

| Memory | File | Loại | Mục đích |
|--------|------|------|----------|
| `PersistentMemory` | `brain/memory/persistent.py` | File-based cross-session | `~/.vibe-trading/memory/`, namespaces, expiry, search, compression |
| `WorkspaceMemory` | `brain/agents/core/memory.py` | Volatile per-run | run_dir, counters |

### 7j. Security & Config

| Component | File | Vai trò |
|-----------|------|---------|
| **Prompt Injection Scanner** | `brain/security/scanner.py` | 5 rules: instruction_override, exfiltration, role_claim, secret_leak, tool_abuse |
| **Agent Config** | `brain/config/loader.py` | JSON/YAML config load, MCP server merge, runtime overrides |
| **Config Schema** | `brain/config/schema.py` | Pydantic: `AgentConfig`, `MCPServerConfig`, `AgentConfigOverride` |
| **Config Paths** | `brain/config/paths.py` | `~/.vibe-trading/` path resolution |
| **State Checkpointer** | `brain/state/checkpointer.py` | JSON checkpoint session state |

### 7k. State System (LangGraph + Swarm Infrastructure)

`brain/state/` — orchestration layer với ~20 files:

| File | Lines | Vai trò |
|------|-------|---------|
| `graph.py` | ~100 | **Graph DUY NHẤT**: build_graph() import từ agents/ |
| `nodes.py` | ~400 | GraphNodes: 12 node methods |
| `edges.py` | 210 | 11 conditional edge functions (route_after_*, should_continue_*) |
| `state.py` | 94 | GraphState TypedDict: 17 fields (symbol, analysis, decision, debate_round, risk...) |
| `models.py` | 295 | Pydantic: SwarmRun, SwarmTask, SwarmAgentSpec, SwarmEvent, WorkerResult, Session, Message |
| `service.py` | ~250 | SessionService: session CRUD, message indexing |
| `session_store.py` | 146 | File-based SessionStore: `runs/.sessions/{id}/session.json + messages.jsonl + attempts/` |
| `search.py` | 361 | SQLite FTS5 cross-session full-text search |
| `store.py` | 551 | SwarmStore: atomic write, stale-run reaper, heartbeat recovery |
| `task_store.py` | 248 | Task persistence, DAG topological layering, cycle detection |
| `worker.py` | 792 | SwarmWorker: standalone ReAct loop, heartbeat, tool registry |
| `events.py` | 227 | EventBus: SSE streaming, last_event_id recovery, thread-safe |
| `concurrency.py` | 201 | ConcurrencyManager: parallel analysts (market/fundamental/news/social) |
| `reflection.py` | 136 | Reflector: self-critique trên past decisions via LLM |
| `signal_processing.py` | 117 | SignalProcessor: 5-tier rating parser (Buy/Overweight/Hold/Underweight/Sell) |
| `grounding.py` | 219 | Pre-fetch OHLCV real cho symbols (chống LLM hallucination giá) |
| `serialization.py` | 51 | SwarmTask → public dict (MCP tools), internal path redaction |
| `presets.py` | — | Preset loader |
| `checkpointer.py` | 201 | JSON checkpoint graph state |

---

## 8. QUANT SYSTEM (453 ALPHA FACTORS)

### 8a. Structure

```
brain/quant/
├── pipeline.py            — impute → winsorize → normalize
├── skills.py              — Skill loader
├── hypotheses/            — Hypothesis registry (JSON-backed)
│   ├── registry.py        — exploring → testing → validated/rejected → monitoring
├── factors/
│   ├── base.py            — 20+ operators (rank, scale, ts_*, delta, ...)
│   ├── registry.py        — AST-scan, lazy-import, validate, compute
│   ├── bench_runner.py    — IC/IR bench, categorize (alive/reversed/dead)
│   ├── factor_analysis_core.py — IC series, group equity
│   └── zoo/
│       ├── academic/      — 7 factors (Fama-French, Carhart, sentiment)
│       ├── alpha101/      — 101 Kakushadze formulaic alphas
│       ├── gtja191/       — 191 Guotai Junan alphas
│       └── qlib158/       — 154 Microsoft Qlib alphas
```

### 8b. Factor Zoos Breakdown

| Zoo | Count | Themes | Source |
|-----|-------|--------|--------|
| **academic** | 7 | momentum, value, quality, sentiment | Fama-French 1993/2015, Carhart 1997, Sharpe 1964 |
| **alpha101** | 101 | reversal, volatility, momentum, volume, liquidity, microstructure | Kakushadze (2015) arXiv:1601.00991 |
| **gtja191** | 191 | volume, reversal, microstructure, momentum, liquidity, volatility | Guotai Junan 2014 |
| **qlib158** | 154 | 31 operator families × 5 windows | Microsoft Qlib |
| **TOTAL** | **453** | | |

### 8c. Base Operators (panel-wide DataFrames)

| Operator | Mô tả |
|----------|-------|
| `rank(df)` | Cross-sectional percentile rank |
| `scale(df, a=1)` | L1 normalize to sum=a |
| `ts_rank(df, n)` | Rolling rank |
| `ts_corr(x, y, n)` | Rolling Pearson |
| `ts_cov(x, y, n)` | Rolling covariance |
| `ts_mean/ts_std/ts_max/ts_min(df, n)` | Rolling stats |
| `ts_argmax/ts_argmin(df, n)` | Rolling index |
| `delta(df, d)` | df - df.shift(d) |
| `decay_linear(df, n)` | Linear decay MA |
| `signed_power(df, p)` | sign(x) * |x|^p |
| `safe_div(a, b)` | a / (b + eps) |
| `vwap(panel)` | (O+H+L+C)/4 |

### 8d. Registry System

`Registry` trong `registry.py`:
- **AST-scan** tất cả file trong zoo/, không import cho tới khi compute
- **Validate** metadata qua `AlphaMeta` Pydantic
- **Lazy-import** module trên lần `compute()` đầu tiên
- **Benchmark** qua `bench_runner.py`: tính IC mean, positive ratio, t-stat
- **Categorize**: alive (IC>0.02, pos_ratio>0.55, |t|>2), reversed, dead

### 8e. Pipeline

```
raw panel → impute_panel() [ffill → median] 
          → winsorize_panel() [quantile clipping]
          → normalize_panel() [z-score]
          → factor compute
          → score output
```

---

## 9. SHADOW ACCOUNT PIPELINE

`brain/tools/shadow_account/` — Journal → Profile → Backtest → Report

```
User Journal (text) 
  → extractor.py: parse → ShadowProfile (trading rules + confidence)
  → codegen.py: render → signal_engine.py (executable backtest)
  → backtester.py: run → metrics (Sharpe, MDD, win rate)
  → reporter.py: render → markdown report
  → scanner.py: scan_today_signals() → today's signals
  → storage.py: save → ~/.vibe-trading/shadows/
```

**Models** (`models.py`): `ShadowRule`, `ShadowProfile`, `ShadowBacktestResult`, `AttributionBreakdown`

---

## 10. HYPOTHESIS SYSTEM

`brain/quant/hypotheses/registry.py` — `HypothesisRegistry`:

- **Lifecycle**: exploring → testing → validated/rejected → monitoring
- **Storage**: JSON file tại `~/.vibe-trading/hypotheses.json`
- **Methods**: create, update, search (token-based vector scoring), list, link_backtest
- **Purpose**: track research ideas through complete cycle

---

## 11. LANGCHAIN TOOLS (55 TOOLS)

`brain/tools/` — 47 files, auto-discovery via `BaseTool.__subclasses__()`:

### Market & Analysis Tools:
| Tool | File | Mục đích |
|------|------|---------|
| **VNStockAnalyzeTool** | `vn_stock_analyze_tool.py` | **Primary**: full VN stock analysis |
| **VNFactorDataTool** | `vn_factor_data_tool.py` | VN factor data |
| **VNQualitativeRagTool** | `vn_qualitative_rag_tool.py` | RAG on annual reports |
| **VNIndexTool** | `vn_index_tool.py` | VN market indices |
| **VNFundsTool** | `vn_funds_tool.py` | VN fund analysis |
| **FundamentalTool** | `fundamental_tool.py` | DNSE fundamentals |
| **IndicatorTool** | `indicator_tool.py` | Technical indicators |
| **MacroTool** | `macro_tool.py` | Macro indicators |
| **MarketDataTool** | `market_data_tool.py` | Market data |
| **EnrichmentTool** | `enrichment_tool.py` | Data enrichment |
| **EconomicsTool** | `economics_tool.py` | Macroeconomics |

### ML & Quant Tools:
| Tool | File | Mục đích |
|------|------|---------|
| **AlphaZooTool** | `alpha_zoo_tool.py` | Compute factor zoo signals |
| **AlphaBenchTool** | `alpha_bench_tool.py` | Benchmark alphas |
| **FactorAnalysisTool** | `factor_analysis_tool.py` | Factor analysis |
| **DeepLearningTool** | `deep_learning_tool.py` | LSTM/Transformer predict |
| **ForecastTool** | `forecast_tool.py` | Time series forecasting |
| **MLAlphaTool** | `ml_alpha_tool.py` | ML-based alpha prediction |
| **ParamOptimizerTool** | `param_optimizer_tool.py` | Parameter optimization |
| **PatternTool** | `pattern_tool.py` | Chart pattern recognition |
| **OptionsPricingTool** | `options_pricing_tool.py` | Options pricing |

### Risk & Screening:
| Tool | File | Mục đích |
|------|------|---------|
| **RiskFlagsTool** | `risk_flags_tool.py` | Risk flag analysis |
| **RiskTool** | `risk_tool.py` | Risk metrics |
| **ScreenerTool** | `screener_tool.py` | Stock screener |
| **SectorTool** | `sector_tool.py` | Sector analysis |
| **DisclosuresTool** | `disclosures_tool.py` | Corporate disclosures |
| **InsiderTradingTool** | `insider_trading_tool.py` | Insider trading |

### Portfolio & Trading:
| Tool | File | Mục đích |
|------|------|---------|
| **ShadowAccountTool** | `shadow_account_tool.py` | Shadow account pipeline |
| **BacktestTool** | `backtest_tool.py` | Run backtests |
| **PortfolioTool** | `portfolio_tool.py` | Portfolio analysis |
| **TradingRulesTool** | `trading_rules_tool.py` | VN trading rules |
| **SeasonalityTool** | `seasonality_tool.py` | Market seasonality |
| **ValuationTool** | `valuation_tool.py` | Valuation analysis |
| **WatchlistTool** | `watchlist_tool.py` | Watchlist management |
| **TradeJournalTool** | `trade_journal_tool.py` | Trade journal management |
| **TradeJournalParsers** | `trade_journal_parsers.py` | Trade journal parsing |

### Sentiment & News:
| Tool | File | Mục đích |
|------|------|---------|
| **SentimentTool** | `sentiment_tool.py` | News sentiment |
| **TechnicalTool** | `technical_tool.py` | Chart patterns |

### Agent Infrastructure:
| Tool | File | Mục đích |
|------|------|---------|
| **CompactTool** | `compact_tool.py` | Context compression |
| **BashTool** | `bash_tool.py` | Execute shell |
| **BackgroundTool** | `background_tools.py` | Background tasks |
| **SwarmTool** | `swarm_tool.py` | Swarm execution |
| **HypothesisTool** | `hypothesis_tool.py` | Hypothesis management |
| **MCP** | `mcp.py` | MCP tool integration |
| **LoadSkillTool** | `load_skill_tool.py` | Skill loading |
| **SkillWriterTool** | `skill_writer_tool.py` | Skill creation |

### File & Web:
| Tool | File | Mục đích |
|------|------|---------|
| **ReadFileTool** | `read_file_tool.py` | File reading |
| **WriteFileTool** | `write_file_tool.py` | File writing |
| **EditFileTool** | `edit_file_tool.py` | File editing |
| **DocReaderTool** | `doc_reader_tool.py` | Document reader |
| **WebReaderTool** | `web_reader_tool.py` | Web content reader |
| **WebSearchTool** | `web_search_tool.py` | Web search |

### Memory & Search:
| Tool | File | Mục đích |
|------|------|---------|
| **RememberTool** | `remember_tool.py` | Memory storage/retrieval |
| **SessionSearchTool** | `session_search_tool.py` | Cross-session full-text search |

### Backtest Subsystem (`tools/backtest/`):
| File | Vai trò |
|------|---------|
| `runner.py` | Backtest runner |
| `metrics.py` | Performance metrics |
| `models.py` | Backtest models |
| `validation.py` | Backtest validation |
| `benchmark.py` | Benchmark computation |
| `correlation.py` | Correlation analysis |
| `run_card.py` | Run card generation |
| `engines/base.py` | Base backtest engine |
| `engines/vietnam_equity.py` | Vietnam-specific equity engine |
| `loaders/base.py` | Base data loader |
| `loaders/dnse_loader.py` | DNSE data loader |
| `loaders/vietfin_loader.py` | VietFin data loader |
| `loaders/registry.py` | Loader registry |
| `optimizers/base.py` | Base portfolio optimizer |
| `optimizers/mean_variance.py` | Mean-variance optimization |
| `optimizers/risk_parity.py` | Risk parity |
| `optimizers/hrp_optimizer.py` | Hierarchical Risk Parity |
| `optimizers/max_diversification.py` | Max diversification |
| `optimizers/equal_volatility.py` | Equal vol weighting |

### Framework (`tools/framework/`):
| File | Vai trò |
|------|---------|
| `runner.py` | Framework runner |
| `state.py` | RunStateStore |

---

## 12. COMPUTE PIPELINE (DATA ENRICHER — 1414 DÒNG)

`services/data_enricher.py` — central compute engine, gồm 10 module con:

| Module | Method | Output |
|--------|--------|--------|
| **Technical** | `compute_technical_indicators()` | 40+ indicators: MA, EMA, RSI, MACD, Stoch, ADX, MFI, BB, ATR, OBV, momentum |
| **Risk** | `compute_risk_metrics()` | 30+ metrics: returns, Sharpe, Sortino, Calmar, VaR, CVaR, Beta, Alpha |
| **Financials** | `fetch_vnstock_financials()` | IS/BS/CF + 30+ ratios (PE, PB, ROE, ROA, D/E, FCF, YoY growth) |
| **Profile** | `fetch_vnstock_profile()` | 25+ fields: CEO, employees, listing, free_float |
| **Macro** | `get_macro_indicators()` | 25+ fields: oil, gold, VND, VIX, lending rates, VNINDEX returns |
| **Risk flags** | `evaluate_risk_flags()` | 7 flags: DELIST, CFO, DELAYED, AUDITOR, PLEDGE, LAWSUIT, LOSS |
| **Market extras** | `compute_market_extras()` | avg_volume, turnover_rate, VWAP, value |
| **Spread** | `compute_spread()` | bid-ask spread |
| **Foreign flow** | `fetch_foreign_flow()` | buy/sell qty & value, net, ownership, room |
| **Factor scores** | `compute_factor_scores()` | 7 factors + total + percentile |
| **Sentiment** | `compute_sentiment_rolling()` | rolling 1d/5d/10d sentiment + news count |

---

## 13. AI/ML MODELS

| Model | File | Input → Output | Status |
|-------|------|----------------|--------|
| XGBoost / RF | `ml_alpha_predictor.py` | OHLCV → forward 5d return | ✅ Production |
| LSTM / Transformer | `deep_learning_alpha.py` | 30-day window → return sign | ✅ Production |
| Auto ARIMA/SARIMA | `time_series_forecast.py` | Close → price forecast ±CI | ✅ Production |
| Grid Search | `param_optimizer.py` | Params → best set | ✅ Production |
| Sentiment Lexicon | `sentiment_scorer.py` | News text → -1..1 | ✅ Production (basic) |
| TF-IDF RAG | `news_rag.py` | Query → relevant news | ✅ Production |
| Factor IC/IR | `factors/bench_runner.py` | Factor df → IC stats | ✅ Production |

---

## 14. MULTI-AGENT PRESETS (SWARM) — 27 TEAMS

`brain/state/presets/`:

### Core Research:
| Preset YAML | Focus | Agents |
|-------------|-------|--------|
| `investment_committee.yaml` | **General investment** | Macro, Equity, Risk analysts |
| `equity_research_team.yaml` | Stock analysis | Fundamental, Technical, Quant analysts |
| `fundamental_research_team.yaml` | Deep fundamental | Financial statement, Valuation, Industry analysts |
| `technical_analysis_panel.yaml` | Chart + technical | Pattern, Indicator, Volume analysts |
| `quant_strategy_desk.yaml` | Quantitative strategy | Screener, Factor, Backtest, Risk |
| `ml_quant_lab.yaml` | ML research | Feature Engineer, Data Scientist, Backtest Engineer |
| `factor_research_committee.yaml` | Factor research | Academic, Alternative, Risk factor analysts |

### Fixed Income & Credit:
| Preset YAML | Focus |
|-------------|-------|
| `credit_research_team.yaml` | Corporate credit analysis |
| `convertible_bond_team.yaml` | Convertible bond analysis |

### Macro & Global:
| Preset YAML | Focus |
|-------------|-------|
| `macro_strategy_forum.yaml` | Macro strategy discussion |
| `macro_rates_fx_desk.yaml` | Interest rates + FX |
| `global_allocation_committee.yaml` | Global asset allocation |
| `global_equities_desk.yaml` | Global equities |
| `geopolitical_war_room.yaml` | Geopolitical risk analysis |

### Sector & Thematic:
| Preset YAML | Focus |
|-------------|-------|
| `sector_rotation_team.yaml` | Sector rotation strategy |
| `commodity_research_team.yaml` | Commodity analysis |
| `earnings_research_desk.yaml` | Earnings analysis |
| `event_driven_task_force.yaml` | Event-driven opportunities |
| `sentiment_intelligence_team.yaml` | Sentiment analysis |
| `social_alpha_team.yaml` | Social media alpha |

### Specialty:
| Preset YAML | Focus |
|-------------|-------|
| `derivatives_strategy_desk.yaml` | Derivatives & options |
| `etf_allocation_desk.yaml` | ETF allocation |
| `fund_selection_panel.yaml` | Fund selection |
| `pairs_research_lab.yaml` | Pair trading research |
| `portfolio_review_board.yaml` | Portfolio review |
| `risk_committee.yaml` | Risk assessment |
| `statistical_arbitrage_desk.yaml` | Statistical arbitrage |

---

## 15. BACKGROUND: HSM & API BRIDGE

- **HSM** (ai-engine.hsm): chuyển đổi internal→external calls, xác thực giữa backend và ai-engine
- **AIEngineService** (back-end): HTTP proxy circuit breaker pattern, cache responses
- **DnseRelayService** (back-end): subscribe Redis Pub/Sub, relay Socket.IO, missed-stream replay

---

## 16. WHAT'S MISSING / ROADMAP

### Priority 🔴 KIẾN TRÚC

| Item | Hiện tại | Giải pháp |
|------|----------|-----------|
| **2 ReAct loops** | `agents/core/loop.py` (AgentLoop) + `state/worker.py` (SwarmWorker, 792 dòng) cố tình tách biệt | Consolidate: worker kế thừa AgentLoop thay vì tự viết loop riêng |
| **38 run directories** | `brain/runs/` tích tụ không cleanup | Thêm retention policy (.gitignore + auto-clean 7 ngày) |
| **57 skills nhưng không VN-specific** | Tất cả global skills, không tuned cho VN market | Thêm `vn_equity_desk.yaml` preset + VN-specific trading rules |
| **Không có eval framework** | Không có LLM-as-judge, không metric cho agent output | Thêm `brain/eval/` với faithfulness, tool-use accuracy |

### Priority 🔴 CAO (Data)

| Item | Hiện tại | Giải pháp |
|------|----------|-----------|
| **Persist compute → DB** | On-demand 5s/request | ETL daily → 9 tables → 5ms |
| **adj_close** | ❌ THIẾU | yfinance dividends/splits → backward adjust |
| **Beta/Alpha thực** | hash-based fake | VNINDEX trong DB → real covariance |
| **Corporate actions** | vnstock ko trả | yfinance dividends + splits |
| **Deposit rates SBV** | hardcode 3.25-4.75% | SBV web scrape / FiinGroup API |
| **Risk flags real** | hash-based (DELAYED, AUDITOR, PLEDGE) | UBCKNN disclosure crawl |
| **nb_trades** | ❌ THIẾU | DNSE WS trade count in aggregation |

### Priority 🟡 TRUNG BÌNH

| Item | Hiện tại | Giải pháp |
|------|----------|-----------|
| **Insider trading structured** | text/news only | CafeF parse số liệu |
| **Enhanced sentiment** | Lexicon 63 từ | PhoBERT fine-tune |
| **Order Book persist** | Real-time only | Stream → hypertable |
| **Factor percentile real** | Per-stock, ko cross-stock | Batch compute all stocks |
| **Hypothesis real backtest** | JSON file only | Link với backtest engine |

### Priority 🟢 THẤP

| Item | Hiện tại | Giải pháp |
|------|----------|-----------|
| **RAG vector DB** | TF-IDF in-memory | pgvector + embedding model |
| **PDF chunking** | PyMuPDF parse, ko lưu | Structured chunks → DB |
| **Đa ngôn ngữ sentiment** | Tiếng Việt only | Thêm English lexicon |
| **Swarm UI** | YAML config | Web UI for DAG editor |

---

## 17. KEY FILES MAP

### ai-engine — Services (Python, ~150 files)

| File | Lines | Vai trò |
|------|-------|---------|
| `app/main.py` | ~20 | FastAPI app, import 31 routers |
| `app/lifespan.py` | ~50 | Startup: DB migration, DnseStreamHub, SessionService |
| `app/config/settings.py` | 78 | `.env` config singleton |
| `app/services/data_enricher.py` | 1414 | **Central compute engine** |
| `app/services/market_data_service.py` | ~600 | **Data facade** |
| `app/services/dnse/stream_hub.py` | ~350 | **WebSocket hub** |
| `app/services/dnse/websocket/client.py` | ~500 | **WS client** |
| `app/services/dnse/rest_client.py` | ~300 | **REST client** |
| `app/services/dnse/models.py` | ~200 | Pydantic models |
| `app/services/risk_flags.py` | ~250 | Risk detection |
| `app/services/ai_service.py` | ~400 | AI agent service |
| `app/services/screener_service.py` | ~300 | Stock screener |
| `app/services/news_ingestion.py` | ~200 | News crawler |
| `app/services/backfill_service.py` | ~200 | Daily ETL |
| `app/services/ml_alpha_predictor.py` | ~250 | XGBoost model |
| `app/services/deep_learning_alpha.py` | ~250 | LSTM/Transformer |
| `app/services/time_series_forecast.py` | ~200 | ARIMA forecast |

### ai-engine — Brain (AI/ML Core)

| File | Lines | Vai trò |
|------|-------|---------|
| `brain/state/graph.py` | ~100 | **Graph DUY NHẤT**: build_graph() |
| `brain/state/nodes.py` | ~400 | GraphNodes (12 node methods) |
| `brain/state/edges.py` | 210 | 11 conditional edge functions |
| `brain/state/state.py` | 94 | GraphState TypedDict |
| `brain/state/models.py` | 295 | SwarmRun, SwarmTask, Session, Message |
| `brain/state/service.py` | ~250 | SessionService |
| `brain/state/runtime.py` | ~500 | SwarmRuntime |
| `brain/state/worker.py` | 792 | SwarmWorker: ReAct loop + heartbeat |
| `brain/state/store.py` | 551 | SwarmStore: persistence, stale reaper |
| `brain/state/task_store.py` | 248 | DAG task store |
| `brain/state/events.py` | 227 | EventBus SSE streaming |
| `brain/state/search.py` | 361 | SQLite FTS5 session search |
| `brain/state/session_store.py` | 146 | File-based session store |
| `brain/state/grounding.py` | 219 | Pre-fetch OHLCV real |
| `brain/state/reflection.py` | 136 | Self-critique LLM |
| `brain/state/concurrency.py` | 201 | Parallel analysts |
| `brain/state/signal_processing.py` | 117 | 5-tier rating parser |
| `brain/state/checkpointer.py` | 201 | JSON checkpoint |
| `brain/state/serialization.py` | 51 | Task → public dict |
| `brain/agents/core/loop.py` | ~300 | AgentLoop (5-layer context) |
| `brain/agents/core/context.py` | — | ContextBuilder |
| `brain/agents/core/progress.py` | — | HeartbeatTimer |
| `brain/agents/core/skills.py` | — | SkillsLoader |
| `brain/agents/core/tools.py` | — | ToolRegistry |
| `brain/agents/core/trace.py` | — | TraceWriter |
| `brain/agents/schemas.py` | 228 | ResearchPlan, TraderProposal, PortfolioDecision |
| `brain/providers/orchestrator.py` | ~300 | 3-model routing |
| `brain/providers/router.py` | 94 | IntentRouter |
| `brain/providers/base.py` | 232 | BaseAgent abstract |
| `brain/providers/chat.py` | 239 | ChatLLM (Groq) |
| `brain/providers/groq_client.py` | ~200 | Groq agent |
| `brain/memory/persistent.py` | ~300 | PersistentMemory |
| `brain/security/scanner.py` | 176 | Prompt injection scanner |
| `brain/dataflows/interface.py` | ~100 | Vendor routing |
| `brain/config/loader.py` | 262 | Agent config + MCP merge |
| `brain/config/schema.py` | 113 | AgentConfig, MCPServerConfig |

### ai-engine — Quant

| File | Lines | Vai trò |
|------|-------|---------|
| `brain/quant/factors/base.py` | ~200 | 20+ operators |
| `brain/quant/factors/registry.py` | ~100 | Alpha registry |
| `brain/quant/factors/bench_runner.py` | ~200 | IC bench runner |
| `brain/quant/factor_analysis_core.py` | — | IC series analysis |
| `brain/quant/pipeline.py` | 123 | impute → winsorize → normalize |
| `brain/quant/hypotheses/registry.py` | ~300 | Hypothesis lifecycle |
| `brain/quant/skills.py` | 22 | Skill loader |

### ai-engine — Tools

| File | Lines | Vai trò |
|------|-------|---------|
| `brain/tools/vn_stock_analyze_tool.py` | — | Full VN stock analysis |
| `brain/tools/shadow_account/extractor.py` | ~300 | Journal parser |
| `brain/tools/shadow_account/codegen.py` | ~300 | Code generator |
| `brain/tools/shadow_account/backtester.py` | ~300 | Backtest runner |
| `brain/tools/shadow_account/reporter.py` | — | Report renderer |
| `brain/tools/shadow_account/scanner.py` | — | Today's signals |
| `brain/tools/shadow_account/storage.py` | — | Persistence |
| `brain/tools/backtest/runner.py` | — | Backtest runner |
| `brain/tools/backtest/engines/vietnam_equity.py` | — | VN equity engine |
| `brain/tools/backtest/loaders/dnse_loader.py` | — | DNSE data loader |
| `brain/tools/backtest/optimizers/hrp_optimizer.py` | — | HRP portfolio opt |

### ai-engine — Other

| File | Lines | Vai trò |
|------|-------|---------|
| `app/database/models.py` | 39 | PaperTrade + SessionLog |
| `brain/runs/` | 38 dirs | Run artifacts, traces, states |

### back-end (Node.js/TypeScript)

| File | Vai trò |
|------|---------|
| `src/services/aiEngine.service.ts` | HTTP proxy + circuit breaker |
| `src/services/dnseRelay.service.ts` | Redis → Socket.IO relay |
| `src/services/socket.service.ts` | Socket.IO server |
| `src/services/redis.service.ts` | Redis wrapper |
| `src/services/scheduler.service.ts` | BullMQ job queue |
| `src/services/portfolio.service.ts` | Paper trading logic |
| `prisma/schema.prisma` | 17 models |
