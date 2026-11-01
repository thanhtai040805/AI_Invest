# Financial backfill

Full provider backfill intentionally replaces historical financial inputs with
the provider's current response. It uses AlphaStock first, CafeF for sparse or
missing statements, then VCI for missing latest-quarter reports. Existing usable
primary rows are not replaced solely because fallback JSON contains more aliases.
No history schema, revision journal, triggers or migration is required.

Run from `ai-engine/` with `DATABASE_URL` explicitly set to the intended database.
Hold competing financial writers while repairing that database. A financial
backfill does not run Replay or regenerate factor scores.

## Re-crawl historical source data

Use the existing full financial ETL entry point:

```powershell
python -c "import json; from app.infrastructure.data_pipelines.financial_etl_alphastock import refresh_all; r=refresh_all(); print(json.dumps(r)); raise SystemExit(1 if r['errors'] or r['vci_fallback']['errors'] else 0)"
```

The job commits batches of symbols. A nonzero error count means the complete run
has not passed, even if earlier batches committed; investigate and rerun. Within
a symbol, statement repair and ratio derivation share the caller's savepoint,
so ratio failures roll back that symbol's writes. The VCI fallback follows the
same rule. Explicit backfill also normalizes retained statements outside the
provider fetch window using their own authoritative source lines.

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

The repair normalizes aliases and recomputes the twelve existing ratio columns
with the same calculator as ETL. Existing override values and ratio formulas
remain in their established owners. For the observed AlphaStock and CafeF rate
schemas, reported-period statement-derived profitability takes precedence when
available; vendor percentage-point rates are converted to fractions as fallback.
Modern AlphaStock `rt_prt_*` rates are already fractions. Unknown profitability
unit schemas fail rather than guessing. This is not a new annualized/TTM ratio
contract, and the job does not infer those bases from NAV.

Statement source/publication metadata is preserved for mapping-only repairs.
Ratios without sufficient stored BS/IS inputs are retained and counted. This
command does not insert missing ratio rows, reset accounts or change factors.

## Interpreting validation

Run mapping preview again after a full crawl or repair: zero proposed changes
confirms that stored data and the calculator agree. Also check source coverage
and error counts; a fixed point alone does not prove source-report accuracy.
Current provider responses may legitimately differ from an older baseline.
Those differences must be reported; do not restore baseline values to make a
crawler test pass. The source's legacy publication metadata is not certified
as an actual filing timestamp. Verify the same code on a PROD-derived clone
before applying it to PROD. A connection failure during commit requires checking
the target state before retrying.
