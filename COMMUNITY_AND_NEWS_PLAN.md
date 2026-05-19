# 🌐 Community Feature & Market News Architecture Plan [COMPLETED]

This document outlines the implemented strategy for the Community feature, Vietnamese market news aggregation, and scheduled AI analysis pipeline. All components have been fully built, synchronized, and verified.

---

## 1. Community Feature Implementation Plan [✅ DONE]
The Community feature is fully operational and connected to a robust, type-safe Postgres backend via Prisma.

### A. Core Features & API Endpoints [✅ DONE]
- **Posts Management:**
  - `POST /api/v1/community/posts` - Create a post (text, image, tagged symbols). [✅ DONE]
  - `GET /api/v1/community/posts` - Fetch news feed (infinite scroll support). [✅ DONE]
- **Interactions (Replies & Reactions):**
  - `POST /api/v1/community/posts/:id/comments` - Add a reply to a post. [✅ DONE]
  - `POST /api/v1/community/posts/:id/react` - Like/Upvote a post. [✅ DONE]
  - `POST /api/v1/community/comments/:id/react` - Like/Upvote a comment. [✅ DONE]
- **AI-Bot Integration:**
  - `POST /api/v1/community/bot/posts` - Dedicated secure authorization endpoint for automated AI posts. [✅ DONE]

### B. Database Schema (Prisma) [✅ DONE]
- **`User`**: [✅ DONE] Maps author relationships and handles credential metadata.
- **`Post`**: [✅ DONE] Contains `id`, `authorId`, `content`, `taggedSymbols`, `likesCount`, `commentsCount`.
- **`Comment`**: [✅ DONE] Supports threaded nested comments through self-relations.
- **`Reaction`**: [✅ DONE] Dynamic target identifier (POST or COMMENT) unique index guard.
- **`News`**: [✅ DONE] Stores unique `newsId`, `symbol`, `title`, `url`, `content`, `publishDate`, `friendlyKeyword`, `sentimentLabel`, and `sentimentScore`.

### C. Frontend Integration [✅ DONE]
- **State Management & Caching:** Handled perfectly via `React Query`. [✅ DONE]
- **Optimistic UI Updates:** Instant updates for likes and feed updates for high-fidelity trading board views. [✅ DONE]

---

## 2. Market News Aggregation Strategy [✅ DONE]

We leverage the **Vnstock V3** library with stable `VCI` source querying VN30 symbols.

### A. Phase 1: Historical Initialization [✅ DONE]
- Populates database history upon symbol querying, establishing robust context vectors.

### B. Phase 2: Real-time Scanning [✅ DONE]
- A active background scanning task scans all VN30 symbols continuously inside [news_ingestion.py](file:///d:/AIInvest/ai-engine/app/services/news_ingestion.py):
  1. Checks for already ingested articles using unique `newsId` queries in PostgreSQL. [✅ DONE]
  2. **If NOT exists (New):** Performs real-time sentiment scoring -> Saves to DB -> Launches AI analysis -> Posts to Community. [✅ DONE]
  3. **If exists (Old):** Breaks loop immediately to prevent rate limits and save resources. [✅ DONE]

---

## 3. Scheduled AI Analysis System [✅ DONE]

We coordinate precise AI Agent analysis pipelines throughout the Vietnamese market day (using exact GMT+7 conversion).

### A. The AI Coordination Schedule [✅ DONE]
1. **Pre-Market Report (08:30 AM GMT+7):** [✅ DONE]
   - Fetches overnight statistics and generates a cohesive Vietnamese "Bản Tin Trước Giờ Mở Cửa" post tagged to `VNINDEX`.
2. **Intraday Emergency Alerts (Real-time):** [✅ DONE]
   - Runs automatically when a high-impact new article is successfully ingested. Analyzes context vectors and publishes urgent alarms.
3. **End-of-Day Summary (15:15 PM GMT+7):** [✅ DONE]
   - Integrates final closing indices data and VN-Index reports to post an EOD session recap feed.

### B. Ensuring High-Quality AI Analysis [✅ DONE]
- **Semantic Search (RAG):** [✅ DONE] Implemented a custom TF-IDF with Cosine Similarity vector database in [news_rag.py](file:///d:/AIInvest/ai-engine/app/services/news_rag.py) using `scikit-learn` to query relevant historical symbol-specific context.
- **Sentiment Scoring:** [✅ DONE] Built a specialized Vietnamese financial lexicon scorer in [sentiment_scorer.py](file:///d:/AIInvest/ai-engine/app/services/sentiment_scorer.py) that assigns scores and tags positive, negative, or neutral labels to articles.

### C. Deep AI Analysis prompt Strategy [✅ DONE]
To enforce high-fidelity outputs, the following strategies have been fully implemented in [news_ingestion.py](file:///d:/AIInvest/ai-engine/app/services/news_ingestion.py):
1. **Hard-Data Injection**: [✅ DONE] Integrates actual real-time bid, ask, changes, and OHLCV indicators from `market_data_svc` context beside article texts.
2. **Strict Quant Persona**: [✅ DONE] Force-instructs AI to bypass conversational "water-brooking" remarks and deliver concrete supports/resistances.
3. **Structured Outputs**: [✅ DONE] Mandates strict quantitative tags:
   - Impact Level [1 to 10]
   - Controlling side [Bò / Gấu]
   - Technical ranges (Support / Resistance zones)
   - Falsification/Counter-risk scenarios

---

*All backend routes, Python models, schemas, and frontend hooks compile perfectly with `Exit Code: 0` (SUCCESS) for the production bundle.*
