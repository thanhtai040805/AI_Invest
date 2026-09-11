"""Clean Markdown artifacts already stored in R2, in place.

This is intentionally a migration rather than part of extraction.  It keeps
the existing R2 object key, updates the SAG asset hash, and is idempotent via
the ``ocr-clean-v2`` parser marker.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from sag_api.core.db import SessionLocal
from sag_api.db.models import Document, DocumentAsset, Issuer
from sag_api.parsing.markdown_noise_cleaner import clean_markdown
from sag_api.services.financial_v2_service import canonicalize_markdown
from sag_api.services.r2_storage import SagR2StorageClient


CLEANER_VERSION = "ocr-clean-v2"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _r2_key(object_uri: str, bucket_name: str) -> str | None:
    prefix = "r2://"
    if not object_uri.startswith(prefix):
        return None
    value = object_uri[len(prefix) :]
    if "/" not in value:
        return None
    bucket, key = value.split("/", 1)
    if bucket != bucket_name or not key:
        return None
    return key


def _role_from_key(key: str) -> str | None:
    upper = key.upper()
    if "GOVERNANCE" in upper:
        return "GOVERNANCE_REPORT"
    if "/YEAR/" in upper or "_YEAR_" in upper:
        return "ANNUAL_BACKBONE"
    if "/Q" in upper or "_Q1_" in upper or "_Q2_" in upper or "_Q3_" in upper or "_Q4_" in upper:
        return "LATEST_QUARTER"
    return None


def _list_markdown_keys(client: SagR2StorageClient) -> list[str]:
    if client.auth_mode != "s3":
        raise RuntimeError("Migration cần S3 credentials để list_objects_v2 trên R2")
    keys: list[str] = []
    paginator = client.get_s3_client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=client.bucket_name):
        for item in page.get("Contents", []):
            key = str(item.get("Key", ""))
            if key.lower().endswith((".md", ".markdown")):
                keys.append(key)
    return keys


async def _load_assets() -> list[tuple[DocumentAsset, str | None, str | None, str]]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(DocumentAsset, Document.doc_role, Issuer.ticker)
                .join(Document, Document.asset_id == DocumentAsset.id)
                .join(Issuer, Issuer.id == Document.issuer_id)
                .where(DocumentAsset.asset_kind == "canonical_markdown")
                .order_by(DocumentAsset.created_at)
            )
        ).all()
        # The same asset can be referenced by duplicate document rows. Keep
        # one role/ticker for the object and update every matching asset later.
        by_id: dict[str, tuple[DocumentAsset, str | None, str | None, str]] = {}
        for asset, role, ticker in rows:
            by_id.setdefault(asset.id, (asset, role, ticker, asset.object_uri))
        return list(by_id.values())


async def migrate(*, apply: bool, limit: int | None) -> int:
    client = SagR2StorageClient()
    if not client.is_configured:
        raise RuntimeError("R2 chưa được cấu hình trong SAG/apps/api/.env")

    assets = await _load_assets()
    asset_by_key: dict[str, tuple[DocumentAsset, str | None, str | None, str]] = {}
    for asset, role, ticker, uri in assets:
        key = _r2_key(uri, client.bucket_name)
        if key and key.lower().endswith((".md", ".markdown")):
            asset_by_key.setdefault(key, (asset, role, ticker, uri))

    candidates: list[tuple[DocumentAsset | None, str | None, str | None, str, str]] = []
    seen_keys: set[str] = set()
    for key in _list_markdown_keys(client):
        if key in seen_keys:
            continue
        seen_keys.add(key)
        asset, role, ticker, uri = asset_by_key.get(key, (None, _role_from_key(key), key.split("/", 2)[1] if "/" in key else None, f"r2://{client.bucket_name}/{key}"))
        if asset is not None and (asset.metadata_json or {}).get("cleaner_version") == CLEANER_VERSION:
            continue
        if role is None:
            role = _role_from_key(key)
        candidates.append((asset, role, ticker, uri, key))

    if limit is not None:
        candidates = candidates[:limit]

    print(json.dumps({
        "bucket": client.bucket_name,
        "asset_rows": len(assets),
        "unique_r2_candidates": len(candidates),
        "apply": apply,
        "cleaner_version": CLEANER_VERSION,
    }, ensure_ascii=False))

    if not apply:
        for asset, role, ticker, _uri, key in candidates[:20]:
            print(f"DRY-RUN {ticker or '-'} {role or '-'} {key}")
        return 0

    stats = defaultdict(int)
    async with SessionLocal() as session:
        for asset, role, ticker, _uri, key in candidates:
            try:
                raw = await asyncio.to_thread(client.download_bctc_bytes, key)
                text = raw.decode("utf-8")
                cleaned, clean_stats = clean_markdown(text, doc_role=role)
                canonical = canonicalize_markdown(cleaned)
                digest = _sha256(canonical)
                await asyncio.to_thread(client.upload_parsed_markdown, key, canonical)

                # Update all rows that reference this same object. The first
                # row may be a duplicate retry while another row points to the
                # verified artifact.
                matching = []
                if asset is not None:
                    matching = (
                        await session.execute(
                            select(DocumentAsset).where(DocumentAsset.object_uri == asset.object_uri)
                        )
                    ).scalars().all()
                for target in matching:
                    old_hash = target.canonical_markdown_sha256 or target.content_sha256
                    metadata = dict(target.metadata_json or {})
                    metadata.update({
                        "cleaner_version": CLEANER_VERSION,
                        "cleaned": True,
                        "pre_clean_sha256": old_hash,
                        "clean_stats": {
                            name: getattr(clean_stats, name)
                            for name in clean_stats.__dataclass_fields__
                        },
                    })
                    target.content_sha256 = digest
                    target.canonical_markdown_sha256 = digest
                    target.size_bytes = len(canonical.encode("utf-8"))
                    target.parser_version = CLEANER_VERSION
                    target.metadata_json = metadata
                await session.commit()
                stats["cleaned"] += 1
                print(f"CLEANED {ticker or '-'} {role or '-'} {key} bytes={len(raw)}->{len(canonical.encode('utf-8'))} sha256={digest}")
            except Exception as exc:  # keep other documents progressing
                await session.rollback()
                stats["failed"] += 1
                print(f"FAILED {ticker or '-'} {role or '-'} {key}: {type(exc).__name__}: {exc}", file=sys.stderr)

    print(json.dumps(dict(stats), ensure_ascii=False))
    return 1 if stats["failed"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="overwrite R2 and update SAG DB")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    return asyncio.run(migrate(apply=args.apply, limit=args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
