"""analyze_industry_keywords.py — Phân tích thực tế văn phong 22 mã ngành

Trích xuất các câu Fact chất lượng cao cào được để làm căn cứ bồi đắp bộ từ khóa.
"""

import os
import json
import re
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("analyze_industry")

class SimpleSentenceClassifier:
    """Trích xuất câu chứa thông tin định tính tài chính quan trọng."""
    KEYWORDS = ["doanh thu", "lợi nhuận", "thị phần", "công suất", "dự án", "đầu tư", "kế hoạch", "biên lợi nhuận"]
    
    @classmethod
    def locate_valuable_sentences(cls, text: str):
        sentences = re.split(r'[.\n]+', text)
        results = []
        for s in sentences:
            s_clean = s.strip()
            if len(s_clean) < 20:
                continue
            matched = [k for k in cls.KEYWORDS if k in s_clean.lower()]
            if matched:
                results.append({
                    "agent_type": "FUNDAMENTAL_RESEARCH",
                    "density_score": round(len(matched) / (len(s_clean.split()) + 1e-6) * 10, 2),
                    "sentence": s_clean
                })
        return results

sentence_classifier = SimpleSentenceClassifier()

def analyze_all_sectors():
    test_data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tests", "test_data"))
    manifest_path = os.path.join(test_data_dir, "manifest.json")
    
    if not os.path.exists(manifest_path):
        logger.error("Manifest not found. Please run crawl_test_data.py first.")
        return

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    logger.info("=== BẮT ĐẦU PHÂN TÍCH VĂN PHONG ĐỊNH TÍNH THEO NGÀNH ===\n")
    
    target_symbols = ["VCB", "SSI", "BVH", "GMD", "GAS", "PVD", "PNJ", "MWG", "DBC", "DHG"]
    
    for item in manifest:
        symbol = item["symbol"]
        if symbol not in target_symbols or item["doc_type"] != "annual_report":
            continue
            
        file_path = os.path.join(test_data_dir, item["local_file"])
        if not os.path.exists(file_path):
            continue
            
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        hits = sentence_classifier.locate_valuable_sentences(content)
        
        logger.info(f"👉 Cổ phiếu: {symbol} | Số câu bóc được: {len(hits)}")
        
        # Lấy tối đa 3 câu tiêu biểu để in ra phân tích
        display_hits = hits[:3]
        for idx, h in enumerate(display_hits):
            logger.info(f"  [{idx+1}] [{h['agent_type']}] [Density: {h['density_score']}]: {h['sentence'][:200]}...")
        logger.info("-" * 80)

if __name__ == "__main__":
    import sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    analyze_all_sectors()
