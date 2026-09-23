"""Neutral forensic evidence contract used by decision agents."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

FORENSIC_CONTRACT_VERSION = "forensic-assessment.v1"


@dataclass(frozen=True)
class ForensicAssessment:
    """Normalized evidence state; downstream code never needs a SAG payload."""

    ticker: str
    contract_version: str = FORENSIC_CONTRACT_VERSION
    status: str = "NOT_ASSESSED"
    decision: str = "DEFER"
    flow_signal: str = "NOT_ASSESSED"
    risk_level: str = "UNKNOWN"
    risk_score: float | None = None
    cycles_detected: int = 0
    source: str = "FORENSIC_EVIDENCE"

    @property
    def is_closed(self) -> bool:
        return self.status == "SAG_CLOSED"

    @property
    def is_blocked(self) -> bool:
        return self.decision == "BLOCK"

    @property
    def is_trade_safe(self) -> bool:
        return self.status == "COMPLETE" and self.decision == "ALLOW"

    def allows_action(self, action: str) -> bool:
        """Allow defensive exits without making missing evidence look safe."""
        normalized = str(action or "").upper().strip()
        if normalized in {
            "SELL",
            "REDUCE",
            "CLOSE",
            "EXIT",
            "STOP",
            "STOP_LOSS",
            "EMERGENCY_STOP_LOSS",
            "TAKE_PROFIT",
            "FORCE_DOWNSIZE",
        }:
            return True
        return self.is_trade_safe

    @classmethod
    def closed(cls, ticker: str) -> "ForensicAssessment":
        return cls(ticker=str(ticker).upper().strip(), status="SAG_CLOSED")

    @classmethod
    def from_payload(cls, ticker: str, payload: Mapping[str, Any] | None) -> "ForensicAssessment":
        """Normalize current and legacy producer payloads at one boundary."""
        clean_ticker = str(ticker).upper().strip()
        data = dict(payload or {})
        raw_status = str(
            data.get("analysis_status")
            or data.get("assessment_status")
            or data.get("status")
            or ""
        ).upper().strip()
        if data.get("error_code") == "SAG_CLOSED" or raw_status == "SAG_CLOSED":
            return cls.closed(clean_ticker)

        explicit_decision = str(data.get("decision") or "").upper().strip()
        if raw_status in {"COMPLETE", "NOT_ASSESSED", "TECHNICAL_ERROR"} and explicit_decision in {
            "ALLOW", "REVIEW", "BLOCK", "DEFER",
        }:
            return cls(
                ticker=clean_ticker,
                contract_version=str(data.get("contract_version") or FORENSIC_CONTRACT_VERSION),
                status=raw_status,
                decision=explicit_decision,
                flow_signal=str(data.get("flow_signal") or "NOT_ASSESSED").upper().strip(),
                risk_level=str(data.get("risk_level") or "UNKNOWN").upper().strip(),
                risk_score=float(data["risk_score"]) if data.get("risk_score") is not None else None,
                cycles_detected=int(data.get("cycles_detected") or 0),
            )

        decision_payload = data.get("decision") if isinstance(data.get("decision"), Mapping) else {}
        flow_signal = str(data.get("flow_signal") or "NOT_ASSESSED").upper().strip()
        flag = str(data.get("gil_flag") or "").upper().strip()
        action = str(decision_payload.get("action") or explicit_decision).upper().strip()

        if decision_payload.get("trade_blocked") is True or action in {"BLOCK", "REJECT"} or flag in {"CATASTROPHIC", "DATA_ERROR"}:
            decision, status = "BLOCK", "COMPLETE"
        elif action in {"CLEAR", "ALLOW", "PASS"} or flag in {"PASS", "NORMAL", "SAFE"}:
            decision, status = "ALLOW", "COMPLETE"
        elif flag == "WARNING" or action in {"REVIEW", "ALLOW_WITH_REVIEW"}:
            decision, status = "REVIEW", "COMPLETE"
        else:
            decision, status = "DEFER", "NOT_ASSESSED"

        if raw_status in {"TECHNICAL_ERROR", "FAILED", "FALLBACK"} and decision != "BLOCK":
            decision, status = "DEFER", "TECHNICAL_ERROR"

        return cls(
            ticker=clean_ticker,
            contract_version=str(data.get("contract_version") or FORENSIC_CONTRACT_VERSION),
            status=status,
            decision=decision,
            flow_signal=flow_signal,
            risk_level=str(data.get("risk_level") or "UNKNOWN").upper().strip(),
            risk_score=float(data["risk_score"]) if data.get("risk_score") is not None else None,
            cycles_detected=int(data.get("cycles_detected") or 0),
        )

    @classmethod
    def from_inputs(cls, ticker: str, *payloads: Mapping[str, Any] | None) -> "ForensicAssessment":
        from app.config.settings import get_settings

        if get_settings().sag_analysis_hold:
            return cls.closed(ticker)

        for payload in payloads:
            if not isinstance(payload, Mapping):
                continue
            candidate = payload.get("evidence_envelope") or payload.get("forensic_assessment")
            if isinstance(candidate, Mapping):
                return cls.from_payload(ticker, candidate)
        return cls(ticker=str(ticker).upper().strip())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
