# sag-api

Dịch vụ backend của sag: FastAPI + `zleap-sag`.

## Phân lớp

| Lớp | Thư mục | Trách nhiệm |
|---|---|---|
| Lớp thích ứng | `sag_api/sag/` | **Duy nhất** import `zleap-sag`; nguồn ↔ `DataEngine` |
| Kết nối | `sag_api/connectors/` | Trừu tượng thu thập + registry (upload tệp → đồng bộ động) |
| Phân tích tài liệu | `sag_api/parsing/` | Markdown thông thẳng; PDF ưu tiên MinerU, lỗi tự fallback; phần còn lại do MarkItDown chuyển đổi |
| Hàng đợi tác vụ | `sag_api/jobs/` | Điều phối xử lý nền (máy trạng thái ingest → extract) |
| Lớp sinh | `sag_api/generation/` | Kết quả truy vấn → LLM stream câu trả lời + trích dẫn |
| Lớp công cụ | `sag_api/tools/` | Công cụ Agent: truy vấn/thực thể tích hợp + thích ứng MCP từ xa (giao diện `Tool` thống nhất) |
| Agent Core | `sag_agent/` | Lõi điều phối độc lập: vòng đời, sự kiện, công cụ, phê duyệt, hủy, cổng lưu trữ |
| Thích ứng Agent | `sag_api/services/agent_service.py` | Đưa model, công cụ, phiên SAG vào Agent Core |
| MCP | `sag_api/mcp/` | Nguồn tức là MCP: FastMCP server + gắn Streamable-HTTP (`/mcp/`) + cổng stdio |
| Dịch vụ miền | `sag_api/services/` | Logic nghiệp vụ thuần, không phụ thuộc FastAPI |
| Giao diện | `sag_api/api/v1/` | Route HTTP, chỉ làm IO / kiểm tra / tuần tự hóa |

## Chạy

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn sag_api.main:app --reload --host 0.0.0.0 --port 8000
```

UI tài liệu: http://localhost:8000/docs

Cũng có thể chạy `make api` ở thư mục gốc repo. Server dev mặc định lắng nghe toàn bộ card mạng máy, tiện truy cập Web qua địa chỉ LAN; môi trường production hãy phơi dịch vụ qua reverse proxy và kiểm soát truy cập.