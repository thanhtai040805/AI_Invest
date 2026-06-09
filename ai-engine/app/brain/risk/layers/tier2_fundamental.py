import logging

logger = logging.getLogger(__name__)


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

        # 2. Altman Z' (emerging market)
        if ta is not None and ta > 0:
            wc = _val(fs, ["wc", "working_capital", "tài sản ngắn hạn"]) or 0
            re_ = _val(fs, ["re", "retained_earnings", "lợi nhuận sau thuế chưa phân phối"]) or 0
            ebit = _val(fs, ["ebit", "lợi nhuận trước thuế"]) or 0
            mcap = _val(fs, ["mcap", "market_cap", "vốn hóa"]) or 0
            liab = _val(fs, ["liabilities", "total_liabilities", "tổng nợ phải trả"]) or 0
            rev = _val(fs, ["revenue", "doanh thu thuần"]) or 0

            x1 = wc / ta
            x2 = re_ / ta
            x3 = ebit / ta
            x4 = mcap / liab if liab > 0 else 0
            x5 = rev / ta
            altman_z = 3.25 + 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4
            detail["altman_z_prime"] = round(float(altman_z), 2)
            if altman_z < 1.1:
                risk_score += 0.30
                flags.append("ALTMAN_Z_DISTRESS")
            elif altman_z < 2.6:
                risk_score += 0.15
                flags.append("ALTMAN_Z_GREY")

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

        # 5. M-Score flag (earnings manipulation)
        mscore = _val(fr, ["m_score"])
        if mscore is not None:
            detail["m_score"] = round(float(mscore), 2)
            if float(mscore) > -2.22:
                risk_score += 0.25
                flags.append("M_SCORE_RISK")

        risk_score = min(risk_score, 1.0)
        results[sym] = {"risk_score": round(risk_score, 3), "flags": flags, "detail": detail}
    return results


def _val(data: dict, keys: list[str]):
    for k in keys:
        v = data.get(k)
        if v is not None:
            return v
    return None
