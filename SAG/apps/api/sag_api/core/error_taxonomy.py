"""Ba chiều phân loại lỗi: mã lỗi (code) + khâu trong chuỗi (stage) + trách nhiệm (layer).

Bối cảnh: trước đây mã lỗi chỉ mang ngữ nghĩa HTTP (not_found / upstream_error / …),
một `UpstreamError` cùng lúc chứa "lỗi nội bộ engine", "lỗi nhà cung cấp LLM", "lỗi lưu trữ DB",
người dùng gửi log lên cũng không biết vấn đề nằm ở khâu nào, nên tìm ai để xử lý.

Tại đây định nghĩa ba chiều trực giao mô tả một lỗi, là nguồn chân lý duy nhất cho phân loại lỗi toàn dự án:

- **code (mã lỗi)**: định danh lỗi máy đọc được, frontend dựa vào đó để rẽ nhánh logic, ánh xạ thông báo lỗi.
  Các chuỗi ký tự rải rác tại các điểm ném trước đây (``code="xxx"``) được quy tụ về :class:`ErrorCode`,
  loại bỏ magic value; mã lỗi mới đều phải đăng ký trong file này.
- **layer (trách nhiệm)**: gốc rễ của lỗi nằm ở ai — frontend / bản thân SAG /
  engine zleap-sag / nhà cung cấp LLM / lưu trữ. Quyết định "tìm ai để xử lý".
- **stage (khâu trong chuỗi)**: lỗi xảy ra ở bước nào trong chuỗi nghiệp vụ. Quyết định "bước nào hỏng".

Frontend thu thập code/layer/stage/message/request_id vào log chẩn đoán, lập trình viên
có log là định vị chính xác "khâu nào + trách nhiệm của ai + nguyên văn lỗi cụ thể".

Quy ước bảo trì:
- **Giá trị thành viên** của enum chính là hợp đồng ra ngoài (logic frontend, log chẩn đoán, frame SSE đều phụ thuộc),
  một khi đã phát hành **không tùy tiện đổi tên**; khi thực sự cần bỏ thì giữ giá trị cũ và đánh dấu deprecated.
- Khi thêm mã lỗi hãy chọn hoặc thêm nhóm phù hợp, viết thêm một dòng docstring mô tả tình huống kích hoạt.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Nơi đăng ký duy nhất cho mã lỗi máy đọc được của toàn dự án.

    Giá trị thành viên phải khớp từng ký tự với chuỗi lịch sử (hợp đồng frontend/backend), chỉ tập trung quy tụ, không đổi ngữ nghĩa.
    Phân nhóm theo lĩnh vực nghiệp vụ, khi thêm mới đưa vào nhóm tương ứng hoặc tạo nhóm mới.
    """

    # —— Ngữ nghĩa HTTP chung (mã mặc định của họ ApiError) ——
    INTERNAL_ERROR = "internal_error"
    """Lỗi nội bộ máy chủ chưa phân loại (dự phòng 500)."""

    NOT_FOUND = "not_found"
    """Tài nguyên được yêu cầu không tồn tại (404)."""

    CONFLICT = "conflict"
    """Xung đột tài nguyên, ví dụ tạo trùng (409)."""

    VALIDATION_ERROR = "validation_error"
    """Kiểm tra tham số đầu vào thất bại (422)."""

    UNAUTHORIZED = "unauthorized"
    """Chưa xác thực hoặc chứng chỉ không hợp lệ (401)."""

    FORBIDDEN = "forbidden"
    """Đã xác thực nhưng không có quyền truy cập tài nguyên (403)."""

    CONFIGURATION_ERROR = "configuration_error"
    """Thiếu cấu hình cần thiết, ví dụ chưa cấu hình LLM (400)."""

    UPSTREAM_ERROR = "upstream_error"
    """Nguồn thượng nguồn (LLM / engine) trả về lỗi (502)."""

    SERVICE_UNAVAILABLE = "service_unavailable"
    """Tạm thời không khả dụng, có thể thử lại, ví dụ giới hạn tốc độ / hết thời gian chờ (503)."""

    # —— LLM / đầu ra có cấu trúc ——
    LLM_UNAVAILABLE = "llm_unavailable"
    """LLM lỗi tạm thời: hết thời gian chờ / giới hạn tốc độ / 5xx, có thể thử lại."""

    LLM_AUTH_ERROR = "llm_auth_error"
    """LLM xác thực thất bại: vấn đề API Key hoặc quyền, cần sửa cấu hình, không thử lại được."""

    LLM_BAD_REQUEST = "llm_bad_request"
    """LLM từ chối yêu cầu: yêu cầu không hợp lệ / vượt giới hạn ngữ cảnh."""

    LLM_EMPTY_RESPONSE = "llm_empty_response"
    """LLM không trả về bất kỳ câu trả lời ứng viên nào."""

    SCHEMA_VALIDATION_ERROR = "schema_validation_error"
    """Đầu ra của model không khớp schema có cấu trúc (ví dụ minItems của references)."""

    # —— Phân trang / cursor ——
    INVALID_CURSOR = "invalid_cursor"
    """Cursor phân trang tin nhắn không hợp lệ (không khớp chữ ký / sai định dạng / quá dài)."""

    INVALID_PAGE_LIMIT = "invalid_page_limit"
    """Kích thước trang vượt giới hạn."""

    # —— Vũ trụ tri thức (universe) ——
    SNAPSHOT_CHANGED = "snapshot_changed"
    """Ảnh chụp đồ thị tri thức đã thay đổi trong lúc khám phá, cần bắt đầu lại quá trình khám phá hiện tại."""

    # —— Truy vấn / nguồn ——
    TOO_MANY_SEARCH_SOURCES = "too_many_search_sources"
    """Số lượng nguồn được chỉ định trong một lần truy vấn vượt giới hạn."""

    # —— Truyền phát (SSE) ——
    STREAM_ERROR = "stream_error"
    """Luồng SSE bị gián đoạn bất ngờ giữa chừng (dùng chung cho hỏi đáp / tìm kiếm)."""

    # —— Công cụ MCP ——
    MCP_CONNECTION_FAILED = "mcp_connection_failed"
    """Kết nối máy chủ MCP bên ngoài thất bại."""


class ErrorLayer(StrEnum):
    """Trách nhiệm thuộc về ai: lỗi này nên tìm ai để điều tra."""

    CLIENT = "client"
    """Tầng frontend / mạng / giao thức SSE — vấn đề phía trình duyệt hoặc truyền tải đường truyền."""

    API = "api"
    """Bản thân backend SAG — logic cục bộ như điều phối, xác thực, kiểm tra tham số, thiếu cấu hình."""

    ENGINE = "engine"
    """Engine zleap-sag — chia chunk, trích xuất, kiểm tra schema, lưu trữ nội bộ engine,..."""

    LLM = "llm"
    """Nhà cung cấp LLM — hết thời gian chờ, giới hạn tốc độ, xác thực thất bại, cấu trúc trả về không hợp lệ (ví dụ schema từ chối)."""

    STORE = "store"
    """Tầng bền vững — đọc ghi cơ sở dữ liệu / kho vector, giao dịch, ràng buộc khóa ngoại,..."""


class ErrorStage(StrEnum):
    """Khâu trong chuỗi: lỗi xảy ra ở bước nào trong quy trình nghiệp vụ.

    Chuỗi nạp tài liệu: upload → parse → chunk → embed → extract → persist
    Chuỗi hỏi đáp:      retrieve → generate → tool → persist
    Cắt ngang:          config / auth / unknown
    """

    # —— Chuỗi nạp tài liệu ——
    UPLOAD = "upload"
    """Nhận upload: kiểm tra file (đuôi / kích thước / file rỗng), ghi đĩa."""

    PARSE = "parse"
    """Phân tích: chuyển tài liệu không phải Markdown sang Markdown (MinerU / MarkItDown)."""

    CHUNK = "chunk"
    """Chia chunk: cắt văn bản, giai đoạn tải trước khi ghi chunk và vector của chúng."""

    EMBED = "embed"
    """Vector hóa: gọi model embedding để sinh vector."""

    EXTRACT = "extract"
    """Trích xuất mục: lấy sự kiện / thực thể theo từng chunk (structured output)."""

    PERSIST = "persist"
    """Lưu trữ: ghi bền vững chunk / sự kiện / câu trả lời và cập nhật bộ đếm."""

    # —— Chuỗi hỏi đáp ——
    RETRIEVE = "retrieve"
    """Truy hồi: truy vấn vector / đa đường các đoạn liên quan."""

    GENERATE = "generate"
    """Tạo: LLM sinh câu trả lời (gồm cả turn streaming)."""

    TOOL = "tool"
    """Gọi công cụ: Agent thực thi các công cụ như search_context / web_search."""

    # —— Cắt ngang ——
    CONFIG = "config"
    """Cấu hình: thiếu hoặc không hợp lệ cấu hình cần cho LLM / embedding / engine."""

    AUTH = "auth"
    """Xác thực: chưa xác thực, chứng chỉ hết hạn, không có quyền truy cập."""

    UNKNOWN = "unknown"
    """Chưa phân loại: lỗi chưa được gắn nhãn stage."""
