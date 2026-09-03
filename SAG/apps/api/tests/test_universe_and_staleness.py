import sys
from pathlib import Path

# Thêm thư mục api vào sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from sag_api.sag.financial_ontology import extract_fiscal_metadata, resolve_canonical_entity
from sag_api.sag.universe_registry import universe_registry

print("=== 1. KIỂM THỬ HIERARCHICAL RESOLUTION & ENTITY SCOPES (BRAND/SUBSIDIARY/PROJECT) ===")
scope_tests = [
    ("Doanh thu Long Châu Q1/2026", "BRAND_LONG_CHAU", "BRAND", "FRT"),
    ("Tiến độ lò cao Dung Quất 2", "PROJECT_HPG_DUNG_QUAT", "FACILITY", "HPG"),
    ("Sản lượng bàn giao xe VinFast", "SUBSIDIARY_VINFAST", "SUBSIDIARY", "VIC"),
    ("Doanh thu chuỗi Bách Hóa Xanh", "BRAND_BACH_HOA_XANH", "BRAND", "MWG"),
    ("Sản lượng cảng Gemalink", "PROJECT_GEMALINK", "PROJECT", "GMD"),
    ("Dự án Vinhomes Ocean Park 2", "PROJECT_VHM_OCEAN_PARK", "PROJECT", "VHM"),
    ("Hợp đồng FPT Software ký mới", "SUBSIDIARY_FPT_SOFTWARE", "SUBSIDIARY", "FPT"),
    ("HPG", "COMPANY_HPG", "COMPANY", "HPG"),
]

passed_scopes = 0
for query, exp_id, exp_type, exp_primary_ticker in scope_tests:
    res = universe_registry.resolve(query)
    if res is None:
        print(f" ❌ '{query}' -> Không nhận diện được!")
        continue
    
    match = (
        res.canonical_id == exp_id
        and res.entity_type == exp_type
        and res.primary_ticker == exp_primary_ticker
        and res.confidence >= 0.80
    )
    status = "✅" if match else "❌"
    if match:
        passed_scopes += 1
    print(f" {status} '{query}' -> ID: {res.canonical_id}, Type: {res.entity_type}, Primary Ticker: {res.primary_ticker}, Conf: {res.confidence}, Match: {res.match_type}")

print(f"\n-> Kết quả Phân loại Entity Scopes: {passed_scopes}/{len(scope_tests)} test cases đạt!")

print("\n=== 2. KIỂM THỬ CHỐNG FALSE POSITIVE (ANTI-NOISE) ===")
noise_tests = [
    "Tình hình kinh doanh năm 2024 tại miền nam",  # Không được nhận diện sang Nam Long (NLG)
    "Chính sách điều hành của ngân hàng nhà nước",  # Không được nhận diện sang VCB/CTG
    "Tăng trưởng kinh tế quý 1 vượt kỳ vọng",        # Không được nhận diện sang mã nào
]

passed_noise = 0
for query in noise_tests:
    res = universe_registry.resolve(query)
    if res is None:
        passed_noise += 1
        print(f" ✅ '{query}' -> Không phát hiện Entity giả (Chính xác!)")
    else:
        print(f" ❌ '{query}' -> False positive: {res.canonical_id} (Conf: {res.confidence})")

print(f"-> Kết quả Chống False Positive: {passed_noise}/{len(noise_tests)} test cases đạt!")

print("\n=== 2. KIỂM THỬ STALENESS TIER & DOCUMENT EXPIRATION ===")
meta_tests = [
    ("BCTC_Q1_2026_HPG.pdf", "FRESH"),
    ("BCTC_Q4_2025_DGC_Hop_nhat.pdf", "ACTIVE"),
    ("BCTC_Kiem_toan_2023_VCB.pdf", "HISTORICAL"),
    ("BCTC_2019_SAB.pdf", "EXPIRED"),
]
for title, expected_tier in meta_tests:
    meta = extract_fiscal_metadata(title, reference_year=2026)
    tier = meta.get("staleness_tier")
    score = meta.get("freshness_score")
    status = "✅" if tier == expected_tier else "❌"
    print(f" {status} '{title}' -> Tier: {tier}, Freshness Score: {score} (Expected: {expected_tier})")
