from __future__ import annotations

import re
from typing import Any

from sag_api.enums import RelationType
from sag_api.extraction.anchors.discourse_resolver import DiscourseResolver
from sag_api.extraction.frames.base import EvidenceReference
from sag_api.extraction.frames.gil_frames import (
    ConversionOptionFrame,
    GuaranteeDebtFrame,
    PledgeCollateralFrame,
)
from sag_api.extraction.parsers.accounting_taxonomy import fold_text, parse_vn_number


def _guarantee_amount_vnd(text: str) -> float | None:
    match = re.search(r"(\d[\d.,]*)\s*(nghin ty|ty|trieu|billion|million|dong|vnd)", text)
    if not match:
        return None
    value = parse_vn_number(match.group(1))
    if value is None:
        return None
    unit = match.group(2)
    multiplier = 1_000_000_000 if unit in {"ty", "billion"} else 1_000_000 if unit in {"trieu", "million"} else 1_000 if unit == "nghin ty" else 1.0
    return value * multiplier


class FrameSlotFiller:
    """Targeted slot filler for debt pledges, guarantees, and convertible securities in notes."""

    def __init__(self, issuer_ticker: str = "") -> None:
        self.issuer_ticker = issuer_ticker.upper().strip()

    def extract_risk_frames(
        self, markdown_text: str
    ) -> list[PledgeCollateralFrame | GuaranteeDebtFrame | ConversionOptionFrame]:
        """Extract debt collateral, personal guarantees, and conversion option frames from narrative & footnotes."""
        frames: list[PledgeCollateralFrame | GuaranteeDebtFrame | ConversionOptionFrame] = []
        lines = markdown_text.splitlines()

        resolver = DiscourseResolver(markdown_text)
        footnotes = resolver.find_footnotes_in_range(1, len(lines))

        # 1. Scan footnotes
        for marker, fn in footnotes.items():
            fn_text = fn.text
            folded_fn = fold_text(fn_text)

            # Check Pledge of Subsidiary Shares
            has_collateral = any(w in folded_fn for w in ("dam bao", "the chap", "cam co"))
            has_sub_equity = any(w in folded_fn for w in ("co phieu", "co phan", "phan von gop")) and any(w in folded_fn for w in ("cong ty con", "don vi thanh vien"))
            if has_collateral and has_sub_equity:
                # Target company
                sub_match = re.search(r"(?:trong|cua)\s+(Công ty [A-Za-z0-9\s]+?)(?:,|\.|\)|;|\svới|\stheo)", fn_text, re.I)
                collateral_entity = sub_match.group(1).strip() if sub_match else "Công ty con"
                frames.append(
                    PledgeCollateralFrame(
                        debtor=self.issuer_ticker or "ISSUER",
                        creditor="Trái chủ / Ngân hàng",
                        facility_type="BOND",
                        collateral_entity=collateral_entity,
                        collateral_type="SUBSIDIARY_SHARES",
                        forensic_flag="CROSS_COLLATERAL_RISK",
                        evidence=EvidenceReference(
                            line_start=fn.line_no,
                            line_end=fn.line_no,
                            quote=fn_text,
                        ),
                    )
                )

            # Check Personal Guarantee
            if "bao lanh thanh toan cua chu tich" in folded_fn or ("bao lanh" in folded_fn and "chu tich hoi dong quan tri" in folded_fn):
                frames.append(
                    GuaranteeDebtFrame(
                        beneficiary_debtor=self.issuer_ticker or "ISSUER",
                        guarantor="Chủ tịch Hội đồng Quản trị",
                        creditor="Trái chủ / Ngân hàng",
                        amount_vnd=_guarantee_amount_vnd(folded_fn),
                        forensic_flag="EXECUTIVE_PERSONAL_GUARANTEE",
                        evidence=EvidenceReference(
                            line_start=fn.line_no,
                            line_end=fn.line_no,
                                quote=fn_text,
                        ),
                    )
                )

            # Check Conversion Option into Subsidiary Shares
            if "quyen chuyen doi" in folded_fn and any(w in folded_fn for w in ("trai phieu", "co phieu", "co phan")):
                sub_match = re.search(r"cổ phiếu phổ thông của\s+(Công ty [A-Za-z0-9\s]+?)(?:,|\.|\)|;|\smột công ty con)", fn_text, re.I)
                target_sub = sub_match.group(1).strip() if sub_match else "Công ty con"
                year_match = re.search(r"\b(20\d{2}(?:\s*[-/]\s*20\d{2})?)\b", fn_text)
                exercise_deadline = year_match.group(1).replace(" ", "") if year_match else None
                frames.append(
                    ConversionOptionFrame(
                        issuer=self.issuer_ticker or "ISSUER",
                        holder_group="Trái chủ",
                        target_equity=target_sub,
                        exercise_deadline=exercise_deadline,
                        evidence=EvidenceReference(
                            line_start=fn.line_no,
                            line_end=fn.line_no,
                            quote=fn_text,
                        ),
                    )
                )

        # 2. Scan text lines directly (for non-footnote bullet lists, e.g. lines 1385-1392 in VIC)
        for line_idx, line in enumerate(lines, 1):
            trimmed = line.strip()
            # Table rows can carry the same guarantee disclosure as prose.
            # Keep headings out, but do not discard tables before applying the
            # explicit guarantee predicates below.
            if not trimmed or trimmed.startswith("#"):
                continue
            folded = fold_text(trimmed)

            # Personal Guarantee in bullet lines
            if "bao lanh thanh toan cua chu tich" in folded or ("bao lanh" in folded and "chu tich hoi dong quan tri" in folded):
                # Avoid duplicates
                if not any(isinstance(f, GuaranteeDebtFrame) and abs(f.evidence.line_start - line_idx) < 10 for f in frames):
                    frames.append(
                        GuaranteeDebtFrame(
                            beneficiary_debtor=self.issuer_ticker or "ISSUER",
                            guarantor="Chủ tịch Hội đồng Quản trị",
                            creditor="Trái chủ / Ngân hàng",
                            amount_vnd=_guarantee_amount_vnd(folded),
                            forensic_flag="EXECUTIVE_PERSONAL_GUARANTEE",
                            evidence=EvidenceReference(
                                line_start=line_idx,
                                line_end=line_idx,
                                quote=trimmed,
                            ),
                        )
                    )

            # Aggregate corporate guarantee disclosures: require explicit direction
            is_outgoing_guarantee = "bao lanh" in folded and any(
                term in folded
                for term in (
                    "bao lanh cho",
                    "bao lanh thanh toan cho",
                    "bao lanh nghia vu",
                    "bao lanh cac khoan vay cua",
                    "bao lanh thuc hien hop dong cho",
                    "cam ket bao lanh vo dieu kien",
                )
            )
            # Exclude self-loan bank facility descriptions (e.g. "Các khoản vay BIDV theo hợp đồng hạn mức tín dụng")
            is_self_bank_facility = any(
                term in folded for term in ("cac khoan vay ngan hang", "hop dong han muc tin dung")
            ) and not any(term in folded for term in ("bao lanh cho", "bao lanh khoan vay cua cong ty con"))

            if is_outgoing_guarantee and not is_self_bank_facility:
                if not any(isinstance(f, GuaranteeDebtFrame) and f.evidence.line_start == line_idx for f in frames):
                    frames.append(
                        GuaranteeDebtFrame(
                            beneficiary_debtor="Công ty con / bên liên quan",
                            guarantor=self.issuer_ticker or "ISSUER",
                            guarantor_type="CORPORATE",
                            creditor="Ngân hàng / Trái chủ",
                            amount_vnd=_guarantee_amount_vnd(folded),
                            forensic_flag="CORPORATE_GUARANTEE_DISCLOSURE",
                            evidence=EvidenceReference(
                                line_start=line_idx,
                                line_end=line_idx,
                                quote=trimmed,
                            ),
                        )
                    )

            # Subsidiary shares pledge in bullet lines
            has_sub_equity_bullet = any(w in folded for w in ("co phieu", "co phan", "phan von gop")) and any(w in folded for w in ("cong ty con", "don vi thanh vien"))
            has_pledge_bullet = any(w in folded for w in ("dam bao", "the chap", "cam co", "nam giu boi"))
            if has_sub_equity_bullet and has_pledge_bullet:
                if not any(isinstance(f, PledgeCollateralFrame) and abs(f.evidence.line_start - line_idx) < 10 for f in frames):
                    frames.append(
                        PledgeCollateralFrame(
                            debtor=self.issuer_ticker or "ISSUER",
                            creditor="Trái chủ / Ngân hàng",
                            facility_type="BOND",
                            collateral_entity="Công ty con",
                            collateral_type="SUBSIDIARY_SHARES",
                            forensic_flag="CROSS_COLLATERAL_RISK",
                            evidence=EvidenceReference(
                                line_start=line_idx,
                                line_end=line_idx,
                                quote=trimmed,
                            ),
                        )
                    )

        return frames
