# Rule 03: Zero-Waste & Garbage Prevention Policy

## 1. Zero Garbage Files Policy
AI agents often leave debris behind when experimenting or debugging. This clutters git history, creates confusion for other developers, and leads to accidental deployment of temporary scripts.

### Strictly Forbidden Files in Production Directories:
- **NO Temporary Scripts**: `test_temp.py`, `check.py`, `script2.py`, `scratch.py` inside `app/`, `src/`, or `domain/`.
- **NO Backup Duplicates**: `service_copy.py`, `model.py.bak`, `router_old.py`.
- **NO Dump Files**: `test_output.json`, `debug.log`, `result.txt` scattered in code folders.
- **NO Commented-out Dead Codeblocks**: Stacks of commented-out code that serve no runtime purpose.

---

## 2. Permitted Scratch & Test Locations
If temporary data or exploratory scripts are strictly required during development:
1. **Agent Scratch Directory**: `<appDataDir>\brain\<conversation-id>\scratch\` (Persisted for agent lifecycle).
2. **Dedicated Project Experiments Folder**:
   - `ai-engine/experiments/` (for quant / ML sandbox work).
   - `.data/temp/` (gitignored local cache).
3. **Always Clean Up**:
   - Delete temporary files before completing the task.
   - Run `git status` mentally or via CLI to verify that only intentional, production-ready files are modified or added.

---

## 3. Pre-Completion Hygiene Checklist
Before marking any task as complete, verify:
- [ ] No unneeded files created in source trees.
- [ ] No unused imports (`ruff check` or linter clean).
- [ ] No debug `print()` statements left in production code (use structured logger).
- [ ] All modified files follow proper formatting and lint rules.
- [ ] Existing comments and docstrings unrelated to changes are preserved.
