# Rule 02: Codebase Harness & Function Relationship Tracing

## 1. The Harness Philosophy
A codebase becomes "legacy" when developers are afraid to touch it because they cannot predict what will break.
A Senior Developer builds a **mental and tooling harness** before changing existing code:
1. Know the incoming callers (Inbound dependencies).
2. Know the outgoing callees (Outbound dependencies).
3. Know the state mutations and side effects (Transactions, Cache, External systems).

---

## 2. The 4-Step Caller Discovery Protocol

Before modifying or refactoring ANY existing function, method, or class signature:

### Step 1: Exact Symbol Search
Run ripgrep with word boundary to locate all invocations across the monorepo:
```powershell
rg "\bexact_function_name\b" --glob "!*.log" --glob "!.git/*" --glob "!node_modules/*"
```

### Step 2: Caller Classification
Classify every match into one of three buckets:
1. **Direct Callers**: Application workflows, API routes, scheduled tasks.
2. **Indirect/Polymorphic Callers**: Abstract interfaces, dependency injection containers, event dispatcher subscriptions.
3. **Tests & Benchmarks**: Pytest cases, backtest evaluation runs.

### Step 3: Contract Verification
- Inspect the parameters passed by callers. Are there optional arguments? Keyword-only arguments? Default values?
- If refactoring: Will changing this function break any caller?
- **Golden Rule**: If a signature must change, update ALL callers within the same atomic commit. Never leave broken call sites.

### Step 4: Regression Prevention Check
- Check if existing unit/integration tests cover this function.
- If no tests exist, verify manually or write a targeted unit test before refactoring to guarantee behavioral equivalence.

---

## 3. Safe Refactoring Workflow (No Legacy Buildup)

1. **Read & Understand First**: Never refactor code you do not fully comprehend. Read the comments, understand the edge cases.
2. **Preserve External Behavior**: Pure refactoring modifies structure, NOT observable behavior. Keep the public contract stable.
3. **Eliminate Dead Code**: When replacing an old routine:
   - Do NOT leave commented-out blocks of old code (`# old implementation...`). Git tracks history. Delete dead code cleanly.
   - Do NOT leave unused helper functions that only the old routine called.
   - Do NOT leave unused imports at the top of the file.
4. **Zero Lingering TODOs**: Either implement the required behavior or do not add a misleading stub.
