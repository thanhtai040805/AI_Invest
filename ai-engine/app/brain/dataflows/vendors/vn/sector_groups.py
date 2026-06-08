"""
Sector Group Classification — 3 optimized groups based on NEU 2017 doctoral research.

Maps Vietnamese industry names from DB → FINANCIALS / REAL_ESTATE_CONSTRUCTION / OTHERS.

References:
  - PGS.TS. Trần Hùng Thao & NCS. Phạm Lệ Mỹ (NEU 2017): "Mức độ phụ thuộc vào nhân tố
    thị trường của nhóm Tài chính, Ngân hàng và Bảo hiểm là cao nhất. Mức độ phụ thuộc
    ngành của nhóm Bất động sản và Xây dựng là cao nhất."
  - Hair et al. (1998): each group needs n ≥ 30 for statistical power
  - Tabachnick & Fidell (1996): n ≥ 5×m (m = observed variables)
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

FINANCIALS = "FINANCIALS"
REAL_ESTATE = "REAL_ESTATE_CONSTRUCTION"
OTHERS = "OTHERS"

SECTOR_GROUPS = {FINANCIALS, REAL_ESTATE, OTHERS}

# Full Vietnamese → group mapping (optimized for VN market)
INDUSTRY_MAP = {
    # ── FINANCIALS ──────────────────────────────────────────────
    "ngân hàng":       FINANCIALS,
    "ngan hang":       FINANCIALS,
    "bảo hiểm":        FINANCIALS,
    "bao hiem":        FINANCIALS,
    "chứng khoán":     FINANCIALS,
    "chung khoan":     FINANCIALS,
    "dịch vụ tài chính":  FINANCIALS,
    "dich vu tai chinh":  FINANCIALS,
    "tài chính khác":  FINANCIALS,
    "tai chinh khac":  FINANCIALS,
    "tài chính":       FINANCIALS,
    "tai chinh":       FINANCIALS,
    "financial services": FINANCIALS,

    # ── REAL ESTATE + CONSTRUCTION ─────────────────────────────
    "bất động sản":            REAL_ESTATE,
    "bat dong san":            REAL_ESTATE,
    "bất động sản (trừ dịch vụ)": REAL_ESTATE,
    "dịch vụ bất động sản":    REAL_ESTATE,
    "dich vu bat dong san":    REAL_ESTATE,
    "xây dựng":               REAL_ESTATE,
    "xay dung":               REAL_ESTATE,
    "vật liệu xây dựng":      REAL_ESTATE,
    "vat lieu xay dung":      REAL_ESTATE,
    "xd":                     REAL_ESTATE,
    "construction":           REAL_ESTATE,
    "real estate":            REAL_ESTATE,
}

# Symbol-level overrides (when industry is empty or misclassified)
SYMBOL_OVERRIDES: dict[str, str] = {
    # Banks
    "ACB": FINANCIALS, "BAB": FINANCIALS, "BID": FINANCIALS, "CTG": FINANCIALS,
    "EIB": FINANCIALS, "HDB": FINANCIALS, "KLB": FINANCIALS, "LPB": FINANCIALS,
    "MBB": FINANCIALS, "MSB": FINANCIALS, "NAB": FINANCIALS, "NAM": FINANCIALS,
    "NCB": FINANCIALS, "NVB": FINANCIALS, "OCB": FINANCIALS, "PGB": FINANCIALS,
    "PVF": FINANCIALS, "SGB": FINANCIALS, "SHB": FINANCIALS, "SSB": FINANCIALS,
    "STB": FINANCIALS, "TCB": FINANCIALS, "TPB": FINANCIALS, "VAB": FINANCIALS,
    "VBB": FINANCIALS, "VCB": FINANCIALS, "VIB": FINANCIALS, "VPB": FINANCIALS,
    # Insurance
    "BMI": FINANCIALS, "BVH": FINANCIALS, "MIG": FINANCIALS, "PTI": FINANCIALS,
    "PVI": FINANCIALS, "AIC": FINANCIALS, "BIC": FINANCIALS, "BLI": FINANCIALS,
    "DNM": FINANCIALS, "PRE": FINANCIALS, "SAB": FINANCIALS,
    "VNR": FINANCIALS,
    # Securities
    "AGR": FINANCIALS, "APG": FINANCIALS, "ART": FINANCIALS, "BMS": FINANCIALS,
    "BSI": FINANCIALS, "CTS": FINANCIALS, "DSC": FINANCIALS, "EVS": FINANCIALS,
    "FTS": FINANCIALS, "HBS": FINANCIALS, "HCM": FINANCIALS, "IVS": FINANCIALS,
    "MBS": FINANCIALS, "ORS": FINANCIALS, "PHS": FINANCIALS, "PIV": FINANCIALS,
    "PSI": FINANCIALS, "SBS": FINANCIALS, "SHS": FINANCIALS, "SSI": FINANCIALS,
    "TCI": FINANCIALS, "TVC": FINANCIALS, "TVB": FINANCIALS, "VCI": FINANCIALS,
    "VDS": FINANCIALS, "VFS": FINANCIALS, "VIX": FINANCIALS, "WSS": FINANCIALS,
    # Other financial
    "TIN": FINANCIALS, "VNF": FINANCIALS, "WTC": FINANCIALS,
    # Real Estate
    "AAM": REAL_ESTATE, "AGG": REAL_ESTATE, "ALS": REAL_ESTATE,
    "API": REAL_ESTATE, "BCR": REAL_ESTATE, "BVL": REAL_ESTATE,
    "C21": REAL_ESTATE, "CCI": REAL_ESTATE, "CIG": REAL_ESTATE,
    "CLG": REAL_ESTATE, "CRE": REAL_ESTATE, "CSN": REAL_ESTATE,
    "D2D": REAL_ESTATE, "DIG": REAL_ESTATE, "DTA": REAL_ESTATE,
    "DXG": REAL_ESTATE, "DXS": REAL_ESTATE, "DXV": REAL_ESTATE,
    "E1VFVN30": REAL_ESTATE, "EIB": REAL_ESTATE,
    "FDC": REAL_ESTATE, "FIR": REAL_ESTATE, "FIT": REAL_ESTATE,
    "HDC": REAL_ESTATE, "HAR": REAL_ESTATE, "HQC": REAL_ESTATE,
    "HSG": REAL_ESTATE, "HTN": REAL_ESTATE, "IDC": REAL_ESTATE,
    "IDJ": REAL_ESTATE, "IJC": REAL_ESTATE, "ITC": REAL_ESTATE,
    "KAC": REAL_ESTATE, "KBC": REAL_ESTATE, "KDH": REAL_ESTATE,
    "KOS": REAL_ESTATE, "LDG": REAL_ESTATE, "LGL": REAL_ESTATE,
    "LHC": REAL_ESTATE, "LHG": REAL_ESTATE, "LIX": REAL_ESTATE,
    "L45": REAL_ESTATE, "MHL": REAL_ESTATE, "MIG": REAL_ESTATE,
    "MTH": REAL_ESTATE, "NLG": REAL_ESTATE, "NRC": REAL_ESTATE,
    "NTL": REAL_ESTATE, "NVL": REAL_ESTATE, "NVT": REAL_ESTATE,
    "PDR": REAL_ESTATE, "PFL": REAL_ESTATE, "PIV": REAL_ESTATE,
    "PTC": REAL_ESTATE, "PTL": REAL_ESTATE, "QCG": REAL_ESTATE,
    "RCL": REAL_ESTATE, "SCR": REAL_ESTATE, "SGR": REAL_ESTATE,
    "SJS": REAL_ESTATE, "SMC": REAL_ESTATE, "SNZ": REAL_ESTATE,
    "SRA": REAL_ESTATE, "SZL": REAL_ESTATE, "TBC": REAL_ESTATE,
    "TCH": REAL_ESTATE, "TDC": REAL_ESTATE, "TEG": REAL_ESTATE,
    "TIX": REAL_ESTATE, "TLC": REAL_ESTATE, "TND": REAL_ESTATE,
    "TNP": REAL_ESTATE, "TNT": REAL_ESTATE, "TSC": REAL_ESTATE,
    "TYD": REAL_ESTATE, "UDC": REAL_ESTATE, "VHM": REAL_ESTATE,
    "VIC": REAL_ESTATE, "VLC": REAL_ESTATE, "VPH": REAL_ESTATE,
    "VPY": REAL_ESTATE, "VRC": REAL_ESTATE, "VRE": REAL_ESTATE,
}


def classify(industry: Optional[str], symbol: str) -> str:
    """Classify a stock into one of 3 sector groups.

    Priority:
      1. Symbol-level override (for empty/misclassified industries)
      2. Industry name match (case-insensitive)
      3. Default: OTHERS
    """
    # 1. Symbol override
    if symbol in SYMBOL_OVERRIDES:
        return SYMBOL_OVERRIDES[symbol]

    # 2. Industry name match
    if industry:
        ind_lower = industry.strip().lower()
        for keyword, group in INDUSTRY_MAP.items():
            if keyword in ind_lower:
                return group

    # 3. Default
    return OTHERS


def get_group_symbols(cur) -> tuple[list[str], list[str], list[str]]:
    """Get symbol lists for each sector group from the stocks table.

    Returns:
        (fin_symbols, re_symbols, other_symbols)
    """
    cur.execute("SELECT symbol, industry FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol")
    rows = cur.fetchall()

    fin, re, other = [], [], []
    for sym, ind in rows:
        group = classify(ind, sym)
        if group == FINANCIALS:
            fin.append(sym)
        elif group == REAL_ESTATE:
            re.append(sym)
        else:
            other.append(sym)

    return fin, re, other
