"""Simplified key mapping test with correct column display."""
import sys, os, json, math
sys.path.insert(0, ".")
os.environ["DB_SCHEMA"] = os.environ.get("DB_SCHEMA", "vndev")
from app.services.pg_pool import DB_URL
import psycopg2
from datetime import date

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# Copy the exact key maps from updated vn_ic_tester.py
STATEMENT_BS_KEYS = {
    "total_assets": ("tổng_cộng_tài_sản", "TỔNG CỘNG TÀI SẢN", "tài_sản", "TÀI SẢN"),
    "total_liabilities": ("tổng_nợ_phải_trả", "TỔNG NỢ PHẢI TRẢ", "c_nợ_phải_trả", "C. NỢ PHẢI TRẢ"),
    "current_assets": ("a_tài_sản_ngắn_hạn", "A. TÀI SẢN NGẮN HẠN", "tài_sản_ngắn_hạn", "Tài sản ngắn hạn"),
    "current_liabilities": ("i_nợ_ngắn_hạn", "I. Nợ ngắn hạn", "nợ_ngắn_hạn", "Nợ ngắn hạn"),
    "cash": ("1_tiền", "1. Tiền", "tiền", "Tiền"),
    "short_term_debt": ("vay_ngắn_hạn", "Vay ngắn hạn", "vay_và_nợ_ngắn_hạn", "Vay và nợ ngắn hạn"),
    "retained_earnings": ("10_lợi_nhuận_sau_thuế_chưa_phân_phối", "10. Lợi nhuận sau thuế chưa phân phối"),
    "depreciation": ("khấu_hao", "Khấu hao", "hao_mòn", "Hao mòn"),
}
STATEMENT_IS_KEYS = {
    "revenue": ("3_doanh_thu_thuần_về_bán_hàng_và_cung_cấp_dịch_vụ", "3. Doanh thu thuần về bán hàng và cung cấp dịch vụ"),
    "net_income": ("18_lợi_nhuận_sau_thuế_thu_nhập_doanh_nghiệp", "18. Lợi nhuận sau thuế thu nhập doanh nghiệp"),
    "ebit": ("11_lợi_nhuận_thuần_từ_hoạt_động_kinh_doanh", "11. Lợi nhuận thuần từ hoạt động kinh doanh"),
}
STATEMENT_CF_KEYS = {
    "cfo": ("lưu_chuyển_tiền_thuần_từ_hoạt_động_kinh_doanh", "Lưu chuyển tiền thuần từ hoạt động kinh doanh",
            "lưu_chuyển_tiền_tệ_ròng_từ_các_hoạt_động_sản_xuất_kinh_doanh",
            "Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh"),
}
BANK_BS_KEYS = {
    "total_assets": ("tổng_cộng_tài_sản", "TỔNG CỘNG TÀI SẢN"),
    "total_liabilities": ("tổng_nợ_phải_trả", "TỔNG NỢ PHẢI TRẢ"),
    "cash": ("1_tiền", "1. Tiền", "tiền", "Tiền"),
}
BANK_IS_KEYS = {
    "revenue": ("1_thu_nhập_lãi_và_các_khoản_thu_nhập_tương_tự", "1. Thu nhập lãi và các khoản thu nhập tương tự"),
    "net_income": ("xiii_lợi_nhuận_sau_thuế_xi_xii", "XIII. Lợi nhuận sau thuế (XI-XII)"),
    "ebit": ("xi_tổng_lợi_nhuận_trước_thuế_ix_x", "XI. Tổng lợi nhuận trước thuế (IX-X)"),
}

def _pick_key(data: dict, candidates: tuple):
    for key in candidates:
        if key in data:
            v = data[key]
            if isinstance(v, (int, float)) and math.isfinite(v):
                return float(v)
            try:
                fv = float(str(v).replace(",", ""))
                if math.isfinite(fv):
                    return fv
            except (ValueError, TypeError):
                continue
    return None

banks = {"VCB", "BID", "CTG", "STB", "TCB", "MBB", "ACB", "VPB", "HDB",
         "TPB", "MSB", "VIB", "OCB", "LPB", "NVB", "SSB", "EIB", "SHB"}
test_symbols = ["AAA", "HPG", "FPT", "VNM", "VCB", "BID", "CTG", "YEG"]
dt = date(2024, 6, 30)

for sym in test_symbols:
    is_bank = sym in banks
    print(f"\n{'='*60}")
    print(f"{sym}  ({'BANK' if is_bank else 'NON-BANK'})")
    print(f"{'='*60}")
    
    bs_keys = BANK_BS_KEYS if is_bank else STATEMENT_BS_KEYS
    is_keys = BANK_IS_KEYS if is_bank else STATEMENT_IS_KEYS
    
    for label, stmt_type, key_map in [
        ("BS", "BS", bs_keys),
        ("IS", "IS", is_keys),
        ("CF", "CF", STATEMENT_CF_KEYS),
    ]:
        cur.execute("SELECT data FROM financial_statements WHERE statement_type = %s AND symbol = %s AND period_end <= %s ORDER BY period_end DESC LIMIT 1",
                    (stmt_type, sym, dt))
        row = cur.fetchone()
        if not row:
            print(f"  [{label}] NO DATA")
            continue
        data = row[0]
        print(f"  [{label}]")
        for key_name in key_map:
            v = _pick_key(data, key_map[key_name])
            if v is not None:
                print(f"    {key_name:20s} = {v:>20,.0f}")
            else:
                # Check first candidate
                c = key_map[key_name][0]
                actual = data.get(c, "MISSING")
                print(f"    {key_name:20s} = NO DATA  (candidate '{c[:35]}' = {actual})")

conn.close()
