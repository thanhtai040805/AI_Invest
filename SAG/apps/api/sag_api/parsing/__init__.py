"""Chuẩn hóa file tải lên thành Markdown mà zleap-sag có thể tiếp nhận."""

from sag_api.parsing.service import ParseStateCallback, PreparedDocument, prepare_document

__all__ = ["ParseStateCallback", "PreparedDocument", "prepare_document"]
