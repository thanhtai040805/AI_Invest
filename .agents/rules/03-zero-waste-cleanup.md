# Rule 03: Keep Task Changes Clean

Keep temporary work and generated output out of production code. Use the
repository's existing scratch, experiment, or ignored-data location when one
fits; do not create a new convention for a one-off task.

Before handoff:

- Remove temporary files created for this task when they are no longer needed.
- Do not delete, rewrite, or clean files that predate the task or contain user
  work.
- Remove debug output, dead code, and unused imports introduced by the change.
- Check the final diff/status for accidental artifacts. Run a linter only when
  it is a relevant project check, not as a universal ritual.
