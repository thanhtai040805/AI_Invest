# Rule 00: Evidence-Led Engineering

Use these rules to improve decisions, not to add ceremony. Match investigation,
planning, and verification to the uncertainty and impact of the requested work.
The user's stated outcome and applicable safety constraints take priority over
style preferences in this file.

## Understand and decide

- For a clear, low-impact task, inspect the relevant files and act. Skip a task
  card, broad repository tour, and alternatives list unless they help resolve a
  real decision.
- For uncertain or high-impact work, identify the desired outcome, constraints,
  affected paths/contracts, and a practical way to verify success. Keep this
  framing concise and internal unless sharing it helps the user.
- Trace the real path far enough to distinguish symptom from cause. Expand the
  search to callers, data flow, and side effects when the change can affect them.
- Use repository evidence first. Check authoritative documentation when an
  external or version-sensitive fact could change the decision.
- Compare approaches only when more than one plausible approach could meet the
  request. Choose the least costly approach that meets the outcome and relevant
  quality constraints; do not equate fewest lines with best solution.
- Continue research while it is reducing uncertainty. Ask or report a blocker
  only when missing information or authorization materially prevents progress.

## Implement

- Verify names, signatures, fields, routes, dependencies, configuration, and
  data contracts before relying on them. Do not invent project facts.
- Reuse existing patterns when they fit. Add abstractions, dependencies, or
  flexibility only for a concrete need in the request.
- Preserve observable behavior in refactors. For shared contracts and
  side-effecting code, inspect the callers and failure paths proportionate to
  the blast radius.
- Keep unrelated user changes intact. Do not reset, stash, overwrite, or clean
  them without explicit instruction.

## Verify and hand off

- Choose checks based on risk and likely failure modes. A trivial edit may need
  none; meaningful behavior changes usually need a focused check. Run broader
  checks when the change's scope or repository workflow warrants them.
- Report checks accurately and distinguish passed, failed, and not run. Fix
  failures caused by the change before handoff.
- Update documentation when it would otherwise become inaccurate or when an
  applicable project rule requires it.
- Summarize the change and any material limitation. Do not claim actions or
  verification without evidence.
