"""Sector Group Classification — 16 ICB Level-2 groups for VN market.

Based on actual DB industry names from stocks table (398 symbols).
Three-tier fallback:
  - classify_icb(): returns 16-group ICB code
  - classify_major(): returns 3-group (FINANCIALS / REAL_ESTATE / OTHERS) for backward compat
  - classify_safe(): classifies with auto-fallback to OTHER_INDUSTRIALS if n<4 in eval context

Merge policy:
  - NEVER merge CONSTRUCTION into REAL_ESTATE (asset-light vs asset-heavy)
  - Sectors with <4 symbols auto-downgrade to OTHER_INDUSTRIALS
"""

from typing import Optional

# ── 16 ICB Sector Groups ─────────────────────────────────────────────
BANKS                  = "BANKS"
FINANCIAL_SERVICES     = "FINANCIAL_SERVICES"
REAL_ESTATE            = "REAL_ESTATE"
CONSTRUCTION           = "CONSTRUCTION"
CONSTRUCTION_MATERIALS = "CONSTRUCTION_MATERIALS"
BASIC_RESOURCES        = "BASIC_RESOURCES"
CHEMICALS              = "CHEMICALS"
OIL_GAS                = "OIL_GAS"
FOOD_BEVERAGE          = "FOOD_BEVERAGE"
TECHNOLOGY             = "TECHNOLOGY"
INDUSTRIAL_GOODS       = "INDUSTRIAL_GOODS"
TRANSPORTATION         = "TRANSPORTATION"
RETAIL_TRADE           = "RETAIL_TRADE"
HEALTHCARE             = "HEALTHCARE"
UTILITIES              = "UTILITIES"
AGRICULTURE            = "AGRICULTURE"
OTHER_INDUSTRIALS      = "OTHER_INDUSTRIALS"

# ── Backward-compat aliases ─────────────────────────────────────────
FINANCIALS = "FINANCIALS"
OTHERS     = "OTHERS"

ICB_SECTORS = {
    BANKS, FINANCIAL_SERVICES, REAL_ESTATE, CONSTRUCTION,
    CONSTRUCTION_MATERIALS, BASIC_RESOURCES, CHEMICALS, OIL_GAS,
    FOOD_BEVERAGE, TECHNOLOGY, INDUSTRIAL_GOODS, TRANSPORTATION,
    RETAIL_TRADE, HEALTHCARE, UTILITIES, AGRICULTURE,
    OTHER_INDUSTRIALS,
}

# ── Vietnamese industry → ICB group mapping (keyword substring match) ──
INDUSTRY_MAP: dict[str, str] = {
    # BANKS
    "ngân hàng":         BANKS,
    "ngan hang":         BANKS,

    # FINANCIAL_SERVICES
    "chứng khoán":       FINANCIAL_SERVICES,
    "chung khoan":       FINANCIAL_SERVICES,
    "bảo hiểm":          FINANCIAL_SERVICES,
    "bao hiem":          FINANCIAL_SERVICES,
    "dịch vụ tài chính": FINANCIAL_SERVICES,
    "dich vu tai chinh": FINANCIAL_SERVICES,
    "tài chính khác":    FINANCIAL_SERVICES,
    "tai chinh khac":    FINANCIAL_SERVICES,
    "tài chính":         FINANCIAL_SERVICES,
    "tai chinh":         FINANCIAL_SERVICES,

    # REAL_ESTATE
    "bất động sản":      REAL_ESTATE,
    "bat dong san":      REAL_ESTATE,

    # CONSTRUCTION_MATERIALS — must come BEFORE CONSTRUCTION
    # because "vật liệu xây dựng" contains "xây dựng" as substring
    "vật liệu xây dựng": CONSTRUCTION_MATERIALS,
    "vat lieu xay dung": CONSTRUCTION_MATERIALS,

    # CONSTRUCTION
    "xây dựng":          CONSTRUCTION,
    "xay dung":          CONSTRUCTION,
    "xd":                CONSTRUCTION,

    # BASIC_RESOURCES
    "khai khoáng":         BASIC_RESOURCES,
    "khai khoang":         BASIC_RESOURCES,
    "sản phẩm cao su":     BASIC_RESOURCES,
    "san pham cao su":     BASIC_RESOURCES,
    "cao su":              BASIC_RESOURCES,
    "thép":                BASIC_RESOURCES,
    "thep":                BASIC_RESOURCES,
    "tài nguyên":          BASIC_RESOURCES,
    "tai nguyen":          BASIC_RESOURCES,

    # CHEMICALS
    "sx nhựa - hóa chất": CHEMICALS,
    "nhựa - hóa chất":    CHEMICALS,
    "nhua - hoa chat":    CHEMICALS,
    "phân bón":           CHEMICALS,
    "phan bon":           CHEMICALS,
    "hóa chất":           CHEMICALS,
    "hoa chat":           CHEMICALS,

    # OIL_GAS
    "dầu khí":            OIL_GAS,
    "dau khi":            OIL_GAS,
    "lọc hóa dầu":        OIL_GAS,

    # FOOD_BEVERAGE
    "thực phẩm":           FOOD_BEVERAGE,
    "thuc pham":           FOOD_BEVERAGE,
    "đồ uống":             FOOD_BEVERAGE,
    "do uong":             FOOD_BEVERAGE,
    "thực phẩm - đồ uống": FOOD_BEVERAGE,

    # TECHNOLOGY
    "công nghệ":            TECHNOLOGY,
    "cong nghe":            TECHNOLOGY,
    "công nghệ và thông tin": TECHNOLOGY,
    "media":                TECHNOLOGY,
    "phần mềm":             TECHNOLOGY,

    # INDUSTRIAL_GOODS
    "sx hàng gia dụng":      INDUSTRIAL_GOODS,
    "sx phụ trợ":            INDUSTRIAL_GOODS,
    "sx thiết bị, máy móc":  INDUSTRIAL_GOODS,
    "thiết bị điện":         INDUSTRIAL_GOODS,
    "thiết bị":              INDUSTRIAL_GOODS,
    "sản xuất":              INDUSTRIAL_GOODS,
    "hàng gia dụng":         INDUSTRIAL_GOODS,
    "personal & household":  INDUSTRIAL_GOODS,
    "dịch vụ tư vấn":        INDUSTRIAL_GOODS,
    "dịch vụ hỗ trợ":        INDUSTRIAL_GOODS,

    # TRANSPORTATION
    "vận tải":                TRANSPORTATION,
    "van tai":                TRANSPORTATION,
    "kho bãi":                TRANSPORTATION,
    "kho bai":                TRANSPORTATION,

    # RETAIL_TRADE
    "bán lẻ":                RETAIL_TRADE,
    "ban le":                RETAIL_TRADE,
    "bán buôn":              RETAIL_TRADE,
    "ban buon":              RETAIL_TRADE,
    "thương mại":            RETAIL_TRADE,
    "thuong mai":            RETAIL_TRADE,

    # HEALTHCARE
    "chăm sóc sức khỏe":     HEALTHCARE,
    "cham soc suc khoe":     HEALTHCARE,
    "dược phẩm":             HEALTHCARE,
    "duoc pham":             HEALTHCARE,
    "y tế":                  HEALTHCARE,
    "y te":                  HEALTHCARE,

    # UTILITIES
    "tiện ích":              UTILITIES,
    "tien ich":              UTILITIES,
    "điện":                  UTILITIES,
    "dien":                  UTILITIES,
    "nước":                  UTILITIES,
    "nuoc":                  UTILITIES,
    "gas":                   UTILITIES,

    # AGRICULTURE
    "thủy sản":              AGRICULTURE,
    "thuy san":              AGRICULTURE,
    "chế biến thủy sản":     AGRICULTURE,
    "nông nghiệp":           AGRICULTURE,
    "nông - lâm":            AGRICULTURE,
    "nông lâm":              AGRICULTURE,
}

# ── Symbol-level overrides ──────────────────────────────────────────
# Priority 1: fix misclassified industry names
# Priority 2: fill empty/missing industry
SYMBOL_OVERRIDES: dict[str, str] = {
    # ===== BANKS =====
    "ACB": BANKS, "BAB": BANKS, "BID": BANKS, "CTG": BANKS,
    "EIB": BANKS, "EVF": BANKS, "HDB": BANKS, "KLB": BANKS,
    "LPB": BANKS, "MBB": BANKS, "MSB": BANKS, "NAB": BANKS,
    "NAM": BANKS, "NCB": BANKS, "NVB": BANKS, "OCB": BANKS,
    "PGB": BANKS, "PVF": BANKS, "SGB": BANKS, "SHB": BANKS,
    "SSB": BANKS, "STB": BANKS, "TCB": BANKS, "TPB": BANKS,
    "VAB": BANKS, "VBB": BANKS, "VCB": BANKS, "VIB": BANKS,
    "VPB": BANKS,

    # ===== FINANCIAL_SERVICES =====
    # Securities
    "AGR": FINANCIAL_SERVICES, "APG": FINANCIAL_SERVICES,
    "ART": FINANCIAL_SERVICES, "BMS": FINANCIAL_SERVICES,
    "BSI": FINANCIAL_SERVICES, "CTS": FINANCIAL_SERVICES,
    "DSC": FINANCIAL_SERVICES, "DSE": FINANCIAL_SERVICES,
    "EVS": FINANCIAL_SERVICES, "FTS": FINANCIAL_SERVICES,
    "HBS": FINANCIAL_SERVICES, "HCM": FINANCIAL_SERVICES,
    "IVS": FINANCIAL_SERVICES, "MBS": FINANCIAL_SERVICES,
    "ORS": FINANCIAL_SERVICES, "PHS": FINANCIAL_SERVICES,
    "PIV": FINANCIAL_SERVICES, "PSI": FINANCIAL_SERVICES,
    "SBS": FINANCIAL_SERVICES, "SHS": FINANCIAL_SERVICES,
    "SSI": FINANCIAL_SERVICES, "TCI": FINANCIAL_SERVICES,
    "TCX": FINANCIAL_SERVICES, "TVC": FINANCIAL_SERVICES,
    "TVB": FINANCIAL_SERVICES, "TVS": FINANCIAL_SERVICES,
    "VCI": FINANCIAL_SERVICES, "VCK": FINANCIAL_SERVICES,
    "VDS": FINANCIAL_SERVICES, "VFS": FINANCIAL_SERVICES,
    "VIX": FINANCIAL_SERVICES, "VND": FINANCIAL_SERVICES,
    "VPX": FINANCIAL_SERVICES, "WSS": FINANCIAL_SERVICES,
    # Insurance
    "BMI": FINANCIAL_SERVICES, "BVH": FINANCIAL_SERVICES,
    "MIG": FINANCIAL_SERVICES, "PTI": FINANCIAL_SERVICES,
    "PVI": FINANCIAL_SERVICES, "AIC": FINANCIAL_SERVICES,
    "BIC": FINANCIAL_SERVICES, "BLI": FINANCIAL_SERVICES,
    "DNM": FINANCIAL_SERVICES, "PRE": FINANCIAL_SERVICES,
    "PGI": FINANCIAL_SERVICES, "VNR": FINANCIAL_SERVICES,
    # Other financial
    "OGC": FINANCIAL_SERVICES, "TIN": FINANCIAL_SERVICES,
    "VNF": FINANCIAL_SERVICES, "WTC": FINANCIAL_SERVICES,

    # ===== BASIC_RESOURCES (Steel overrides) =====
    # DB says "Vật liệu xây dựng" but are actually steel producers
    "HPG": BASIC_RESOURCES, "HSG": BASIC_RESOURCES,
    "NKG": BASIC_RESOURCES, "TLH": BASIC_RESOURCES,
    "POM": BASIC_RESOURCES, "SMC": BASIC_RESOURCES,
    "VIS": BASIC_RESOURCES, "VGS": BASIC_RESOURCES,

    # ===== OIL_GAS =====
    # DB misclassifies these as UTILITIES / INDUSTRIAL / MINING
    "GAS": OIL_GAS,          # PetroVietnam Gas — DB says "Tiện ích"
    "BSR": OIL_GAS,          # Binh Son Refining — DB says "SX Phụ trợ"
    "PVD": OIL_GAS,          # PV Drilling — DB says "Khai khoáng"
    "PVS": OIL_GAS,          # PV Services — DB says "Vận tải - kho bãi"
    "PVT": TRANSPORTATION,   # PV Transport — oil transport but still transport
    "POW": UTILITIES,        # PV Power — DB says "Tiện ích" (correct)
    "PLX": RETAIL_TRADE,     # Petrolimex — DB says "Bán buôn" (correct as retail)

    # ===== TECHNOLOGY overrides =====
    "YEG": TECHNOLOGY,       # Yeah1 — empty industry
    "FPT": TECHNOLOGY,       # DB says "Công nghệ và thông tin" (correct)
    "CMG": TECHNOLOGY,       # DB says "Công nghệ và thông tin" (correct)
    "ELC": TECHNOLOGY,       # DB says "Công nghệ và thông tin" (correct)
    "ICT": TECHNOLOGY,       # Correct
    "SGT": TECHNOLOGY,       # Correct

    # ===== CONSTRUCTION overrides (DB misclassifications) =====
    "TV2": CONSTRUCTION,     # Power engineering consulting — DB says "Dịch vụ tư vấn"
    "LGC": CONSTRUCTION,     # DB says "Xây dựng" (correct)
    "VCG": CONSTRUCTION,     # Correct
    "PC1": CONSTRUCTION,     # DB says "Xây dựng" (correct, power construction)
    "VNE": CONSTRUCTION,     # DB says "Xây dựng" (correct)
    "CII": CONSTRUCTION,     # DB says "Xây dựng" (infrastructure investment)
    "HTN": CONSTRUCTION,     # DB says "Xây dựng" (correct)
    "HHV": CONSTRUCTION,     # DB says "Xây dựng" (highway construction)

    # ===== REAL_ESTATE overrides (fix DB mis-sorts) =====
    "NTC": REAL_ESTATE,      # Industrial park — DB says "Bất động sản" (correct)
    "SZC": REAL_ESTATE,      # Sonadezi — correct
    "TIP": REAL_ESTATE,      # Correct (industrial park)
    "IDC": REAL_ESTATE,      # Correct
    "SNZ": REAL_ESTATE,      # Correct
    "KBC": REAL_ESTATE,      # Correct (industrial park developer)

    # ===== AGRICULTURE overrides =====
    "HAG": AGRICULTURE,      # DB says "Nông - Lâm - Ngư" (correct)
    "HNG": AGRICULTURE,      # Correct
    "NSC": AGRICULTURE,      # DB says "Nông - Lâm - Ngư" (correct)
    "SSC": AGRICULTURE,      # DB says "Nông - Lâm - Ngư" (correct)
    "HSL": AGRICULTURE,      # DB says "Nông - Lâm - Ngư" (correct)

    # ===== FOOD_BEVERAGE overrides =====
    "KDC": FOOD_BEVERAGE,    # Kido — correct
    "MSN": FOOD_BEVERAGE,    # Masan — correct
    "SAB": FOOD_BEVERAGE,    # Sabeco — correct
    "BHN": FOOD_BEVERAGE,    # Habeco — correct
    "VNM": FOOD_BEVERAGE,    # Vinamilk — correct
    "VCF": FOOD_BEVERAGE,    # Vinacafe — correct
    "MCH": FOOD_BEVERAGE,    # Masan Consumer — correct
    "SBT": FOOD_BEVERAGE,    # Thanh Thanh Cong — correct (sugar)
    "LSS": FOOD_BEVERAGE,    # La Ngan Sugar — correct

    # ===== RETAIL_TRADE overrides =====
    "MWG": RETAIL_TRADE,     # Mobile World — correct
    "FRT": RETAIL_TRADE,     # FPT Retail — correct
    "PNJ": RETAIL_TRADE,     # Phu Nhuan Jewelry — correct
    "DGW": RETAIL_TRADE,     # Digiworld — DB says "Bán buôn" (distributor)
    "PET": RETAIL_TRADE,     # Petrolimex IT — DB says "Bán buôn"
    "COM": RETAIL_TRADE,     # Coma — DB says "Bán lẻ" (correct)

    # ===== HEALTHCARE overrides =====
    "DHG": HEALTHCARE,       # DHG Pharma — correct
    "FIT": HEALTHCARE,       # DB says "Chăm sóc sức khỏe" (correct)
    "IMP": HEALTHCARE,       # Correct
    "TRA": HEALTHCARE,       # Correct
    "DBD": HEALTHCARE,       # Correct
    "DCL": HEALTHCARE,       # Correct
    "OPC": HEALTHCARE,       # Correct
    "SPM": HEALTHCARE,       # Correct
    "TNH": HEALTHCARE,       # Correct
    "VDP": HEALTHCARE,       # Correct

    # ===== UTILITIES overrides =====
    "BWE": UTILITIES,        # Binh Duong Water — correct
    "REE": CONSTRUCTION,     # REE Corp — DB says "Xây dựng" but is M&E + power
    "GEG": UTILITIES,        # Gia Lai Electricity — correct
    "NT2": UTILITIES,        # Nhon Trach 2 Power — correct
    "VSH": UTILITIES,        # Vinh Son Song Hinh — correct
    "CHP": UTILITIES,        # Correct

    # ===== INDUSTRIAL_GOODS overrides =====
    "GEX": INDUSTRIAL_GOODS, # Gelex — DB says "Thiết bị điện" (electrical equip)
    "REE": CONSTRUCTION,     # Already above

    # ===== OTHER_INDUSTRIALS (hospitality, services) =====
    "DAH": OTHER_INDUSTRIALS,  # DB says "Dịch vụ lưu trú, ăn uống"
    "DSN": OTHER_INDUSTRIALS,  # Same
    "NVT": OTHER_INDUSTRIALS,  # Same
    "VNG": OTHER_INDUSTRIALS,  # Same
    "VPL": OTHER_INDUSTRIALS,  # Same
    "ADG": OTHER_INDUSTRIALS,  # Media — but let's keep as OTHER_INDUSTRIALS
}


def classify(industry: Optional[str], symbol: str) -> str:
    """Classify stock into 16-group ICB sector.

    Priority:
      1. Symbol-level override (fixes DB misclassifications)
      2. Industry keyword match
      3. Default: OTHER_INDUSTRIALS
    """
    if symbol in SYMBOL_OVERRIDES:
        return SYMBOL_OVERRIDES[symbol]
    if industry:
        ind_lower = industry.strip().lower()
        for keyword, sector in INDUSTRY_MAP.items():
            if keyword in ind_lower:
                return sector
    return OTHER_INDUSTRIALS


# ── Backward-compat 3-group classification ─────────────────────────

MAJOR_GROUP_MAP: dict[str, str] = {
    BANKS:              FINANCIALS,
    FINANCIAL_SERVICES: FINANCIALS,
    REAL_ESTATE:        REAL_ESTATE,
    CONSTRUCTION:       OTHERS,
    CONSTRUCTION_MATERIALS: OTHERS,
    BASIC_RESOURCES:    OTHERS,
    CHEMICALS:          OTHERS,
    OIL_GAS:            OTHERS,
    FOOD_BEVERAGE:      OTHERS,
    TECHNOLOGY:         OTHERS,
    INDUSTRIAL_GOODS:   OTHERS,
    TRANSPORTATION:     OTHERS,
    RETAIL_TRADE:       OTHERS,
    HEALTHCARE:         OTHERS,
    UTILITIES:          OTHERS,
    AGRICULTURE:        OTHERS,
    OTHER_INDUSTRIALS:  OTHERS,
}


def classify_major(industry: Optional[str], symbol: str) -> str:
    """Backward-compat: return FINANCIALS / REAL_ESTATE / OTHERS."""
    icb = classify(industry, symbol)
    return MAJOR_GROUP_MAP.get(icb, OTHERS)


# ── Sector-group symbol lists (backward compat) ─────────────────────

def get_group_symbols(cur) -> tuple[list[str], list[str], list[str]]:
    """Return (fin_symbols, re_symbols, other_symbols) for legacy code."""
    cur.execute("SELECT symbol, industry FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol")
    rows = cur.fetchall()
    fin, re, other = [], [], []
    for sym, ind in rows:
        g = classify_major(ind, sym)
        if g == FINANCIALS:
            fin.append(sym)
        elif g == REAL_ESTATE:
            re.append(sym)
        else:
            other.append(sym)
    return fin, re, other


# ── New helper: safe ICB classification with min-size guard ─────────

def classify_safe(industry: Optional[str], symbol: str,
                  sector_counts: Optional[dict[str, int]] = None,
                  min_size: int = 4) -> str:
    """Classify with auto-fallback to OTHER_INDUSTRIALS if sector has <min_size members.

    Args:
        sector_counts: dict of {sector_name: count} for the evaluation date.
                       If provided, sectors below min_size get merged.
    """
    icb = classify(industry, symbol)
    if sector_counts is not None and icb in sector_counts:
        if sector_counts[icb] < min_size and icb != OTHER_INDUSTRIALS:
            return OTHER_INDUSTRIALS
    return icb


def compute_sector_counts(
    symbols: list[str],
    industries: Optional[dict[str, Optional[str]]] = None,
) -> dict[str, int]:
    """Compute count of symbols per ICB sector.

    Args:
        symbols: list of symbol strings
        industries: dict of {symbol: industry_name}. If None, uses classify()
                    with industry=None (falls back to symbol overrides).
    Returns:
        {sector_name: count}
    """
    counts: dict[str, int] = {}
    for sym in symbols:
        ind = industries.get(sym) if industries else None
        sec = classify(ind, sym)
        counts[sec] = counts.get(sec, 0) + 1
    return counts
