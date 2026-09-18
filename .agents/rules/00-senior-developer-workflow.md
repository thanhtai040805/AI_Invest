# Rule 00: Senior Developer Workflow & Anti-Hallucination Protocol

## 1. Senior Developer Mindset
A Senior Developer does not write code to show off knowledge or generate volume. A Senior Developer:
- **Questions the premise first (YAGNI)**: Solves the actual problem with the minimal necessary change.
- **Thinks in systems**: Understands how one function impacts the entire pipeline, database, cache, and operational costs.
- **Validates before coding**: Never guesses. Reads the codebase, inspects real files, and validates facts.
- **Fixes root causes**: Refuses to put shallow `try...catch` or `None` checks around broken underlying contracts.

---

## 2. Ponytail Integration (Always Active)
Every coding response must strictly evaluate the **7-Step Decision Ladder**:
1. **Does this need to exist at all?** (Skip speculative features / unneeded abstractions).
2. **Already in this codebase?** (Reuse existing helpers, classes, types, models).
3. **Does the standard library provide it?** (Prefer standard library over custom wheels).
4. **Does a native platform feature cover it?** (Use database constraints, HTML/CSS, OS features).
5. **Does an installed dependency already solve it?** (Never install a new package for 10 lines of code).
6. **Can it be one line?** (Keep it concise, clear, and readable).
7. **Only then: Write the minimum necessary new code.**

---

## 3. Strict Anti-Hallucination & Anti-Heuristic Directives

### Directives:
1. **No Guessed Functions or Signatures**:
   - You MUST NOT guess whether a class has a method `find_by_id`, `get_by_id`, or `query_one`.
   - You MUST run a grep or view the file defining the class to confirm the exact method name and parameter types.
2. **No Fabricated Database Columns**:
   - Verify column names in the ORM model (SQLAlchemy/Prisma) or migration files before writing queries.
3. **No Phantom Libraries**:
   - Never import a third-party package without verifying it exists in `pyproject.toml`, `requirements.txt`, or `package.json`.
4. **No Heuristic Assumptions**:
   - Do not assume: "In framework X, configuration is usually in `config.json`." -> Check where configuration is *actually* loaded in this repository (`ai-engine/app/core/config.py` or `.env`).
   - Do not assume: "This repository uses Pydantic v1 syntax (`.dict()`)" -> Check whether it uses Pydantic v2 (`.model_dump()`).
