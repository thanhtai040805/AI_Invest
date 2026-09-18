# Rule 01: Clean Architecture & Strict Folder Responsibilities

In AIInvest, every folder has a strict single responsibility. Code must NEVER be placed in a directory not architected for that concern.

---

## 1. Monorepo Layer Breakdown

```
AIInvest/
├── ai-engine/               # Autonomous Quant Organization & ML Pipeline
│   └── app/
│       ├── domain/          # PURE Domain layer (Entities, Value Objects, Abstract Repos)
│       ├── application/     # Application use-cases, pipelines, orchestrators
│       ├── infrastructure/  # DB (SQLAlchemy/Postgres), Redis, MinerU OCR, SAG Connector
│       ├── presentation/    # FastAPI routers, endpoint controllers, HTTP DTOs, CLI
│       ├── adapters/        # Format adapters, legacy bridges
│       ├── core/            # Config, telemetry, security, database session managers
│       ├── backtest/        # Quant simulation, backtest engine, portfolio math
│       └── eval/            # Model evaluations and benchmark metrics
├── SAG/                     # Financial Evidence Engine (MOAT/GIL extraction, line citations)
├── back-end/                # NestJS / Prisma API services
└── front-end/               # Next.js 15+ App Router UI
```

---

## 2. Directory Responsibility Boundaries

### Layer 1: `ai-engine/app/domain/` (The Core)
- **ALLOWED**: Pure business models, domain entities, value objects, domain exceptions, abstract repository interfaces (Protocols/ABCs), business calculation functions.
- **STRICTLY FORBIDDEN**:
  - NO SQL queries, SQLAlchemy session calls, or raw database drivers.
  - NO FastAPI routers, HTTP request/response schemas, or headers.
  - NO external API calls (e.g. MinIO, MinerU, Telegram, OpenAI/Gemini SDK directly).
  - NO helper functions that belong to application or infrastructure.

### Layer 2: `ai-engine/app/application/` (Orchestration & Use Cases)
- **ALLOWED**: Application services, multi-step business pipelines (e.g., `bctc_to_sag_pipeline`), event handlers, quant workflow orchestrators.
- **STRICTLY FORBIDDEN**:
  - NO raw HTTP transport details (status codes, JSON serialization).
  - NO direct SQL table definitions.
  - Interacts with domain entities and repository interfaces, not raw database tables.

### Layer 3: `ai-engine/app/infrastructure/` (Technical Details & External Adapters)
- **ALLOWED**: Concrete database repositories (PostgreSQL implementations of domain repository interfaces), Redis cache providers, Celery/Kafka queue workers, OCR clients (MinerU), SAG connector client, MinIO/S3 file storage.
- **STRICTLY FORBIDDEN**:
  - NO business logic decisions (e.g. buy/sell logic, portfolio risk weights). Infrastructure only provides data and execution capabilities.

### Layer 4: `ai-engine/app/presentation/` (Presentation & Transport)
- **ALLOWED**: FastAPI router files, APIRouter endpoints, Pydantic Request/Response DTOs, query param validation, status codes, WebSocket handlers.
- **STRICTLY FORBIDDEN**:
  - NO business logic or quant calculations written inline inside endpoints.
  - Endpoints must only parse requests, invoke application services, and return DTOs.

---

## 3. Placement Violation Examples & Corrections

| Violation (Anti-Pattern) | Why It Is Wrong | Correct Location |
|---|---|---|
| Putting a FastAPI `@router.post` endpoint inside `domain/repositories/` | Repositories manage persistence, not HTTP requests. | `app/presentation/api/` or `app/presentation/routers/` |
| Writing raw SQL queries inside `domain/models/` | Domain models must be pure and decoupled from storage. | `app/infrastructure/repositories/` |
| Putting an ad-hoc math helper in a new random file `app/my_utils.py` | Pollutes root directory with untracked utility files. | Reuse or place in `app/core/` or `app/domain/services/` |
| Storing test output or dump files in `app/domain/` | Clutters production source tree. | Designated `.data/`, `experiments/`, or `scratch/` |
