# Financial backfill

Full provider backfill stages the selected symbols in empty temporary statement
and ratio tables. It uses AlphaStock first, CafeF for sparse or missing statements,
then VCI for missing latest-quarter reports. Source precedence is applied only
within the new batch, independently of previously stored financial rows.
No history schema, revision journal, triggers or migration is required.

Run from `ai-engine/` with `DATABASE_URL` explicitly set to the intended database.
Hold competing financial and factor writers while repairing that database.
A successful financial reset invalidates all cached factor dates for the selected
symbols in the same transaction. It returns `factor_scores_invalidated`; failure
rolls back all three tables. It does not run Replay or regenerate factor scores.
Do not resume consumers until the intended factor date range is rebuilt and
validated; a financial-only success is not a three-table readiness certificate.

## Re-crawl historical source data

Use the existing full financial ETL entry point:

```powershell
python -c "import json; from app.infrastructure.data_pipelines.financial_etl_alphastock import refresh_all; r=refresh_all(); print(json.dumps(r)); raise SystemExit(1 if r['errors'] or r['vci_fallback']['errors'] else 0)"
```

The job validates BS/IS/derived-ratio coverage for every selected symbol, rejects
future or out-of-scope staged rows, then replaces all stored statement/ratio rows
for those symbols in one transaction. Old rows omitted by the current response
are removed, including rows older than the provider fetch window. Other symbols
are preserved. No mid-job commit is performed; source or validation failures
roll back the entire batch. An empty symbol universe is an error.

`refresh_incremental(backfill=True)` delegates to this full reset. Ordinary
incremental refresh retains its existing bounded update behavior. Standalone
CafeF full refresh uses the same staging/publication policy for its selected
symbols. Provider backfill rejects unavailable/malformed required responses
rather than treating them as successful empty source data.

AlphaStock publication dates come from `published_at`; absence remains NULL.
Observation time belongs in `fetched_at`. This does not independently certify
publication vintage: readers still contain a reporting-lag heuristic, and the
provider can omit publication, currency or consolidation metadata.

## Repair mappings using statements already stored in the target

This alternative does not fetch provider data. It is useful when the source
statements are present and only legacy aliases/derived ratios need repair:

```powershell
python scripts/backfill_financials.py --database TARGET_DB --output scratch/output/financial_preview
python scripts/backfill_financials.py --database TARGET_DB --apply --review scratch/output/financial_preview/manifest.json --output scratch/output/financial_apply
```

Each output directory must be new. The default is read-only preview. Review
statement changes, ratio field changes and transitions to/from NULL in the
manifest and `changes.jsonl.gz`. Apply recalculates the plan from the target,
binds it to the preview's database identity, input and source-code hashes, checks
before-values, and verifies that recalculation produces no remaining changes
before committing. Stale reviews or pre-commit failures roll back the transaction.
There is no LOCAL reference database, snapshot import or historical patch input.

The repair normalizes aliases and recomputes the twelve ratio columns with the
same calculator as ETL. Existing issuer overrides remain in their established
owners and still require source evidence. For the observed AlphaStock and CafeF rate
schemas, reported-period statement-derived profitability takes precedence when
available; vendor percentage-point rates are converted to fractions as fallback.
Modern AlphaStock `rt_prt_*` rates are already fractions. Unknown profitability
unit schemas fail rather than guessing. This is not a new annualized/TTM ratio
contract, and the job does not infer those bases from NAV. Amount-magnitude
annualization has been removed. Derived annual FCF yield/EV-EBITDA uses annual
flows; quarterly fallback remains NULL without a verified TTM basis. Reported
EV/EBITDA is preserved. Missing cash/investments are not assumed to be zero and
total liabilities are not substituted for interest-bearing debt in EV. YoY
requires the matching period one year earlier, not any older available year.

Statement source/publication metadata is preserved for mapping-only repairs.
Ratios without sufficient stored BS/IS inputs are retained and counted. This
command does not insert missing ratio rows or reset accounts. Applying changes
invalidates the affected symbols' cache in the same transaction; the preview
reports that symbol scope. VN peer-rank caches are also invalidated because one
issuer's financial change can change other issuers' cross-sectional ranks.

## Baseline factor contract and release limits

The reference remains `cache_readonly_replay_28_20261025`, NAV 1,005,857,685 VND.
Its observed Research input uses 1,570 cached and 414 dynamic symbol/date vectors.
Do not replace this behavior with a uniform producer or change F4/F6 definitions
as an incidental data fix. The current LOCAL candidate leaves FactorService and
Research score formulas unchanged. A restored-cache comparison is a control,
not proof that a fresh crawl/recalculation reproduces that cache.

A read-only input-date trace found 1,165 cached observed vectors with all five
generic market raw metrics present. All 1,165 match an earlier as-of date, not
their cache date (three market factors plus five raw metrics must all match).
Another 405 cached vectors lack that complete trace and are not classified by
this test. For example, ACB/BCM/CTG/HPG cache rows dated 2026-08-11 match inputs
through 2026-08-07. The historical Research writer read `market_data_date` and
saved under `target_d`; commit 44721ae removed this source-cache write. Do not
reintroduce that propagation to force historical NAV parity. Preserve the
original baseline as the comparison reference and report its date mismatch.

New RL cache queries have an explicit symbol tie-break after score_date, so their
100-row selection does not depend on physical insertion order. Original sealed
snapshots retain their recorded learning lists; the new deterministic selection
is not claimed to reproduce those historically unordered lists.

LOCAL validation has covered an atomic 6-symbol reset with captured AlphaStock,
CafeF and VCI responses, rollback, repeatability and independent ratio invariants.
The subsequent full 407-symbol source reset completed on LOCAL, but its audit
did not establish baseline parity or source-filing certification. A confirmed
insurance mapping fix now selects total net insurance-business revenue and
gross insurance-business profit by their explicit codes/full labels, rather
than a net-premium component. All 617 independently flagged insurer ratio cells
pass after a real 5-symbol source reset, with repeatable financial hashes.
The same run invalidated 140 stale factors and rebuilt 28 dates for those symbols.
This candidate is not production-certified: fresh factor regeneration against
the observed baseline contract remains open.
Financial backfill invalidates stale factors but does not certify or rebuild
the factor table. Unknown source period/publication/consolidation basis must be resolved before certifying
point-in-time financial accuracy or NAV parity. No Replay is an implicit step.

Daily factors preserve genuine zero returns/margins/debt and report missing required financial
or market inputs explicitly. The daily writer does not persist imputed scores,
reports per-symbol errors and checks persistence success. A partial daily batch
is a failed ETL step, without switching producers to hide incomplete inputs.
Research defers missing dynamic inputs instead of using their default scores.
Zero P/E or P/B is explicitly unavailable for scoring, rather than a default
multiple or a falsely cheap valuation.
Neither this input gate nor the mapping fix changes F4/F6 formulas.

## Cache producer and date checks

Producer rows record `source.producer`, `source.as_of` and `source.cache_complete`
inside the existing `factor_details` JSON. FactorService also records the actual
market-data date and selected financial period/publication date. This introduces
no history tables, revision storage or schema migration. An as-of tag certifies
the calculation's requested date, not an unknown filing publication date.

Live Research rejects untraceable, incomplete or date-mismatched cached vectors
and uses the existing dynamic calculation/defer path. Generic cached columns
must also agree with their canonical F1-F6 JSON values. Source writers cannot
carry a prior as-of tag into a new score date or let extra metadata overwrite
the newly calculated factors. RL's learning query applies the same producer,
as-of and completion filters to avoid learning from the old decision cache.

After a financial reset, rebuild the requested as-of range with the existing
daily factor producer before resuming consumers. Existing rows without these
tags must be recalculated, rather than stamped with a tag after the fact.
Replay captures only usable cache rows; missing rows retain the existing
dynamic fallback. Before account reset or any trading sessions, a supplied
snapshot must pass provenance checks and match the current DB's cached factor
and learning inputs. An old file is rejected if backfill/recalculation changed
those inputs. Historical baseline files remain readable for offline comparison;
they are not accepted as trusted cache merely because their content hash is valid.

Hold financial, market and factor writers during a replay. The preflight checks
the cache at startup; it does not freeze all statements or market tables for the
entire run. Cache checks alone do not certify filing accuracy or baseline NAV parity.

Direct/incremental ratio recalculation invalidates cache from the earliest
changed ratio period onward, including VN peer ranks. A no-op recalculation
preserves cache. Temporary backfill staging does not invalidate public cache
early; publication handles the reset in the same transaction as before.

## Interpreting validation

A reset rejects a staged ratios statement that loses a reported EPS or BVPS
on an existing period. It rolls back rather than merging old values into the
new source payload. Reported zero and negative values count as present.
This regression check does not certify coverage of entirely new periods.

Peer valuation records whether per-share inputs were reported or implied from
market close and P/E or P/B. The latter is an estimate, not a recovered filing
value or a TTM earnings certificate. The market calculation table's close is
in thousand VND and is converted explicitly to VND for this estimate. Reported
zero/negative per-share inputs are never replaced by implied positive values.
Matching recalculated factors does not establish EPS/BVPS or valuation parity.

Historical reproduction is a separate, explicit Replay use case. The standard
Replay CLI accepts `--historical-inputs <manifest.json>` with an existing
`--factor-snapshot`. The manifest must declare
`purpose=HISTORICAL_BASELINE_REPRODUCTION`, identify the target database,
pin the snapshot SHA-256, and match row counts and complete-content fingerprints
for all three restored source tables. Replay verifies these before account reset.
For a remote database the manifest must also pin `target_host` exactly as used
by the database connection. Without that field only localhost is accepted.
Fingerprint comparisons use Vietnam time so timestamps hash consistently across hosts.
This mode preserves an archived cache for historical comparison; it does not
retag legacy cache, rebuild factors, approve fresh backfill, or change live cache
acceptance. Without this flag, the normal producer/date checks still apply.
For exact historical behavior this mode also permits the calculator's legacy
defaults on missing dynamic inputs, with explicit warnings. These observations
remain incomplete historical inputs; they are not certified live factors and
cannot be persisted while the Replay snapshot is active.

Run mapping preview again after a full crawl or repair: zero proposed changes
confirms that stored data and the calculator agree. Also check source coverage
and error counts; a fixed point alone does not prove source-report accuracy.
Current provider responses may legitimately differ from an older baseline.
Those differences must be reported; do not restore baseline values to make a
crawler test pass. The source's legacy publication metadata is not certified
as an actual filing timestamp. Verify the same code on a PROD-derived clone
before applying it to PROD. A connection failure during commit requires checking
the target state before retrying.

## Continue the approved baseline through the latest closed session

Use the standard Replay with `--start 2026-08-12`; this overrides `--days`.
There is no 52-session cap. Without `--end`, the CLI includes today's available
market session after 15:00 Vietnam time, otherwise the latest earlier session.
Removing a count cap does not extend a provider's historical API retention.
Raw trades and depth for old dates must still be available from the original
provider/cache; a missing history warning must be investigated if parity fails.

```bash
python -u experiments/replay_agent_pipeline.py \
  --start 2026-08-12 --reset --max-candidates 3 --capital 1000000000 \
  --account-id 940b0c70-2010-42f3-b947-797e6419b794 \
  --factor-snapshot /approved/baseline/frozen_original_cache_v1.json \
  --historical-inputs /approved/baseline/target_manifest.json \
  --baseline-reference /approved/baseline/reference.json \
  --audit-output /evidence/replay-unique-run
```

The reference is exclusively `cache_readonly_replay_28_20261025_NAV_1005857685`.
Ship its adjacent `effective_F1_F6.json`, preserve their hashes, and use a new
evidence directory for every attempt. This is explicit historical restoration.

Before account reset, Replay verifies the restored three source tables and the
exact archive, calendar prefix, account, capital and scanner settings. It builds
later dates with the existing VN producer in a read-only transaction. Every
eligible producer symbol must be returned; F1–F6 must be finite within 0–100,
with producer/as-of provenance and nonmissing P/E, P/B and ROE. These checks
establish factor availability, not independent source-filing certification.
The approved archive and its learning inputs remain intact for the first 28
dates. Later learning inputs use that day's VN producer, sorted by symbol.
New computed factors are saved in the run snapshot, never published over source
`factor_scores` by Replay.

For each of the first 28 closes, `daily_baseline_comparison.jsonl` records exact
NAV/cash differences, differences in every observed F1–F6 vector and source
path, fills, pipeline phase diagnostics, snapshot SHA and agent error count.
Any difference stops before the next session. The account retains the failed
session's state for diagnosis; this is not an automatic rollback. Inspect the
recorded factor differences, source fingerprints, fills/decision contexts and
code release before assigning a cause. Do not change thresholds to force parity.
Only after all 28 sessions match does execution continue without further NAV
baseline comparisons. Later missing VN cache inputs stop the run, even if an
agent wrapper catches its exception; generic dynamic fallback and the eight
legacy historical defaults are unavailable outside the archive dates.
Preflight/runtime failures also write `failure.json`.

Production rollout uses the existing `aiinvest` database, with no separate PROD
replay database. Prepare the PR, pass CI, deploy its main release SHA via the
existing deployment workflow and verify health. Before restoring any tables or
resetting the account, preserve and verify a backup, prevent concurrent portfolio
automation/ETL writes during the run, restore the approved three tables in one
transaction, and pin the exact connection host/database in the manifest. Keep
the source archive immutable. This plan does not enable live broker orders.

Read-only checks on 2026-11-02 found PROD market data through 2026-11-02, but only
1 of 409 stock records had positive `stocks.market_cap` (LOCAL: 1 of 408).
VN F5 divides foreign net value by this market cap, so foreign-flow rows alone
cannot provide complete F5. The LOCAL first-future-date preflight (2026-09-23
as-of) failed even when tested with PROD flow in a LOCAL temporary table.
Populate verified, correctly scaled market-cap inputs and rerun full date/symbol
coverage before PROD reset. Do not fill missing values with 50 or infer units
from magnitude. Full continuation is not yet certified by the prior 28-session
historical acceptance result.
