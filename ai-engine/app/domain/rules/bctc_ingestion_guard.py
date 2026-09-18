"""bctc_ingestion_guard.py — Bộ gác cổng kiểm soát nạp BCTC vào SAG Pipeline.

Mục tiêu cốt lõi:
  - Cắt giảm chi phí vận hành (LLM token & MinerU OCR quota).
  - Loại bỏ hoàn toàn các cổ phiếu cực rác / mất thanh khoản kéo dài (Đỉnh ADTV20 2 năm < 5 tỷ VNĐ).
  - Bảo vệ hàng đợi SAG, chỉ tập trung tài nguyên vào cổ phiếu có thanh khoản thực và đủ chuẩn giao dịch.

Cơ chế điều chỉnh:
  - Danh sách `LOW_LIQUIDITY_PURGED_TICKERS` có thể được cập nhật bất kỳ lúc nào nếu có mã bùng nổ thanh khoản.
  - Hỗ trợ cờ `force_reprocess=True` để vượt qua bộ gác cổng khi cần kiểm thử hoặc nạp thủ công.
"""

from typing import Tuple, List, Set

# Danh sách 146 mã cổ phiếu trên sàn HOSE có Đỉnh ADTV20 trong 2 năm (497 phiên từ 2024-2026) < 5 tỷ VNĐ/phiên
LOW_LIQUIDITY_PURGED_TICKERS: Set[str] = frozenset([
    # Vần A (14 mã)
    "AAM", "AAT", "ABR", "ABS", "ABT", "ACG", "ACL", "ADG", "ADP", "ADS",
    "ANT", "ASG", "ASP", "AST",
    # Vần B, C (15 mã)
    "BCE", "BHN", "BKG", "BRC", "BTP", "C32", "C47", "CCC", "CCI", "CHP",
    "CLC", "CLL", "CMV", "COM", "CVT",
    # Vần D, E, F (16 mã)
    "DAH", "DAT", "DBT", "DHG", "DHM", "DQC", "DRL", "DSN", "DTA", "DTL",
    "DVP", "DXV", "EVE", "FCM",
    # Vần G, H (16 mã)
    "GDT", "GHC", "GMH", "GTA", "HAP", "HCD", "HMC", "HNA", "HRC", "HTG",
    "HTL", "HTV", "HU1", "HUB",
    # Vần I, K, L, M (15 mã)
    "ICT", "ILB", "ITD", "KMR", "LAF", "LBM", "LGL", "LIX", "MCM", "MCP",
    # Vần N, O, P, Q (18 mã)
    "NAV", "NCT", "NHT", "NSC", "NVT", "OPC", "PDN", "PDV", "PGD", "PGI",
    "PGV", "PHC", "PIT", "PJT", "PLP", "PMG", "PNC", "PTC", "PTL", "QNP",
    # Vần R, S (20 mã)
    "RAL", "S4A", "SAV", "SBA", "SBV", "SC5", "SFC", "SFG", "SFI", "SGT",
    "SHA", "SHP", "SJD", "SMB", "SPM", "SRC", "SRF", "STG", "STK", "SVC",
    "SVD", "SVT", "SZL",
    # Vần T (20 mã)
    "TBC", "TCR", "TCT", "TDG", "TDM", "TDW", "TEG", "THG", "TLD", "TMP",
    "TMS", "TMT", "TN1", "TNC", "TNT", "TPC", "TRA", "TSA", "TVB", "TVT",
    "TYA",
    # Vần U, V, Y (12 mã)
    "UIC", "VCA", "VCF", "VDP", "VID", "VNG", "VNL", "VNS", "VPD", "VPH",
    "VPS", "VRC", "VSI", "VTB", "YBM",
])


def should_ingest_bctc(ticker: str, force_reprocess: bool = False) -> Tuple[bool, str]:
    """Kiểm tra xem một mã cổ phiếu có đủ điều kiện nạp BCTC vào SAG hay không.
    
    Args:
        ticker: Mã cổ phiếu (ví dụ 'FPT', 'HPG', 'VPS', 'TCR')
        force_reprocess: Cờ cưỡng bức xử lý (bỏ qua bộ lọc)
        
    Returns:
        (allow, reason): Tuple boolean và lý do chi tiết
    """
    clean = str(ticker or "").upper().strip()
    if not clean:
        return False, "Mã cổ phiếu rỗng"

    if force_reprocess:
        return True, "Cưỡng chế xử lý (force_reprocess=True)"

    if clean in LOW_LIQUIDITY_PURGED_TICKERS:
        return (
            False,
            f"Bỏ qua nạp BCTC: Mã {clean} thuộc danh mục rác/đóng băng thanh khoản (Đỉnh ADTV20 2 năm < 5 tỷ VNĐ)"
        )

    return True, "Đủ điều kiện thanh khoản để nạp BCTC"


def filter_ingestible_tickers(tickers: List[str]) -> List[str]:
    """Lọc danh sách các mã được phép nạp BCTC, loại bỏ các mã trong danh sách đóng băng."""
    return [
        t for t in tickers
        if should_ingest_bctc(t)[0]
    ]
