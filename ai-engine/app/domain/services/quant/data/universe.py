"""Survivorship-Free Universe — Danh sách mã HOSE lịch sử.

Bảng hose_universe_history chứa mã đã niêm yết và hủy niêm yết từ 2018→nay.
Backtest engine dùng get_universe_at_date(t) để build universe tại mỗi ngày t.
"""
import logging
from datetime import date
from typing import Any, Optional

logger = logging.getLogger(__name__)


HOSE_DELISTED_SYMBOLS = {
    "FTM": date(2023, 12, 20),
    "ITA": date(2024, 6, 15),
    "HVG": date(2023, 8, 30),
    "ROS": date(2024, 3, 25),
    "TTF": date(2023, 10, 15),
    "OGC": date(2022, 11, 30),
    "KSS": date(2023, 7, 20),
    "TCD": date(2024, 1, 15),
    "VNE": date(2022, 9, 20),
    "SJF": date(2023, 5, 10),
    "HBC": date(2024, 4, 5),
    "DRH": date(2022, 8, 15),
    "LCM": date(2023, 2, 28),
    "PXL": date(2022, 7, 30),
    "HQC": date(2023, 9, 10),
    "NVT": date(2022, 10, 25),
    "AMD": date(2023, 11, 20),
    "CII": None,
    "VIC": None,
    "VNM": None,
    "VHM": None,
    "VCB": None,
    "TCB": None,
    "HPG": None,
    "MSN": None,
    "MWG": None,
    "REE": None,
    "FPT": None,
    "SSI": None,
    "HDB": None,
    "MBB": None,
    "ACB": None,
    "STB": None,
    "CTG": None,
    "BID": None,
    "VPB": None,
    "GAS": None,
    "VRE": None,
    "LPB": None,
    "SHB": None,
    "SCB": None,
    "BVH": None,
    "POW": None,
    "PLX": None,
    "SAB": None,
    "PNJ": None,
    "NVL": None,
    "DXG": None,
    "KBC": None,
    "DIG": None,
    "PDR": None,
    "KDH": None,
    "NLG": None,
    "HDG": None,
    "BCM": None,
    "BMP": None,
    "DPM": None,
    "DCM": None,
    "AAA": None,
    "HSG": None,
    "NKG": None,
    "GEX": None,
    "MSB": None,
    "OCB": None,
    "TPB": None,
    "EIB": None,
    "VIB": None,
    "CII": None,
    "HT1": None,
    "GMD": None,
    "SCL": None,
    "HAH": None,
    "VSC": None,
    "VJC": None,
    "HVN": None,
    "FRT": None,
    "DGW": None,
    "PET": None,
    "CMG": None,
    "TDM": None,
    "NT2": None,
    "QTP": None,
    "PC1": None,
    "TNH": None,
    "CTD": None,
    "CTR": None,
    "LHG": None,
    "PAC": None,
    "SMC": None,
    "TMC": None,
    "VND": None,
    "VCI": None,
    "SHS": None,
    "MBS": None,
    "EVF": None,
    "AGR": None,
    "BSI": None,
    "ORS": None,
    "TVS": None,
    "WSS": None,
    "VFS": None,
    "GTS": None,
    "DSE": None,
    "CSI": None,
    "ART": None,
    "TAG": None,
    "BWE": None,
    "SZC": None,
    "NTC": None,
    "SIP": None,
    "IDC": None,
    "KOS": None,
    "LH": None,
    "AGR": None,
    "PHS": None,
    "PSD": None,
    "SD9": None,
    "TIG": None,
    "VIP": None,
    "VOS": None,
    "VTO": None,
    "PHP": None,
    "PVT": None,
    "PDN": None,
    "CDN": None,
    "IMP": None,
    "DHG": None,
    "TRA": None,
    "DCL": None,
    "OPC": None,
    "SPM": None,
    "VDP": None,
    "DHT": None,
    "AMV": None,
    "DBD": None,
    "DGC": None,
    "DMC": None,
    "PHR": None,
    "TRC": None,
    "LSS": None,
    "SBT": None,
    "QNS": None,
    "HHC": None,
    "HII": None,
    "VGS": None,
    "TVS": None,
    "BCG": None,
    "C4G": None,
    "CEO": None,
    "CRE": None,
    "D2D": None,
    "DTA": None,
    "FIT": None,
    "HAG": None,
    "HNG": None,
    "HUT": None,
    "IDJ": None,
    "ITA": None,
    "KAC": None,
    "KHA": None,
    "LGC": None,
    "L14": None,
    "NBB": None,
    "NDN": None,
    "NHA": None,
    "NTL": None,
    "PEC": None,
    "PFL": None,
    "PGC": None,
    "PJT": None,
    "PMT": None,
    "PNC": None,
    "PSP": None,
    "PTC": None,
    "PXA": None,
    "QBS": None,
    "RAL": None,
    "SFC": None,
    "SGD": None,
    "SMA": None,
    "SPC": None,
    "SPD": None,
    "SPH": None,
    "SRA": None,
    "SRC": None,
    "SSC": None,
    "SVI": None,
    "TAC": None,
    "TET": None,
    "THG": None,
    "TJC": None,
    "TMP": None,
    "TPC": None,
    "TRI": None,
    "TSB": None,
    "TTC": None,
    "TV2": None,
    "TV3": None,
    "TV4": None,
    "UDC": None,
    "V11": None,
    "V12": None,
    "V21": None,
    "V24": None,
    "V25": None,
    "V32": None,
    "V35": None,
    "V47": None,
    "V64": None,
    "V74": None,
    "V84": None,
    "VAB": None,
    "VFG": None,
    "VGP": None,
    "VHE": None,
    "VHI": None,
    "VLA": None,
    "VMC": None,
    "VNE": None,
    "VNL": None,
    "VNS": None,
    "VPC": None,
    "VSI": None,
    "VST": None,
    "VWS": None,
    "WTC": None,
    "XDH": None,
    "XPH": None,
    "YBM": None,
}

HOSE_LISTED_DATE = date(2000, 7, 20)


def get_universe_at_date(as_of: date) -> list[str]:
    """Get survivorshp-free HOSE universe at a given date.

    Args:
        as_of: Ngày cần lấy universe

    Returns:
        List of symbols actively traded on HOSE at as_of date
    """
    universe = []
    for symbol, delisted_date in HOSE_DELISTED_SYMBOLS.items():
        if delisted_date is None or delisted_date > as_of:
            if as_of >= HOSE_LISTED_DATE:
                universe.append(symbol)
    return sorted(universe)


def get_universe_history_summary() -> dict[str, Any]:
    """Get summary statistics about the universe."""
    active = sum(1 for v in HOSE_DELISTED_SYMBOLS.values() if v is None)
    delisted = sum(1 for v in HOSE_DELISTED_SYMBOLS.values() if v is not None)
    return {
        "total": len(HOSE_DELISTED_SYMBOLS),
        "active": active,
        "delisted": delisted,
        "delisted_symbols": sorted(
            k for k, v in HOSE_DELISTED_SYMBOLS.items() if v is not None
        ),
    }


def add_universe_table_migration(cur) -> None:
    """Migration: create hose_universe_history table."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS hose_universe_history (
            symbol          VARCHAR(10) NOT NULL,
            listed_date     DATE NOT NULL,
            delisted_date   DATE,
            delist_reason   VARCHAR(100),
            exchange        VARCHAR(5) DEFAULT 'HOSE',
            PRIMARY KEY (symbol)
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_universe_delisted
        ON hose_universe_history(delisted_date)
    """)


def create_or_update_function_get_universe(cur) -> None:
    """Create or replace the get_universe_at_date SQL function."""
    cur.execute("""
        CREATE OR REPLACE FUNCTION get_universe_at_date(as_of DATE)
        RETURNS TABLE(symbol VARCHAR) AS $$
            SELECT symbol FROM hose_universe_history
            WHERE listed_date <= as_of
              AND (delisted_date IS NULL OR delisted_date > as_of)
              AND exchange = 'HOSE';
        $$ LANGUAGE SQL
    """)
