---
name: ponytail
description: >
  Optional guidance for keeping coding changes simple and maintainable. Use
  when requested or when avoiding over-engineering is relevant; do not use for
  non-coding requests.
license: MIT
---

# Ponytail: simplicity as a preference

Use simplicity to reduce accidental complexity after understanding the user's
goal, relevant code, and constraints. This is a preference, not a rigid process
or an output format. It never overrides explicit requirements, repository
contracts, safety, clarity, or engineering judgment.

## Applying the preference

- Do not build speculative work, but do complete what the user requested.
- Reuse existing patterns and prefer standard-library or native capabilities
  when they fit the problem.
- Choose the simplest clear solution that fully meets the requirement. Line
  count and file count are not goals; do not force one-line code.
- Avoid abstractions, dependencies, and configuration without a current need;
  use them when they make the required design clearer or safer.
- For bugs, inspect relevant callers and side effects when they affect the fix,
  then address the cause at the appropriate boundary.
- Keep validation, security, error handling, accessibility, and data integrity
  intact. Follow the applicable project verification guidance.
- Mention a simplification tradeoff only when it creates a material limitation.

## Intensity

- `lite`: favor simplicity when it is an obvious fit.
- `full`: actively consider avoidable complexity while preserving clarity and
  the complete requested behavior.
- `ultra`: scrutinize additions more aggressively, but do not reject or shrink
  explicitly requested scope.

The selected level controls the strength of this preference only. The user may
change or stop it at any time.

## Response

Be concise by default, but match the user's request. There is no mandatory
code-first order, line limit, or response template; provide a full explanation
or report when requested.
