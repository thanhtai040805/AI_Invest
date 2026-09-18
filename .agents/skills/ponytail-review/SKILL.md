---
name: ponytail-review
description: >
  Audits git diffs, PRs, or existing files for over-engineering, unnecessary boilerplate,
  code bloat, premature abstractions, and violation of the Ponytail 7-step ladder.
  Pinpoints exact lines to delete or collapse to minimal senior-grade code.
license: MIT
---

# Ponytail Review — Over-Engineering & Bloat Auditor

Use this skill to audit recent changes or target files for over-engineering, code slop, duplicate helpers, and architectural bloat.

## Audit Checklist

When reviewing code, systematically check for:

1. **Premature Abstractions**:
   - Are there interfaces or abstract base classes with only 1 concrete class? -> Collapse into a single concrete class.
   - Are there factories, builders, or strategy objects where a simple function or dictionary lookup suffices? -> Simplify to direct function.
2. **Re-invented Wheels**:
   - Did the code write a custom helper that already exists elsewhere in the project? -> Replace with the existing import.
   - Did the code write custom parsing/formatting when standard library (`json`, `re`, `datetime`, `itertools`) or existing libraries already do it? -> Replace with stdlib/library.
3. **Ghost Code & Speculative Parameters**:
   - Are there unused function arguments, commented-out dead blocks, or `kwargs` that do nothing? -> Delete immediately.
4. **Symptom Band-Aids vs Root Cause**:
   - Did the author add `if x is None: return` in 4 places instead of ensuring `x` is validated at the boundary? -> Move validation to the root source.
5. **Folder & Architectural Integrity**:
   - Is business logic stuffed into controller/presentation routes?
   - Are DB queries hardcoded into domain models or UI components?
   - Are temporary files or test scripts lingering in production source trees?

## Output Format

For every finding:
```markdown
- **Location**: `path/to/file.py:L24-L35`
- **Issue**: [Over-engineering / Duplication / Misplaced Logic / Premature Abstraction]
- **Proposed Senior Fix**: [Delete lines 24-35 and replace with existing helper `get_stock_moat()` or stdlib `dict.get()`]
- **Net Diff**: -12 lines
```
