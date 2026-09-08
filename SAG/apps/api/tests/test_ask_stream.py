"""/ask SSE full path: model turns, tools, terminal errors, and persistence.

Các test HTTP v1 đã xóa cùng kiến trúc API v1; giữ lại unit test schema thuần túy.
"""

from sag_api.schemas.agent import AskRequest


def test_ask_request_web_switch_defaults_off_and_accepts_legacy_field():
    assert AskRequest(query="问题").effective_web_enabled is False
    assert AskRequest(query="问题", web_enabled=True).effective_web_enabled is True
    assert AskRequest(query="问题", knowledge_only=True).effective_web_enabled is False
    assert AskRequest(query="问题", knowledge_only=False).effective_web_enabled is True
    # The explicit new field wins when a transitional client happens to send both.
    assert (
        AskRequest(query="问题", web_enabled=False, knowledge_only=False).effective_web_enabled
        is False
    )
