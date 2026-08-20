"""Task prompts for OCR requests (Vietnamese-aware, model-agnostic)."""

PROMPTS = {
    "text": (
        "Nhận dạng văn bản tiếng Việt. Xuất chính xác toàn bộ nội dung văn bản, "
        "giữ nguyên dấu tiếng Việt (ă, â, đ, ê, ô, ơ, ư và thanh điệu). "
        "Không diễn giải, không thêm bớt, chỉ xuất văn bản đã đọc được."
    ),
    "table": (
        "Nhận dạng bảng số liệu tiếng Việt. Xuất ra bảng HTML dạng <table>, "
        "giữ nguyên dấu tiếng Việt và số liệu chính xác từng ô. Không diễn giải."
    ),
    "formula": "Formula Recognition:",
}
