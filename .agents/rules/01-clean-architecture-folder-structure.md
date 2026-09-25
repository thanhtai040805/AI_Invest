# Rule 01: Repository Architecture Map

This map describes intended responsibilities in AIInvest. Use it to orient
changes, then verify the target area's actual structure and established
patterns. Do not move code or invent layers merely to make a change match this
map. If current code and this map disagree in a way that affects the task,
follow the working contracts and call out the documentation drift when useful.

## Repository overview

- `ai-engine/app/domain/`: domain concepts and business calculations.
- `ai-engine/app/application/`: use cases, workflows, and orchestration.
- `ai-engine/app/infrastructure/`: persistence and external system adapters.
- `ai-engine/app/presentation/`: API, CLI, and transport-facing code.
- `ai-engine/app/core/`: shared runtime concerns such as configuration,
  telemetry, security, and connection management.
- `ai-engine/app/adapters/`: format adapters and legacy bridges.
- `ai-engine/app/backtest/`: simulation and portfolio mathematics.
- `ai-engine/app/eval/`: model evaluation and benchmark metrics.
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
