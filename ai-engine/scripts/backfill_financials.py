"""Preview or apply legacy statement repair and ratio rebuild using the target DB only."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import sys

import psycopg2
from psycopg2.extras import Json

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))
from app.domain.services import financial_fields as fields
from app.infrastructure.data_pipelines import financial_etl_cafef as cafef
from app.infrastructure.data_pipelines import financial_etl_alphastock as alpha

COLS = "pe pb roe roa debt_equity current_ratio gross_margin net_margin fcf_yield ev_ebitda yoy_revenue_growth yoy_earnings_growth".split()


def encode(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def source_hashes():
    paths = [
        Path(__file__),
        Path(fields.__file__),
        Path(cafef.__file__),
        Path(alpha.__file__),
    ]
    paths += [ENGINE / "app/infrastructure/data_pipelines/financial_statement_store.py"]
    return {
        str(path.relative_to(ENGINE)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


def equal(left, right):
    return (
        left is None
        and right is None
        or left is not None
        and right is not None
        and math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-12)
    )


def plan(cur):
    """Read current target rows and calculate a new plan; never import a saved data patch."""
    cur.execute(
        "SELECT DISTINCT symbol FROM financial_statements UNION SELECT DISTINCT symbol FROM financial_ratios ORDER BY symbol"
    )
    symbols = [row[0] for row in cur.fetchall()]
    changes = []
    inputs = hashlib.sha256()
    counts = dict(statements=0, ratios=0, missing_source=0)
    normalizers = {
        "cafef": cafef.normalize_statement,
        "alphastock": alpha.normalize_statement,
    }
    for index, symbol in enumerate(symbols, 1):
        cur.execute(
            "SELECT period_end,frequency,statement_type,data,published_date,source FROM financial_statements WHERE symbol=%s ORDER BY period_end,frequency,statement_type",
            (symbol,),
        )
        rows = cur.fetchall()
        repaired = []
        counts["statements"] += len(rows)
        inputs.update(encode([symbol, rows]).encode())
        for period, frequency, kind, data, published, source in rows:
            normalize = normalizers.get(source)
            fixed = (
                normalize(symbol, period, kind, frequency, data)
                if normalize
                else fields.normalize_financial_statement(data, kind, source)
            )
            if fixed != data:
                changes.append(
                    dict(
                        table="financial_statements",
                        key=[symbol, period, kind, frequency],
                        before=data,
                        after=fixed,
                    )
                )
            repaired.append((period, frequency, kind, fixed, published, source))
        computed = {
            (row[1], row[2]): list(row[3:15])
            for row in cafef.derive_financial_ratios(symbol, repaired)
        }
        cur.execute(
            "SELECT ratio_date,frequency,"
            + ",".join(COLS)
            + ",published_date FROM financial_ratios WHERE symbol=%s ORDER BY ratio_date,frequency",
            (symbol,),
        )
        old = cur.fetchall()
        counts["ratios"] += len(old)
        inputs.update(encode([symbol, old]).encode())
        for row in old:
            values = computed.get((row[0], row[1]))
            if values is None:
                counts["missing_source"] += 1
                continue
            if any(value is not None and not math.isfinite(value) for value in values):
                raise ValueError(
                    f"Non-finite derived ratio for {symbol}/{row[0]}/{row[1]}"
                )
            if any(not equal(a, b) for a, b in zip(row[2:14], values)):
                changes.append(
                    dict(
                        table="financial_ratios",
                        key=[symbol, row[0], row[1]],
                        before=list(row[2:14]),
                        after=values,
                    )
                )
        if index % 40 == 0:
            print(
                encode(
                    dict(
                        symbols_processed=index,
                        symbols_total=len(symbols),
                        changes=len(changes),
                    )
                ),
                flush=True,
            )
    field_updates = Counter()
    to_null = Counter()
    from_null = Counter()
    for change in changes:
        if change["table"] != "financial_ratios":
            continue
        for field, before, after in zip(COLS, change["before"], change["after"]):
            if not equal(before, after):
                field_updates[field] += 1
                if after is None:
                    to_null[field] += 1
                if before is None:
                    from_null[field] += 1
    return changes, dict(
        input_sha256=inputs.hexdigest(),
        plan_sha256=digest(changes),
        symbols=len(symbols),
        coverage=counts,
        ratio_field_updates=dict(field_updates),
        ratio_fields_to_null=dict(to_null),
        ratio_fields_from_null=dict(from_null),
        statement_updates=sum(
            row["table"] == "financial_statements" for row in changes
        ),
        ratio_updates=sum(row["table"] == "financial_ratios" for row in changes),
    )


def validate_review(review, target, hashes, summary):
    if review.get("status") != "PREVIEW":
        raise ValueError("Apply requires a successful preview manifest")
    for key, actual in [
        ("target", target),
        ("source_code_sha256", hashes),
        ("summary", summary),
    ]:
        if review.get(key) != actual:
            raise ValueError(
                f"Preview no longer matches {key}; regenerate preview on this target"
            )


def apply_changes(cur, changes):
    assignments = ",".join(f"{name}=%s" for name in COLS)
    old_columns = ",".join(COLS)
    for change in changes:
        if change["table"] == "financial_statements":
            cur.execute(
                "UPDATE financial_statements SET data=%s WHERE symbol=%s AND period_end=%s AND statement_type=%s AND frequency=%s AND data=%s",
                (Json(change["after"]), *change["key"], Json(change["before"])),
            )
        else:
            cur.execute(
                "UPDATE financial_ratios SET "
                + assignments
                + ",updated_at=NOW() WHERE symbol=%s AND ratio_date=%s AND frequency=%s AND ("
                + old_columns
                + ") IS NOT DISTINCT FROM ("
                + ",".join(["%s::double precision"] * len(COLS))
                + ")",
                (*change["after"], *change["key"], *change["before"]),
            )
        if cur.rowcount != 1:
            raise ValueError(
                f"Before-value guard failed for {change['table']}/{change['key']}"
            )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        required=True,
        help="Expected database name; must match DATABASE_URL and server",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="New artifact directory, never overwritten",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit after checking the target-specific preview",
    )
    parser.add_argument(
        "--review",
        type=Path,
        help="Preview manifest from this database and source version",
    )
    args = parser.parse_args(argv)
    if bool(args.review) != args.apply:
        parser.error("--apply and --review must be provided together")
    url = os.environ.get("DATABASE_URL")
    if not url or psycopg2.extensions.parse_dsn(url).get("dbname") != args.database:
        parser.error(
            "DATABASE_URL must explicitly name --database; no default or implicit target rewrite"
        )
    review = json.loads(args.review.read_text(encoding="utf8")) if args.review else None
    args.output.mkdir(parents=True, exist_ok=False)
    report = dict(
        status="RUNNING",
        started_at=datetime.now(timezone.utc).isoformat(),
        mode="APPLY" if args.apply else "PREVIEW",
        source_code_sha256=source_hashes(),
        data_source="CURRENT_TARGET_DATABASE_ONLY",
        provider_requests=False,
    )
    conn = None
    try:
        conn = psycopg2.connect(
            url, connect_timeout=10, options="-c timezone=Asia/Ho_Chi_Minh"
        )
        conn.set_session(isolation_level="REPEATABLE READ", readonly=not args.apply)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT current_database(), current_schema(), "
                "(SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid='financial_statements'::regclass), "
                "(SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid='financial_ratios'::regclass), "
                "(SELECT oid FROM pg_database WHERE datname=current_database()), "
                "inet_server_addr()::text,inet_server_port(),(pg_control_system()).system_identifier::text"
            )
            row = cur.fetchone()
            if row[0] != args.database:
                raise ValueError("Connected database differs from --database")
            report["target"] = dict(
                zip(
                    [
                        "database",
                        "schema",
                        "statement_schema",
                        "ratio_schema",
                        "database_oid",
                        "server_address",
                        "server_port",
                        "server_system_identifier",
                    ],
                    row,
                )
            )
            if args.apply:
                cur.execute(
                    "LOCK TABLE financial_statements,financial_ratios IN SHARE ROW EXCLUSIVE MODE NOWAIT"
                )
            changes, summary = plan(cur)
            report["summary"] = summary
            with gzip.open(
                args.output / "changes.jsonl.gz", "xt", encoding="utf8"
            ) as stream:
                for change in changes:
                    stream.write(encode(change) + "\n")
            if source_hashes() != report["source_code_sha256"]:
                raise ValueError("Source changed while calculating preview")
            if args.apply:
                validate_review(
                    review, report["target"], report["source_code_sha256"], summary
                )
                apply_changes(cur, changes)
                remaining, _ = plan(cur)
                if remaining:
                    raise ValueError(
                        "Recomputed changes remain after apply; rolling back"
                    )
                if source_hashes() != report["source_code_sha256"]:
                    raise ValueError("Source changed during apply; rolling back")
                conn.commit()
                report.update(status="APPLIED", idempotence_verified=True)
            else:
                conn.rollback()
                report["status"] = "PREVIEW"
    except Exception as exc:
        if conn:
            conn.rollback()
        report.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if conn:
            conn.close()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        (args.output / "manifest.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf8",
        )
        print(encode(report), flush=True)


if __name__ == "__main__":
    main()
