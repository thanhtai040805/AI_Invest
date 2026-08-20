"""Dịch họ `SagError` của zleap-sag thành các ngoại lệ miền sag."""

from __future__ import annotations

from contextlib import contextmanager

from zleap.sag.exceptions import (
    ConfigError,
    InvalidInputError,
    NonRetryableError,
    ResourceNotFoundError,
    RetryableError,
    SagError,
)

from sag_api.core.error_taxonomy import ErrorCode, ErrorLayer, ErrorStage
from sag_api.core.errors import (
    ConfigurationError,
    NotFoundError,
    ServiceUnavailableError,
    UpstreamError,
    ValidationError,
)

try:  # jsonschema là phụ thuộc chuyển tiếp của việc kiểm tra structured-output trong zleap-sag
    from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
except Exception:  # noqa: BLE001 - khi thiếu phụ thuộc thì thoái lui thành sentinel không bao giờ khớp
    JsonSchemaValidationError = ()  # type: ignore[assignment]


@contextmanager
def map_sag_errors(*, stage: ErrorStage = ErrorStage.UNKNOWN):
    """Ngoại lệ engine xảy ra trong context này sẽ được dịch thành ApiError kèm layer/stage.

    ``stage`` do phía gọi truyền vào theo khâu của chuỗi xử lý hiện tại (ví dụ process_document chủ yếu bao phủ
    extract, search bao phủ retrieve), khiến lỗi được dịch mang dấu khâu chính xác.
    """
    try:
        yield
    except ConfigError as e:
        raise ConfigurationError(str(e), layer=ErrorLayer.ENGINE, stage=stage) from e
    except ResourceNotFoundError as e:
        raise NotFoundError(str(e), layer=ErrorLayer.ENGINE, stage=stage) from e
    except InvalidInputError as e:
        raise ValidationError(str(e), layer=ErrorLayer.ENGINE, stage=stage) from e
    except RetryableError as e:
        # Giới hạn tốc độ / hết thời gian / upstream tạm không khả dụng —— có thể thử lại
        raise ServiceUnavailableError(str(e), layer=ErrorLayer.ENGINE, stage=stage) from e
    except NonRetryableError as e:
        raise ValidationError(str(e), layer=ErrorLayer.ENGINE, stage=stage) from e
    except JsonSchemaValidationError as e:  # type: ignore[misc]
        # Kiểm tra schema structured-output thất bại (như minItems của references):
        # loại này không thuộc họ SagError, trước đây từng lọt ra thành Exception trần. Gốc là
        # mô hình không xuất theo schema → xếp vào lớp LLM, khâu kế thừa stage phía gọi truyền vào.
        message = getattr(e, "message", None) or str(e)
        raise ValidationError(
            f"Đầu ra mô hình không khớp schema structured-output: {message}",
            code=ErrorCode.SCHEMA_VALIDATION_ERROR,
            layer=ErrorLayer.LLM,
            stage=stage,
        ) from e
    except SagError as e:
        raise UpstreamError(str(e), layer=ErrorLayer.ENGINE, stage=stage) from e
