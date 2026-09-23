"""Lock the strict single-request extraction contract.

- Exactly one LLM call per file (no validation retry, no chunked fallback).
- Invented enum values (e.g. cash_balance) are coerced to 'other' server-side.
- finish_reason=length suggests increasing max tokens, never chunking.
"""

import asyncio
import json

from sag_api.services import extraction_v2_service as service
from sag_api.services.extraction_v2_service import (
    TAXONOMY_VERSION,
    ExtractionManifestIn,
    _infer_accounting_scope,
    _iter_manifest_refs,
    coerce_unknown_enums,
    enrich_manifest_facets_from_observations,
)


def _manifest_payload(**overrides):
    payload = {
        "taxonomy_version": TAXONOMY_VERSION,
        "node_annotations": [{"node_id": "n1", "relevance": "NONE"}],
        "entity_mentions": [],
        "facts": [],
        "relations": [],
        "moat_signals": [],
        "gil_disclosures": [],
    }
    payload.update(overrides)
    return payload


def _evidence():
    return {"node_id": "n1", "quote": "HPG"}


def test_scope_uses_document_title_before_conflicting_note_text():
    document = type("Document", (), {"filename": "BCTC Hợp nhất Kiểm toán năm 2025"})()
    markdown = "Báo cáo tài chính riêng của công ty mẹ được trình bày trong thuyết minh."
    assert _infer_accounting_scope(markdown, document) == "CONSOLIDATED"


def test_coerce_unknown_enums_keeps_original_concept():
    payload = _manifest_payload(
        facts=[
            {
                "fact_type": "cash_balance",
                "label": "Tien mat",
                "evidence": _evidence(),
            },
            {
                "fact_type": "revenue",
                "label": "Doanh thu",
                "evidence": _evidence(),
            },
        ],
        entity_mentions=[
            {
                "raw_text": "Cong ty X",
                "canonical_name": "Cong ty X",
                "entity_type": "steel_distributor",
                "evidence": _evidence(),
            }
        ],
        relations=[
            {
                "subject": "HPG",
                "object": "Cong ty X",
                "relation_type": "supplies_to",
                "evidence": _evidence(),
            }
        ],
    )

    coerced = coerce_unknown_enums(payload)

    assert len(coerced) == 3
    assert payload["facts"][0]["fact_type"] == "other"
    assert payload["facts"][0]["raw_label"] == "cash_balance"
    assert payload["facts"][0]["taxonomy_candidate"] == "cash_balance"
    assert payload["facts"][1]["fact_type"] == "revenue"
    assert payload["entity_mentions"][0]["entity_type"] == "other"
    assert payload["relations"][0]["relation_type"] == "other"

    # A previously failing cash_balance manifest now validates.
    manifest = ExtractionManifestIn.model_validate(payload)
    assert manifest.facts[0].fact_type.value == "other"


def test_coerce_preserves_explicit_raw_label():
    payload = _manifest_payload(
        facts=[
            {
                "fact_type": "cash_balance",
                "label": "Tien mat",
                "raw_label": "So du tien mat",
                "evidence": _evidence(),
            }
        ]
    )

    coerce_unknown_enums(payload)

    assert payload["facts"][0]["raw_label"] == "So du tien mat"
    assert payload["facts"][0]["taxonomy_candidate"] == "cash_balance"




def test_open_observations_keep_sector_meaning_without_taxonomy_enum():
    manifest = ExtractionManifestIn.model_validate(
        _manifest_payload(
            document_facets=[
                {
                    "facet": "blast_furnace_capacity",
                    "confidence": 0.91,
                    "evidence": _evidence(),
                }
            ],
            observations=[
                {
                    "statement": "Hieu qua van hanh lo cao la dong luc cai thien bien loi nhuan.",
                    "subject": "HPG",
                    "predicate": "margin driver",
                    "object": "blast furnace utilization",
                    "topic_tags": ["Steel", "blast furnace", "steel"],
                    "confidence": 0.88,
                    "evidence": _evidence(),
                }
            ],
        )
    )

    assert manifest.document_facets[0].facet == "blast_furnace_capacity"
    assert manifest.observations[0].predicate == "margin driver"
    assert manifest.observations[0].topic_tags == ["steel", "blast furnace"]
    paths = [path for path, _evidence_ref in _iter_manifest_refs(manifest)]
    assert "document_facets.0" in paths
    assert "observations.0" in paths
    assert enrich_manifest_facets_from_observations(manifest)["facets_derived_from_observations"] == 2
    assert {facet.facet for facet in manifest.document_facets} == {"blast_furnace_capacity", "steel", "blast furnace"}


def _make_request_parts():
    from sag_api.db.models import Document, DocumentTreeNode, Issuer

    issuer = Issuer(ticker="HPG")
    document = Document(id="d1", doc_role="ANNUAL_BACKBONE", fiscal_year=2025)
    nodes = [DocumentTreeNode(document_id="d1", node_id="n1", start_line=1, end_line=5)]
    markdown = "# HPG\n## Balance Sheet\n## Income Statement\n## Cash Flow\n## Thuyet minh\n"
    return issuer, document, markdown, nodes


def test_missing_node_annotations_are_filled_deterministically():
    _, _, markdown, nodes = _make_request_parts()
    manifest = service.ExtractionManifestIn.model_validate(_manifest_payload(node_annotations=[]))

    assert service.fill_missing_node_annotations(nodes, manifest) == 1
    service._validate_manifest_shape_and_references(markdown, nodes, manifest)


def test_line_based_evidence_resolves_to_persistent_node_ids():
    from sag_api.db.models import DocumentTreeNode

    nodes = [
        DocumentTreeNode(document_id="d1", node_id="n_aaaaaaaaaaaaaaaaaaaaaaaa", start_line=1, end_line=1),
        DocumentTreeNode(document_id="d1", node_id="n_bbbbbbbbbbbbbbbbbbbbbbbb", start_line=2, end_line=2),
    ]
    manifest = service.ExtractionManifestIn.model_validate(
            {
                "taxonomy_version": service.TAXONOMY_VERSION,
                "node_annotations": [],
                "facts": [
                {
                    "fact_type": "other",
                    "label": "Revenue",
                    "value_text": "10",
                    "evidence": {"quote": "10", "line_hint": 2},
                }
            ],
        }
    )

    service.resolve_llm_evidence_nodes("alpha\n10\n", nodes, manifest)

    assert manifest.node_annotations == []
    assert manifest.facts[0].evidence.node_id == nodes[1].node_id


def test_extraction_uses_deterministic_compiler_without_llm(monkeypatch):
    from sag_api.enums import ProcessingStageStatus

    class _Savepoint:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class _Session:
        def begin_nested(self):
            return _Savepoint()

    async def _persist(*_args, **_kwargs):
        return {"fact_count": 0, "relation_count": 0, "observation_count": 0, "facet_count": 0, "evidence_count": 0}

    monkeypatch.setattr(service, "validate_and_persist_manifest", _persist)
    assert not hasattr(service, "LLMClient")
    issuer, document, markdown, nodes = _make_request_parts()
    result = asyncio.run(service.extract_and_persist_manifest(_Session(), issuer, document, markdown, nodes))

    assert result.status == ProcessingStageStatus.COMPLETE.value
    assert result.metadata["mode"] == "deterministic_compiler"
    assert result.metadata["llm_used"] is False


def test_normalized_fuzzy_grounds_split_number_multiline_quote():
    # Numbers live on different lines so no single line is compatible with
    # the whole quote; only folded multi-line containment can ground it.
    # Mirrors the live annual failure (occurrences=0, OCR-mangled diacritics).
    from sag_api.db.models import DocumentTreeNode
    from sag_api.services.extraction_v2_service import _locate_quote

    markdown = (
        "| Doanh thu cung cấp dịch vụ | 46.186.800.000 |\n"
        "| Doanh thu bất động sản đầu tư cho thuê | 43.604.821.701 |\n"
        "| Tổng cộng | 99 |\n"
    )
    node = DocumentTreeNode(document_id="d1", node_id="n1", start_line=1, end_line=3)

    location = _locate_quote(
        markdown,
        node,
        "Doanh thu cung cp dch v | 46.186.800.000 |\nDoanh thu bt đng sn đu tư cho thu | 43.604.821.701 |",
    )

    assert location["resolution"] == "multiline_fuzzy"
    assert location["start_line"] == 1
    assert location["end_line"] == 2
    assert "cung cấp dịch vụ" in location["resolved_quote"]


def test_normalized_fuzzy_grounds_multiline_quote():
    from sag_api.db.models import DocumentTreeNode
    from sag_api.services.extraction_v2_service import _locate_quote

    markdown = "Doanh thu cung cấp dịch vụ\ncho thuê | 46.186.800.000 |\nkhác\n"
    node = DocumentTreeNode(document_id="d1", node_id="n1", start_line=1, end_line=3)

    location = _locate_quote(markdown, node, "doanh thu cung cap dich vu\ncho thue | 46.186.800.000 |")

    # Pure diacritic difference: folded containment gives the exact span.
    assert location["resolution"] == "normalized_fuzzy"
    assert location["start_line"] == 1
    assert location["end_line"] == 2
    assert location["resolved_quote"].startswith("Doanh thu cung cấp dịch vụ")


def test_normalized_fuzzy_rejects_ambiguous_quote():
    from sag_api.db.models import DocumentTreeNode
    from sag_api.services.extraction_v2_service import _locate_quote

    import pytest

    node = DocumentTreeNode(document_id="d1", node_id="n1", start_line=1, end_line=3)
    with pytest.raises(ValueError, match="xuất hiện duy nhất"):
        _locate_quote("abc\nabc\nxyz\n", node, "ABC")


def _nodes_for_reattr():
    from sag_api.db.models import DocumentTreeNode

    return [
        DocumentTreeNode(document_id="d1", node_id="root", start_line=1, end_line=5),
        DocumentTreeNode(document_id="d1", node_id="wrong", start_line=4, end_line=5),
        DocumentTreeNode(document_id="d1", node_id="owner", start_line=1, end_line=2),
    ]


def test_reattribute_wrong_node_citation_to_unique_true_span():
    from sag_api.services.extraction_v2_service import _locate_quote

    markdown = "Tien mat 46.186.800.000 dong\n chilling\nkhac\nmore\nend\n"
    nodes = _nodes_for_reattr()
    cited = next(node for node in nodes if node.node_id == "wrong")

    location = _locate_quote(
        markdown, cited, "Tien mat 46.186.800.000 dong", None, all_nodes=nodes
    )

    assert location["resolution"] == "reattributed_exact"
    assert location["actual_node_id"] == "owner"
    assert location["cited_node_id"] == "wrong"
    assert location["start_line"] == 1
    assert location["end_line"] == 1


def test_reattribute_refuses_ambiguous_document_match():
    from sag_api.db.models import DocumentTreeNode
    from sag_api.services.extraction_v2_service import _locate_quote

    import pytest

    nodes = [
        DocumentTreeNode(document_id="d1", node_id="root", start_line=1, end_line=5),
        DocumentTreeNode(document_id="d1", node_id="wrong", start_line=2, end_line=3),
    ]
    cited = nodes[1]
    with pytest.raises(ValueError, match="xuất hiện duy nhất"):
        _locate_quote("abc xyz\ndef\nghi\nabc xyz\njkl\n", cited, "abc xyz", None, all_nodes=nodes)


def test_reattribute_skipped_without_all_nodes():
    from sag_api.services.extraction_v2_service import _locate_quote

    import pytest

    nodes = _nodes_for_reattr()
    cited = next(node for node in nodes if node.node_id == "wrong")
    with pytest.raises(ValueError, match="xuất hiện duy nhất"):
        _locate_quote(
            "Tien mat 46.186.800.000 dong\n chilling\nkhac\nmore\nend\n",
            cited,
            "Tien mat 46.186.800.000 dong",
        )


def test_iter_manifest_refs_stable_order():
    manifest = ExtractionManifestIn.model_validate(_manifest_payload(
        entity_mentions=[
            {"raw_text": "a", "canonical_name": "a", "entity_type": "issuer", "evidence": {"node_id": "n1", "quote": "q"}},
            {"raw_text": "b", "canonical_name": "b", "entity_type": "issuer", "evidence": {"node_id": "n1", "quote": "q2"}},
            {"raw_text": "c", "canonical_name": "c", "entity_type": "issuer", "evidence": {"node_id": "n1", "quote": "q3"}},
        ],
        facts=[{"fact_type": "revenue", "label": "x", "evidence": {"node_id": "n1", "quote": "f"}}],
        relations=[{"subject": "a", "object": "b", "relation_type": "transacts_with", "evidence": {"node_id": "n1", "quote": "r"}}],
        moat_signals=[{"pillar": "network_effects", "direction": "positive", "strength": "medium", "durability": "medium", "materiality": "medium", "signal": "m", "evidence": {"node_id": "n1", "quote": "ms"}}],
        gil_disclosures=[{"disclosure_type": "risk", "label": "g", "evidence": {"node_id": "n1", "quote": "gd"}}],
    ))
    paths = [path for path, _ in service._iter_manifest_refs(manifest)]
    assert paths[:3] == ["entity_mentions.0", "entity_mentions.1", "entity_mentions.2"]
    assert paths[3] == "facts.0"
    assert paths[4] == "relations.0"
    assert paths[5] == "moat_signals.0"
    assert paths[6] == "gil_disclosures.0"
