# Factor ownership, financial mapping and replay decision audit

Status: isolated for review only. No merge, release, deployment, migration,
database repair, replay/reset or paid LLM run is requested by this PR.

## Problem and resulting behavior

Research previously read `factor_scores`, computed a policy-specific decision,
then UPSERTed that output back into the source table. Later LIVE/replay decisions
could consume prior Agent outputs. Financial field matching also confused equity,
funding and profit fields, and Research financial metrics were lost on the way to
Thesis. Naive learning timestamps could differ from HCM receipt times.

Producer jobs now retain source-write ownership; Agents only read factor inputs.
Research CSS/conviction stays in decision output/audit. New replay snapshots use
the original exact-date cache with the existing missing/neutral generic fallback,
and freeze the original RL query results, including order and repeated symbols.
The default does not rebuild F1–F6. Financial handoff and HCM timestamps are fixed;
Counter/CIO traces explain existing decisions without adding a new veto gate.

## Review and rollback boundaries

The PR starts from `main` at `da6b8fa`. Earlier work, unrelated diagrams/skills,
raw DB backups, API keys and full LLM logs are excluded. Commits are ordered by
dependency; reversing a foundation commit alone can break dependent commits.

| Group / commit | Classification and scope | Positive effect / evidence | Limits and review risk |
|---|---|---|---|
| 1 / `aa1e613` | Financial mapping hotfix: shared exact aliases, finite/zero handling, ETLs, Beneish and financial repository | Consistent equity, NI/CFO, EPS/BVPS and ratio inputs; CTG mapping-to-gate chain verified | Correct data changes decisions. Existing generic bank F2 is not validated as a bank-specific model. No automatic historical DB backfill is shipped. |
| 2 / `16db40f` | Producer input basis/provenance: shared mapping, ROE fraction unit, unavailable NIM, technical as-of lookup and bounded history queries | Removes mislabeled financial-revenue/assets NIM proxy and double ROE scaling; makes source basis inspectable | NIM unavailable and latest-known technical selection change inputs; these are explicitly not log-only. PROD global-rank/equal-component formulas remain; no sector/period benchmark redesign. |
| 3 / `44721ae` | Ownership/cache plus financial handoff: Research, repositories, RL and Thesis | Stops Agent feedback for both LIVE and replay; source hash stayed unchanged for 28 sessions. PE/PB/ROE/GPM/growth reach Thesis with units and provenance | The handoff and narrative prompt text can change LLM output. Removing writes changes future learning inputs. V2 historical loading remains guarded, not the default. Generic fallback and narrative inputs still use their existing DB readers. |
| 4 / `c479691` | Counter/CIO audit only | Captures prompt hash, parsed response, exact citation validation, quantitative gate path and prior suspensions; no missing explanation needs to be inferred | Does not enforce prior suspension clearance or certify semantic correctness of a fatal claim. Historical lookup lacks account/run identifiers in the existing ledger. |
| 5 / `63e4abe` | HCM accounting/reset hotfix | Naive paper timestamps match receipt wall time for entries/full and partial exits; pooled/direct connections agree; campaign state participates in explicit agent cleanup | Affects shared DB session timezone. Existing reset cleanup is broad; the PR does not invoke it and does not add `factor_scores` to reset. |
| 6 / `7acdc7e` | Replay flow/performance: serial candidates, conservative feasibility precheck, cached state, batched audit and peak checkpoints | Fewer redundant per-bar DB operations; preserves cash/holdings/receipts and candidate sequencing. Existing positions bypass the new-BUY precheck | Replay may skip LLM calls for infeasible orders, so this is not audit-only or proof of identical LLM state. Keep it separate from mapping/ownership. Latest full-universe LIVE fix from main is preserved. No increased concurrency. |
| 7 / `5bdda20` | Model/config change: DeepSeek V4.1 Flash 1017 and local DeepInfra examples/compose defaults | Isolates the exact route used by the measured replay; tests cover same-model retry recovery/exhaustion | Main already has read timeout 75s and one transport retry; this PR does not increase them. Model/default-route changes are operational/behavioral changes, not financial hotfixes. Credentials are not included. |

The final documentation/CI commit adds sanitized causal-control fixtures, expands
offline CI coverage, and records these boundaries. The seven functional groups
are not seven independently measured NAV improvements.

## Observed 28-session result

Window: 12 August–23 September 2026; LOCAL capital 1,000,000,000 VND;
maximum three qualified orders, serial processing; DeepSeek V4.1 via DeepInfra.
This evidence was collected before porting the reviewed diff onto current main.
The assembled PR is rechecked offline, not rerun against a paid provider.

| Same-day comparison, 23 September | NAV (VND) |
|---|---:|
| Cache/read-only run | 1,005,857,685 |
| Historical PROD | 1,004,416,888 |
| Mapping replay | 1,005,408,695 |
| Prior rebuilt-factor experiment | 997,410,205 |

The cache/read-only run is +1,440,797 VND versus historical PROD and +448,990 VND
versus mapping. These are favorable observations, not a certified strategy
improvement or an isolated causal effect of reverting CSS. Formula thresholds,
fatal gates, Kelly policy and concurrency are not redesigned by this work.

All 28 daily cash/receipt/NAV reconciliations had zero error. Runtime code, stored
RL weights and 4,008 source factor rows stayed unchanged; 90 paper rows matched
HCM receipts. Snapshot SHA256:
`730aa18a67bf4169ffcc1ff89cdfffe09a5be9680957f30758cc84d3ec6de581`.

Runtime: 54m44.47s; 130 successful LLM calls, zero transport retries, HTTP errors,
JSON errors or tracebacks. Eleven raw responses reached the output-length limit;
VND on August 24 used a template because required fields were missing. The run
therefore must not be described as having complete LLM output on every call.

## Causal findings and accounting attribution

| Ticker | Final marked contribution delta vs PROD (VND) | First divergence and subsequent path |
|---|---:|---|
| CTG | +339,340 | Missing Sunday cache invokes the original generic fallback. Correct D/E 0.9355→14.4971 changes F2 85.41→15 and CSS 72.52/B→58.44/C; no August 24 BUY300@32,100, so subsequent loss fragments disappear. |
| PVT | +790,090 | Factors/CSS match on September 7. Missing CTG loss samples change calibration, then Kelly and BUY quantity: 1,300→1,600 shares. Further feedback changes September 11 BUY900→1,400 and exit quantities. |
| HAG | +28,840 | A legacy cache F3 of62.12 instead of PROD41.96 opens the independent-signal gate on September 18. LOCAL buys800; PROD waits. Still held at the end: this contribution includes unrealized mark-to-market. |
| PVP | +282,527 | HAG consumes a qualified-order slot on September 18. LOCAL opens PVP later with different holdings/calibration; September 21 BUY1,400 versus PROD's incremental700, after PROD had bought600 on September 18. |
| Total | **+1,440,797** | Exact accounting sum; not four independent causal effects. |

Two offline controls are committed in
`ai-engine/tests/unit/test_replay_domino_controls.py`:

1. Hold the 29 actual LOCAL September7 returns fixed; add only the two missing
   CTG losses. Sample count29→31, with17 wins unchanged; calibrated p/R moves
   0.5556/1.63→0.5357/1.53, exactly matching the recorded PROD PVT calibration.
2. Hold PROD HAG CSS, quality, regime and all other factors fixed; change only
   F3 41.96→62.12. The actual Thesis gate changes2/3→3/3. This demonstrates a
   factor-input domino, independently of any change to the total CSS formula.

Later feedback loops and all model-text effects are not isolated by those two
controls. On August12 HPG/PNJ/VCI factors/CSS and final fills match despite
Counter/CIO differences; a changed trace does not necessarily change NAV.

## Residual limits

- Cache lookup behavior is restored, but pristine historical cache data is not.
  Prior replay outputs already exist in the frozen cache. Of matched Research
  rows,603 cache-sourced and246 generic-sourced records differ from PROD;
  148 Research gate outcomes differ. Seven LOCAL rows have no matching PROD row.
- Publication dates use a reporting-lag heuristic; ROE is reported-period,
  not certified annual/TTM; verified bank NIM and historical stock metadata
  remain unavailable. This is not historical/PIT certification.
- RL retains the original no-tie-break query. Paper confidence0.8 maps to tierA,
  and partial fragments count as samples under the existing policy. These are
  disclosed, not changed here.
- No SAG access, DB/schema migration, automatic data repair, additional paid
  replay, new timeout/token budget, merge or deployment is part of this PR.
- New HTTP-client reuse / further history-I/O optimization is not implemented;
  the replay performance group contains only changes already in the baseline.

Detailed local evidence remains under
`ai-engine/scratch/output/cache_readonly_replay_28_20261025/` (ignored):
`RESULT_ANALYSIS_VI.md`, `final_validation.json`,
`RL_calibration_actual_and_CTG_control.json`,
`HAG_20260918_F3_gate_control.json` and retained traces. Large backups, account
identifiers and raw prompts are intentionally not published in the PR.

## Validation of the assembled PR branch

- 132 offline tests passed, two seeded-DB Beneish cases deselected, three existing
  joblib/NumPy deprecation warnings. This includes the existing CI critical suite,
  replay/handoff/mapping/ownership/audit tests, model-route tests and both causal
  controls. No paid LLM call or trading replay was used to validate the PR branch.
- `python -m ruff check app tests/unit --select E9,F63,F7,F82`: passed.
- Both base and production-overlay `docker compose ... config -q`: passed;
  parsing configuration does not start containers or deploy anything.
- `git diff --check`: passed. Frontend/backend checks are left to PR CI; this
  change does not modify their implementation.
