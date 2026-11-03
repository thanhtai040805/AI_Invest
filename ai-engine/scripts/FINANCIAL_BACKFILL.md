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

Generic is the sole production F1-F6 definition: F4 is YoY revenue/earnings
growth and F6 is price versus MA20/MA50. VN percentile ranks are a different
model and must not replace these values. The ETL has no VN fallback; its VN
refresh entry point rejects cache writes, and VN composite scoring cannot
overwrite Generic rows.

Producer rows record `source.producer=FACTOR_SERVICE`,
`source.version=generic-f1-f6-v1`, `source.as_of` and `source.cache_complete`
inside the existing `factor_details` JSON. FactorService also records the actual
market-data date and selected financial period/publication date. This introduces
no history tables, revision storage or schema migration. An as-of tag certifies
the calculation's requested date, not an unknown filing publication date.

Research rejects old-version, VN, untraceable, incomplete or date-mismatched cached vectors
and uses the existing dynamic calculation/defer path. Generic cached columns
and CSS must agree with their canonical JSON values. Source writers cannot
carry a prior as-of tag into a new score date or let extra metadata overwrite
the newly calculated factors. A valid neutral vector is still usable; a score
of 50 is not itself evidence that a cache is missing. RL reads exact-as-of
Generic rows, with separate F3 and F6 columns and no previous-day cache carry.

After a financial reset, rebuild the requested as-of range with the existing
daily factor producer before resuming consumers:
`FactorService().compute_daily_factors(as_of, symbols=eligible_symbols)`.
The batch deletes that date/symbol scope before recomputing, including stale
rows whose new inputs are missing. It returns both `factor_scores_invalidated`
and per-symbol `errors`; incomplete inputs are not cached and any errors mean
the requested scope is not ready. Normal daily ETL uses the same method with
the default HOSE/HSX scope.

For historical Replay preparation, `as_of` is the previous calendar day of
each requested trading session, including weekend dates. Rebuild the entire
requested session range with this same method; do not splice producers at the
28-session boundary. This operation does not run Replay or reset a portfolio.
Existing rows without these
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

Run mapping preview again after a full crawl or repair: zero proposed changes
confirms that stored data and the calculator agree. Also check source coverage
and error counts; a fixed point alone does not prove source-report accuracy.
Current provider responses may legitimately differ from an older baseline.
Those differences must be reported; do not restore baseline values to make a
crawler test pass. The source's legacy publication metadata is not certified
as an actual filing timestamp. Verify the same code on a PROD-derived clone
before applying it to PROD. A connection failure during commit requires checking
the target state before retrying.

## Verify the full interval with one producer

The acceptance interval starts on 2026-08-12 and extends through the latest closed
market session. Use one VN producer, the same eligible-universe rules, and the
previous calendar day as factor as-of for every session. Do not splice archived
factors/defaults into the first 28 dates and compute later dates differently.
The approved NAV baseline remains unchanged; its 1,984 observed F1–F6 vectors
are an external comparison reference, never an input to the new calculation.
The archived-cache bypass and date-window continuation flags were removed from
this PR. Earlier restoration acceptance only demonstrates historical reproduction.

Validate data on an isolated LOCAL copy of PROD source tables before Replay.
The source copy must exclude portfolio and SAG outputs and preserve a manifest
of the source transaction, filters, counts and file hashes. Compute every eligible
symbol on every date without reading `factor_scores`, record incomplete inputs,
compare the 28-session observations after computation, repeat the full calculation,
and verify all three financial/factor source tables remain unchanged. Zero batch
errors and repeatability are necessary but do not establish completeness or filing
accuracy. Never fill missing scores with 50 to make coverage pass.

Financial availability now uses the same reported-publication/lag validation rule
on every date. The former hardcoded 2026-09-01 exception was removed from the
financial repository, dynamic service and VN producer. A plausible reported date
(10–120 days after the period) is respected; otherwise the existing reporting-lag
heuristic applies. A heuristic is not a verified filing date; legacy publication
metadata and the remaining sources must be certified before production promotion.

VN momentum components retain their formulas but use their own available windows:
21 closes for the one-month component, 61 for F3, 126 for the six-month component.
The former shared 130-close gate incorrectly suppressed valid F3 observations.
A reported negative P/E now produces signed E/P rather than dropping the factor
or replacing the TTM signal with annualized quarterly earnings. Missing/zero P/E
still needs verified source inputs; no new numeric defaults were added.

The standard Replay accepts `--start 2026-08-12` to override `--days`, with no
52-session count cap. Without `--end`, today's session is eligible only after
15:00 Vietnam time. Explicit paper replay context also applies to that same-day
session; its factor as-of is the prior day, consistently with earlier sessions.
Removing a count cap does not extend provider API retention. Full raw trades and
depth still need independent coverage checks before a run can be certified.

Read-only checks on 2026-11-02 found PROD market data through 2026-11-02, but
only 1 of 409 stock rows had positive market cap. VN F5 requires market cap;
foreign-flow records alone are insufficient. The full LOCAL uniform audit and its
source evidence are kept under ignored scratch output, outside the production PR.
Do not certify future factor completeness from the earlier cached baseline run.

A controlled LOCAL comparison used the same calculator and market/support inputs
throughout, swapping only the two financial tables with frozen baseline inputs.
All first-28-session VN outputs were unchanged; financial changes affected 1,771
symbol-days only from 2026-10-15 onward, in F1/F2/F4. Both variants still differed
from all 1,984 observed historical vectors. For 1,165 archived cached observations
with sufficient raw metadata, F1/F3/F6 reproduce the generic FactorService formulas;
baseline F6 there uses price/MA. The remaining 405 cached observations have VN
component-rank metadata: all six exported factors reconstruct within 0.061, the
bound allowed by their one-decimal saved ranks. VN F4 is earnings yield and F6
volatility ranks; Generic F4 is YoY growth and F6 price/MA. These are two factor
families inside one historical cache/fallback pipeline, not one shared formula.
Of the 405 VN observations, 400 have no saved F5 rank and consume 50; 14 have no
saved F4 rank and consume 50. Eight Generic dynamic observations also use old
financial defaults. The original reader and Generic implementation from Git
reproduce all 1,984 observations/source paths exactly with the archived cache,
but that does not prove fresh cache regeneration or source completeness.
Generic cached F2/F4 still have 96/87 observations without a matching available
historical financial-input set; do not infer missing inputs from target scores.
Do not describe a uniform VN calculation as baseline-equivalent or attribute all
baseline differences to financial-table data.

No explicit DMX/LPS exception was found in LOCAL or running PROD source. The VN
liquid universe permits 45 observed trading days, while F3 needs 61 closes.
Their 22 missing F3 observations occur only after the baseline window. Classify
them as insufficient history/eligibility, not evidence of backfill data loss.
Do not silently add ticker exclusions to make completeness metrics pass.

Production uses the existing `aiinvest` database, never a separate PROD replay DB.
Only after source/factor acceptance passes: pass PR CI, deploy a successful main
release SHA through the existing workflow, preserve a verified backup, prevent
concurrent writers, and use standard Replay `--reset`. No PROD restoration/reset
or deployment is authorized by a failed data-only check. No live broker mode or
SAG integration is enabled by this work.

## LOCAL Generic cache rebuild check — 2026-11-03

On the disposable LOCAL input-copy database, 12,714 old cache rows were removed
and the current Generic producer processed the same 7,541 eligible symbol-days
over 56 sessions (2026-08-12 through 2026-11-02). All 7,433 complete vectors
were persisted and read through the real repository with zero differences from
the independently calculated vectors. All 108 incomplete vectors had no cache
row. Learning's 5,600 selected rows matched all six corresponding cache values,
including independent F3/F6. The first 28 sessions had 3,777 valid vectors and
two incomplete inputs; the next 28 had 3,656 valid and 106 incomplete.

The source-copy fixture originally omitted the unique key needed by the existing
upsert. Its PRIMARY KEY(symbol,score_date) was restored only in the disposable
LOCAL database; no application schema migration was introduced. The frozen
baseline's three tables and both financial tables in the input copy were
unchanged by the cache rebuild. No fresh financial crawl, Replay, PROD write or
SAG operation ran. This validates the cache lifecycle and calculator/reader
agreement, not filing accuracy, publication metadata or equality with the old
mixed Generic/VN baseline cache. The publication-period selection issue and
the 108 missing-input observations remain unresolved acceptance items.
