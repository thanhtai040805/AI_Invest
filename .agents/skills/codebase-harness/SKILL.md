---
name: codebase-harness
description: >
  Harness engineering skill for analyzing function call graphs, tracing relationships,
  grounding context to prevent hallucinations and heuristics, mapping blast radius before
  refactoring, and preventing legacy code buildup.
license: MIT
---

# Codebase Harness & Relationship Tracing

This skill provides a rigorous engineering harness for understanding source code, mapping function relationships, verifying facts to eliminate hallucinations, and safely refactoring without breaking callers.

---

## 1. Anti-Hallucination & Anti-Heuristic Protocol (Grounding)

AI assistants frequently suffer from two fatal errors:
1. **Hallucination**: Inventing function names, parameters, table columns, or imports that sound plausible but don't exist.
2. **Heuristic Bias**: Assuming a library or framework works the way it did in generic tutorials, without checking how it is configured in *this specific repo*.

### The 4 Grounding Gates (Mandatory Before Writing Code)
- **Gate 1: Verify Symbol Existence**: Before invoking any class or function, grep for its definition (`def symbol` or `class symbol`). Confirm the exact signature and argument names.
- **Gate 2: Verify Import Paths**: Check how existing files in the same module import that dependency. Never guess relative vs absolute imports.
- **Gate 3: Verify Schema & Models**: When dealing with database queries or API payloads, check the Pydantic schema, Prisma schema, or SQLAlchemy model directly. Never invent column names.
- **Gate 4: Verify Environment & Config**: Check `core/config.py`, `.env.example`, or configuration models before introducing or reading environment variables.

---

## 2. Function Call-Graph & Relationship Tracing Protocol

Before modifying, refactoring, or deleting any function:

### Step 1: Upstream Caller Search (Inbound References)
Identify **who calls this function** across the entire workspace:
```powershell
# Search for function usages across all modules
rg "\bfunction_name\b" --glob "!*.log" --glob "!node_modules/*" --glob "!.venv/*"
```
- Categorize callers:
  - Internal module callers (private callers within the same file).
  - External package callers (application services, API presentation layer, cron jobs).
  - Test suites (unit tests, integration benchmarks).

### Step 2: Downstream Callee Search (Outbound Dependencies)
Identify **what this function depends on**:
- What helper functions does it call?
- Does it make network I/O calls (HTTP client, SAG connector, MinerU OCR)?
- Does it execute DB transactions (SQLAlchemy session commit/rollback, Prisma transaction)?
- Does it alter state in Redis or global caches?

### Step 3: Blast Radius Assessment
Calculate the risk score before touching code:
- **Low Blast Radius**: Private function (`_helper_func`) called only within 1 file.
- **Medium Blast Radius**: Method of a repository called by 2–3 application services.
- **High Blast Radius**: Shared domain entity, core database session provider, or connector used across both `ai-engine` and `SAG`. Requires backward-compatible signatures or explicit caller migration.

---

## 3. Anti-Legacy & Refactoring Protocol

Legacy code is code that cannot be safely changed because:
- Its intent is forgotten.
- Its callers are unknown.
- It lacks automated test coverage.
- It accumulates dead, commented-out, or obsolete code paths.

### Rules for Safe Refactoring:
1. **The Boy Scout Rule**: Leave the file cleaner than you found it, but keep the diff strictly scoped to the task.
2. **Never Leave Dead Paths**: If a function is replaced, remove the old implementation or mark it with a clear deprecation warning. Do not leave commented-out blocks of code.
3. **Trace and Update All Callers**: If a signature changes, update every single upstream caller found in Step 1.
4. **Behavioral Parity**: For refactoring, ensure input/output contracts remain identical. Run existing tests to verify zero regressions.
