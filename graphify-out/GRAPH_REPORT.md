# Graph Report - AIInvest  (2026-05-19)

## Corpus Check
- 152 files · ~93,724 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 812 nodes · 1346 edges · 58 communities (46 shown, 12 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 109 edges (avg confidence: 0.73)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `93b39904`
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
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]

## God Nodes (most connected - your core abstractions)
1. `TradingClient` - 58 edges
2. `DnseStreamHub` - 38 edges
3. `DNSEClient` - 34 edges
4. `AIEngineService` - 31 edges
5. `cn()` - 31 edges
6. `AIService` - 17 edges
7. `MarketDataService` - 16 edges
8. `NewsIngestionService` - 16 edges
9. `publish_json()` - 16 edges
10. `Badge()` - 16 edges

## Surprising Connections (you probably didn't know these)
- `get_ws_client()` --calls--> `TradingClient`  [INFERRED]
  ai-engine/app/config.py → ai-engine/app/services/dnse/websocket/client.py
- `get_dnse_client()` --calls--> `DNSEClient`  [INFERRED]
  ai-engine/app/config.py → ai-engine/app/services/dnse/api/client.py
- `main()` --calls--> `get_dnse_client()`  [INFERRED]
  ai-engine/app/services/marketdata-api/get_instruments.py → ai-engine/app/config.py
- `main()` --calls--> `get_dnse_client()`  [INFERRED]
  ai-engine/app/services/marketdata-api/get_latest_trade.py → ai-engine/app/config.py
- `main()` --calls--> `get_dnse_client()`  [INFERRED]
  ai-engine/app/services/marketdata-api/get_ohlc.py → ai-engine/app/config.py

## Communities (58 total, 12 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.05
Nodes (20): ChatMessage(), usePortfolioPerformance(), usePortfolioPositions(), usePortfolioRiskMetrics(), usePortfolioSummary(), exportScreenerCsv(), useBuiltinPresets(), useScreenerFilter() (+12 more)

### Community 1 - "Community 1"
Cohesion: 0.05
Nodes (32): add_to_sorted_set(), _channel(), get_hash(), get_list_range(), get_redis(), get_sorted_set_range(), publish_json(), push_to_list() (+24 more)

### Community 2 - "Community 2"
Cohesion: 0.06
Nodes (30): Exception, Establish WebSocket connection and authenticate.          Raises:, Perform HMAC authentication.          Raises:             AuthenticationError, Wrap handler to only fire when obj.{attr} == board_id., Each worker owns 1 queue. Symbol is hashed to worker_idx,         so same symbo, Check if an exception is related to connection issues.          Args:, Async WebSocket client for real-time trading data.      Features:     - Autom, Get (or create) a dedicated queue for a specific event type.         Use this w (+22 more)

### Community 3 - "Community 3"
Cohesion: 0.06
Nodes (31): useDebounce(), useAIConsensus(), useDashboardMarketData(), useMarketBreadth(), useMarketHeatmap(), useMarketIndices(), useMarketLiquidity(), useMarketSnapshot() (+23 more)

### Community 4 - "Community 4"
Cohesion: 0.05
Nodes (32): AuthManager, Initialize auth manager.          Args:             api_key: API key, Compute HMAC-SHA256 signature.          Args:             timestamp: Unix tim, HMAC-SHA256 authentication manager.      Handles signature generation and nonc, TradingClient - High-level async WebSocket client for real-time trading data., Initialize trading client.          Args:             api_key: API key for au, Close connection gracefully, Allow async iteration over messages (+24 more)

### Community 5 - "Community 5"
Cohesion: 0.05
Nodes (18): health(), AIInvest AI Engine — FastAPI + DNSE Open API (WebSocket market data)., get_settings(), Application settings — DNSE Open API credentials and feature flags., Settings, lifespan(), FastAPI lifespan — start/stop DNSE WebSocket hub., DnseRestClient (+10 more)

### Community 6 - "Community 6"
Cohesion: 0.06
Nodes (28): AIService, AI Service — Vibe-Trading integration customized for VN stock market. Provides c, Get backtest job status., Extract symbols from prompt and fetch relevant market data., Extract symbols from prompt and fetch relevant market data., Simple technical signal based on price action., Simple technical signal based on price action., Simple fundamental signal based on ratios. (+20 more)

### Community 7 - "Community 7"
Cohesion: 0.11
Nodes (5): DNSEClient, build_signature(), get_api_version(), get_date_header_name(), send_signed_request()

### Community 9 - "Community 9"
Cohesion: 0.11
Nodes (17): BaseModel, backtest_status(), BacktestRequest, chat(), ChatRequest, get_consensus(), AI Chat router — AI-powered analysis using Vibe-Trading agents customized for VN, AI chat with streaming response (SSE). (+9 more)

### Community 10 - "Community 10"
Cohesion: 0.11
Nodes (11): get_dnse_client(), get_ws_client(), get_stocks_by_market(), main(), Hàm phụ dùng để quét toàn bộ cổ phiếu của một sàn cụ thể, main(), main(), main() (+3 more)

### Community 11 - "Community 11"
Cohesion: 0.2
Nodes (3): NewsIngestionService, Phân tách nội dung của một thẻ <p> thành một danh sách các node text và link xen, Phân tách nội dung của một thẻ <p> thành một danh sách các node text và link xen

### Community 12 - "Community 12"
Cohesion: 0.21
Nodes (3): authMiddleware(), optionalAuth(), cached()

### Community 13 - "Community 13"
Cohesion: 0.12
Nodes (15): get_breadth(), get_heatmap(), get_indices(), get_liquidity(), get_snapshot(), get_stock_list(), Market Data router — wraps vnstock v4 for VN market indices, breadth, snapshot., Get VN-Index, HNX-Index, UPCOM real-time values. (+7 more)

### Community 14 - "Community 14"
Cohesion: 0.13
Nodes (8): NewsRAGService, News RAG Service — Lightweight semantic news retrieval using TF-IDF and Cosine S, Add new articles to the in-memory RAG database., Retrieve most semantically similar articles using Cosine Similarity., Format an article for vectorization., Check if article exists in RAG by newsId., Get all stored articles., Completely resets the in-memory database and clears vectors.

### Community 15 - "Community 15"
Cohesion: 0.2
Nodes (4): errorHandler(), DnseRelayService, shutdownScheduler(), shutdown()

### Community 16 - "Community 16"
Cohesion: 0.28
Nodes (12): get_core_symbols(), get_time(), handle_expected_price(), handle_foreign_trading(), handle_market_index(), handle_ohlc(), handle_ohlc_closed(), handle_quote() (+4 more)

### Community 17 - "Community 17"
Cohesion: 0.15
Nodes (12): 1. Community Feature Implementation Plan [✅ DONE], 2. Market News Aggregation Strategy [✅ DONE], 3. Scheduled AI Analysis System [✅ DONE], A. Core Features & API Endpoints [✅ DONE], A. Phase 1: Historical Initialization [✅ DONE], A. The AI Coordination Schedule [✅ DONE], B. Database Schema (Prisma) [✅ DONE], B. Ensuring High-Quality AI Analysis [✅ DONE] (+4 more)

### Community 18 - "Community 18"
Cohesion: 0.27
Nodes (6): initScheduler(), queueOhlcvBackfill(), scheduleRecurringJobs(), backfillOhlcv(), syncStocksFromEngine(), bootstrap()

### Community 19 - "Community 19"
Cohesion: 0.17
Nodes (11): **1. EXECUTIVE SUMMARY**, **2. ARCHITECTURAL FOUNDATION (SOLID & SOLIDIFIED)**, **3. PRO TRADER FEATURE MATRIX (REVEALING THE 98% COMPLETION)**, **4. THE "LAST 2%" - NEXT STEPS**, **5. EXPERT EVALUATION NOTE**, **A. Market Microstructure & Execution**, 🛡️ AIInvest Professional Trading Ecosystem - Final Architecture & Data Audit, **B. Advanced Technical Analysis** (+3 more)

### Community 20 - "Community 20"
Cohesion: 0.27
Nodes (7): createAccessToken(), createRefreshToken(), createSessionTokens(), getRefreshTokenFromRequest(), parseCookies(), parseDuration(), setRefreshTokenCookie()

### Community 22 - "Community 22"
Cohesion: 0.22
Nodes (8): 1. Audit Dữ liệu ảo (Mock Data Areas), 3. Yêu cầu Backend (API Requirements), 4. Kế hoạch loại bỏ Mock Data, A. Dashboard & Market Data, 📊 AIInvest FE - Architecture Gap Analysis & BE Requirements, B. Market Screener, C. Chi tiết cổ phiếu (Stock Detail), D. Quản lý danh mục (Portfolio)

### Community 23 - "Community 23"
Cohesion: 0.36
Nodes (7): extract_article_content(), get_links_by_scrolling(), main(), parse_paragraph_with_links(), Bóc tách chi tiết bài viết, bóc tách chính xác newsId từ URL., Cuộn trang tự động bằng Playwright để load thêm bài viết và thu thập các URL., Phân tách nội dung của một thẻ <p> thành một danh sách các node text và link xen

### Community 25 - "Community 25"
Cohesion: 0.36
Nodes (5): _estimate_rsi(), _mock_fundamentals(), _passes(), Multi-criteria stock screener — enriches snapshot with fundamentals and filters., ScreenerService

### Community 26 - "Community 26"
Cohesion: 0.43
Nodes (6): getPerformance(), getPositions(), getRiskMetrics(), getSummary(), getUserCash(), placeOrder()

### Community 27 - "Community 27"
Cohesion: 0.25
Nodes (7): Brand & Style, Colors, Components, Elevation & Depth, Layout & Spacing, Shapes, Typography

### Community 30 - "Community 30"
Cohesion: 0.4
Nodes (3): FinancialSentimentScorer, Vietnamese Stock Market Sentiment Scorer. Provides specialized lexicon-based sen, Analyze text and return sentiment score and label.

### Community 31 - "Community 31"
Cohesion: 0.4
Nodes (4): code:bash (npm run dev), Deploy on Vercel, Getting Started, Learn More

## Knowledge Gaps
- **159 isolated node(s):** `Cuộn trang tự động bằng Playwright để load thêm bài viết và thu thập các URL.`, `Phân tách nội dung của một thẻ <p> thành một danh sách các node text và link xen`, `Bóc tách chi tiết bài viết, bóc tách chính xác newsId từ URL.`, `AIInvest AI Engine — FastAPI + DNSE Open API (WebSocket market data).`, `Application settings — DNSE Open API credentials and feature flags.` (+154 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **12 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `DnseStreamHub` connect `Community 1` to `Community 2`, `Community 5`?**
  _High betweenness centrality (0.133) - this node is a cross-community bridge._
- **Why does `TradingClient` connect `Community 2` to `Community 16`, `Community 1`, `Community 10`, `Community 4`?**
  _High betweenness centrality (0.124) - this node is a cross-community bridge._
- **Why does `get_stream_hub()` connect `Community 5` to `Community 1`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **Are the 22 inferred relationships involving `TradingClient` (e.g. with `DnseStreamHub` and `AuthManager`) actually correct?**
  _`TradingClient` has 22 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `DNSEClient` (e.g. with `get_dnse_client()` and `._get_client()`) actually correct?**
  _`DNSEClient` has 4 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Cuộn trang tự động bằng Playwright để load thêm bài viết và thu thập các URL.`, `Phân tách nội dung của một thẻ <p> thành một danh sách các node text và link xen`, `Bóc tách chi tiết bài viết, bóc tách chính xác newsId từ URL.` to the rest of the system?**
  _159 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.05 - nodes in this community are weakly interconnected._