from __future__ import annotations

import re
from datetime import date
from typing import Any

from sag_api.enums import EntityType, FactType, RelationType
from sag_api.extraction.frames.gil_frames import (
    ConversionOptionFrame,
    CorporateOwnershipFrame,
    GovernanceInsiderFrame,
    GuaranteeDebtFrame,
    PledgeCollateralFrame,
    RelatedPartyFlowFrame,
)
from sag_api.extraction.parsers.accounting_taxonomy import fold_text
from sag_api.extraction.parsers.governance_table_parser import GovernanceTableParser
from sag_api.extraction.parsers.rpt_table_parser import RptTableParser
from sag_api.extraction.parsers.statement_table_parser import StatementTableParser
from sag_api.extraction.parsers.subsidiary_table_parser import SubsidiaryTableParser
from sag_api.extraction.slot_filler.frame_slot_filler import FrameSlotFiller
from sag_api.services.extraction_v2_service import (
    TAXONOMY_VERSION,
    DocumentFacetIn,
    EntityMentionIn,
    EvidenceRef,
    ExtractionManifestIn,
    FactIn,
    NodeAnnotationIn,
    ObservationIn,
    RelationIn,
)


def _clean_entity_canonical_name(name: str) -> str:
    """Normalize legal prefixes/suffixes for canonical entity resolution."""
    raw = name.strip()
    if not raw:
        return ""
    # Strip prefixes
    cleaned = re.sub(
        r"^(Công ty Cổ phần|Công ty CP|Công ty TNHH MTV|Công ty TNHH|Ngân hàng TMCP|Ngân hàng|Tập đoàn|Tổng Công ty|Ông|Bà)\s+",
        "",
        raw,
        flags=re.I,
    ).strip()
    # Strip English or aliases in parenthesis/slash
    if "/" in cleaned:
        cleaned = cleaned.split("/")[0].strip()
    cleaned = re.sub(r"\([^\)]+\)", "", cleaned).strip()
    return cleaned if len(cleaned) >= 2 else raw


def _entity_candidate(name: str | None) -> tuple[str, str] | None:
    """Reject OCR rows that are too broad to be a persisted graph entity."""
    raw = " ".join(str(name or "").split())
    cleaned = _clean_entity_canonical_name(raw)
    if not raw or len(raw) > 512 or len(cleaned) > 512:
        return None
    return raw, cleaned


def _bounded_label(value: str | None, limit: int = 512) -> str:
    """Keep display labels bounded; evidence quote retains the source text."""
    return " ".join(str(value or "").split())[:limit] or "Chỉ tiêu"


def _find_node_for_line(nodes: list[Any], line_no: int) -> str:
    """Find the best matching document tree node for a line number."""
    if not nodes:
        return "root"
    best_node = nodes[0].node_id
    min_len = 999999
    for n in nodes:
        l_start = getattr(n, "start_line", getattr(n, "line_start", 1))
        l_end = getattr(n, "end_line", getattr(n, "line_end", 1))
        if l_start <= line_no <= l_end:
            node_len = l_end - l_start + 1
            if node_len < min_len:
                min_len = node_len
                best_node = n.node_id
    return best_node


class GilGraphCompiler:
    """Compiler that transforms deterministic financial frames into a validated ExtractionManifestIn."""

    def __init__(self, issuer_ticker: str) -> None:
        self.issuer_ticker = issuer_ticker.upper().strip()

    def compile_manifest(
        self,
        markdown_text: str,
        nodes: list[Any],
        doc_role: str,
        statement_markdown: str | None = None,
    ) -> ExtractionManifestIn:
        lines = markdown_text.splitlines()
        role_upper = str(doc_role or "").upper()

        entity_mentions: list[EntityMentionIn] = []
        facts: list[FactIn] = []
        relations: list[RelationIn] = []
        observations: list[ObservationIn] = []
        facets: list[DocumentFacetIn] = []

        seen_entities: dict[str, str] = {}  # canonical -> raw

        # Always add the Issuer entity mention
        issuer_node = _find_node_for_line(nodes, 1)
        quote_line1 = lines[0].strip() if lines else self.issuer_ticker
        entity_mentions.append(
            EntityMentionIn(
                raw_text=self.issuer_ticker,
                canonical_name=self.issuer_ticker,
                entity_type=EntityType.ISSUER,
                evidence=EvidenceRef(
                    node_id=issuer_node,
                    line_start=1,
                    line_end=1,
                    quote=quote_line1,
                ),
            )
        )
        seen_entities[fold_text(self.issuer_ticker)] = self.issuer_ticker

        # 1. Financial Statement Facts (Balance Sheet, Income Statement)
        if role_upper in {"ANNUAL_BACKBONE", "LATEST_QUARTER"}:
            stmt_text = statement_markdown or markdown_text
            stmt_parser = StatementTableParser(markdown=stmt_text, ticker=self.issuer_ticker)
            parsed_facts = stmt_parser.parse_statement_facts()
            for f in parsed_facts:
                ev = f.get("evidence", {})
                line_no = ev.get("line_start", 1)
                node_id = _find_node_for_line(nodes, line_no)
                fact_type_val = f.get("fact_type", FactType.OTHER.value)
                try:
                    ft_enum = FactType(fact_type_val)
                except ValueError:
                    ft_enum = FactType.OTHER
                f["label"] = _bounded_label(f.get("label"))

                facts.append(
                    FactIn(
                        fact_type=ft_enum,
                        label=f.get("label", "Chỉ tiêu"),
                        semantic_key=f.get("semantic_key"),
                        value_numeric=f.get("value_numeric"),
                        value_text=str(f.get("value_numeric")),
                        unit=f.get("unit", "VND"),
                        currency="VND",
                        evidence=EvidenceRef(
                            node_id=node_id,
                            line_start=line_no,
                            line_end=ev.get("line_end", line_no),
                            quote=ev.get("quote", ""),
                        ),
                    )
                )

        # 2. Corporate Ownership (Subsidiaries & Associates)
        if role_upper in {"ANNUAL_BACKBONE", "LATEST_QUARTER"}:
            sub_parser = SubsidiaryTableParser(self.issuer_ticker)
            sub_frames = sub_parser.parse_subsidiary_tables(markdown_text)
            for sf in sub_frames:
                node_id = _find_node_for_line(nodes, sf.evidence.line_start)
                child_candidate = _entity_candidate(sf.child_company)
                if child_candidate is None:
                    continue
                child_raw, child_clean = child_candidate

                # Entity mention
                child_key = fold_text(sf.child_company)
                if child_key not in seen_entities:
                    seen_entities[child_key] = child_clean
                    entity_mentions.append(
                        EntityMentionIn(
                            raw_text=child_raw,
                            canonical_name=child_clean,
                            entity_type=EntityType.SUBSIDIARY if sf.consolidation_status == "SUBSIDIARY" else EntityType.AFFILIATE,
                            evidence=EvidenceRef(
                                node_id=node_id,
                                line_start=sf.evidence.line_start,
                                line_end=sf.evidence.line_end,
                                quote=sf.evidence.quote,
                            ),
                        )
                    )

                # OWNS relation
                relations.append(
                    RelationIn(
                        subject=self.issuer_ticker,
                        object=child_clean,
                        relation_type=RelationType.OWNS,
                        flow_kind="subsidiary_shareholding",
                        ownership_pct=sf.ownership_pct if sf.ownership_pct > 0 else None,
                        amount_vnd=None,
                        evidence=EvidenceRef(
                            node_id=node_id,
                            line_start=sf.evidence.line_start,
                            line_end=sf.evidence.line_end,
                            quote=sf.evidence.quote,
                        ),
                    )
                )

            if sub_frames:
                facets.append(
                    DocumentFacetIn(
                        facet="corporate_structure",
                        confidence=1.0,
                        rationale="Extracted corporate ownership and subsidiary list.",
                        evidence=EvidenceRef(
                            node_id=_find_node_for_line(nodes, sub_frames[0].evidence.line_start),
                            line_start=sub_frames[0].evidence.line_start,
                            line_end=sub_frames[0].evidence.line_end,
                            quote=sub_frames[0].evidence.quote,
                        ),
                    )
                )

        # 3. Related Party Transactions and Balances
        if role_upper in {"ANNUAL_BACKBONE", "LATEST_QUARTER"}:
            rpt_parser = RptTableParser(self.issuer_ticker)
            rpt_frames = rpt_parser.parse_rpt_tables(markdown_text)
            for rf in rpt_frames:
                node_id = _find_node_for_line(nodes, rf.evidence.line_start)
                obj_candidate = _entity_candidate(rf.object)
                if obj_candidate is None:
                    continue
                obj_raw, obj_clean = obj_candidate

                obj_key = fold_text(rf.object)
                if obj_key not in seen_entities and len(obj_clean) >= 3:
                    seen_entities[obj_key] = obj_clean
                    entity_mentions.append(
                        EntityMentionIn(
                            raw_text=obj_raw,
                            canonical_name=obj_clean,
                            entity_type=EntityType.RELATED_PARTY,
                            evidence=EvidenceRef(
                                node_id=node_id,
                                line_start=rf.evidence.line_start,
                                line_end=rf.evidence.line_end,
                                quote=rf.evidence.quote,
                            ),
                        )
                    )

                rel_type_enum = RelationType.TRANSACTS_WITH
                try:
                    rel_type_enum = RelationType(rf.relation_type)
                except ValueError:
                    pass
                subject_candidate = _entity_candidate(rf.subject)
                relation_subject = (
                    self.issuer_ticker
                    if rf.subject == self.issuer_ticker
                    else subject_candidate[1] if subject_candidate else None
                )
                if relation_subject is None:
                    continue

                relations.append(
                    RelationIn(
                        subject=relation_subject,
                        object=obj_clean,
                        relation_type=rel_type_enum,
                        flow_kind=rf.flow_kind,
                        amount_vnd=rf.amount_vnd if rf.amount_vnd and rf.amount_vnd > 0 else None,
                        raw_label=(rf.relationship or "")[:256] or None,
                        evidence=EvidenceRef(
                            node_id=node_id,
                            line_start=rf.evidence.line_start,
                            line_end=rf.evidence.line_end,
                            quote=rf.evidence.quote,
                        ),
                    )
                )

            if rpt_frames:
                facets.append(
                    DocumentFacetIn(
                        facet="related_party",
                        confidence=1.0,
                        rationale="Extracted related party transactions and financing balances.",
                        evidence=EvidenceRef(
                            node_id=_find_node_for_line(nodes, rpt_frames[0].evidence.line_start),
                            line_start=rpt_frames[0].evidence.line_start,
                            line_end=rpt_frames[0].evidence.line_end,
                            quote=rpt_frames[0].evidence.quote,
                        ),
                    )
                )

        # 4. Footnote Risk Slots (Pledges, Guarantees, Conversions)
        if role_upper in {"ANNUAL_BACKBONE", "LATEST_QUARTER"}:
            slot_filler = FrameSlotFiller(self.issuer_ticker)
            risk_frames = slot_filler.extract_risk_frames(markdown_text)
            for rk in risk_frames:
                node_id = _find_node_for_line(nodes, rk.evidence.line_start)
                ev_ref = EvidenceRef(
                    node_id=node_id,
                    line_start=rk.evidence.line_start,
                    line_end=rk.evidence.line_end,
                    quote=rk.evidence.quote,
                )

                if isinstance(rk, PledgeCollateralFrame):
                    collateral_candidate = _entity_candidate(rk.collateral_entity)
                    if collateral_candidate is None:
                        continue
                    _, collateral_entity = collateral_candidate
                    relations.append(
                        RelationIn(
                            subject=self.issuer_ticker,
                            object=collateral_entity,
                            relation_type=RelationType.OWNS,
                            flow_kind="pledged_equity",
                            amount_vnd=rk.amount_vnd,
                            evidence=ev_ref,
                        )
                    )
                    observations.append(
                        ObservationIn(
                            statement=f"Khoản nợ của {self.issuer_ticker} được đảm bảo bằng cổ phiếu tại {rk.collateral_entity}.",
                            subject=self.issuer_ticker,
                            predicate="pledged collateral",
                            object=rk.collateral_entity,
                            topic_tags=["collateral", "pledge", "debt_security"],
                            confidence=0.95,
                            evidence=ev_ref,
                        )
                    )
                elif isinstance(rk, GuaranteeDebtFrame):
                    if rk.amount_vnd and rk.amount_vnd > 0:
                        facts.append(
                            FactIn(
                                fact_type=FactType.GUARANTEE_BALANCE,
                                label=f"Bảo lãnh liên quan đến {self.issuer_ticker}",
                                semantic_key="guarantee_balance",
                                value_numeric=rk.amount_vnd,
                                value_text=str(rk.amount_vnd),
                                unit="VND",
                                evidence=ev_ref,
                            )
                        )
                    guarantor_candidate = _entity_candidate(rk.guarantor)
                    target_candidate = _entity_candidate(
                        rk.beneficiary_debtor if rk.guarantor == self.issuer_ticker else self.issuer_ticker
                    )
                    if guarantor_candidate is None or target_candidate is None:
                        continue
                    _, guarantor_name = guarantor_candidate
                    _, target_object = target_candidate
                    flow_kind = "corporate_guarantee" if rk.guarantor == self.issuer_ticker else "personal_guarantee"
                    if guarantor_name != target_object:
                        relations.append(
                            RelationIn(
                                subject=guarantor_name,
                                object=target_object,
                                relation_type=RelationType.GUARANTEES_FOR,
                                flow_kind=flow_kind,
                                amount_vnd=rk.amount_vnd,
                                evidence=ev_ref,
                            )
                        )
                        observations.append(
                            ObservationIn(
                                statement=f"Bảo lãnh thanh toán bởi {rk.guarantor} cho nghĩa vụ nợ của {target_object}.",
                                subject=guarantor_name,
                                predicate="guarantees",
                                object=target_object,
                                topic_tags=["guarantee", "debt", "key_person"],
                                confidence=0.95,
                                evidence=ev_ref,
                            )
                        )
                elif isinstance(rk, ConversionOptionFrame):
                    observations.append(
                        ObservationIn(
                            statement=f"Trái chủ có quyền hoán đổi trái phiếu thành cổ phiếu của {rk.target_equity}.",
                            subject=self.issuer_ticker,
                            predicate="exchangeable bond",
                            object=rk.target_equity,
                            topic_tags=["exchangeable_bond", "liquidity_risk"],
                            confidence=0.95,
                            evidence=ev_ref,
                        )
                    )

            if any(isinstance(f, GuaranteeDebtFrame) for f in risk_frames):
                facets.append(
                    DocumentFacetIn(
                        facet="guarantees",
                        confidence=1.0,
                        rationale="Extracted personal and corporate guarantees.",
                        evidence=EvidenceRef(
                            node_id=_find_node_for_line(nodes, risk_frames[0].evidence.line_start),
                            line_start=risk_frames[0].evidence.line_start,
                            line_end=risk_frames[0].evidence.line_end,
                            quote=risk_frames[0].evidence.quote,
                        ),
                    )
                )

        # 5. Governance Report (Insiders & Board)
        if role_upper == "GOVERNANCE_REPORT":
            gov_parser = GovernanceTableParser(self.issuer_ticker)
            gov_frames = gov_parser.parse_governance_tables(markdown_text)
            for gf in gov_frames:
                node_id = _find_node_for_line(nodes, gf.evidence.line_start)
                person_candidate = _entity_candidate(gf.person_name)
                if person_candidate is None:
                    continue
                person_raw, person_clean = person_candidate

                person_key = fold_text(gf.person_name)
                if person_key not in seen_entities and len(person_clean) >= 3:
                    seen_entities[person_key] = person_clean
                    entity_mentions.append(
                        EntityMentionIn(
                            raw_text=person_raw,
                            canonical_name=person_clean,
                            entity_type=EntityType.PERSON,
                            evidence=EvidenceRef(
                                node_id=node_id,
                                line_start=gf.evidence.line_start,
                                line_end=gf.evidence.line_end,
                                quote=gf.evidence.quote,
                            ),
                        )
                    )

                # Shareholder/related-person tables often have no role column;
                # do not turn those rows into governance edges.
                if gf.role_position != "INSIDER":
                    relations.append(
                        RelationIn(
                            subject=person_clean,
                            object=self.issuer_ticker,
                            relation_type=RelationType.MANAGES,
                            flow_kind="governance_role",
                            evidence=EvidenceRef(
                                node_id=node_id,
                                line_start=gf.evidence.line_start,
                                line_end=gf.evidence.line_end,
                                quote=gf.evidence.quote,
                            ),
                        )
                    )

                # Family affiliation relation if present
                if gf.related_to_insider:
                    rel_person_candidate = _entity_candidate(gf.related_to_insider)
                    if rel_person_candidate:
                        _, rel_person_clean = rel_person_candidate
                        relations.append(
                            RelationIn(
                                subject=person_clean,
                                object=rel_person_clean,
                                relation_type=RelationType.AFFILIATED_WITH,
                                flow_kind="family_relationship",
                                evidence=EvidenceRef(
                                    node_id=node_id,
                                    line_start=gf.evidence.line_start,
                                    line_end=gf.evidence.line_end,
                                    quote=gf.evidence.quote,
                                ),
                            )
                        )

                # Ownership fact
                if gf.ownership_pct > 0:
                    facts.append(
                        FactIn(
                            fact_type=FactType.OWNERSHIP_BALANCE,
                            label=f"Tỷ lệ sở hữu của {person_clean}",
                            semantic_key="insider_ownership_pct",
                            value_numeric=gf.ownership_pct,
                            value_text=f"{gf.ownership_pct}%",
                            unit="%",
                            evidence=EvidenceRef(
                                node_id=node_id,
                                line_start=gf.evidence.line_start,
                                line_end=gf.evidence.line_end,
                                quote=gf.evidence.quote,
                            ),
                        )
                    )

            if gov_frames:
                facets.append(
                    DocumentFacetIn(
                        facet="ownership_structure",
                        confidence=1.0,
                        rationale="Extracted insider ownership and governance positions.",
                        evidence=EvidenceRef(
                            node_id=_find_node_for_line(nodes, gov_frames[0].evidence.line_start),
                            line_start=gov_frames[0].evidence.line_start,
                            line_end=gov_frames[0].evidence.line_end,
                            quote=gov_frames[0].evidence.quote,
                        ),
                    )
                )

        # 6. Build Node Annotations for all nodes
        node_annotations = [
            NodeAnnotationIn(
                node_id=n.node_id,
                relevance="HIGH" if any(w in fold_text(getattr(n, "title", "")) for w in ["thuyet minh", "bang can doi", "ket qua", "hoi dong quan tri"]) else "MEDIUM",
                summary=f"Section: {getattr(n, 'title', 'Document Section')}"[:200],
            )
            for n in nodes
        ]

        # The compiler is authoritative; silently slicing here would lose
        # evidence from broad documents.
        manifest = ExtractionManifestIn(
            taxonomy_version=TAXONOMY_VERSION,
            node_annotations=node_annotations,
            entity_mentions=entity_mentions,
            facts=facts,
            relations=relations,
            document_facets=facets,
            observations=observations,
        )

        return manifest
