# Graph Report - AIInvest  (2026-05-15)

## Corpus Check
- 126 files · ~78,288 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 487 nodes · 805 edges · 40 communities (32 shown, 8 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 29 edges (avg confidence: 0.8)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `069ac8e6`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 24|Community 24]]

## God Nodes (most connected - your core abstractions)
1. `AIEngineService` - 31 edges
2. `cn()` - 29 edges
3. `DnseStreamHub` - 19 edges
4. `MarketDataService` - 16 edges
5. `Badge()` - 15 edges
6. `SocketService` - 14 edges
7. `AIService` - 13 edges
8. `SocketClient` - 13 edges
9. `RedisService` - 12 edges
10. `SubscriptionService` - 9 edges

## Surprising Connections (you probably didn't know these)
- `subscribe_symbols()` --calls--> `get_stream_hub()`  [INFERRED]
  ai-engine/app/routers/stream.py → ai-engine/app/services/dnse/stream_hub.py
- `stream_status()` --calls--> `get_stream_hub()`  [INFERRED]
  ai-engine/app/routers/stream.py → ai-engine/app/services/dnse/stream_hub.py
- `health()` --calls--> `get_settings()`  [INFERRED]
  ai-engine/app/main.py → ai-engine/app/config/settings.py
- `health()` --calls--> `get_stream_hub()`  [INFERRED]
  ai-engine/app/main.py → ai-engine/app/services/dnse/stream_hub.py
- `_channel()` --calls--> `get_settings()`  [INFERRED]
  ai-engine/app/services/dnse/redis_pub.py → ai-engine/app/config/settings.py

## Communities (40 total, 8 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.06
Nodes (16): authMiddleware(), optionalAuth(), errorHandler(), DnseRelayService, RedisService, initScheduler(), queueOhlcvBackfill(), scheduleRecurringJobs() (+8 more)

### Community 1 - "Community 1"
Cohesion: 0.09
Nodes (8): PageHeader(), formatVolume(), getPriceColor(), cn(), FundamentalData(), TradingData(), Badge(), ErrorBoundary

### Community 2 - "Community 2"
Cohesion: 0.05
Nodes (26): health(), AIInvest AI Engine — FastAPI + DNSE Open API (WebSocket market data)., get_settings(), Application settings — DNSE Open API credentials and feature flags., Settings, lifespan(), FastAPI lifespan — start/stop DNSE WebSocket hub., _channel() (+18 more)

### Community 3 - "Community 3"
Cohesion: 0.06
Nodes (29): useAIConsensus(), useDashboardMarketData(), useMarketBreadth(), useMarketHeatmap(), useMarketIndices(), useMarketLiquidity(), useMarketSnapshot(), useStockFundamentals() (+21 more)

### Community 4 - "Community 4"
Cohesion: 0.11
Nodes (10): usePortfolioPerformance(), usePortfolioPositions(), usePortfolioRiskMetrics(), usePortfolioSummary(), exportScreenerCsv(), useBuiltinPresets(), useScreenerFilter(), formatCurrency() (+2 more)

### Community 5 - "Community 5"
Cohesion: 0.1
Nodes (14): AIService, AI Service — Vibe-Trading integration customized for VN stock market. Provides c, Extract symbols from prompt and fetch relevant market data., Simple technical signal based on price action., Simple fundamental signal based on ratios., Combine technical and fundamental signals., Generate Vietnamese summary text., Generate analysis text (placeholder for LLM integration). (+6 more)

### Community 7 - "Community 7"
Cohesion: 0.15
Nodes (13): createAccessToken(), createRefreshToken(), createSessionTokens(), getRefreshTokenFromRequest(), parseCookies(), parseDuration(), setRefreshTokenCookie(), getPerformance() (+5 more)

### Community 8 - "Community 8"
Cohesion: 0.11
Nodes (16): BaseModel, backtest_status(), BacktestRequest, chat(), ChatRequest, get_consensus(), AI Chat router — AI-powered analysis using Vibe-Trading agents customized for VN, AI chat with streaming response (SSE). (+8 more)

### Community 10 - "Community 10"
Cohesion: 0.12
Nodes (15): get_breadth(), get_heatmap(), get_indices(), get_liquidity(), get_snapshot(), get_stock_list(), Market Data router — wraps vnstock v4 for VN market indices, breadth, snapshot., Get VN-Index, HNX-Index, UPCOM real-time values. (+7 more)

### Community 11 - "Community 11"
Cohesion: 0.17
Nodes (11): **1. EXECUTIVE SUMMARY**, **2. ARCHITECTURAL FOUNDATION (SOLID & SOLIDIFIED)**, **3. PRO TRADER FEATURE MATRIX (REVEALING THE 98% COMPLETION)**, **4. THE "LAST 2%" - NEXT STEPS**, **5. EXPERT EVALUATION NOTE**, **A. Market Microstructure & Execution**, 🛡️ AIInvest Professional Trading Ecosystem - Final Architecture & Data Audit, **B. Advanced Technical Analysis** (+3 more)

### Community 13 - "Community 13"
Cohesion: 0.22
Nodes (8): 1. Audit Dữ liệu ảo (Mock Data Areas), 3. Yêu cầu Backend (API Requirements), 4. Kế hoạch loại bỏ Mock Data, A. Dashboard & Market Data, 📊 AIInvest FE - Architecture Gap Analysis & BE Requirements, B. Market Screener, C. Chi tiết cổ phiếu (Stock Detail), D. Quản lý danh mục (Portfolio)

### Community 15 - "Community 15"
Cohesion: 0.36
Nodes (5): _estimate_rsi(), _mock_fundamentals(), _passes(), Multi-criteria stock screener — enriches snapshot with fundamentals and filters., ScreenerService

### Community 16 - "Community 16"
Cohesion: 0.25
Nodes (7): Brand & Style, Colors, Components, Elevation & Depth, Layout & Spacing, Shapes, Typography

### Community 18 - "Community 18"
Cohesion: 0.4
Nodes (4): code:bash (npm run dev), Deploy on Vercel, Getting Started, Learn More

## Knowledge Gaps
- **68 isolated node(s):** `AIInvest AI Engine — FastAPI + DNSE Open API (WebSocket market data).`, `Application settings — DNSE Open API credentials and feature flags.`, `FastAPI lifespan — start/stop DNSE WebSocket hub.`, `AI Chat router — AI-powered analysis using Vibe-Trading agents customized for VN`, `AI chat with streaming response (SSE).` (+63 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `AIEngineService` connect `Community 6` to `Community 0`, `Community 7`?**
  _High betweenness centrality (0.020) - this node is a cross-community bridge._
- **Why does `cn()` connect `Community 1` to `Community 17`, `Community 3`, `Community 12`, `Community 4`?**
  _High betweenness centrality (0.019) - this node is a cross-community bridge._
- **What connects `AIInvest AI Engine — FastAPI + DNSE Open API (WebSocket market data).`, `Application settings — DNSE Open API credentials and feature flags.`, `FastAPI lifespan — start/stop DNSE WebSocket hub.` to the rest of the system?**
  _68 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.06 - nodes in this community are weakly interconnected._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.09 - nodes in this community are weakly interconnected._
- **Should `Community 2` be split into smaller, more focused modules?**
  _Cohesion score 0.05 - nodes in this community are weakly interconnected._
- **Should `Community 3` be split into smaller, more focused modules?**
  _Cohesion score 0.06 - nodes in this community are weakly interconnected._