# Graph Report - AIInvest  (2026-05-19)

## Corpus Check
- 132 files · ~81,316 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 570 nodes · 915 edges · 46 communities (39 shown, 7 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 33 edges (avg confidence: 0.8)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `1e8a2c71`
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
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 29|Community 29]]

## God Nodes (most connected - your core abstractions)
1. `AIEngineService` - 31 edges
2. `cn()` - 29 edges
3. `DnseStreamHub` - 19 edges
4. `AIService` - 17 edges
5. `MarketDataService` - 16 edges
6. `NewsIngestionService` - 15 edges
7. `Badge()` - 15 edges
8. `SocketService` - 14 edges
9. `SocketClient` - 13 edges
10. `RedisService` - 12 edges

## Surprising Connections (you probably didn't know these)
- `lifespan()` --calls--> `get_stream_hub()`  [INFERRED]
  ai-engine/app/core/lifespan.py → ai-engine/app/services/dnse/stream_hub.py
- `subscribe_symbols()` --calls--> `get_stream_hub()`  [INFERRED]
  ai-engine/app/routers/stream.py → ai-engine/app/services/dnse/stream_hub.py
- `stream_status()` --calls--> `get_stream_hub()`  [INFERRED]
  ai-engine/app/routers/stream.py → ai-engine/app/services/dnse/stream_hub.py
- `health()` --calls--> `get_settings()`  [INFERRED]
  ai-engine/app/main.py → ai-engine/app/config/settings.py
- `health()` --calls--> `get_stream_hub()`  [INFERRED]
  ai-engine/app/main.py → ai-engine/app/services/dnse/stream_hub.py

## Communities (46 total, 7 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.07
Nodes (17): useAIConsensus(), useStockFundamentals(), useStockProfile(), useStockQuote(), usePortfolioPerformance(), usePortfolioPositions(), usePortfolioRiskMetrics(), usePortfolioSummary() (+9 more)

### Community 1 - "Community 1"
Cohesion: 0.06
Nodes (16): authMiddleware(), optionalAuth(), errorHandler(), DnseRelayService, RedisService, initScheduler(), queueOhlcvBackfill(), scheduleRecurringJobs() (+8 more)

### Community 2 - "Community 2"
Cohesion: 0.06
Nodes (27): useDebounce(), useDashboardMarketData(), useMarketBreadth(), useMarketHeatmap(), useMarketIndices(), useMarketLiquidity(), useMarketSnapshot(), useStockNews() (+19 more)

### Community 3 - "Community 3"
Cohesion: 0.07
Nodes (19): health(), AIInvest AI Engine — FastAPI + DNSE Open API (WebSocket market data)., get_settings(), Application settings — DNSE Open API credentials and feature flags., Settings, _channel(), get_redis(), publish_json() (+11 more)

### Community 4 - "Community 4"
Cohesion: 0.06
Nodes (28): AIService, AI Service — Vibe-Trading integration customized for VN stock market. Provides c, Get backtest job status., Extract symbols from prompt and fetch relevant market data., Extract symbols from prompt and fetch relevant market data., Simple technical signal based on price action., Simple technical signal based on price action., Simple fundamental signal based on ratios. (+20 more)

### Community 5 - "Community 5"
Cohesion: 0.08
Nodes (7): AIEngineService, getPerformance(), getPositions(), getRiskMetrics(), getSummary(), getUserCash(), placeOrder()

### Community 6 - "Community 6"
Cohesion: 0.11
Nodes (17): BaseModel, backtest_status(), BacktestRequest, chat(), ChatRequest, get_consensus(), AI Chat router — AI-powered analysis using Vibe-Trading agents customized for VN, AI chat with streaming response (SSE). (+9 more)

### Community 7 - "Community 7"
Cohesion: 0.13
Nodes (7): lifespan(), FastAPI lifespan — start/stop DNSE WebSocket hub., DnseRestClient, get_rest_client(), DNSE REST client — wraps official `dnse` SDK when credentials are present., Historical OHLC via REST — extend when trading token available., get_news_ingestion_service()

### Community 9 - "Community 9"
Cohesion: 0.12
Nodes (15): get_breadth(), get_heatmap(), get_indices(), get_liquidity(), get_snapshot(), get_stock_list(), Market Data router — wraps vnstock v4 for VN market indices, breadth, snapshot., Get VN-Index, HNX-Index, UPCOM real-time values. (+7 more)

### Community 11 - "Community 11"
Cohesion: 0.13
Nodes (8): NewsRAGService, News RAG Service — Lightweight semantic news retrieval using TF-IDF and Cosine S, Add new articles to the in-memory RAG database., Retrieve most semantically similar articles using Cosine Similarity., Format an article for vectorization., Check if article exists in RAG by newsId., Get all stored articles., Completely resets the in-memory database and clears vectors.

### Community 12 - "Community 12"
Cohesion: 0.15
Nodes (6): exportScreenerCsv(), useBuiltinPresets(), useScreenerFilter(), ScreenerContent(), ScreenerPage(), setAccessToken()

### Community 13 - "Community 13"
Cohesion: 0.15
Nodes (12): 1. Community Feature Implementation Plan [✅ DONE], 2. Market News Aggregation Strategy [✅ DONE], 3. Scheduled AI Analysis System [✅ DONE], A. Core Features & API Endpoints [✅ DONE], A. Phase 1: Historical Initialization [✅ DONE], A. The AI Coordination Schedule [✅ DONE], B. Database Schema (Prisma) [✅ DONE], B. Ensuring High-Quality AI Analysis [✅ DONE] (+4 more)

### Community 14 - "Community 14"
Cohesion: 0.17
Nodes (11): **1. EXECUTIVE SUMMARY**, **2. ARCHITECTURAL FOUNDATION (SOLID & SOLIDIFIED)**, **3. PRO TRADER FEATURE MATRIX (REVEALING THE 98% COMPLETION)**, **4. THE "LAST 2%" - NEXT STEPS**, **5. EXPERT EVALUATION NOTE**, **A. Market Microstructure & Execution**, 🛡️ AIInvest Professional Trading Ecosystem - Final Architecture & Data Audit, **B. Advanced Technical Analysis** (+3 more)

### Community 15 - "Community 15"
Cohesion: 0.27
Nodes (7): createAccessToken(), createRefreshToken(), createSessionTokens(), getRefreshTokenFromRequest(), parseCookies(), parseDuration(), setRefreshTokenCookie()

### Community 17 - "Community 17"
Cohesion: 0.22
Nodes (8): 1. Audit Dữ liệu ảo (Mock Data Areas), 3. Yêu cầu Backend (API Requirements), 4. Kế hoạch loại bỏ Mock Data, A. Dashboard & Market Data, 📊 AIInvest FE - Architecture Gap Analysis & BE Requirements, B. Market Screener, C. Chi tiết cổ phiếu (Stock Detail), D. Quản lý danh mục (Portfolio)

### Community 18 - "Community 18"
Cohesion: 0.36
Nodes (7): extract_article_content(), get_links_by_scrolling(), main(), parse_paragraph_with_links(), Bóc tách chi tiết bài viết, bóc tách chính xác newsId từ URL., Cuộn trang tự động bằng Playwright để load thêm bài viết và thu thập các URL., Phân tách nội dung của một thẻ <p> thành một danh sách các node text và link xen

### Community 20 - "Community 20"
Cohesion: 0.36
Nodes (5): _estimate_rsi(), _mock_fundamentals(), _passes(), Multi-criteria stock screener — enriches snapshot with fundamentals and filters., ScreenerService

### Community 21 - "Community 21"
Cohesion: 0.25
Nodes (7): Brand & Style, Colors, Components, Elevation & Depth, Layout & Spacing, Shapes, Typography

### Community 22 - "Community 22"
Cohesion: 0.4
Nodes (3): FinancialSentimentScorer, Vietnamese Stock Market Sentiment Scorer. Provides specialized lexicon-based sen, Analyze text and return sentiment score and label.

### Community 23 - "Community 23"
Cohesion: 0.4
Nodes (4): code:bash (npm run dev), Deploy on Vercel, Getting Started, Learn More

## Knowledge Gaps
- **102 isolated node(s):** `Cuộn trang tự động bằng Playwright để load thêm bài viết và thu thập các URL.`, `Phân tách nội dung của một thẻ <p> thành một danh sách các node text và link xen`, `Bóc tách chi tiết bài viết, bóc tách chính xác newsId từ URL.`, `AIInvest AI Engine — FastAPI + DNSE Open API (WebSocket market data).`, `Application settings — DNSE Open API credentials and feature flags.` (+97 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `get_stream_hub()` connect `Community 3` to `Community 7`?**
  _High betweenness centrality (0.043) - this node is a cross-community bridge._
- **Why does `get_settings()` connect `Community 3` to `Community 4`, `Community 7`?**
  _High betweenness centrality (0.036) - this node is a cross-community bridge._
- **What connects `Cuộn trang tự động bằng Playwright để load thêm bài viết và thu thập các URL.`, `Phân tách nội dung của một thẻ <p> thành một danh sách các node text và link xen`, `Bóc tách chi tiết bài viết, bóc tách chính xác newsId từ URL.` to the rest of the system?**
  _102 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.07 - nodes in this community are weakly interconnected._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.06 - nodes in this community are weakly interconnected._
- **Should `Community 2` be split into smaller, more focused modules?**
  _Cohesion score 0.06 - nodes in this community are weakly interconnected._
- **Should `Community 3` be split into smaller, more focused modules?**
  _Cohesion score 0.07 - nodes in this community are weakly interconnected._