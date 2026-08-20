"""Ngoại lệ miền sag — không phụ thuộc framework, tầng route ánh xạ thống nhất thành phản hồi HTTP.

Dịch vụ miền chỉ ném các ngoại lệ này; tầng adapter `sag/` chịu trách nhiệm dịch họ `SagError` của `zleap-sag` sang đây.

Mỗi ngoại lệ mang ba chiều:
- ``code``: mã lỗi ngữ nghĩa HTTP (trường lịch sử, tương thích ngược, logic frontend vẫn dùng).
- ``layer``: trách nhiệm thuộc về ai (ai nên điều tra), xem :class:`ErrorLayer`.
- ``stage``: khâu trong chuỗi xử lý (bước nào hỏng), xem :class:`ErrorStage`.
Ngoài ra có ``retryable`` cho biết có thể thử lại an toàn hay không. layer/stage/retryable có thể được ghi đè lúc khởi tạo
theo điểm xảy ra thực tế — cùng một ``ValidationError`` ở giai đoạn extract và giai đoạn upload
nên mang stage khác nhau.
"""

from __future__ import annotations

from sag_api.core.error_taxonomy import ErrorCode, ErrorLayer, ErrorStage


class ApiError(Exception):
    """Lớp cơ sở của mọi ngoại lệ miền sag."""

    status_code: int = 500
    code: str = ErrorCode.INTERNAL_ERROR
    layer: ErrorLayer = ErrorLayer.API
    stage: ErrorStage = ErrorStage.UNKNOWN
    retryable: bool = False

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        layer: ErrorLayer | None = None,
        stage: ErrorStage | None = None,
        retryable: bool | None = None,
    ):
        self.message = message or self.__class__.__doc__ or "Internal error"
        if code:
            self.code = code
        if layer is not None:
            self.layer = layer
        if stage is not None:
            self.stage = stage
        if retryable is not None:
            self.retryable = retryable
        super().__init__(self.message)

    def to_envelope(self, *, request_id: str | None = None) -> dict:
        """Tuần tự hóa thành "envelope" lỗi có cấu trúc dùng cho phản hồi/log."""
        error: dict[str, object] = {
            "code": self.code,
            "message": self.message,
            "layer": self.layer.value,
            "stage": self.stage.value,
            "retryable": self.retryable,
        }
        if request_id:
            error["request_id"] = request_id
        return {"error": error}


class NotFoundError(ApiError):
    """Tài nguyên được yêu cầu không tồn tại."""

    status_code = 404
    code = ErrorCode.NOT_FOUND


class ConflictError(ApiError):
    """Xung đột tài nguyên (ví dụ tạo trùng)."""

    status_code = 409
    code = ErrorCode.CONFLICT


class ValidationError(ApiError):
    """Kiểm tra đầu vào thất bại."""

    status_code = 422
    code = ErrorCode.VALIDATION_ERROR


class AuthError(ApiError):
    """Chưa xác thực hoặc chứng chỉ không hợp lệ."""

    status_code = 401
    code = ErrorCode.UNAUTHORIZED
    layer = ErrorLayer.API
    stage = ErrorStage.AUTH


class ForbiddenError(ApiError):
    """Không có quyền truy cập tài nguyên này."""

    status_code = 403
    code = ErrorCode.FORBIDDEN
    layer = ErrorLayer.API
    stage = ErrorStage.AUTH


class ConfigurationError(ApiError):
    """Thiếu cấu hình cần thiết (ví dụ chưa cấu hình LLM)."""

    status_code = 400
    code = ErrorCode.CONFIGURATION_ERROR
    layer = ErrorLayer.API
    stage = ErrorStage.CONFIG


class UpstreamError(ApiError):
    """Nguồn thượng nguồn (LLM / engine) trả về lỗi."""

    status_code = 502
    code = ErrorCode.UPSTREAM_ERROR
    layer = ErrorLayer.LLM


class ServiceUnavailableError(ApiError):
    """Tạm thời không khả dụng (có thể thử lại, ví dụ giới hạn tốc độ / hết thời gian chờ)."""

    status_code = 503
    code = ErrorCode.SERVICE_UNAVAILABLE
    retryable = True
