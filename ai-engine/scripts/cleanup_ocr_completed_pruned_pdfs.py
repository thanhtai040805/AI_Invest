"""Delete pruned PDF staging artifacts whose Markdown OCR is already durable."""

from __future__ import annotations

import argparse
import sys

from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=False)

from app.domain.services.r2_storage import R2StorageService
from app.infrastructure.database.pg_pool import get_conn


def main(*, ticker: str | None = None, dry_run: bool = False) -> int:
    conditions = [
        "is_ocr_completed = TRUE",
        "r2_md_uploaded = TRUE",
        "r2_pdf_uploaded = TRUE",
        "r2_pdf_key IS NOT NULL",
    ]
    params: list[str] = []
    if ticker:
        conditions.append("upper(ticker) = %s")
        params.append(ticker.upper())

    query = f"""
        SELECT ticker, fiscal_year, fiscal_quarter, report_scope, r2_pdf_key
        FROM bctc_pipeline_records
        WHERE {' AND '.join(conditions)}
        ORDER BY ticker, fiscal_year, fiscal_quarter
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()

    r2 = R2StorageService()
    deleted = 0
    failed = 0
    for row in rows:
        key = row[4]
        if dry_run:
            print(f"DRY-RUN {key}")
            continue
        try:
            if r2.delete_object(key):
                deleted += 1
                print(f"DELETED {key}")
            else:
                failed += 1
                print(f"FAILED {key}")
        except Exception as exc:
            failed += 1
            print(f"FAILED {key}: {exc}")

    print(f"Scanned={len(rows)} deleted={deleted} failed={failed} dry_run={dry_run}")
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", help="Chỉ cleanup một mã.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(main(ticker=args.ticker, dry_run=args.dry_run))
