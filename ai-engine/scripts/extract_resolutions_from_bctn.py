"""extract_resolutions_from_bctn.py — Trích xuất câu Nghị quyết từ BCTN
"""

import os

def extract_resolutions():
    test_data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tests", "test_data"))
    scratch_dir = os.path.dirname(__file__)
    
    symbols = ["HPG", "PNJ", "VNM"]
    output_path = os.path.join(test_data_dir, "extracted_resolutions_sample.txt")
    
    with open(output_path, "w", encoding="utf-8") as out:
        out.write("=== CÁC CÂU NGHỊ QUYẾT / QUYẾT NGHỊ TRÍCH XUẤT TỪ BCTN THỰC TẾ ===\n\n")
        
        for symbol in symbols:
            # Tìm file BCTN của symbol
            filename = None
            for f in os.listdir(test_data_dir):
                if f.startswith(f"raw_text_{symbol}_BCTN") and f.endswith(".txt"):
                    filename = f
                    break
                    
            if not filename:
                continue
                
            file_path = os.path.join(test_data_dir, filename)
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                
            # Tách câu
            sentences = [s.strip() for s in content.split(".") if s.strip()]
            
            # Lọc các câu chứa từ khóa Nghị quyết
            keywords = ["nghị quyết", "quyết nghị", "đại hội đồng cổ đông", "hội đồng quản trị", "thông qua", "phê duyệt"]
            matched_sentences = []
            for s in sentences:
                s_clean = s.replace("\n", " ").replace("\r", " ").strip()
                if any(k in s_clean.lower() for k in keywords) and len(s_clean) > 30:
                    matched_sentences.append(s_clean)
                    
            out.write(f"👉 Cổ phiếu: {symbol} | Tìm thấy: {len(matched_sentences)} câu nghị quyết\n")
            # Lấy 10 câu mẫu ngẫu nhiên/tiêu biểu
            for idx, s in enumerate(matched_sentences[:10]):
                out.write(f"  [{idx+1}]: {s}\n")
            out.write("-" * 80 + "\n\n")
            
    print(f"Extracted samples saved to: {output_path}")

if __name__ == "__main__":
    extract_resolutions()
