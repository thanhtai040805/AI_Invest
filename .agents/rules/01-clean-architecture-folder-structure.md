# Rule 01: AIInvest Architecture Map

This map describes intended responsibilities in AIInvest. Use it to orient
changes, then verify the target area's actual structure and established
patterns. Do not move code or invent layers merely to make a change match this
map. If current code and this map disagree in a way that affects the task,
follow the working contracts and call out the documentation drift when useful.

## Repository overview

- `ai-engine/app/domain/`: domain concepts, value objects, exceptions, business
  calculations, and repository contracts.
- `ai-engine/app/application/`: use cases, services, pipelines, handlers, and
  orchestration.
- `ai-engine/app/infrastructure/`: concrete repositories, sessions, Redis,
  queues, OCR, storage, and external connectors.
- `ai-engine/app/presentation/`: FastAPI, DTOs, validation, transport, and CLI.
- `ai-engine/app/core/`: shared runtime concerns such as configuration,
  telemetry, security, and connection management.
- `ai-engine/app/adapters/`, `backtest/`, and `eval/`: adapters, simulation,
  and evaluation respectively.
- `SAG/`: financial evidence engine.
- `back-end/`: NestJS and Prisma services.
- `front-end/`: Next.js application.

## Responsibility guide

Keep business decisions independent of transport and storage where the existing
architecture supports that boundary. Keep HTTP handling, persistence, and
external integrations in their established owners. Endpoints should generally
validate/translate requests, invoke application behavior, and shape responses;
infrastructure should provide technical capabilities rather than quietly own
business policy.

These are design goals, not a reason to force a broad refactor into an unrelated
task. Follow the nearest working pattern unless evidence shows it is the source
of the problem or the requested change requires a boundary decision.
