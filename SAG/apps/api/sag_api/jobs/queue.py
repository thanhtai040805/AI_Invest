"""Trừu tượng hàng đợi tác vụ.

MVP dùng hàng đợi asyncio trong tiến trình (`InProcessAsyncQueue`); giao diện giữ gọn,
tương lai có thể triển khai backend phân tán như Celery / RQ / Arq mà không ảnh hưởng phía gọi.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class JobQueue(ABC):
    @abstractmethod
    async def enqueue(self, job_id: str) -> None:
        """Đưa một Job đã lưu vào hàng đợi chờ thực thi."""

    async def start(self) -> None:  # noqa: B027 - Hook vòng đời tùy chọn
        """Khởi động worker nền (nếu có)."""

    async def stop(self) -> None:  # noqa: B027
        """Dừng worker một cách nhẹ nhàng."""
