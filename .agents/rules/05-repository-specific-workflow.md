# Rule 05: Repository-Specific Workflow & Architecture Memory

## 1. Project Memory Gate

Before any complex feature or refactoring task, read `docs/architecture/PROJECT_MEMORY.md` to understand the current system landscape and inter-module contracts.

## 2. Detailed Layer Boundaries

- `ai-engine/app/domain/`: pure entities, value objects, exceptions, business calculations, and abstract repository contracts. No SQL, SQLAlchemy, FastAPI, HTTP, or external SDKs.
- `ai-engine/app/application/`: use cases, application services, pipelines, event handlers, and orchestration. No raw HTTP transport or direct SQL table definitions.
- `ai-engine/app/infrastructure/`: concrete repositories, database sessions, Redis, queues, OCR, storage, and external connectors. No portfolio allocation or other core business decisions.
- `ai-engine/app/presentation/`: FastAPI routers, endpoints, DTOs, validation, status codes, WebSockets, and CLI commands. No inline business logic, quant calculations, or direct database queries.
- `ai-engine/app/core/`: configuration, telemetry, security, and connection/session management. No domain logic or use cases.

Do not place one-off utilities, raw SQL, endpoints, business logic, or test output in the wrong layer. Reuse existing helpers or place new code in the layer that owns the concern.

The broader repository map is:

- `ai-engine/app/adapters/`: format adapters and legacy bridges.
- `ai-engine/app/backtest/`: quant simulation, backtest engine, and portfolio mathematics.
- `ai-engine/app/eval/`: model evaluations and benchmark metrics.
- `SAG/`: financial evidence engine for MOAT/GIL extraction and line citations.
- `back-end/`: NestJS and Prisma API services.
- `front-end/`: Next.js App Router UI.

## 3. Documentation Synchronization Details

When a change affects one of these areas, update the corresponding documentation and diagram atomically:

| Change | Documentation | Diagram |
|---|---|---|
| New service, port, protocol, network boundary, or gateway route | `docs/architecture/IT_SYSTEM_ARCHITECTURE.md` | Relevant file in `docs/diagrams/` |
| BCTC ingestion, MinerU OCR, SAG v2 hashing, GIL/MOAT rules, quant debate, or HOSE execution | `docs/architecture/PROJECT_MEMORY.md` | `docs/diagrams/paper-grade-algorithmic-data-flow.html` |
| Prisma/SQLAlchemy schema or migration | Persistence and data dictionary sections of `docs/architecture/IT_SYSTEM_ARCHITECTURE.md` | Relevant entity/store diagram |
| HOSE settlement, price bands, or risk model | `PROJECT_MEMORY.md` and `IT_SYSTEM_ARCHITECTURE.md` | Relevant risk-node annotations |

Any modified diagram must use orthogonal `r=8` lines, label masks with at least 6px margins, no more than two coral focal accents, and pass:

```powershell
python "C:\Users\This PC\.gemini\config\skills\diagram-design\scripts\self_check.py" <diagram.html>
```

Code that diverges from affected documentation or diagrams is incomplete.
