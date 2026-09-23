from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sag_api.enums import EntityType, FactType, RelationType
from sag_api.extraction.frames.base import BaseFinancialFrame, EvidenceReference, FrameRegistry


@FrameRegistry.register
@dataclass
class PledgeCollateralFrame(BaseFinancialFrame):
    """Pledge / Collateral frame: debt secured by subsidiary shares, real estate, or affiliate deposits."""
    frame_name: str = "pledge_collateral"
    debtor: str = ""
    creditor: str = ""
    facility_type: str = "BOND"  # "BOND", "BANK_LOAN"
    amount_vnd: float | None = None
    collateral_entity: str = ""  # e.g. Vinhomes, Vinpearl
    collateral_type: str = "SUBSIDIARY_SHARES"
    asset_owner: str = ""  # Issuer or Related Party
    forensic_flag: str = "CROSS_COLLATERAL_RISK"
    evidence: EvidenceReference = field(default_factory=lambda: EvidenceReference(1, 1, ""))

    def to_relations(self) -> list[dict[str, Any]]:
        relations = []
        if self.debtor and self.collateral_entity:
            relations.append({
                "subject": self.debtor,
                "object": self.collateral_entity,
                "relation_type": RelationType.OWNS.value,
                "flow_kind": "pledged_equity",
                "amount_vnd": self.amount_vnd,
                "evidence": {
                    "line_start": self.evidence.line_start,
                    "line_end": self.evidence.line_end,
                    "quote": self.evidence.quote,
                    "node_id": self.evidence.node_id,
                },
                "metadata_json": {
                    "frame": self.frame_name,
                    "forensic_flag": self.forensic_flag,
                    "creditor": self.creditor,
                },
            })
        return relations

    def to_facts(self) -> list[dict[str, Any]]:
        return []

    def to_observations(self) -> list[dict[str, Any]]:
        statement = (
            f"Khoản nợ/trái phiếu của {self.debtor} được đảm bảo bằng {self.collateral_type.lower()} "
            f"tại {self.collateral_entity}."
        )
        return [{
            "statement": statement,
            "topic_tags": ["collateral", "pledge", "debt_security", self.forensic_flag.lower()],
            "confidence": 0.95,
            "evidence": {
                "line_start": self.evidence.line_start,
                "line_end": self.evidence.line_end,
                "quote": self.evidence.quote,
                "node_id": self.evidence.node_id,
            },
        }]


@FrameRegistry.register
@dataclass
class GuaranteeDebtFrame(BaseFinancialFrame):
    """Debt Guarantee frame: debt guaranteed by Chairman, related parties, or affiliates."""
    frame_name: str = "guarantee_debt"
    guarantor: str = ""
    guarantor_type: str = "INSIDER_PERSON"  # "INSIDER_PERSON", "AFFILIATE", "THIRD_PARTY"
    beneficiary_debtor: str = ""
    creditor: str = ""
    amount_vnd: float | None = None
    forensic_flag: str = "KEY_PERSON_DEPENDENCY"
    evidence: EvidenceReference = field(default_factory=lambda: EvidenceReference(1, 1, ""))

    def to_relations(self) -> list[dict[str, Any]]:
        relations = []
        if self.guarantor and self.beneficiary_debtor:
            relations.append({
                "subject": self.guarantor,
                "object": self.beneficiary_debtor,
                "relation_type": RelationType.GUARANTEES_FOR.value,
                "flow_kind": "guarantee",
                "amount_vnd": self.amount_vnd,
                "evidence": {
                    "line_start": self.evidence.line_start,
                    "line_end": self.evidence.line_end,
                    "quote": self.evidence.quote,
                    "node_id": self.evidence.node_id,
                },
                "metadata_json": {
                    "frame": self.frame_name,
                    "forensic_flag": self.forensic_flag,
                    "creditor": self.creditor,
                },
            })
        return relations

    def to_facts(self) -> list[dict[str, Any]]:
        return []

    def to_observations(self) -> list[dict[str, Any]]:
        statement = f"Bảo lãnh thanh toán bởi {self.guarantor} cho nghĩa vụ nợ của {self.beneficiary_debtor}."
        return [{
            "statement": statement,
            "topic_tags": ["guarantee", "debt", self.forensic_flag.lower()],
            "confidence": 0.95,
            "evidence": {
                "line_start": self.evidence.line_start,
                "line_end": self.evidence.line_end,
                "quote": self.evidence.quote,
                "node_id": self.evidence.node_id,
            },
        }]


@FrameRegistry.register
@dataclass
class ConversionOptionFrame(BaseFinancialFrame):
    """Convertible / Exchangeable bond option frame: option over subsidiary shares or early redemption."""
    frame_name: str = "conversion_option"
    issuer: str = ""
    holder_group: str = "Trái chủ"
    target_equity: str = ""  # e.g. Vinhomes
    exercise_deadline: str = ""  # e.g. 11/2026
    early_redemption_triggered: bool = False
    cash_drained_vnd: float | None = None
    evidence: EvidenceReference = field(default_factory=lambda: EvidenceReference(1, 1, ""))

    def to_relations(self) -> list[dict[str, Any]]:
        return []

    def to_facts(self) -> list[dict[str, Any]]:
        return []

    def to_observations(self) -> list[dict[str, Any]]:
        statement = (
            f"Trái chủ có quyền hoán đổi trái phiếu thành cổ phiếu của {self.target_equity} "
            f"hoặc yêu cầu mua lại trước hạn vào {self.exercise_deadline}."
        )
        return [{
            "statement": statement,
            "topic_tags": ["exchangeable_bond", "liquidity_risk", "contingent_dilution"],
            "confidence": 0.90,
            "evidence": {
                "line_start": self.evidence.line_start,
                "line_end": self.evidence.line_end,
                "quote": self.evidence.quote,
                "node_id": self.evidence.node_id,
            },
        }]


@FrameRegistry.register
@dataclass
class RelatedPartyFlowFrame(BaseFinancialFrame):
    """Related party transactions and balances frame: loans, borrowings, receivables, payables, capital."""
    frame_name: str = "related_party_flow"
    subject: str = ""
    object: str = ""
    relationship: str = "SUBSIDIARY"
    relation_type: str = "LOANS_TO"  # LOANS_TO, BORROWS_FROM, RECEIVABLE_FROM, PAYABLE_TO, INVESTS_IN, TRANSACTS_WITH
    flow_kind: str = "loan"
    amount_vnd: float = 0.0
    period_type: str = "BALANCE"  # "BALANCE" or "FLOW"
    transaction_description: str = ""
    interest_rate: str | None = None
    maturity_date: str | None = None
    collateral_note: str | None = None
    evidence: EvidenceReference = field(default_factory=lambda: EvidenceReference(1, 1, ""))

    def to_relations(self) -> list[dict[str, Any]]:
        return [{
            "subject": self.subject,
            "object": self.object,
            "relation_type": self.relation_type,
            "flow_kind": self.flow_kind,
            "amount_vnd": self.amount_vnd,
            "evidence": {
                "line_start": self.evidence.line_start,
                "line_end": self.evidence.line_end,
                "quote": self.evidence.quote,
                "node_id": self.evidence.node_id,
            },
            "metadata_json": {
                "frame": self.frame_name,
                "relationship": self.relationship,
                "period_type": self.period_type,
                "transaction_description": self.transaction_description,
                "interest_rate": self.interest_rate,
                "maturity_date": self.maturity_date,
                "collateral_note": self.collateral_note,
            },
        }]

    def to_facts(self) -> list[dict[str, Any]]:
        return []

    def to_observations(self) -> list[dict[str, Any]]:
        return []


@FrameRegistry.register
@dataclass
class CorporateOwnershipFrame(BaseFinancialFrame):
    """Subsidiaries and associates corporate ownership structure frame."""
    frame_name: str = "corporate_ownership"
    parent_company: str = ""
    child_company: str = ""
    ownership_pct: float = 0.0
    voting_power_pct: float = 0.0
    consolidation_status: str = "SUBSIDIARY"
    evidence: EvidenceReference = field(default_factory=lambda: EvidenceReference(1, 1, ""))

    def to_relations(self) -> list[dict[str, Any]]:
        return [{
            "subject": self.parent_company,
            "object": self.child_company,
            "relation_type": RelationType.OWNS.value,
            "flow_kind": "subsidiary_shareholding",
            "ownership_pct": self.ownership_pct,
            "amount_vnd": 0.0,
            "evidence": {
                "line_start": self.evidence.line_start,
                "line_end": self.evidence.line_end,
                "quote": self.evidence.quote,
                "node_id": self.evidence.node_id,
            },
            "metadata_json": {
                "frame": self.frame_name,
                "voting_power_pct": self.voting_power_pct,
                "consolidation_status": self.consolidation_status,
            },
        }]

    def to_facts(self) -> list[dict[str, Any]]:
        return []

    def to_observations(self) -> list[dict[str, Any]]:
        return []


@FrameRegistry.register
@dataclass
class GovernanceInsiderFrame(BaseFinancialFrame):
    """Corporate governance insider frame: BOD, Supervisors, share transactions."""
    frame_name: str = "governance_insider"
    person_name: str = ""
    issuer_ticker: str = ""
    role_position: str = "BOD_MEMBER"
    related_to_insider: str | None = None
    relationship_kind: str | None = None
    shares_held: int = 0
    ownership_pct: float = 0.0
    shares_traded: int | None = None
    evidence: EvidenceReference = field(default_factory=lambda: EvidenceReference(1, 1, ""))

    def to_relations(self) -> list[dict[str, Any]]:
        relations = []
        if self.person_name and self.issuer_ticker and self.role_position != "INSIDER":
            relations.append({
                "subject": self.person_name,
                "object": self.issuer_ticker,
                "relation_type": RelationType.MANAGES.value,
                "flow_kind": "governance_role",
                "evidence": {
                    "line_start": self.evidence.line_start,
                    "line_end": self.evidence.line_end,
                    "quote": self.evidence.quote,
                    "node_id": self.evidence.node_id,
                },
                "metadata_json": {
                    "frame": self.frame_name,
                    "role_position": self.role_position,
                },
            })
        if self.person_name and self.related_to_insider:
            relations.append({
                "subject": self.person_name,
                "object": self.related_to_insider,
                "relation_type": RelationType.AFFILIATED_WITH.value,
                "flow_kind": "family_relationship",
                "evidence": {
                    "line_start": self.evidence.line_start,
                    "line_end": self.evidence.line_end,
                    "quote": self.evidence.quote,
                    "node_id": self.evidence.node_id,
                },
                "metadata_json": {
                    "frame": self.frame_name,
                    "relationship_kind": self.relationship_kind,
                },
            })
        return relations

    def to_facts(self) -> list[dict[str, Any]]:
        facts = []
        if self.ownership_pct > 0:
            facts.append({
                "fact_type": FactType.OWNERSHIP_BALANCE.value,
                "label": f"Tỷ lệ sở hữu của {self.person_name}",
                "semantic_key": "insider_ownership_pct",
                "value_numeric": self.ownership_pct,
                "unit": "%",
                "evidence": {
                    "line_start": self.evidence.line_start,
                    "line_end": self.evidence.line_end,
                    "quote": self.evidence.quote,
                    "node_id": self.evidence.node_id,
                },
                "metadata_json": {
                    "frame": self.frame_name,
                    "shares_held": self.shares_held,
                },
            })
        return facts

    def to_observations(self) -> list[dict[str, Any]]:
        return []
