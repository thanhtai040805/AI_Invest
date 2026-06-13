import logging

logger = logging.getLogger(__name__)

# VN-specific (VAS) accounting flags — qualitative signals, not hard US GAAP thresholds.
# Altman Z and Beneish M are computed as raw metrics for reference only;
# risk decisions use VN-specific qualitative signals.
#
# VN signals:
# - Audit opinion: qualified / adverse / disclaimer → high concern
# - Auditor change: consecutive changes → concern
# - Tax vs accounting profit gap: large gap → earnings quality concern
# - High accrual ratio → earnings manipulation flag
# - Persistent negative working capital → liquidity stress


def _vn_accounting_flags(fs: dict, fr: dict) -> list[str]:
    """Return VN-specific qualitative accounting concern flags.

    Uses Vietnamese-language keys and VAS-specific indicators.
    """
    flags: list[str] = []

    # 1. Audit opinion concern
    audit_op = _val(fs, ["audit_opinion", "ý kiến kiểm toán", "audit"])
    if audit_op:
        op_str = str(audit_op).lower()
        if any(kw in op_str for kw in ["từ chối", "disclaimer", "bác bỏ", "adverse"]):
            flags.append("AUDIT_DISCLAIMER_OR_ADVERSE")
        elif any(kw in op_str for kw in ["ngoại trừ", "qualified", "ngoai tru"]):
            flags.append("AUDIT_QUALIFIED")

    # 2. Auditor change
    auditor = _val(fs, ["auditor", "kiểm toán viên", "audit_firm"])
    prev_auditor = _val(fs, ["prev_auditor", "kiểm toán viên trước", "prev_audit_firm"])
    if auditor and prev_auditor and str(auditor) != str(prev_auditor):
        flags.append("AUDITOR_CHANGED")

    # 3. Tax vs accounting profit gap
    accounting_profit = _val(fs, ["ebit", "lợi nhuận trước thuế", "profit_before_tax"])
    taxable_income = _val(fs, ["taxable_income", "thu nhập chịu thuế", "tax_profit"])
    if accounting_profit is not None and taxable_income is not None and accounting_profit != 0:
        gap = abs(float(accounting_profit) - float(taxable_income)) / abs(float(accounting_profit))
        if gap > 0.50:
            flags.append("LARGE_TAX_ACCTG_GAP")

    # 4. Persistent negative working capital
    wc_cur = _val(fs, ["wc", "working_capital", "tài sản ngắn hạn"])
    wc_prev = _val(fs, ["wc_prev", "working_capital_prev", "tài sản ngắn hạn kỳ trước"])
    if wc_cur is not None and wc_prev is not None:
        if float(wc_cur) < 0 and float(wc_prev) < 0:
            flags.append("PERSISTENT_NEGATIVE_WC")

    return flags


def compute_fundamental_risk(
    symbols: list[str],
    fs_data: dict[str, dict],
    fr_data: dict[str, dict],
) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for sym in symbols:
        flags: list[str] = []
        risk_score = 0.0
        detail = {}

        fs = fs_data.get(sym, {})
        fr = fr_data.get(sym, {})

        # 0. VN-specific qualitative flags (P0 — replaces hard US GAAP thresholds)
        vn_flags = _vn_accounting_flags(fs, fr)
        flags.extend(vn_flags)
        if "AUDIT_DISCLAIMER_OR_ADVERSE" in vn_flags:
            risk_score += 0.40
        if "AUDIT_QUALIFIED" in vn_flags:
            risk_score += 0.20
        if "AUDITOR_CHANGED" in vn_flags:
            risk_score += 0.15
        if "LARGE_TAX_ACCTG_GAP" in vn_flags:
            risk_score += 0.20
        if "PERSISTENT_NEGATIVE_WC" in vn_flags:
            risk_score += 0.20

        # 1. Accrual ratio
        ni = _val(fs, ["ni", "net_income", "lợi nhuận sau thuế"])
        cfo = _val(fs, ["cfo", "operating_cash_flow", "lưu chuyển tiền thuần từ hoạt động kinh doanh"])
        ta = _val(fs, ["ta", "total_assets", "tổng cộng tài sản"])
        if ni is not None and cfo is not None and ta is not None and ta > 0:
            accrual = (ni - cfo) / ta
            detail["accrual_ratio"] = round(float(accrual), 4)
            if accrual > 0.20:
                risk_score += 0.35
                flags.append("HIGH_ACCRUAL")
            elif accrual > 0.10:
                risk_score += 0.15

        # 2. Altman Z' (emerging market) — reference only, not used for risk scoring
        # US GAAP thresholds do not apply to VAS; kept for reference / monitoring
        if ta is not None and ta > 0:
            wc = _val(fs, ["wc", "working_capital", "tài sản ngắn hạn"]) or 0
            re_ = _val(fs, ["re", "retained_earnings", "lợi nhuận sau thuế chưa phân phối"]) or 0
            ebit = _val(fs, ["ebit", "lợi nhuận trước thuế"]) or 0
            mcap = _val(fs, ["mcap", "market_cap", "vốn hóa"]) or 0
            liab = _val(fs, ["liabilities", "total_liabilities", "tổng nợ phải trả"]) or 0

            x1 = wc / ta
            x2 = re_ / ta
            x3 = ebit / ta
            x4 = mcap / liab if liab > 0 else 0
            altman_z = 3.25 + 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4
            detail["altman_z_prime"] = round(float(altman_z), 2)
            # No risk score from Altman Z — VAS thresholds not validated

        # 3. Leverage stress
        debt_equity = _val(fr, ["debt_equity", "debt_to_equity"])
        if debt_equity is not None:
            de = float(debt_equity)
            detail["debt_equity"] = round(de, 2)
            if de > 3.0:
                risk_score += 0.25
                flags.append("HIGH_LEVERAGE")
            elif de > 2.0:
                risk_score += 0.10

        # 4. F-Score flag (weak fundamentals)
        fscore = _val(fr, ["piotroski_f", "f_score"])
        if fscore is not None:
            detail["f_score"] = int(fscore)
            if int(fscore) < 4:
                risk_score += 0.20
                flags.append("WEAK_FSCORE")

        # 5. M-Score flag (earnings manipulation) — reference only
        mscore = _val(fr, ["m_score"])
        if mscore is not None:
            detail["m_score"] = round(float(mscore), 2)
            # No risk score from M-Score — US GAAP threshold not VN-validated

        risk_score = min(risk_score, 1.0)
        results[sym] = {"risk_score": round(risk_score, 3), "flags": flags, "detail": detail}
    return results


def _val(data: dict, keys: list[str]):
    for k in keys:
        v = data.get(k)
        if v is not None:
            return v
    return None
