# AIInvest Project Memory & Architecture Harness
> **Version**: 1.0.0 (Living Architecture Memory)
> **Last Updated**: 2026-09-17
> **Purpose**: Source of truth for all AI agents and developers working on AIInvest. Eliminates hallucinations, establishes system boundaries, and documents core data pipelines.

---

## 1. System Ecosystem Overview

AIInvest is an autonomous investment and financial forensics organization engineered specifically for the HOSE (Ho Chi Minh Stock Exchange) market:

```
[BCTC PDF / Financial Data / OHLCV / News]
                   │
                   ▼
       ┌──────────────────────┐
       │   MinerU / OCR Engine│
       └──────────┬───────────┘
                  │ Markdown & Structured Tables
                  ▼
       ┌──────────────────────┐
       │       SAG v2         │ ──▶ Line-by-line evidence, quote_hash,
       │   Evidence Engine    │     MOAT (competitive advantage) &
       └──────────┬───────────┘     GIL (fraud/governance risk flags)
                  │
                  │ sag_connector.py (Typed Client)
                  ▼
       ┌──────────────────────┐
       │      ai-engine       │ ──▶ 12 Autonomous Quant Agents
       │   Quant Organization │     Clean Architecture: Domain, App, Infra
       └──────────┬───────────┘     Dual-book risk execution (T+2.5 HOSE rules)
                  │
                  ▼
       ┌──────────────────────┐
       │      back-end        │ ──▶ Express + Prisma ORM + PostgreSQL
       │   Core API Gateway   │     User auth, portfolios, alerts, order state
       └──────────┬───────────┘
                  │
                  ▼
       ┌──────────────────────┐
       │      front-end       │ ──▶ Next.js 15+ App Router, Tailwind CSS,
       │   Interactive UI     │     3D Evidence Graph, Trading view, Chat
       └──────────────────────┘
```

---

## 2. Monorepo Directory & Architectural Responsibilities

### `ai-engine/` (Python 3.11+, Clean Architecture)
- **`app/domain/`**:
  - **Core responsibility**: Business entities, value objects, domain logic, and abstract repository protocols (e.g. `bctc_pipeline_repository.py`).
  - **Rule**: Absolutely ZERO imports of database ORMs (SQLAlchemy), HTTP frameworks (FastAPI), or external SDKs.
- **`app/application/`**:
  - **Core responsibility**: Use-case orchestration, pipelines (e.g. `bctc_to_sag_pipeline.py`), multi-agent coordination.
  - **Rule**: Coordinates domain interfaces with infrastructure services without exposing HTTP transport concerns.
- **`app/infrastructure/`**:
  - **Core responsibility**: Concrete repository implementations (SQLAlchemy repositories), database engine sessions, Redis cache, SAG connector (`sag_connector.py`), external APIs.
  - **Rule**: Implements domain interfaces; contains all I/O details.
- **`app/presentation/`**:
  - **Core responsibility**: FastAPI routers, request/response DTOs, endpoint controllers.
  - **Rule**: Thin layer. Parses incoming requests, delegates to application services, serializes responses.
- **`app/core/`**:
  - **Core responsibility**: Application settings (`config.py`), structured logging, security tokens, DB connection pools.
- **`app/backtest/` & `app/eval/`**:
  - **Core responsibility**: Walk-forward backtesting, Sharpe/Sortino/Drawdown evaluations, statistical validation.

### `SAG/` (Financial Evidence Engine)
- **Core responsibility**: Ingests company reports, extracts facts with exact line numbers, hashes quotes (`quote_hash`), and flags forensic accounting anomalies (`GIL`). MOAT is retired; embeddings are optional and currently disabled.

### `back-end/` (Node.js / TypeScript)
- **Core responsibility**: Express backend service, Prisma ORM, user accounts, authorization, portfolio tracking, alert dispatch.

### `front-end/` (React / TypeScript)
- **Core responsibility**: Next.js 15+ App Router, UI components, chart visualizations, streaming agent reasoning, evidence inspector.

---

## 3. Key Relationships & Call Paths

### The BCTC-to-SAG Ingestion Pipeline:
1. **Trigger**: New BCTC PDF detected or uploaded via endpoint in `app/presentation/routers/` or scheduled batch job.
2. **OCR Parsing**: PDF passed to OCR processor (MinerU), converting document into Markdown with preserved line anchors. The clean phase removes OCR boilerplate and long English mirror paragraphs interleaved with Vietnamese while preserving tables and short proper names. `OCR_ONLY` is a local/manual boundary; production promotes the result to `FULL` automatically.
3. **Evidence Extraction**: SAG builds the document tree, normalizes deterministic statement-table figures to VND when the table declares million/thousand units, then persists those facts plus grounded semantic entities, relations, observations and GIL disclosures.
4. **GIL Analysis**: Current GIL decisions use the active extracted document set; all completed Historical documents feed temporal deterioration analysis without being summed into the current exposure. Denominator ratios are period-aligned and use only classified relation flow kinds: service revenue/revenue, capital allocation/assets, related-party receivables/total receivables, payables/payables, loan balances/(cash + equity), and guarantees/equity. A balance numerator is restricted to issuer-outgoing relations, explicit balance flow kinds, the same accounting scope, and the same accounting universe as its denominator. Subset-to-total ratios above 1.0 emit `RATIO_INVARIANT_VIOLATION` and are excluded from interpretation. Sparse disclosures use explicit `DISCLOSED`, `CONFIRMED_NONE`, `NOT_DISCLOSED`, `UNRESOLVED`, or `NOT_APPLICABLE` states; missing evidence is never coerced to zero. Flow risk reports unknown sparse categories as null with status and computes `OBSERVED_EVIDENCE_ONLY` scores plus evidence coverage. Receivable concentration is reported separately from receivable-to-equity materiality. Aggregate evidence fields are separate from current balance fields; observed evidence amounts are provenance metrics and never denominator inputs. Temporal comparison is semantic: same issuer, accounting scope, metric meaning, measurement type, and compatible unit; balance snapshots can compare Annual and Latest Quarter, while flows require period normalization. Two comparable points are reported as observed change with low trend confidence. Relationship resolution counts all classified categories, with external affiliate kept separate from unresolved. External capital outflow is distinct from potential leakage and tunneling evidence. Insider observations expose explicit transaction amounts separately from ownership percentage value proxies; ownership is never treated as a transaction amount. Materiality is reported as economic and risk-adjusted materiality, with holding-company dividends contextualized. Risk severity is separate from review status and data confidence; missing required latest-quarter denominators mark data quality as `PARTIAL`.
5. **Persistence**: SAG stores canonical Markdown in R2, metadata/evidence in PostgreSQL, and uses local Markdown only as an evictable cache. Embeddings are optional and do not gate READY while disabled.
6. **Downstream Quant Signals**: `ai-engine` risk agent checks GIL flags before allocating capital in the dual-book portfolio.

---

## 4. Anti-Hallucination Verified Rules & Conventions

- **Database Access**: Always use repository pattern in `app/infrastructure/repositories/`. Never write ad-hoc SQL queries in application services.
- **Async Execution**: I/O operations in `ai-engine` are asynchronous (`async/await`). Ensure database sessions and HTTP client calls (`httpx` or `aiohttp`) are awaited properly.
- **Environment Variables**: Defined in `ai-engine/app/core/config.py` using Pydantic Settings. Do not access `os.environ` directly in domain or application code.
- **Zero Garbage Files**: No `.bak`, `.tmp`, or scratch scripts committed to production folders.

---

## 5. Living Scientific Architecture Diagrams Hub (`docs/diagrams/`)

- [full-12-agents-sovereign-architecture.html](file:///d:/AIInvest/docs/diagrams/full-12-agents-sovereign-architecture.html): Full 12-Agent Sovereign Quantitative Organization (IOS v5.1) featuring Separation of Powers (Tam Giác Quyền Lực: Compliance, Audit, Change), Sticky HMM, CSAD Herding, Layer-0 Beneish Gate, F1-F6 CSS, 3-Tier Counter-Thesis CTS, Game-Theoretic CIO Arbitration, Quarter Kelly Sizing, 5-Layer Pre-Trade Risk Gate, EAE VWAP Execution, 6-Tier Stop Loss T0-T5, and SHA-256 Hash Chained Ledger.
- [unified-quant-algorithms-blueprint.html](file:///d:/AIInvest/docs/diagrams/unified-quant-algorithms-blueprint.html): Master Unified Mathematical & Quantitative Architecture connecting all 6 algorithmic pillars (FFD $w_k$, Lead Shock Shift(1,2), Gram-Schmidt $u_k \perp u_j$, Adaptive Beneish $M_8/M_5$, Hard Laws Polytope $\mathcal{P}_{\text{HOSE}}$, Quarter Kelly Sizing).
- [paper-grade-algorithmic-data-flow.html](file:///d:/AIInvest/docs/diagrams/paper-grade-algorithmic-data-flow.html): End-to-end data pipeline from raw BCTC ingestion, SHA-256 quote hash, Milvus 1024-d embedding to Queue Accumulator $32 \to 24 \to 8$.
- [gil-decision-tree-gating.html](file:///d:/AIInvest/docs/diagrams/gil-decision-tree-gating.html): Temporal Corporate Graph (TCG) multi-layer graph ($G_{\text{own}}, G_{\text{gov}}, G_{\text{flow}}$) and 4-gate GIL decision tree.
- [SCIENTIFIC_ALGORITHMS_PROMPT_CATALOG.md](file:///d:/AIInvest/docs/diagrams/SCIENTIFIC_ALGORITHMS_PROMPT_CATALOG.md): Complete prompt catalog for reproducing publication-grade diagrams using `/scientific-diagram-prompt-crafter`.

## 2026-09-19 — System integrity hardening

- Internal admin, ingestion and bot routes now require service tokens; SAG production rejects debug bypasses, weak service tokens and default signing secrets.
- Portfolio cash, positions, orders, executions and account-scoped paper trades commit atomically. Missing users, insufficient cash/shares, T+2.5 locks and unavailable governance fail closed.
- Prisma migration history now reconstructs the full checked-in schema; DB constraints enforce unique positions and exactly one reaction target.
- Daily pipeline runs use PostgreSQL advisory locks and persisted job state. Market workers use Asia/Ho_Chi_Minh and the VN trading calendar.
- Backtests use canonical market data, next-session fills, fees, cash limits and real equity/trade metrics. Unsupported strategies and unsafe run identifiers are rejected.
- Runtime topology includes separate Core PostgreSQL and SAG pgvector PostgreSQL services. AI Engine connects to Core and SAG with explicit credentials.
