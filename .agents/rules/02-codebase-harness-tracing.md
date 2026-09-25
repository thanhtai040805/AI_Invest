# Rule 02: Trace the Affected Code Path

Trace enough of the code to understand the change's blast radius. The depth of
investigation depends on whether a local implementation detail or a shared
contract/side effect is changing.

## When changing a shared function or contract

- Search for direct callers and relevant indirect references such as interfaces,
  dependency injection, event registrations, and tests.
- Check how affected callers use the contract, including optional arguments and
  failure behavior.
- Update in-scope call sites together when a signature or behavior changes.
- Inspect state changes and external effects when the function can write data,
  publish events, call services, or affect caches.

Use targeted symbol search (for example, `rg`) and follow references far enough
to answer the specific compatibility question. Do not classify every textual
match when generated files, unrelated names, or a narrower search make that
unnecessary.

## Refactoring

Preserve observable behavior unless the request explicitly changes it. Remove
obsolete code and imports that the refactor makes dead. Add or update a focused
check when it gives useful confidence, especially if there is no existing
coverage for a meaningful behavior change.
