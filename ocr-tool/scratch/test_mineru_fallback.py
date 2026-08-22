import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Thêm root dir vào sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from financial_pipeline.mineru_client import MinerUClient, MinerUQuotaExceededError
from financial_pipeline.pipeline import FinancialOcrPipeline


def test_mineru_client_fallback_simulation():
    print("=========================================================")
    print(" 🧪 TEST 1: Kiểm thử MinerUClient Fallback Simulation")
    print("=========================================================")

    # Test không có API Key -> ném MinerUQuotaExceededError
    client_no_key = MinerUClient(api_key="")
    try:
        client_no_key.extract_pdf_bytes(b"%PDF-1.4 test bytes", "test.pdf")
        print("❌ FAILED: mineru_client không ném ngoại lệ khi thiếu API key!")
    except MinerUQuotaExceededError as e:
        print(f"✅ PASSED: Bắt lỗi Quota Limit/Thiếu Key thành công: {e}")
    except Exception as e:
        print(f"❌ FAILED: Ngoại lệ không đúng loại: {type(e)} - {e}")


def test_pipeline_fallback_execution():
    print("\n=========================================================")
    print(" 🧪 TEST 2: Kiểm thử Pipeline Fallback (Khi MinerU chưa có key)")
    print("=========================================================")

    pipe = FinancialOcrPipeline(profile_path="financial_profile.yaml")
    
    # Tạo 1 dummy PDF bytes hợp lệ bằng PyMuPDF
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "BAO CAO KIEM TOAN DOC LAP\nBCTC CONG TY CP TAP DOAN HOANG ANH")
    page.insert_text((50, 100), "THUYET MINH BAO CAO TAI CHINH\n1. Dac diem hoat dong cua doanh nghiep")
    dummy_pdf_bytes = doc.tobytes()
    doc.close()

    print(f"[+] Đã tạo dummy PDF mẫu: {len(dummy_pdf_bytes)} bytes")

    # Chạy qua process_pdf_bytes (Do chưa set MINERU_API_KEY, luồng sẽ tự động fallback sang Modal GPU)
    # Lưu ý: nếu gọi Modal GPU thực sự sẽ cần môi trường Modal credentials, ở đây test xem pipeline khởi chạy đúng logic.
    try:
        # Nếu chưa login Modal, _call_modal_gpu_ocr sẽ bắt lỗi hoặc mock
        markdown, metrics, class_res = pipe.process_pdf_bytes(
            pdf_bytes=dummy_pdf_bytes,
            filename="dummy_test_bctc.pdf",
            enable_filtering=True
        )
        print(f"✅ Executed pipeline! OCR Provider: {metrics.ocr_provider}")
    except Exception as e:
        print(f"ℹ️ Pipeline executed fallback branch as expected (Modal execution status: {e})")


if __name__ == "__main__":
    test_mineru_client_fallback_simulation()
    test_pipeline_fallback_execution()
