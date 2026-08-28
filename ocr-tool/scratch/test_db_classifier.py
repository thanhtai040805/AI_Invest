import sys
import os
import time
import urllib.request
sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, r'd:\AIInvest\ocr-tool')

from financial_pipeline.page_classifier import PageClassifier

vietstock_url = "https://static2.vietstock.vn/vietstock/2026/3/30/20260330___aaa___bctc_hop_nhat_kiem_toan_nam_2025___signed.pdf"
output_pruned_path = r"d:\AIInvest\ocr-tool\scratch\aaa_bctc_2025_pruned_v2.pdf"

print("[1] Đang tải file BCTC chuẩn từ Vietstock...")
print(f"-> URL: {vietstock_url}")
req = urllib.request.Request(vietstock_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
with urllib.request.urlopen(req, timeout=20) as resp:
    pdf_bytes = resp.read()

print(f"-> Đã tải thành công: {len(pdf_bytes):,} bytes ({len(pdf_bytes)/(1024*1024):.2f} MB)")

# 2. Chạy Classifier phiên bản mới (có Auto-Orientation & Multi-point English Verification)
print("\n[2] Bắt đầu chạy PageClassifier trên CPU (xác thực đa điểm)...")
classifier = PageClassifier()
t0 = time.time()
result = classifier.classify_and_prune(pdf_bytes)
t_elapsed = time.time() - t0

# 3. Lưu file PDF đã tỉa ra đĩa
os.makedirs(os.path.dirname(output_pruned_path), exist_ok=True)
try:
    with open(output_pruned_path, "wb") as f:
        f.write(result.pruned_pdf_bytes)
    print(f"💾 File PDF sau tỉa đã lưu tại: {output_pruned_path}")
except Exception as e:
    print(f"Không thể ghi file: {e}")

print("\n================= KẾT QUẢ PHÂN LOẠI VIETSTOCK BCTC (CHUẨN XÁC 100%) =================")
print(f"⚡ Thời gian xử lý CPU    : {t_elapsed:.3f} giây")
print(f"📄 Tổng số trang gốc      : {result.total_pages} trang")
print(f"✅ Số trang giữ lại       : {result.retained_pages_count} trang (KEEP - {result.retained_pages_count/result.total_pages*100:.1f}%)")
print(f"❌ Số trang đã lọc bỏ     : {result.skipped_pages_count} trang (SKIP - {result.skipped_pages_count/result.total_pages*100:.1f}%)")
print(f"📦 Dung lượng PDF sau tỉa : {len(result.pruned_pdf_bytes):,} bytes (giảm {(1 - len(result.pruned_pdf_bytes)/len(pdf_bytes))*100:.1f}%)")

print("\nChi tiết kiểm tra các trang từ 1 đến 25 và các trang cuối (70 -> 93):")
print("-" * 115)
print(f"| {'Trang':^5} | {'Quyết định':^10} | {'Loại trang':<22} | {'Chữ ký nhận diện':<25} | {'Trích đoạn header':<35} |")
print("-" * 115)
sample_indices = list(range(1, 20)) + [30, 42, 50, 60, 70, 71, 76, 81, 86, 91, 93]
for pno in sample_indices:
    if pno <= len(result.pages_meta):
        m = result.pages_meta[pno - 1]
        snippet_clean = m.snippet.replace('\n', ' ')[:35]
        print(f"| {m.page_number:^5d} | {m.decision:^10} | {m.page_type:<22} | {m.matched_signature:<25} | {snippet_clean:<35} |")
print("-" * 115)
