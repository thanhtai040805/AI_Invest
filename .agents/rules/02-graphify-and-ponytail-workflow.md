# Rule 02: Graphify-First Discovery & Ponytail Simplicity Workflow

Use **Graphify** to locate exact modules, callers, and reusable helpers quickly with minimal token cost, then apply **Ponytail** (`D:\AIInvest\.agents\skills\ponytail\SKILL.md`) to implement the simplest correct change.

## 1. Phase 1 — Graphify-First Codebase Discovery (Before Broad Reading)

When starting any coding, debugging, or refactoring task (and `graphify-out/graph.json` exists):

1. **Query the graph before running broad `grep`/`rg` or opening large files:**
   - Architecture / feature flow: `graphify query "<question>"` (returns a scoped subgraph within token budget).
   - Trace coupling / blast radius: `graphify path "<SymbolA>" "<SymbolB>"`.
   - Inspect a symbol's callers, callees, and file line: `graphify explain "<Symbol>"`.
   - Broad system review only: read `graphify-out/GRAPH_REPORT.md`.
2. **Ground facts before editing (`codebase-harness`):**
   - Every Graphify edge is tagged `EXTRACTED` or `INFERRED`. Always open only the exact target line range (`read_file` with `StartLine`/`EndLine`) to verify exact signatures, imports, and Prisma/SQL columns before writing code.
3. **Keep the graph current (0 LLM cost):**
   - After modifying code files, run `graphify update .` (or rely on the installed `graphify hook install` post-commit/post-checkout hook).
   - Keep `SAG/` excluded in `.graphifyignore` while Rule 06 is active.

## 2. Phase 2 — Ponytail Simplicity Ladder (After Graph Grounding)

Once Graphify pinpoints the exact files and callers, apply the **Ponytail** preference before adding new code:

1. **YAGNI:** Build only what the current requirement needs; no speculative abstractions or dead configurability.
2. **Existing Codebase:** Reuse existing utilities, repositories, and patterns discovered via `graphify explain` / `graphify query` instead of duplicating logic.
3. **Standard Library -> Native Platform -> Installed Dependency:** Prefer built-in Python/Node/SQL/browser capabilities before introducing new packages.
4. **Minimal Clear Change:** Choose the simplest readable implementation at the right architectural boundary (Rule 01) without sacrificing validation, security, or data integrity.
