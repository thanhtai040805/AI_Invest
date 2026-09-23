# AIInvest Agent Bootstrap

This file is intentionally a loader, not a second rulebook.

## Rule Loading

Before starting any task in this repository:

1. Read every `*.md` file in `D:\AIInvest\.agents\rules\`, in filename order.
2. Treat `D:\AIInvest\.agents\rules\` as the canonical source for repo-wide engineering rules. Future rule changes belong there and do not require editing this file.
3. Read the most specific `AGENTS.md` files for the target path. For example, `front-end/AGENTS.md` applies to work under `front-end/`.
4. Load files under `D:\AIInvest\.agents\skills\` only when the task or user explicitly triggers the relevant skill; skills are not unconditional repository rules.

Normal instruction precedence still applies: system and developer instructions, followed by the user's direct request, take priority over repository guidance. More-specific scoped instructions may add constraints for their subtree.

Do not duplicate repository rules here. Update `.agents/rules/` instead.
