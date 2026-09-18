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
       │      back-end        │ ──▶ NestJS + Prisma ORM + PostgreSQL
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
- **Core responsibility**: Ingests company reports, extracts facts with exact line numbers, hashes quotes (`quote_hash`), scores economic moats (`MOAT`), and flags forensic accounting anomalies (`GIL`).

### `back-end/` (Node.js / TypeScript)
- **Core responsibility**: NestJS backend service, Prisma ORM, user accounts, authorization, portfolio tracking, alert dispatch.

### `front-end/` (React / TypeScript)
- **Core responsibility**: Next.js 15+ App Router, UI components, chart visualizations, streaming agent reasoning, evidence inspector.

---

## 3. Key Relationships & Call Paths

### The BCTC-to-SAG Ingestion Pipeline:
1. **Trigger**: New BCTC PDF detected or uploaded via endpoint in `app/presentation/routers/` or scheduled batch job.
2. **OCR Parsing**: PDF passed to OCR processor (MinerU), converting document into Markdown with preserved line anchors.
3. **Evidence Extraction**: `bctc_to_sag_pipeline.py` calls `sag_connector.py` to ingest markdown into SAG.
4. **Moat & GIL Analysis**: SAG processes facts, emits Moat signals and GIL risk indicators.
5. **Persistence**: `bctc_pipeline_repository.py` (implemented in `app/infrastructure/repositories/`) saves status, hashes, and flags to PostgreSQL.
6. **Downstream Quant Signals**: `ai-engine` risk agent checks GIL flags before allocating capital in the dual-book portfolio.

---

## 4. Anti-Hallucination Verified Rules & Conventions

- **Database Access**: Always use repository pattern in `app/infrastructure/repositories/`. Never write ad-hoc SQL queries in application services.
- **Async Execution**: I/O operations in `ai-engine` are asynchronous (`async/await`). Ensure database sessions and HTTP client calls (`httpx` or `aiohttp`) are awaited properly.
- **Environment Variables**: Defined in `ai-engine/app/core/config.py` using Pydantic Settings. Do not access `os.environ` directly in domain or application code.
- **Zero Garbage Files**: No `.bak`, `.tmp`, or scratch scripts committed to production folders.
