# Rule 06: Temporary SAG Analysis Hold

**Status: ACTIVE until the repository owner explicitly releases it.**

SAG is temporarily in analysis/research mode. The current extraction and GIL
outputs must not be treated as a production-certified abnormal-capital-flow
detector.

While this hold is active, `ai-engine` may:

- prepare a proposal, impact analysis, tests, or a reversible prototype that
  does not connect to SAG production data;
- use local code/tests/metadata that do not contain or fetch SAG outputs.

`ai-engine` must not, without an explicit release and scoped instruction:

- connect to SAG for OCR, reprocess, extraction, embedding, activation, GIL,
  assessment, document status, Markdown, or any other read/write operation;
- upload, overwrite, delete, or replace SAG-owned canonical Markdown/R2
  artifacts;
- change SAG parser, GIL policy, document-selection scope, database schema, or
  runtime configuration as an incidental integration fix;
- assume a `PASS`, `CLEAR`, or `NO_ABNORMAL_FLOW_OBSERVED` result is proof that
  the issuer has no unstable or abnormal capital flow.

Any proposed change that touches a SAG boundary must first document:

1. the exact ai-engine caller and SAG endpoint/contract;
2. the source document evidence and expected output change;
3. state mutations, rollback, and observability impact;
4. tests covering both the normal path and a fail-closed path.

Until release, SAG changes and SAG-derived outputs are unavailable to ordinary
ai-engine workflows. A closed workflow must be reported as `SAG_CLOSED` or
deferred; it must not be relabeled as `DATA_INSUFFICIENT`, used to lower a
score, or treated as a negative GIL signal. This rule is temporary and must be
removed or explicitly marked released when the owner resumes implementation.
