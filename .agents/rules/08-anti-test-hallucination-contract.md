# Rule 08: Anti-"Test Hallucination" Contract & 4-Layer Testing Standard

**Status: MANDATORY REPO-WIDE ENGINEERING POLICY — Applies to `SAG/`, `back-end/`, `front-end/`, and `ai-engine/`.**

## 1. Why This Rule Exists ("Development / Test Hallucination")

Passing hundreds of isolated unit tests creates a dangerous **Development / Test Hallucination** whenever tests call private helpers in isolation or replace real component seams with hand-crafted mocks that diverge from production code.

Concrete production defects that slipped past 400+ green unit tests during the AIInvest SAG forensic audit:

1. **Unwired Dead-Code Illusion:** `kg_entity_linker`, `kg_macro_coupling`, `kg_true_peer`, and `SQLAlchemyKGStore.upsert_*` all passed isolated unit tests, yet both `run_research_ingest.py` and `POST /api/v1/research-chat/ingest-batch` passed `kg_refresher=None` — leaving `kg_nodes` and `kg_edges` completely empty in production.
2. **Phantom-Attribute / Mock-Drift Illusion:** `POST /api/v1/research-chat/ingest-batch` accessed `getattr(engine_manager, "qdrant_client", None)` and `getattr(engine_manager, "embedder", None)` — attributes that **do not exist** on the real `EngineManager` (which exposes async `get_search_unit_qdrant_client()` and `get_sag_embedding()`). Isolated mocks hid the bug completely.
3. **External-API & Settings Clobbering Illusion:** Unit tests called `MinerUOcrAdapter` directly with `language="vi"` (mocking the HTTP client), hiding both that MinerU Official API requires canonical `"latin"` (not `"vi"`) and that `settings_service.py` silently overwrote persisted `sag_language` back to `"en"`.
4. **Second-Callsite Bypass Illusion:** Gating `KnowledgeWorker.run_one()` without testing `KnowledgeWorker.discover()` left `_request_completed_trees()` still enqueueing `REBUILD_UNIVERSE_TREE` on the next poll cycle.
5. **Survivorship-Bias Vanity Metrics:** Stage 7 stripped rejected citations before populating `metadata.citations`, so Express BFF computed `G1 = 100%` over survivors only and defaulted `G3 = 1.0` when `feedbackCount == 0`.
6. **Config Drift Illusion:** `config.py`, `compose.yaml`, `docker-compose.yml`, and root/`.env.example` diverged on DB name (`sag` vs `aiinvest_sag`), model (`vlm` vs `pipeline`), timezone, and duplicate keys without any test noticing.

**Core Law:** *A unit test suite that only exercises isolated functions with hand-crafted stubs proves algorithm math — it NEVER proves the system works for a real user.*

---

## 2. Mandatory 4-Layer Testing Pyramid

Every feature, bug fix, or architectural change MUST cover the appropriate layers below before being declared complete:

### Layer 1 — Pure Unit Tests (Algorithms & Invariants Only)
- **Scope:** Pure functions (parsers, regexes, AST calculator formulas, PIT window math, SSE frame parsers).
- **Limit:** Necessary for fast regression feedback, **NEVER sufficient** to claim a feature is wired, reachable, or working end-to-end.

### Layer 2 — Unmocked Seam & Wiring Contract Tests (Mandatory for Every Feature)
Tests MUST exercise the **real seam** between components without mocking away the boundary under test:
1. **Real Production Class Contract:** Instantiate or introspect the **real production class** (`EngineManager`, `Settings`, `OfficialMinerUClient`, `KnowledgeWorker`, `ResearchChatOrchestrator`, `SagResearchChatService`). Never invent fake attribute names on `SimpleNamespace` / `MagicMock` that are not verified against the real class.
2. **Zero-Orphan Entrypoint Rule:** Every new domain/research engine MUST have a test reaching it through its **real production entrypoint** (FastAPI router via `httpx.AsyncClient(ASGITransport(app))`, Express router, or production CLI runner) and asserting real persistent side effects (DB rows written, HTTP/SSE payload emitted).
3. **Wire-Boundary-Only Mocking for External APIs:** For third-party HTTP services (MinerU, DeepInfra, Qdrant HTTP, Core API), intercept **only** at the transport boundary (`httpx.MockTransport` or injected `fetchImpl`), never mock the wrapper method itself — and assert the exact outbound URL, headers, and JSON body.
4. **Config & Env Parity Contract:** Automated contract tests MUST verify parity across `config.py` (`Settings()`), `compose.yaml`, `docker-compose.yml`, and every `.env.example` (no conflicting DB URLs, no duplicate keys, aligned models/flags/timezones).

### Layer 3 — User-Journey Vertical Slice E2E Tests (Mandatory Before Claiming Done)
Simulate what a **real user actually experiences** across real HTTP/ASGI/DB/SSE seams with the **exact headers and payloads** sent by the browser and Express BFF:
- **Happy-Path User Journeys:**
  - Ingest / Upload -> Canonical Blocks + Verified `table_card` + 3-Layer KG refresh -> `GET /tickers` -> `POST /research-chat/stream` -> SSE `stage`/`delta`/`done` -> Stage 7 verification -> BFF persistence & KPI accounting.
  - Multi-ticker, free-form (no locked ticker), `GENERAL` & `MOAT` modes, and full (>15-row) financial tables reaching LLM synthesis.
- **Adversarial & Fail-Closed User Journeys (Required on Every AI/RAG Feature):**
  - Invented numeric claim next to a valid `[C1]` badge -> `UNSUPPORTED_NUMERIC_CLAIM` blindspot + telemetry counter incremented.
  - Uncited financial figure -> `UNCITED_NUMERIC_CLAIM` blindspot.
  - Hallucinated inline citation `[C99]` -> `HALLUCINATED_CITATION_ID` + recorded in `rejected_citations` + penalizes Gate `G1` & `G3`.
  - Off-topic question on an ingested ticker WITH fallback tables & KG hops present -> `LOW_RELEVANCE_EVIDENCE` abstention.
  - Future-dated / revoked / superseded filing at `as_of_date` -> `0%` PIT leakage (`OUT_OF_PIT_WINDOW` / `NO_DOC_COVERAGE`).
  - Zero human feedback -> `G3` reports `null` (unmeasured), NEVER fake `100%` PASS.
  - Stream dropped before `done` -> UI flags `STREAM_INTERRUPTED_UNVERIFIED` instead of showing unverified text as final.

### Layer 4 — Real-Corpus & Live-Stack Verification Gate (Honest Disclosure)
- Offline ASGI/SQLite/MockTransport suites prove **code wiring, contracts, and fail-closed logic**, NOT live container health or external API credentials.
- For UI verification on running apps, ALWAYS use `agent-desktop` (OS Accessibility Tree) — never Playwright.
- **Mandatory Honest Disclosure in Every Completion Report:** Every report MUST explicitly separate:
  1. **Verified Offline (Unit + Unmocked Seam Contracts + ASGI User-Journey E2E + Typecheck/Lint/Build)**
  2. **Verified on Real Local Corpus** (when local PDF/data benchmarks were actually executed)
  3. **NOT Yet Verified Live** (e.g., live Docker stack, live Postgres/Qdrant, live MinerU/DeepInfra API keys, live browser `agent-desktop` walkthrough) — **never** imply offline tests proved live infrastructure!

---

## 3. Seven Banned Test Anti-Patterns

1. **Masking typos with `getattr(obj, "wrong_name", None)`:** Never use `getattr` with a `None` default to read a required collaborator attribute on an internal service; call the typed method/attribute directly so mismatches fail loudly in tests.
2. **Mocking the seam you claim to test:** Replacing `OfficialMinerUClient.parse` with a lambda does NOT test MinerU integration; replacing `refresh_research_knowledge_graph` with `AsyncMock` does NOT test KG wiring.
3. **Private-helper-only coverage:** Testing `_parse_foo()` while leaving the HTTP route or CLI entrypoint unwired (`refresher=None`) is forbidden.
4. **Testing only the happy path:** Every AI pipeline change MUST pair at least one positive user journey with at least one adversarial hallucination / abstention / PIT-leak / auth-rejection test.
5. **Survivorship-bias KPIs:** Never drop failures before computing denominators (`G1`, `G2`, `G3`) and never default unmeasured gates to `1.0` / `100%`.
6. **Silent `except Exception: pass` across core seams:** Swallowing import/wiring/runtime errors silently in production paths hides broken integrations behind green tests.
7. **Citing raw test count as proof of system readiness:** Saying `"432 tests passed"` without specifying which layers (L1/L2/L3/L4) ran is banned.
