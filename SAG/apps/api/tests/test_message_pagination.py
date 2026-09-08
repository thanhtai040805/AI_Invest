"""消息历史的有界 keyset 分页与 Agent 上下文装载上限。"""

from sag_api.db.models import Message


def test_message_history_declares_keyset_index():
    index = next(
        item for item in Message.__table__.indexes if item.name == "ix_messages_thread_created_id"
    )
    assert tuple(column.name for column in index.columns) == ("thread_id", "created_at", "id")


