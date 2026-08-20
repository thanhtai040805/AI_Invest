import os
import json
import re
import io
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Bộ Regex sử dụng Positive Lookahead giải quyết triệt để vấn đề đảo ngữ tiếng Việt
FACT_SEMANTIC_PATTERNS = {
    "RESEARCH": [
        # Cơ cấu doanh thu/lợi nhuận theo sản phẩm/mảng
        r"^(?=.*(?:cơ cấu|tỷ trọng|đóng góp|khối|mảng))(?=.*(?:doanh thu|lợi nhuận|doanh số))(?=.*(?:chiếm|đạt|tăng|\d+)).*$",
        # Thị phần & Vị thế cạnh tranh
        r"^(?=.*(?:thị phần|dẫn đầu|số 1|đứng đầu|nhà sản xuất hàng đầu))(?=.*(?:việt nam|khu vực|thế giới|ngành)).*$",
        # Hoạt động kinh doanh chính (Mô hình kinh doanh)
        r"^(?=.*(?:hoạt động|sản phẩm|dịch vụ|lĩnh vực|cung cấp))(?=.*(?:chính|cốt lõi|chủ đạo|chiếm tỷ trọng)).*$"
    ],
    "THESIS": [
        # Dự án đầu tư & Công suất (CapEx)
        r"^(?=.*(?:dự án|nhà máy|tổ hợp|dây chuyền|fpt ai factory|ai studio|khu liên hợp|klh|gang thép|phân kỳ))(?=.*(?:đầu tư|vốn đầu tư|quy mô|tổng vốn|công suất))(?=.*(?:tỷ|triệu|tấn|ha|mw|gw)).*$",
        # Tiến độ dự án & Vận hành
        r"^(?=.*(?:khởi công|xây dựng|triển khai|vận hành|hoàn thành|đi vào hoạt động|đưa vào sử dụng|chạy thử|ra mắt))(?=.*(?:dự án|nhà máy|tổ hợp|dây chuyền|khu liên hợp|klh|gang thép|phân kỳ))(?=.*(?:tháng|quý|năm\s+\d{4}|q\d/\d{4})).*$"
    ],
    "COUNTER_THESIS": [
        # Rào cản pháp lý & Chậm tiến độ dự án
        r"^(?=.*(?:dự án|nhà máy|tổ hợp|khu liên hợp|klh|gang thép))(?=.*(?:chưa được|điều chỉnh|chờ phê duyệt|vướng mắc|chậm tiến độ|đền bù|giải phóng mặt bằng))(?=.*(?:chủ trương đầu tư|quy hoạch|giấy phép|đất đai)).*$",
        # Cầm cố & Thế chấp tài sản (Nợ vay)
        r"^(?=.*(?:cầm cố|thế chấp|đảm bảo|tín chấp))(?=.*(?:vay|hàng tồn kho|quyền sử dụng đất|tài sản cố định|bảo lãnh)).*$",
        # Rủi ro kiểm toán & Kiện tụng
        r"^(?=.*(?:ngoại trừ|nhấn mạnh|nghi ngờ|không thể xác thực|tranh chấp|kiện tụng|xử phạt|vi phạm))(?=.*(?:kiểm toán|đơn vị kiểm toán|tòa án|thuế)).*$"
    ],
    "RISK_PORTFOLIO": [
        # Guidance đặt ra của Ban điều hành
        r"^(?=.*(?:kế hoạch|mục tiêu|kỳ vọng|dự kiến|phấn đấu))(?=.*(?:doanh thu|lợi nhuận|lnst|lntt|doanh số))(?=.*(?:đạt|tăng trưởng|tỷ|triệu|%)).*$",
        # Nghị quyết & Quyết sách pháp lý quan trọng (ĐHĐCĐ/HĐQT)
        r"^(?=.*(?:thông qua|phê duyệt|quyết nghị|quyết định|ban hành))(?=.*(?:đại hội đồng cổ đông|đhđcđ|hội đồng quản trị|hđqt))(?=.*(?:kế hoạch|dự án|cổ tức|phát hành|tăng vốn|bảo lãnh|giao dịch|bổ nhiệm)).*$",
        # Tỷ lệ biểu quyết thông qua trong biên bản họp
        r"^(?=.*(?:tỷ lệ|đồng ý|biểu quyết|thông qua))(?=.*(?:\d+\s*%|\d+,\d+\s*%)).*$"
    ]
}

# Các biểu thức Regex dùng để compile trước tăng hiệu năng
COMPILED_PATTERNS = {
    agent: [re.compile(pat, re.IGNORECASE | re.DOTALL) for pat in pats]
    for agent, pats in FACT_SEMANTIC_PATTERNS.items()
}

class SentenceClassifier:
    def __init__(self):
        self.industry_patterns = {}
        # Tự động nạp file cấu hình industry_patterns.json từ thư mục app/config
        config_path = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "config", "industry_patterns.json"
        ))
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    self.industry_patterns = json.load(f)
                logger.info(f"Loaded industry_patterns.json successfully with {len(self.industry_patterns)} industries.")
            except Exception as e:
                logger.error(f"Failed to load industry_patterns.json: {e}")
        else:
            logger.warning(f"industry_patterns.json not found at: {config_path}")

    def locate_valuable_sentences(self, text: str, industry_code: Optional[str] = None) -> List[Dict[str, Any]]:
        """Chia nhỏ văn bản thành các câu và định vị các câu có giá trị thông tin phân tích."""
        if not text:
            return []

        # Tạo Regex động từ cấu hình ngành nếu có
        industry_regex_list = []
        if industry_code and industry_code in self.industry_patterns:
            ind_data = self.industry_patterns[industry_code]
            for agent, keywords in ind_data.items():
                # Quyết định nhãn Agent tương ứng
                agent_type = "RESEARCH"
                if "risk" in agent.lower():
                    agent_type = "COUNTER_THESIS"
                elif "thesis" in agent.lower():
                    agent_type = "THESIS"
                elif "portfolio" in agent.lower():
                    agent_type = "RISK_PORTFOLIO"

                for kw in keywords:
                    # Lookahead dynamic pattern: Có từ khóa ngành + số hoặc phần trăm
                    pat_str = rf"^(?=.*(?:{kw}))(?=.*(?:\d+|chục|trăm|tỷ|triệu|%)).*$"
                    try:
                        industry_regex_list.append((re.compile(pat_str, re.IGNORECASE | re.DOTALL), agent_type))
                    except Exception as e:
                        logger.error(f"Failed to compile dynamic pattern for {kw}: {e}")

        # Tách câu đơn giản bằng dấu chấm
        raw_sentences = [s.strip() for s in text.split(".") if s.strip()]
        valuable_hits = []

        for sentence in raw_sentences:
            # Sửa lỗi ký tự xuống dòng \n làm trượt Regex lookahead
            sentence_clean = sentence.replace("\n", " ").replace("\r", " ").strip()
            sentence_clean = re.sub(r'\s+', ' ', sentence_clean) # Xử lý khoảng trắng thừa
            
            if not sentence_clean:
                continue

            # Loại bỏ nhanh boilerplate văn mẫu dài dòng xã giao phổ biến
            if self._is_boilerplate(sentence_clean):
                continue

            # 1. Quét qua các General Patterns trước
            matched = False
            for agent, patterns in COMPILED_PATTERNS.items():
                for pattern in patterns:
                    if pattern.search(sentence_clean):
                        valuable_hits.append({
                            "sentence": sentence_clean,
                            "agent_type": agent,
                            "density_score": self._calculate_density(sentence_clean)
                        })
                        matched = True
                        break
                if matched:
                    break

            if matched:
                continue

            # 2. Nếu không khớp mẫu chung, quét tiếp qua các Industry-specific Patterns động
            for pattern, agent_type in industry_regex_list:
                if pattern.search(sentence_clean):
                    valuable_hits.append({
                        "sentence": sentence_clean,
                        "agent_type": agent_type,
                        "density_score": self._calculate_density(sentence_clean)
                    })
                    break

        return valuable_hits

    def extract_table_data_from_pdf(self, pdf_content: bytes) -> List[str]:
        """Trích xuất và cấu trúc hóa bảng biểu (Tables) từ PDF thô."""
        tables_text = []
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
                for i, page in enumerate(pdf.pages):
                    tables = page.extract_tables()
                    for t_idx, table in enumerate(tables):
                        # Chuyển bảng thành chuỗi text có cấu trúc dòng cột dễ đọc cho LLM
                        rows_str = []
                        for row in table:
                            # Lọc bỏ cột trống hoặc giá trị None
                            clean_row = [str(cell).strip().replace('\n', ' ') for cell in row if cell is not None]
                            if any(clean_row):
                                rows_str.append(" | ".join(clean_row))
                        if rows_str:
                            table_markdown = f"--- TABLE PAGE {i+1} IDX {t_idx+1} ---\n" + "\n".join(rows_str)
                            tables_text.append(table_markdown)
        except Exception as e:
            logger.debug(f"Table parsing skipped or failed: {e}")
        return tables_text

    def _is_boilerplate(self, sentence: str) -> bool:
        """Kiểm tra xem câu có thuộc nhóm boilerplate rác hay không."""
        boilerplate_keywords = [
            "luôn giám sát chặt chẽ",
            "hoàn thành tốt nhiệm vụ",
            "nỗ lực vượt qua khó khăn",
            "đoàn kết thống nhất",
            "phát triển bền vững",
            "tự hào là",
            "phát huy truyền thống",
            "tuân thủ đúng quy định"
        ]
        s_lower = sentence.lower()
        # Nếu câu chứa cụm từ rác mà KHÔNG có bất kỳ con số nào -> Boilerplate rác 100%
        if any(kw in s_lower for kw in boilerplate_keywords):
            if not any(char.isdigit() for char in s_lower):
                return True
        return False

    def _calculate_density(self, sentence: str) -> float:
        """Tính toán mật độ thông tin thực tế của câu."""
        score = 0.0
        s_lower = sentence.lower()
        
        # 1. Có số liệu cứng (Metric/Value)
        if any(char.isdigit() for char in sentence):
            score += 0.4
            
        # 2. Có mốc thời gian cụ thể (Time)
        if re.search(r'(?:quý|quý\s+[i|v|x]+|năm\s+\d{4}|q\d/\d{4}|q\d)', s_lower):
            score += 0.3
            
        # 3. Có thực thể tên riêng viết hoa (Entity)
        if len(re.findall(r'\b[A-Z][a-zA-Z0-9_]*\b', sentence)) >= 1:
            score += 0.3
            
        return round(score, 2)

sentence_classifier = SentenceClassifier()
