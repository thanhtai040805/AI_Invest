# Rule 05: AIInvest Context

Use this file and the repository architecture map as orientation. Verify current
code, configuration, and project documentation before relying on a detail; the
repository may evolve faster than these notes.

## Architecture guide

- `ai-engine/app/domain/`: domain concepts, value objects, exceptions, business
  calculations, and repository contracts.
- `ai-engine/app/application/`: use cases, services, pipelines, handlers, and
  orchestration.
- `ai-engine/app/infrastructure/`: concrete repositories, sessions, Redis,
  queues, OCR, storage, and external connectors.
- `ai-engine/app/presentation/`: FastAPI, DTOs, validation, transport, and CLI.
- `ai-engine/app/core/`: configuration, telemetry, security, and connections.
- `ai-engine/app/adapters/`, `backtest/`, and `eval/`: adapters, simulation,
  and evaluation respectively.
- `SAG/`: financial evidence engine; `back-end/`: NestJS/Prisma; `front-end/`:
  Next.js.

Prefer the owning layer when the current code supports it. Avoid broad
reorganization to satisfy this guide during a localized task.

## Documentation

Use Rule 04's mapping as a navigation aid. Update only the references made
inaccurate by the change or required by a current project process.
