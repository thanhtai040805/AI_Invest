# Rule 04: Mandatory Documentation & Diagram Synchronization (Zero-Outdated Docs Policy)

## 1. Context & Objective
Architectural drift and outdated documentation ("stale docs") are severe technical debts that lead to agent hallucinations, broken assumptions, and integration failures.

Whenever an engineer or AI agent performs a **commit**, **major functional change**, or **architectural refactoring**, all corresponding documentation and diagrams **MUST be updated atomically in the same change**.

---

## 2. Synchronization Matrix

| Nature of Change | Impacted Code / Area | Required Documentation Update | Required Diagram Update |
|---|---|---|---|
| **System Architecture / Services** | New microservice, changed port, network boundary, protocol, or gateway routing | [IT_SYSTEM_ARCHITECTURE.md](file:///d:/AIInvest/docs/architecture/IT_SYSTEM_ARCHITECTURE.md) | Relevant existing file in `docs/diagrams/` |
| **Pipeline & Evidence Lifecycle** | BCTC ingestion, MinerU OCR, SAG v2 hashing, GIL/MOAT rules, quant debate, or HOSE execution | [PROJECT_MEMORY.md](file:///d:/AIInvest/docs/architecture/PROJECT_MEMORY.md) | [paper-grade-algorithmic-data-flow.html](file:///d:/AIInvest/docs/diagrams/paper-grade-algorithmic-data-flow.html) |
| **Database Models & Schemas** | Prisma schema migration, SQLAlchemy models, new tables or altered columns | [IT_SYSTEM_ARCHITECTURE.md](file:///d:/AIInvest/docs/architecture/IT_SYSTEM_ARCHITECTURE.md) (Persistence section) | Update entity/store references in relevant diagrams |
| **Domain Rules & Exchange Logic** | HOSE T+2.5 settlement, ±7% price bands, risk models | [PROJECT_MEMORY.md](file:///d:/AIInvest/docs/architecture/PROJECT_MEMORY.md) & [IT_SYSTEM_ARCHITECTURE.md](file:///d:/AIInvest/docs/architecture/IT_SYSTEM_ARCHITECTURE.md) | Update Risk Node annotations |

---

## 3. Pre-Commit / Pre-Completion Architecture Gate

Before finalizing any task or declaring completion:
1. **Did this change introduce, deprecate, or modify an architectural component?**
   - If YES, verify that `docs/architecture/` files reflect the change.
2. **Did this change modify any data flow or service relationship?**
   - If YES, update the corresponding HTML diagram in `docs/diagrams/`.
3. **Did all modified diagrams pass the `/diagram-design` self-check?**
   - Run: `python "C:\Users\This PC\.gemini\config\skills\diagram-design\scripts\self_check.py" <diagram_path>`
   - Result must be `OK`.
4. **Stale Docs Gate**:
   - A pull request or commit that updates code without updating out-of-sync documentation is strictly rejected.
