# Rule 06: Scoped SAG Analysis Hold (Phase 1 Research Chat Released; GIL & 12-Agent Integration Closed)

**Status: NARROWED — Phase 1 Stock Research / MOAT & Enterprise Analysis AI Chat is RELEASED; GIL and live 12-Agent decision integration remain permanently CLOSED/DEFERRED (`SAG_CLOSED`) until Phase 2 (Chặng 4) after passing the Phase 1 Chat Exit Gate.**

## 1. Permanently Closed / Deferred Scope (GIL & `ai-engine` 12-Agent Decision Runtime)

Legacy GIL abnormal-capital-flow detection and direct `ai-engine` runtime integration with SAG remain **CLOSED**:

- `ai-engine` (including Agent 04 `EquityResearchAgent`, Agent 05 `CounterThesisAgent`, and all 12 Decision Agents) must **not**:
  - connect to SAG for OCR, reprocess, extraction, embedding, activation, GIL, assessment, document status, Markdown, or any other read/write operation;
  - upload, overwrite, delete, or replace SAG-owned canonical Markdown/R2 artifacts;
  - treat GIL or unverified extraction outputs as production-certified abnormal-capital-flow detectors or assume `PASS`, `CLEAR`, or `NO_ABNORMAL_FLOW_OBSERVED` proves no abnormal flow exists;
  - modify `docker-compose.yml` to make `ai-engine` depend on `sag-api` or `SAG_API_BASE`.
- `ai-engine/tests/test_sag_runtime_separation.py` MUST continue to pass 100%.
- Until an explicit Phase 2 release after the owner approves the Phase 1 Chat Exit Gate (`G1 >= 98%` `quote_hash` match, `G2 >= 98%` table/calculator accuracy, `G3 = 100%` abstention, `G4 = 0%` PIT leakage, `G5 >= 90% PASS` across `>= 40` sessions on the 8 pilot tickers), any SAG-derived MOAT integration into `ai-engine` decision workflows remains `SAG_CLOSED` / deferred. It must not be relabeled as `DATA_INSUFFICIENT`, used to lower a score, or treated as a negative GIL signal.

## 2. Released Scope: Phase 1 Stock Research / MOAT & Enterprise Analysis AI Chat

The repository owner has explicitly released **Phase 1 Stock Research / MOAT & Enterprise Analysis AI Chat** (`SAG` v2 + `back-end` Express 4 streaming gateway + `front-end` `/research-chat` verification UI), running isolated under the `research` Docker Compose profile (`sag-postgres`, `sag-qdrant`, `sag-api`).

### Boundary Documentation (4 Mandatory Items)

1. **Exact Callers and Endpoints / Contracts**
   - **BFF Caller -> SAG API:** `back-end/src/services/sagResearchChat.service.ts` (dedicated `60000ms` SSE client, strictly bypassing the `800ms` dashboard `aiEngineService`) mounted behind authenticated routes in `back-end/src/modules/ai/ai.routes.ts` (`/api/v1/ai/research-chat/sessions`, `/api/v1/ai/research-chat/sessions/:id`, `/api/v1/ai/research-chat/stream`, `/api/v1/ai/research-chat/feedback`, `/api/v1/ai/research-chat/eval-summary`, `/api/v1/ai/research-chat/kg/:ticker`, `/api/v1/ai/research-chat/tables/:tableId`). Supports optional `ticker` (free-form general chat without single-ticker lock) and `mode: "GENERAL" | "MOAT"`, forwarding org-scoped read-only headers (`X-Continuum-Organization-Id: <SAG_RESEARCH_ORG_ID | aiinvest-default>`, `X-Continuum-Project-Id: all`, `X-Continuum-Security-Partition: all`, `X-Continuum-Allowed-Project-Ids: *`, strictly tenant-scoped).
   - **SAG API Endpoints:** `SAG/apps/api/sag_api/api/v1/research_chat.py` (`/api/v1/research-chat/*`), `/api/v1/documents/*`, and `/api/v1/search/*` (wired via `api/v1/router.py`), authenticated with Continuum BFF headers (`X-SAG-BFF-Service-Token`, `X-Continuum-User-Id`, `X-Continuum-Organization-Id`, `X-Continuum-Project-Id`, `X-Continuum-Security-Partition`, `X-Continuum-Allowed-Project-Ids`) with Auth-Before-Action enforcement on `/ingest-batch`.
   - **Frontend Caller:** `front-end/src/app/(workspace)/research-chat/**`, `front-end/src/components/research-chat/**`, and `front-end/src/lib/api/researchChat.ts` implement the ChatGPT-style `/research-chat` workspace (collapsible conversation sidebar, free-form multi-hop chat stream with optional `@ticker` picker & `General` / `MOAT Broker` mode, and slide-over Evidence Drawer) calling only Express 4 BFF routes under `/api/v1/ai/research-chat/*` (never `ai-engine` Agent 04/05 panels or direct SAG).
   - **Explicitly Excluded Callers:** Zero callers from `ai-engine` (`EquityResearchAgent` / Agent 04, `CounterThesisAgent` / Agent 05, or any of the 12 Decision Agents).

2. **Source Documents and Expected Outputs**
   - **Source Documents:** HOSE corporate filings from Core DB (`knowledge_documents`, `bctc_pipeline_records`, `stocks`, `financial_statements`, `financial_ratios`, `macro_indicators`, `market_regime`) and R2 PDFs (`annual_report`, `CONSOLIDATED`/`SEPARATE` audited BCTC notes, conditional `agm_resolution` and `LATEST_QUARTER` notes; excluding parent `SEPARATE` holdings, `governance_report`, `analyst_report`, and `news`), triaged by `InputInspector` (Zip-Slip-safe & multi-part `.zip` merge, `NOTICE_ONLY < 15p` with AGM/Quarter exemption, `BAD_FONT`, image-coverage/watermark scan detection, 4-branch A/B/C/D hybrid router) with `0` LLM calls at ingest.
   - **Structured Outputs:**
     - **4 Natural Document Classes** (`BCTN_CORE`, `BCTN_PR`, `BCTC_AUDITED`, `AGM_TARGETS`/`QUARTER_NOTES`; with legacy `S01..S13` read compatibility) featuring `<!-- PAGE N -->` original PDF page anchors, stateful Markdown heading normalization (`0` Notes under LCTT, `< 12%` `BCTN_CORE` headings), `\x16` font-quirk repair, and `valid_from = effective_published_at` + bi-temporal `valid_to_ts` on restatement.
     - `sag_extracted_tables` (normalized 2D matrices with reconstructed headers, semantic `column_roles`/`amount_col_indices`, `unit_multiplier`, `currency`, `verification_status` `VERIFIED | PARTIAL | TABLE_UNVERIFIED`) + grouped `Row-Linearized Table Search Cards` indexed via `CanonicalBlock(block_type="table_card")` + atomic `SearchUnit` (`table_id` in Qdrant payload, skipped from `KnowledgeUnit` Leiden tree) (`Qwen/Qwen3-Embedding-0.6B` 1024d + VN-Dot BM25).
     - Zero-hardcode 3-Layer Knowledge Graph (`kg_nodes`, `kg_edges`, `macro_sensitivity_snapshots`) linking Macro, Sector, True-Peer clusters, and Company value-chain evidence with PIT validity (`effective_from <= as_of_date < valid_to`).
     - Grounded SSE chat responses (`routing -> retrieval -> graph_hop -> table_hydration -> calculator -> synthesis -> verification -> done`) supporting `GENERAL` and `MOAT` modes, multi-turn contextual query rewrite, dynamic ticker/universe scope resolution (capped at `MAX_TICKERS_PER_REQUEST = 6`, fail-closed `NO_DOC_COVERAGE`), Vietnamese multi-hop query decomposition with dense + sparse subquery search & 1-round conditional bridge Hop-2, adaptive per-ticker/subquery retrieval balancing, relevance-floor sufficiency gating, verbatim `quote_hash`-verified citations (`[C99]` hallucination rejection), deterministic `CalculatorTrace` objects (zero LLM mental math), `DiscrepancyAlert` flags, and explicit `declared_blindspots[]`.

3. **State Mutations, Rollback, and Observability Impact**
   - **SAG PostgreSQL (`sag-postgres`):** Idempotent SQL migration `SAG/apps/api/migrations/0013_stock_research_tables_and_kg.sql` (and ORM models in `sag_api/db/models/stock_research.py`) creating `sag_extracted_tables`, `kg_nodes`, `kg_edges`, and `macro_sensitivity_snapshots` with PIT indexes. Rollback via `DROP TABLE IF EXISTS macro_sensitivity_snapshots, kg_edges, kg_nodes, sag_extracted_tables;`.
   - **Qdrant (`sag-qdrant`):** Shared multi-tenant `aiinvest_search_units` collection (`project_id` `is_tenant: true`, `on_disk: true`, `int8` quantization) with extended `PAYLOAD_INDEX_FIELDS` (`valid_from_ts`, `valid_to_ts`, `fiscal_year`, `fiscal_quarter`, `doc_type`, `sub_doc_slot`, `table_id`). Restatement updates `valid_to_ts` in place instead of deleting historical points so PIT replay never loses restated filings. Rollback by deleting/re-indexing the research-profile Qdrant collection without touching Core DB.
   - **Core PostgreSQL (`back-end` Prisma):** Reuses existing `chat_sessions` and `chat_messages` (`metadata` JSONB) and adds `sag_chat_eval_feedback` via an isolated Prisma migration (`back-end/prisma/migrations/*_sag_chat_eval_feedback/`). Rollback by reverting the Prisma migration / dropping `sag_chat_eval_feedback` (no mutations to trading, portfolio, or agent scoring tables).
   - **Observability:** SSE pipeline stage progress events, DeepInfra `deepseek-ai/DeepSeek-V4-Flash-0731` and `Qwen/Qwen3-Embedding-0.6B` token/cost tracking in `token_usage.py`, Leiden `routing_slot` / HippoRAG hop counts, `quote_hash` verification outcomes, and G1–G5 Exit Gate KPI telemetry via `GET /api/v1/ai/research-chat/eval-summary`.

4. **Tests Covering Normal Path and Fail-Closed Paths**
   - **Normal Path Tests:** 4-branch HOSE input selection/inspection, dual-representation table unmerge/cross-page stitching/unit binding/hydration, deterministic Python Financial Calculator (`CalculatorTrace`), 3-layer zero-hardcode KG construction/querying, VN-Dot BM25 + `A1..A7` routing/retrieval, BFF SSE streaming + Prisma persistence, and frontend `/research-chat` build/lint.
   - **Fail-Closed Path Tests:**
     - Scan PDF failure raises fail-closed error instead of silent `MarkItDown` fallback; `NOTICE_ONLY` (`< 15p`) and `BAD_FONT` (`vn_readability_score < 0.72`) routing.
     - Arithmetic sanity gate failure (`> 0.5%` error or missing right-hand numeric column) marks `TABLE_UNVERIFIED` and blocks Python Calculator interpolation.
     - Strict Point-in-Time enforcement (`valid_from_ts <= as_of_ts` and `effective_from <= as_of_date < valid_to`) verified with `0%` future leakage across Qdrant, Leiden Tree, HippoRAG 2 hops, tables, and KG edges.
     - Post-synthesis `quote_hash` verifier flags/strips unverified claims (`UNVERIFIED_CLAIM`) and 3-layer abstention routes missing evidence to `declared_blindspots[]`.
     - Runtime boundary test `ai-engine/tests/test_sag_runtime_separation.py` remains 100% passing.

Any future change expanding SAG beyond this Phase 1 Stock Research Chat boundary (including Phase 2 dossier generation for Agent 04/05 or `/research`) requires updating this rule and re-documenting all 4 boundary items before implementation.
