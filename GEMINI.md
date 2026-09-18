# AIInvest Master Engineering Rules & Global Workflow (Gemini Specification)

This file enforces the exact same rules defined in [AGENTS.md](file:///d:/AIInvest/AGENTS.md).

## Core Principles Recap:
1. **Ponytail Philosophy (Always Active)**: Follow the 7-Step Ladder: YAGNI -> Existing codebase -> Standard library -> Native platform -> Installed dependency -> One line -> Minimal code.
2. **Anti-Hallucination & Anti-Heuristic**: Always verify symbols, method signatures, database columns, and imports in actual files before writing code.
3. **Strict Folder Boundaries**:
   - `domain/`: Pure business logic & abstract interfaces. No SQL, no FastAPI, no external SDKs.
   - `application/`: Pipelines & use cases. No raw HTTP or inline SQL.
   - `infrastructure/`: Concrete database repositories, OCR, external clients.
   - `presentation/`: FastAPI routers and DTOs only. No business logic.
4. **Codebase Harness & Relationship Tracing**: Always trace upstream callers and downstream callees before refactoring. Fix root causes, not symptoms.
5. **Zero Waste**: No `.bak`, `.tmp`, or temporary scripts in code folders. No commented-out legacy code.
6. **Project Memory**: Refer to [PROJECT_MEMORY.md](file:///d:/AIInvest/docs/architecture/PROJECT_MEMORY.md) for architectural topology and data flows.
7. **Mandatory Documentation & Diagram Synchronization**: Any commit, major feature, or architectural change MUST atomically update corresponding documentation in `docs/architecture/` and diagrams in `docs/diagrams/` to prevent stale docs.
