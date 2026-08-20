from __future__ import annotations

from sqlalchemy import JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, IDMixin, TimestampMixin


class Setting(IDMixin, TimestampMixin, Base):
    """Cấu hình kiểu key-value (dự trữ: cài đặt cấp global / workspace có thể ghi đè lúc chạy)."""

    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("scope", "key", name="uq_setting_scope_key"),)

    scope: Mapped[str] = mapped_column(String(64), default="global", index=True)
    key: Mapped[str] = mapped_column(String(128), index=True)
    value: Mapped[dict] = mapped_column("value_json", JSON, default=dict)
