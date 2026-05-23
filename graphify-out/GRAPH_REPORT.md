# Graph Report - AIInvest  (2026-05-22)

## Corpus Check
- 167 files · ~103,168 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 978 nodes · 1708 edges · 84 communities (65 shown, 19 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 167 edges (avg confidence: 0.73)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `2a09fc82`
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
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 62|Community 62]]
- [[_COMMUNITY_Community 66|Community 66]]
- [[_COMMUNITY_Community 72|Community 72]]
- [[_COMMUNITY_Community 73|Community 73]]

## God Nodes (most connected - your core abstractions)
1. `TradingClient` - 60 edges
2. `DnseStreamHub` - 54 edges
3. `DNSEClient` - 37 edges
4. `AIEngineService` - 34 edges
5. `cn()` - 34 edges
6. `SocketClient` - 26 edges
7. `SocketService` - 24 edges
8. `get_redis()` - 23 edges
9. `publish_json()` - 19 edges
10. `MarketDataService` - 18 edges

## Surprising Connections (you probably didn't know these)
- `get_ws_client()` --calls--> `TradingClient`  [INFERRED]
  ai-engine/app/config.py → ai-engine/app/services/dnse/websocket/client.py
- `health()` --calls--> `get_settings()`  [INFERRED]
  ai-engine/app/main.py → ai-engine/app/config/settings.py
- `health_detailed()` --calls--> `get_rate_limiter()`  [INFERRED]
  ai-engine/app/main.py → ai-engine/app/services/dnse/redis_pub.py
- `Settings` --uses--> `TradingClient`  [INFERRED]
  ai-engine/app/config/settings.py → ai-engine/app/services/dnse/websocket/client.py
- `_channel()` --calls--> `get_settings()`  [INFERRED]
  ai-engine/app/services/dnse/redis_pub.py → ai-engine/app/config/settings.py

## Communities (84 total, 19 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.06
Nodes (30): Exception, Establish WebSocket connection and authenticate.          Raises:, Perform HMAC authentication.          Raises:             AuthenticationError, Wrap handler to only fire when obj.{attr} == board_id., Each worker owns 1 queue. Symbol is hashed to worker_idx,         so same symbo, Check if an exception is related to connection issues.          Args:, Async WebSocket client for real-time trading data.      Features:     - Autom, Get (or create) a dedicated queue for a specific event type.         Use this w (+22 more)

### Community 1 - "Community 1"
Cohesion: 0.08
Nodes (11): useStockOHLCV(), getNextEvent(), useMarketSession(), QueryProvider(), RealtimeProvider(), useRealtimeContext(), SocketClient, normalizeTimestamp() (+3 more)

### Community 2 - "Community 2"
Cohesion: 0.06
Nodes (28): AIService, AI Service — Vibe-Trading integration customized for VN stock market. Provides c, Get backtest job status., Extract symbols from prompt and fetch relevant market data., Extract symbols from prompt and fetch relevant market data., Simple technical signal based on price action., Simple technical signal based on price action., Simple fundamental signal based on ratios. (+20 more)

### Community 3 - "Community 3"
Cohesion: 0.08
Nodes (25): get_dnse_client(), get_ws_client(), get_dnse_client(), get_settings(), get_ws_client(), Application settings — DNSE Open API credentials and feature flags., Settings, ensure_table() (+17 more)

### Community 4 - "Community 4"
Cohesion: 0.14
Nodes (7): Validate data against a Pydantic model. Returns None if invalid., validate_payload(), publish_json(), set_cache(), DnseStreamHub, Register symbols for stream., Register symbols for stream (reconnect applies new set).

### Community 5 - "Community 5"
Cohesion: 0.11
Nodes (5): DNSEClient, build_signature(), get_api_version(), get_date_header_name(), send_signed_request()

### Community 6 - "Community 6"
Cohesion: 0.16
Nodes (3): PageHeader(), cn(), Badge()

### Community 7 - "Community 7"
Cohesion: 0.08
Nodes (18): health(), health_detailed(), AIInvest AI Engine — FastAPI + DNSE Open API (WebSocket market data)., lifespan(), FastAPI lifespan — start/stop DNSE WebSocket hub., DnseRestClient, get_rest_client(), DNSE REST client — wraps official `dnse` SDK when credentials are present. Fall (+10 more)

### Community 8 - "Community 8"
Cohesion: 0.09
Nodes (24): add_to_sorted_set(), add_to_stream(), _channel(), get_hash(), get_rate_limiter(), publish_batch(), push_to_list(), Publish DNSE market events to Redis for the Node.js backend relay. (+16 more)

### Community 10 - "Community 10"
Cohesion: 0.12
Nodes (6): ChannelHealthTracker, Health monitoring for DNSE Stream Hub.  Tracks message flow per channel, detects, MarketSessionManager, MarketState, Vietnam Stock Market Session Manager.  Handles trading hours awareness for HOSE, Enum

### Community 11 - "Community 11"
Cohesion: 0.14
Nodes (12): useDashboardMarketData(), useMarketBreadth(), useMarketHeatmap(), useMarketIndices(), useMarketLiquidity(), useMarketSnapshot(), useStockNews(), useStockOrderBook() (+4 more)

### Community 12 - "Community 12"
Cohesion: 0.15
Nodes (8): useAIConsensus(), useStockFundamentals(), useStockProfile(), useStockQuote(), formatVolume(), getPriceColor(), FundamentalData(), TradingData()

### Community 13 - "Community 13"
Cohesion: 0.18
Nodes (7): errorHandler(), initScheduler(), queueOhlcvBackfill(), scheduleRecurringJobs(), shutdownScheduler(), bootstrap(), shutdown()

### Community 15 - "Community 15"
Cohesion: 0.19
Nodes (4): NewsIngestionService, Phân tách nội dung của một thẻ <p> thành một danh sách các node text và link xen, Phân tách nội dung của một thẻ <p> thành một danh sách các node text và link xen, Phân tách nội dung của một thẻ <p> thành một danh sách các node text và link xen

### Community 16 - "Community 16"
Cohesion: 0.19
Nodes (7): get_redis(), MarketDataService, Return ALL stocks from DNSE hub.                  Strategy:         1. Get ALL s, Return live liquidity from Redis or compute from snapshot., Compute heatmap from live snapshot., Return live indices from hub or Redis. Fallback to REST., Calculate breadth from live snapshot.

### Community 17 - "Community 17"
Cohesion: 0.13
Nodes (11): Close connection gracefully, Allow async iteration over messages, WebSocket connection manager with automatic reconnection.      Features:, Initialize connection manager.          Args:             url: WebSocket URL, Establish WebSocket connection.          Raises:             ConnectionError:, WebSocketConnection, ConnectionClosed, ConnectionError (+3 more)

### Community 18 - "Community 18"
Cohesion: 0.17
Nodes (6): exportScreenerCsv(), useBuiltinPresets(), useScreenerFilter(), ScreenerContent(), ScreenerPage(), setAccessToken()

### Community 19 - "Community 19"
Cohesion: 0.25
Nodes (13): autoBackfillIfNeeded(), backfillTodayOhlcv(), backfillTodaySnapshot(), createSessionLog(), getBackfillHistory(), getTodaySessionLog(), getVietnamDate(), getVietnamHolidays() (+5 more)

### Community 20 - "Community 20"
Cohesion: 0.12
Nodes (15): get_breadth(), get_heatmap(), get_indices(), get_liquidity(), get_snapshot(), get_stock_list(), Market Data router — wraps vnstock v4 for VN market indices, breadth, snapshot., Get VN-Index, HNX-Index, UPCOM real-time values. (+7 more)

### Community 21 - "Community 21"
Cohesion: 0.13
Nodes (8): NewsRAGService, News RAG Service — Lightweight semantic news retrieval using TF-IDF and Cosine S, Add new articles to the in-memory RAG database., Retrieve most semantically similar articles using Cosine Similarity., Format an article for vectorization., Check if article exists in RAG by newsId., Get all stored articles., Completely resets the in-memory database and clears vectors.

### Community 22 - "Community 22"
Cohesion: 0.23
Nodes (3): authMiddleware(), optionalAuth(), cached()

### Community 23 - "Community 23"
Cohesion: 0.22
Nodes (9): mapBreadthResponse(), mapHeatmapSectors(), mapIndicesResponse(), mapLiquidityResponse(), mapQuoteResponse(), mapRowToQuote(), mapSnapshotToQuotes(), signalFromChange() (+1 more)

### Community 24 - "Community 24"
Cohesion: 0.22
Nodes (10): BaseModel, Pydantic models for DNSE WebSocket payload validation.  All incoming DNSE data i, ValidatedExpectedPrice, ValidatedForeignTrading, ValidatedMarketIndex, ValidatedOhlc, ValidatedOrderBook, ValidatedSecurityDef (+2 more)

### Community 25 - "Community 25"
Cohesion: 0.14
Nodes (9): get_sorted_set_range(), Get range from Redis Sorted Set (ZRANGEBYSCORE)., Get range from Redis Sorted Set (ZRANGEBYSCORE)., Get OHLC closed history from Redis Sorted Set., _query_pg_ohlcv(), Unified market data facade — DNSE WebSocket hub (primary) + PostgreSQL + REST fa, Query daily OHLCV from PostgreSQL., Fetch OHLCV from DNSE REST API or public fallback. (+1 more)

### Community 26 - "Community 26"
Cohesion: 0.28
Nodes (12): get_core_symbols(), get_time(), handle_expected_price(), handle_foreign_trading(), handle_market_index(), handle_ohlc(), handle_ohlc_closed(), handle_quote() (+4 more)

### Community 27 - "Community 27"
Cohesion: 0.26
Nodes (5): usePortfolioPerformance(), usePortfolioPositions(), usePortfolioRiskMetrics(), usePortfolioSummary(), formatCurrency()

### Community 28 - "Community 28"
Cohesion: 0.15
Nodes (12): 1. Community Feature Implementation Plan [✅ DONE], 2. Market News Aggregation Strategy [✅ DONE], 3. Scheduled AI Analysis System [✅ DONE], A. Core Features & API Endpoints [✅ DONE], A. Phase 1: Historical Initialization [✅ DONE], A. The AI Coordination Schedule [✅ DONE], B. Database Schema (Prisma) [✅ DONE], B. Ensuring High-Quality AI Analysis [✅ DONE] (+4 more)

### Community 29 - "Community 29"
Cohesion: 0.17
Nodes (11): backtest_status(), BacktestRequest, chat(), ChatRequest, get_consensus(), AI Chat router — AI-powered analysis using Vibe-Trading agents customized for VN, AI chat with streaming response (SSE)., Get AI consensus analysis for a stock. (+3 more)

### Community 30 - "Community 30"
Cohesion: 0.17
Nodes (11): **1. EXECUTIVE SUMMARY**, **2. ARCHITECTURAL FOUNDATION (SOLID & SOLIDIFIED)**, **3. PRO TRADER FEATURE MATRIX (REVEALING THE 98% COMPLETION)**, **4. THE "LAST 2%" - NEXT STEPS**, **5. EXPERT EVALUATION NOTE**, **A. Market Microstructure & Execution**, 🛡️ AIInvest Professional Trading Ecosystem - Final Architecture & Data Audit, **B. Advanced Technical Analysis** (+3 more)

### Community 31 - "Community 31"
Cohesion: 0.24
Nodes (3): RateLimitedPublisher, Token Bucket Rate Limiter for Redis publish.  Prevents Redis flood from high-fre, TokenBucket

### Community 32 - "Community 32"
Cohesion: 0.27
Nodes (7): createAccessToken(), createRefreshToken(), createSessionTokens(), getRefreshTokenFromRequest(), parseCookies(), parseDuration(), setRefreshTokenCookie()

### Community 34 - "Community 34"
Cohesion: 0.22
Nodes (8): 1. Audit Dữ liệu ảo (Mock Data Areas), 3. Yêu cầu Backend (API Requirements), 4. Kế hoạch loại bỏ Mock Data, A. Dashboard & Market Data, 📊 AIInvest FE - Architecture Gap Analysis & BE Requirements, B. Market Screener, C. Chi tiết cổ phiếu (Stock Detail), D. Quản lý danh mục (Portfolio)

### Community 35 - "Community 35"
Cohesion: 0.36
Nodes (7): extract_article_content(), get_links_by_scrolling(), main(), parse_paragraph_with_links(), Bóc tách chi tiết bài viết, bóc tách chính xác newsId từ URL., Cuộn trang tự động bằng Playwright để load thêm bài viết và thu thập các URL., Phân tách nội dung của một thẻ <p> thành một danh sách các node text và link xen

### Community 37 - "Community 37"
Cohesion: 0.36
Nodes (5): _estimate_rsi(), _mock_fundamentals(), _passes(), Multi-criteria stock screener — enriches snapshot with fundamentals and filters., ScreenerService

### Community 38 - "Community 38"
Cohesion: 0.25
Nodes (5): get_list_range(), Get range of values from Redis List., Get range of values from Redis List., Get trade history from Redis List (most recent first)., Get trade extra history from Redis List.

### Community 39 - "Community 39"
Cohesion: 0.25
Nodes (3): DNSE WebSocket market stream hub.  Runs TradingClient in a background thread, ca, Emit mock ticks when DNSE keys are not configured., _trend()

### Community 40 - "Community 40"
Cohesion: 0.29
Nodes (4): AuthManager, Initialize auth manager.          Args:             api_key: API key, Compute HMAC-SHA256 signature.          Args:             timestamp: Unix tim, HMAC-SHA256 authentication manager.      Handles signature generation and nonc

### Community 41 - "Community 41"
Cohesion: 0.36
Nodes (7): AuthenticationError, EncodingError, Base exception for all SDK errors, Authentication failed, Message encoding/decoding failed, SubscriptionError, TradingWebSocketError

### Community 42 - "Community 42"
Cohesion: 0.25
Nodes (5): Initialize trading client.          Args:             api_key: API key for au, MessageDecoder, Decode messages from WebSocket, Initialize decoder.          Args:             encoding: "json" or "msgpack", Decode message.          Args:             data: Encoded bytes          Ret

### Community 43 - "Community 43"
Cohesion: 0.43
Nodes (6): getPerformance(), getPositions(), getRiskMetrics(), getSummary(), getUserCash(), placeOrder()

### Community 44 - "Community 44"
Cohesion: 0.25
Nodes (7): Brand & Style, Colors, Components, Elevation & Depth, Layout & Spacing, Shapes, Typography

### Community 45 - "Community 45"
Cohesion: 0.29
Nodes (3): Version information for api sdk., DNSE Open API integration — REST + WebSocket market stream., Version information for trading_websocket sdk.

### Community 46 - "Community 46"
Cohesion: 0.29
Nodes (4): MessageEncoder, Initialize encoder.          Args:             encoding: "json" or "msgpack", Encode message.          Args:             data: Message dict          Retu, Encode messages for WebSocket transmission

### Community 50 - "Community 50"
Cohesion: 0.33
Nodes (4): filter_stocks(), Screener router — filter stocks by financial/technical criteria., Screen stocks based on multi-criteria filters., ScreenerFilter

### Community 53 - "Community 53"
Cohesion: 0.4
Nodes (3): FinancialSentimentScorer, Vietnamese Stock Market Sentiment Scorer. Provides specialized lexicon-based sen, Analyze text and return sentiment score and label.

### Community 57 - "Community 57"
Cohesion: 0.4
Nodes (4): code:bash (npm run dev), Deploy on Vercel, Getting Started, Learn More

## Knowledge Gaps
- **187 isolated node(s):** `Cuộn trang tự động bằng Playwright để load thêm bài viết và thu thập các URL.`, `Phân tách nội dung của một thẻ <p> thành một danh sách các node text và link xen`, `Bóc tách chi tiết bài viết, bóc tách chính xác newsId từ URL.`, `Test DNSE market.trades API with historical data. Tests fetching trades from lon`, `AIInvest AI Engine — FastAPI + DNSE Open API (WebSocket market data).` (+182 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **19 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `DnseStreamHub` connect `Community 4` to `Community 0`, `Community 5`, `Community 38`, `Community 39`, `Community 7`, `Community 10`, `Community 24`, `Community 25`, `Community 58`, `Community 60`, `Community 62`?**
  _High betweenness centrality (0.134) - this node is a cross-community bridge._
- **Why does `TradingClient` connect `Community 0` to `Community 3`, `Community 4`, `Community 40`, `Community 41`, `Community 42`, `Community 46`, `Community 17`, `Community 54`, `Community 26`?**
  _High betweenness centrality (0.112) - this node is a cross-community bridge._
- **Why does `get_settings()` connect `Community 3` to `Community 2`, `Community 7`, `Community 8`, `Community 10`, `Community 16`?**
  _High betweenness centrality (0.066) - this node is a cross-community bridge._
- **Are the 24 inferred relationships involving `TradingClient` (e.g. with `Settings` and `DnseStreamHub`) actually correct?**
  _`TradingClient` has 24 INFERRED edges - model-reasoned connections that need verification._
- **Are the 13 inferred relationships involving `DnseStreamHub` (e.g. with `DNSEClient` and `TradingClient`) actually correct?**
  _`DnseStreamHub` has 13 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `DNSEClient` (e.g. with `DnseStreamHub` and `get_dnse_client()`) actually correct?**
  _`DNSEClient` has 7 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Cuộn trang tự động bằng Playwright để load thêm bài viết và thu thập các URL.`, `Phân tách nội dung của một thẻ <p> thành một danh sách các node text và link xen`, `Bóc tách chi tiết bài viết, bóc tách chính xác newsId từ URL.` to the rest of the system?**
  _187 weakly-connected nodes found - possible documentation gaps or missing edges._