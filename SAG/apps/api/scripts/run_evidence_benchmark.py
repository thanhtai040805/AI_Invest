"""Audit evidence-native extraction results without imposing a sector taxonomy.

The command evaluates persisted output after a document was ingested. A JSON
spec may declare only observable expectations (minimum counts, required open
facets, and phrases that must survive as observations), making the same
harness usable for banks, industrials, retailers, insurers, or new sectors.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))
os.chdir(API_ROOT)


def _load_env() -> None:
    path = API_ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()

from sqlalchemy import select  # noqa: E402

from sag_api.core.db import SessionLocal, dispose_db  # noqa: E402
from sag_api.db.models import Document, DocumentFacet, EvidenceSpan, Fact, Observation  # noqa: E402


async def audit_case(case: dict[str, Any]) -> dict[str, Any]:
    document_id = str(case["document_id"])
    expected = case.get("expect") or {}
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            return {"name": case.get("name", document_id), "status": "FAIL", "reasons": ["document_not_found"]}
        facts = (await session.execute(select(Fact).where(Fact.document_id == document_id))).scalars().all()
        observations = (await session.execute(select(Observation).where(Observation.document_id == document_id))).scalars().all()
        facets = (await session.execute(select(DocumentFacet).where(DocumentFacet.document_id == document_id))).scalars().all()
        spans = {
            item.id: item
            for item in (await session.execute(select(EvidenceSpan).where(EvidenceSpan.document_id == document_id))).scalars().all()
        }

    reasons: list[str] = []
    if document.extraction_status != "COMPLETE" or document.embedding_status != "COMPLETE":
        reasons.append("pipeline_not_complete")
    for field, actual in (("facts", len(facts)), ("observations", len(observations)), ("facets", len(facets))):
        minimum = int(expected.get(f"min_{field}", 0))
        if actual < minimum:
            reasons.append(f"{field}_below_minimum:{actual}<{minimum}")
    facet_names = {facet.facet for facet in facets}
    missing_facets = sorted(set(expected.get("required_facets", [])) - facet_names)
    if missing_facets:
        reasons.append(f"missing_facets:{','.join(missing_facets)}")
    statements = "\n".join(item.statement.casefold() for item in observations)
    for phrase in expected.get("observation_phrases", []):
        if str(phrase).casefold() not in statements:
            reasons.append(f"missing_observation_phrase:{phrase}")
    ungrounded = [item.id for item in observations if not item.evidence_span_id or item.evidence_span_id not in spans]
    if ungrounded:
        reasons.append(f"ungrounded_observations:{len(ungrounded)}")
    return {
        "name": case.get("name", document_id),
        "document_id": document_id,
        "status": "PASS" if not reasons else "FAIL",
        "counts": {"facts": len(facts), "observations": len(observations), "facets": len(facets)},
        "facets": sorted(facet_names),
        "reasons": reasons,
    }


async def main(spec_path: Path) -> int:
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    if not isinstance(cases, list) or not cases:
        raise ValueError("Benchmark spec requires a non-empty cases list")
    results = [await audit_case(case) for case in cases]
    output = {"status": "PASS" if all(item["status"] == "PASS" for item in results) else "FAIL", "cases": results}
    sys.stdout.buffer.write((json.dumps(output, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return 0 if output["status"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark evidence-native SAG extraction")
    parser.add_argument("spec", type=Path, help="Path to benchmark JSON spec")
    args = parser.parse_args()
    async def _run() -> int:
        try:
            return await main(args.spec)
        finally:
            await dispose_db()

    raise SystemExit(asyncio.run(_run()))
