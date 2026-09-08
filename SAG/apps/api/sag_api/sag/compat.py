"""Compatibility shims for dependency-owned zleap-sag behavior.

These patches live at the application boundary so we can keep user workflows
working while waiting for upstream package releases.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from sag_api.core.logging import get_logger

log = get_logger("sag.compat")





def _llm_model_name(client: Any) -> str:
    current = client
    while current is not None:
        model = getattr(getattr(current, "config", None), "model", None)
        if isinstance(model, str) and model:
            return model
        current = getattr(current, "client", None)
    return ""


def _uses_deepseek(client: Any) -> bool:
    name = _llm_model_name(client).rsplit("/", 1)[-1].casefold()
    return "deepseek" in name or "kira" in name


def _is_json_schema_response_format_unsupported(error: Exception) -> bool:
    """Only downgrade the known structured-output capability rejection."""
    message = str(error).casefold()
    return "response_format" in message and (
        "unavailable" in message or "not support" in message or "unsupported" in message
    )


def _validate_response_schema(result: Any, schema: dict[str, Any]) -> None:
    """Keep local validation when the provider only guarantees a JSON object."""
    try:
        import jsonschema
    except ImportError:
        expected_type = schema.get("type")
        if expected_type == "object" and not isinstance(result, dict):
            raise ValueError("Kiểu phản hồi không khớp Schema: mong đợi object") from None
        if expected_type == "array" and not isinstance(result, list):
            raise ValueError("Kiểu phản hồi không khớp Schema: mong đợi array") from None
        return
    jsonschema.validate(instance=result, schema=schema)


def _without_required_field(node: dict[str, Any], field: str) -> bool:
    required = node.get("required")
    if not isinstance(required, list) or field not in required:
        return False
    node["required"] = [item for item in required if item != field]
    return True


def _looks_like_extract_response_schema(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    data = schema.get("properties", {}).get("data")
    if not isinstance(data, dict):
        return False
    data_props = data.get("properties")
    return (
        schema.get("type") == "object"
        and schema.get("properties", {}).get("type", {}).get("const") == "response"
        and isinstance(data_props, dict)
        and "items" in data_props
        and "meta" in data_props
    )


# Event fields upstream marks required but validates as warning-only in
# ``_validate_output``.  Enforcing them as a strict structured-output schema
# makes providers reject otherwise-usable chunks and retry to the limit, so a
# single omitted field discards the whole chunk.  We relax them to match what
# upstream actually tolerates, then backfill defaults in ``_repair_extract_response``.
_SOFT_EVENT_FIELDS = ("references", "title", "content", "is_valid")


def _relax_extract_schema(schema: dict[str, Any]) -> dict[str, Any]:
    relaxed = copy.deepcopy(schema)
    data = relaxed.get("properties", {}).get("data")
    if isinstance(data, dict):
        _without_required_field(data, "meta")
        meta = data.get("properties", {}).get("meta")
        if isinstance(meta, dict):
            _without_required_field(meta, "reason")
    event = relaxed.get("definitions", {}).get("event")
    if isinstance(event, dict):
        for field in _SOFT_EVENT_FIELDS:
            _without_required_field(event, field)
        # Drop the ``minItems: 1`` floor on references: upstream only warns on
        # empty references, it never fails validation on them.
        references = event.get("properties", {}).get("references")
        if isinstance(references, dict):
            references.pop("minItems", None)
    return relaxed


def _repair_extract_response(result: Any) -> set[str]:
    repaired: set[str] = set()
    if not isinstance(result, dict) or result.get("type") != "response":
        return repaired
    data = result.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return repaired
    meta = data.get("meta")
    if not isinstance(meta, dict):
        data["meta"] = {"reason": "model omitted data.meta; filled by SAG compatibility layer"}
        meta = data["meta"]
        repaired.add("data.meta")
    reason = meta.get("reason")
    if not isinstance(reason, str):
        meta["reason"] = ""
        repaired.add("data.meta.reason")

    def repair_item(item: Any) -> None:
        if not isinstance(item, dict):
            return
        if "is_valid" not in item:
            item["is_valid"] = True
            repaired.add("data.items[].is_valid")
        # Backfill the warning-only fields so the item stays schema-shaped for
        # the downstream parser even when the model dropped them.
        if not isinstance(item.get("references"), list):
            item["references"] = []
            repaired.add("data.items[].references")
        if not isinstance(item.get("title"), str):
            item["title"] = ""
            repaired.add("data.items[].title")
        if not isinstance(item.get("content"), str):
            item["content"] = ""
            repaired.add("data.items[].content")
        children = item.get("children")
        if isinstance(children, list):
            for child in children:
                repair_item(child)

    for item in data["items"]:
        repair_item(item)
    return repaired


_VIETNAMESE_FINANCIAL_FEW_SHOT_INPUT = json.dumps(
    {
        "type": "request",
        "data": {
            "items": [
                {
                    "id": 1,
                    "content": (
                        "BÁO CÁO TÀI CHÍNH CÔNG TY CỔ PHẦN TẬP ĐOÀN ABC (MÃ CK: ABC)\n"
                        "Thuyết minh 4: Đầu tư vào các công ty con và công ty liên kết\n"
                        "Tại ngày 30/06/2026, Tập đoàn ABC sở hữu các công ty con sau:\n"
                        "1. CTCP Công nghệ & Dịch vụ Số ABC-Tech: Tỷ lệ sở hữu 84,5357%, tỷ lệ biểu quyết 84,9992%, giá trị vốn góp 2.500.000.000.000 VND.\n"
                        "2. CTCP Sản xuất & Phát triển Dự án ABC-Core: Tỷ lệ sở hữu 99,99%, tỷ lệ biểu quyết 99,99%, giá trị vốn góp 30.000.000.000.000 VND.\n\n"
                        "Thuyết minh 15: Vay và nợ thuê tài chính\n"
                        "- Vay ngắn hạn Ngân hàng Thương mại X: 5.200.000.000.000 VND, lãi suất 5,5%/năm, đảm bảo bằng tài sản tương đương.\n"
                        "- Vay dài hạn Ngân hàng Thương mại Y: 8.500.000.000.000 VND phục vụ Dự án Trọng điểm ABC, thời hạn 7 năm.\n\n"
                        "Thuyết minh 22: Kết quả kinh doanh\n"
                        "- Doanh thu thuần: 39.500.000.000.000 VND (tăng 25% so với cùng kỳ).\n"
                        "- Lợi nhuận sau thuế (LNST): 3.200.000.000.000 VND (tăng 38% so với cùng kỳ)."
                    ),
                }
            ],
            "meta": {
                "source_type": "article",
                "source_title": "BCTC Q2.2026 ABC - Thuyết minh Báo cáo Tài chính",
                "source_summary": "Báo cáo tài chính quý 2 năm 2026 của Tập đoàn ABC",
                "entity_types": [
                    {"type": "TICKER", "description": "Mã cổ phiếu niêm yết"},
                    {"type": "SUBSIDIARY_AFFILIATE", "description": "Công ty con, công ty liên kết"},
                    {"type": "COMPANY", "description": "Ngân hàng, đối tác, pháp nhân"},
                    {"type": "PROJECT_CAPACITY", "description": "Dự án năng lực sản xuất / công nghệ trọng điểm"},
                    {"type": "FINANCIAL_METRIC", "description": "Chỉ số tài chính"},
                ],
            },
        },
    },
    ensure_ascii=False,
)

_VIETNAMESE_FINANCIAL_FEW_SHOT_OUTPUT = json.dumps(
    {
        "type": "response",
        "data": {
            "items": [
                {
                    "title": "ABC - Cơ cấu Công ty con & Tỷ lệ Sở hữu/Biểu quyết Q2.2026",
                    "summary": "Tập đoàn ABC nắm giữ 84,5357% vốn tại CTCP Công nghệ & Dịch vụ Số ABC-Tech và 99,99% tại CTCP Sản xuất & Phát triển Dự án ABC-Core",
                    "content": (
                        "Tại ngày 30/06/2026, Tập đoàn ABC sở hữu các công ty con trọng yếu:\n\n"
                        "| Tên công ty con | Tỷ lệ sở hữu | Tỷ lệ biểu quyết | Giá trị vốn góp (VND) |\n"
                        "| :--- | :--- | :--- | :--- |\n"
                        "| CTCP Công nghệ & Dịch vụ Số ABC-Tech | 84,5357% | 84,9992% | 2.500.000.000.000 |\n"
                        "| CTCP Sản xuất & Phát triển Dự án ABC-Core | 99,99% | 99,99% | 30.000.000.000.000 |"
                    ),
                    "category": "OWNERSHIP_CHANGE",
                    "keywords": ["ABC", "Tập đoàn ABC", "ABC-Tech", "ABC-Core", "công ty con", "tỷ lệ sở hữu 84,5357%"],
                    "priority": "HIGH",
                    "status": "COMPLETED",
                    "references": [1],
                    "is_valid": True,
                    "entities": [
                        {"type": "TICKER", "name": "ABC", "description": "Công ty Cổ phần Tập đoàn ABC"},
                        {"type": "SUBSIDIARY_AFFILIATE", "name": "CTCP Công nghệ & Dịch vụ Số ABC-Tech", "description": "Công ty con do ABC nắm giữ 84,5357% vốn, 84,9992% biểu quyết"},
                        {"type": "SUBSIDIARY_AFFILIATE", "name": "CTCP Sản xuất & Phát triển Dự án ABC-Core", "description": "Công ty con do ABC nắm giữ 99,99% vốn"},
                    ],
                    "children": [],
                },
                {
                    "title": "ABC - Thuyết minh Vay ngắn hạn và dài hạn Ngân hàng Q2.2026",
                    "summary": "Dư nợ vay ngắn hạn 5.200 tỷ VND tại Ngân hàng Thương mại X và vay dài hạn 8.500 tỷ VND tại Ngân hàng Thương mại Y cho Dự án Trọng điểm ABC",
                    "content": (
                        "Tình hình nợ vay của ABC tại Q2.2026:\n"
                        "- Vay ngắn hạn Ngân hàng Thương mại X: 5.200.000.000.000 VND, lãi suất 5,5%/năm.\n"
                        "- Vay dài hạn Ngân hàng Thương mại Y: 8.500.000.000.000 VND, thời hạn 7 năm phục vụ Dự án Trọng điểm ABC."
                    ),
                    "category": "DEBT_RESTRUCTURING",
                    "keywords": ["ABC", "vay nợ", "Ngân hàng Thương mại X", "Ngân hàng Thương mại Y", "Dự án Trọng điểm ABC", "lãi suất"],
                    "priority": "HIGH",
                    "status": "COMPLETED",
                    "references": [1],
                    "is_valid": True,
                    "entities": [
                        {"type": "TICKER", "name": "ABC", "description": "Bên đi vay"},
                        {"type": "COMPANY", "name": "Ngân hàng Thương mại X", "description": "Ngân hàng cho vay ngắn hạn 5.200 tỷ VND"},
                        {"type": "COMPANY", "name": "Ngân hàng Thương mại Y", "description": "Ngân hàng cho vay dài hạn 8.500 tỷ VND"},
                        {"type": "PROJECT_CAPACITY", "name": "Dự án Trọng điểm ABC", "description": "Dự án nhận giải ngân vốn vay dài hạn"},
                    ],
                    "children": [],
                },
                {
                    "title": "ABC - Kết quả Hoạt động Kinh doanh & Doanh thu - Lợi nhuận Q2.2026",
                    "summary": "Doanh thu thuần đạt 39.500 tỷ VND (+25%), LNST đạt 3.200 tỷ VND (+38% so với cùng kỳ)",
                    "content": (
                        "Kết quả kinh doanh quý 2 năm 2026 của ABC ghi nhận tăng trưởng mạnh:\n"
                        "- Doanh thu thuần: 39.500.000.000.000 VND (tăng trưởng 25% so với cùng kỳ).\n"
                        "- Lợi nhuận sau thuế (LNST): 3.200.000.000.000 VND (tăng trưởng 38% so với cùng kỳ)."
                    ),
                    "category": "REVENUE_EBITDA_SHOCK",
                    "keywords": ["ABC", "Doanh thu thuần", "LNST", "Lợi nhuận sau thuế", "Q2.2026"],
                    "priority": "HIGH",
                    "status": "COMPLETED",
                    "references": [1],
                    "is_valid": True,
                    "entities": [
                        {"type": "TICKER", "name": "ABC", "description": "Mã cổ phiếu công bố kết quả kinh doanh"},
                        {"type": "FINANCIAL_METRIC", "name": "Doanh thu thuần", "description": "39.500 tỷ VND (+25%)"},
                        {"type": "FINANCIAL_METRIC", "name": "LNST", "description": "Lợi nhuận sau thuế 3.200 tỷ VND (+38%)"},
                    ],
                    "children": [],
                },
            ],
            "meta": {
                "reason": "Phân rã thành 3 sự kiện tài chính độc lập trong data.items theo từng chủ đề nghiệp vụ cốt lõi: 1) Cơ cấu sở hữu và công ty con (giữ nguyên tỷ lệ 84,5357%); 2) Tình hình vay nợ ngân hàng; 3) Kết quả kinh doanh. Bảng biểu Markdown được giữ nguyên vẹn.",
                "confidence": 0.98,
            },
        },
    },
    ensure_ascii=False,
)


def install_zleap_sag_extract_compat() -> None:
    """Allow event extraction to accept minor omissions in model output.

    Some OpenAI-compatible models produce valid event ``data.items`` but omit
    telemetry-only ``data.meta`` or per-item fields (``is_valid``, ``references``,
    ``title``, ``content``).  Upstream zleap-sag lists these as schema-required,
    yet its own ``_validate_output`` only *warns* on missing/empty
    ``references``/``title``/``content`` and defaults ``is_valid`` to true — so a
    single omitted field makes structured-output providers reject and retry the
    whole chunk to the limit, discarding otherwise-usable events.  We relax the
    schema to what upstream actually tolerates and restore compatible defaults
    before zleap-sag's own output validator runs.
    """

    from zleap.sag.modules.extract.processor import EventProcessor

    # 0. Patch EventSaver to expand embedding_max_length to 4000 chars (prevent table truncation in vector store)
    try:
        from zleap.sag.modules.extract.saver import EventSaver

        if not getattr(EventSaver._batch_sync_events, "_sag_api_expand_embed_len", False):
            orig_batch_sync_events = EventSaver._batch_sync_events

            async def _patched_batch_sync_events(self: Any, events: list[Any], config: Any) -> dict[str, Any]:
                if hasattr(config, "embedding_max_length"):
                    object.__setattr__(config, "embedding_max_length", 4000)
                return await orig_batch_sync_events(self, events, config)

            _patched_batch_sync_events._sag_api_expand_embed_len = True  # type: ignore[attr-defined]
            EventSaver._batch_sync_events = _patched_batch_sync_events
    except Exception:  # pragma: no cover
        pass

    # 1. Patch _build_system_prompt to use pure Vietnamese Financial prompt if custom_requirements present
    if not getattr(EventProcessor._build_system_prompt, "_sag_api_vi_sysprompt", False):
        orig_build_system_prompt = EventProcessor._build_system_prompt

        def _patched_build_system_prompt(self: Any) -> str:
            custom_reqs = getattr(self.config, "custom_requirements", "") or ""
            if (
                "CHUYÊN GIA PHÂN TÍCH TÀI CHÍNH" in custom_reqs
                or "QUY TẮC BẮT BUỘC" in custom_reqs
                or "QUY TẮC SAG v2" in custom_reqs
            ):
                return (
                    "## VAI TRÒ\n"
                    "Bạn là Chuyên gia Phân tích Tài chính Cấp cao & Trưởng phòng Phân tích Chứng khoán (Senior Equity Research Analyst) "
                    "hàng đầu tại Thị trường Chứng khoán Việt Nam.\n"
                    "Nhiệm vụ: đọc tài liệu BCTC/BCQT đầy đủ và xuất manifest ngắn gồm sự kiện, thực thể, fact định lượng và quan hệ có evidence. "
                    "Không chép lại toàn bộ Markdown hoặc bảng; nội dung gốc được SAG v2 hydrate bằng line span. "
                    "Không ép một heading thành một event; chỉ tạo item khi có thông tin phân tích rõ. "
                    "Không tự suy diễn điểm MOAT/GIL hoặc PASS khi thiếu evidence.\n\n"
                    f"{custom_reqs}\n"
                )
            return orig_build_system_prompt(self)

        _patched_build_system_prompt._sag_api_vi_sysprompt = True  # type: ignore[attr-defined]
        EventProcessor._build_system_prompt = _patched_build_system_prompt

    # 2. Patch _build_messages to remove few-shot anchoring for SAG v2 full-document extraction.
    if not getattr(EventProcessor._build_messages, "_sag_api_vi_fewshot", False):
        def _patched_build_messages(self: Any, system_prompt: str, user_input: dict[str, Any]) -> list[Any]:
            from zleap.sag.core.ai.models import LLMMessage, LLMRole

            messages = [
                LLMMessage(role=LLMRole.SYSTEM, content=system_prompt),
                LLMMessage(role=LLMRole.USER, content=json.dumps(user_input, ensure_ascii=False)),
            ]
            log.info("Xây dựng prompt SAG v2 không dùng few-shot tài chính 3 sự kiện")
            return messages

        _patched_build_messages._sag_api_vi_fewshot = True  # type: ignore[attr-defined]
        EventProcessor._build_messages = _patched_build_messages

    current = EventProcessor._call_llm_with_retry
    if getattr(current, "_sag_api_extract_meta_compat", False):
        return

    async def _patched_call_llm_with_retry(self, messages, schema):  # type: ignore[no-untyped-def]
        active_schema = schema
        if _looks_like_extract_response_schema(schema):
            active_schema = _relax_extract_schema(schema)

        # Defensive check: Replace any remaining Chinese news few-shot with Vietnamese financial few-shot
        from zleap.sag.core.ai.models import LLMMessage, LLMRole
        cleaned_messages = []
        for msg in messages:
            content = getattr(msg, "content", "")
            if isinstance(content, str) and ("AI大模型" in content or "OpenAI与谷歌" in content):
                if msg.role == LLMRole.USER:
                    cleaned_messages.append(LLMMessage(role=LLMRole.USER, content=_VIETNAMESE_FINANCIAL_FEW_SHOT_INPUT))
                elif msg.role == LLMRole.ASSISTANT:
                    cleaned_messages.append(LLMMessage(role=LLMRole.ASSISTANT, content=_VIETNAMESE_FINANCIAL_FEW_SHOT_OUTPUT))
                continue
            cleaned_messages.append(msg)
        messages = cleaned_messages

        import litellm
        litellm.request_timeout = 300.0
        if hasattr(getattr(self.llm_client, "config", None), "timeout"):
            self.llm_client.config.timeout = 300.0
        if _uses_deepseek(self.llm_client):
            log.info("Mô hình dùng response_format=json_object với timeout=300s và retry tự động")
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    result = await self.llm_client.chat_with_schema(
                        messages,
                        response_schema=None,
                        response_format={"type": "json_object"},
                    )
                    break
                except Exception as err:
                    if attempt == max_retries - 1:
                        raise
                    log.warning("Lỗi tạm thời từ API LLM (thử %d/%d): %s. Đang chờ thử lại...", attempt + 1, max_retries, err)
                    import asyncio
                    await asyncio.sleep(3 * (attempt + 1))
            _validate_response_schema(result, active_schema)
        else:
            try:
                result = await current(self, messages, active_schema)
            except Exception as error:
                if not _is_json_schema_response_format_unsupported(error):
                    raise
                log.warning("Mô hình không hỗ trợ response_format=json_schema, hạ cấp xuống json_object")
                result = await self.llm_client.chat_with_schema(
                    messages,
                    response_schema=None,
                    response_format={"type": "json_object"},
                )
                _validate_response_schema(result, active_schema)
        repaired = _repair_extract_response(result)
        if repaired:
            log.info("Đã bổ sung tương thích các trường phản hồi trích xuất sự kiện zleap-sag: %s", ", ".join(sorted(repaired)))
        return result

    _patched_call_llm_with_retry._sag_api_extract_meta_compat = True  # type: ignore[attr-defined]
    EventProcessor._call_llm_with_retry = _patched_call_llm_with_retry


# ---------------------------------------------------------------------------
# Vietnamese localization shims (100% tiếng Việt)
# ---------------------------------------------------------------------------

# Entity types seeded by zleap-sag on a fresh database.  zleap-sag ships them
# with Chinese descriptions; we override the module constant so new databases
# seed Vietnamese labels/descriptions instead.
_BASE_ENTITY_TYPES: list[tuple[str, str, str]] = [
    ("person", "Người", "Người / cá nhân"),
    ("organization", "Tổ chức", "Tổ chức / cơ quan / công ty"),
    ("location", "Địa điểm", "Địa điểm / vị trí địa lý"),
    ("product", "Sản phẩm", "Sản phẩm / dịch vụ / dự án"),
    ("event", "Sự kiện", "Sự kiện / hoạt động"),
    ("time", "Thời gian", "Thời gian / ngày / khoảng thời gian"),
    ("concept", "Khái niệm", "Khái niệm / thuật ngữ / chủ đề"),
    ("work", "Tác phẩm", "Tác phẩm / tài liệu / kết quả"),
    ("technology", "Công nghệ", "Công nghệ / phương pháp / công cụ"),
    ("metric", "Chỉ số", "Chỉ số / giá trị / thước đo"),
]

from sag_api.sag.financial_ontology import FinancialEntityType, _ENTITY_TYPE_DESCRIPTIONS

_FINANCIAL_ENTITY_TYPES: list[tuple[str, str, str]] = [
    (e.value, e.value, _ENTITY_TYPE_DESCRIPTIONS.get(e.value, e.value))
    for e in FinancialEntityType
]

_VIETNAMESE_DEFAULT_ENTITY_TYPES: list[tuple[str, str, str]] = _BASE_ENTITY_TYPES + _FINANCIAL_ENTITY_TYPES

# Search-chain NER / rerank prompts are hardcoded English module constants in
# zleap-sag.  We replace them with Vietnamese translations so every user-facing
# retrieval step stays in Vietnamese (JSON keys are preserved for the parsers).
_VIETNAMESE_NER_SYSTEM = (
    "Bạn là một hệ thống trích xuất thực thể rất hiệu quả."
)
_VIETNAMESE_NER_ONE_SHOT_INPUT = (
    "Hãy trích xuất tất cả thực thể có tên quan trọng để trả lời các câu hỏi dưới đây.\n"
    "Đặt các thực thể có tên theo định dạng json.\n"
    "\n"
    "Câu hỏi: Tạp chí nào được thành lập trước, tạp chí Arthur's Magazine hay First for Women?\n"
)
_VIETNAMESE_NER_ONE_SHOT_OUTPUT = (
    '{"named_entities": ["First for Women", "Arthur\'s Magazine"]}'
)
_VIETNAMESE_NER_TEMPLATE = "Câu hỏi: {}"

_VIETNAMESE_RERANK_SYSTEM = (
    "Tôi sẽ cung cấp cho bạn một tập hợp các mô tả mối quan hệ từ đồ thị tri thức. "
    "Hãy chọn chính xác {top_k} mối quan hệ hữu ích nhất để trả lời câu hỏi đa chặng này.\n"
    "\n"
    "Trả về JSON với \"thought_process\" và \"useful_relations\" (danh sách {top_k} dòng quan hệ, hữu ích nhất đứng đầu)."
)
_VIETNAMESE_RERANK_SYSTEM_LOCAL = (
    "Tôi sẽ cung cấp cho bạn một tập hợp các mô tả mối quan hệ từ đồ thị tri thức. "
    "Hãy chọn chính xác {top_k} mối quan hệ hữu ích nhất để trả lời câu hỏi đa chặng này.\n"
    "\n"
    'Trả về JSON chỉ với "useful_relations" (danh sách {top_k} chỉ số, hữu ích nhất đứng đầu). '
    "Không kèm lý do, thought_process, giải thích hay văn bản quan hệ."
)
_VIETNAMESE_RERANK_TEMPLATE = (
    "Câu hỏi:\n"
    "{question}\n"
    "\n"
    "Mô tả các mối quan hệ:\n"
    "{relations}\n"
)


def _patch_search_module(module: Any) -> None:
    """Thay các hằng số prompt NER / rerank tiếng Anh bằng bản tiếng Việt."""
    updates = {
        "_NER_SYSTEM_PROMPT": _VIETNAMESE_NER_SYSTEM,
        "_NER_ONE_SHOT_INPUT": _VIETNAMESE_NER_ONE_SHOT_INPUT,
        "_NER_ONE_SHOT_OUTPUT": _VIETNAMESE_NER_ONE_SHOT_OUTPUT,
        "_NER_TEMPLATE": _VIETNAMESE_NER_TEMPLATE,
        "_RERANK_SYSTEM_PROMPT": _VIETNAMESE_RERANK_SYSTEM,
        "_RERANK_TEMPLATE": _VIETNAMESE_RERANK_TEMPLATE,
    }
    for name, value in updates.items():
        if hasattr(module, name):
            setattr(module, name, value)


def _install_vietnamese_entity_types_seed() -> None:
    """Wrap ``DataEngine._seed_entity_types`` to localize existing rows.

    zleap-sag only seeds defaults on an *empty* database.  Databases created
    before this patch already hold Chinese names/descriptions; the wrapper
    re-syncs every global entity type to the Vietnamese labels/descriptions so
    both fresh and pre-existing deployments converge on Vietnamese.
    """
    try:
        from zleap.sag.engine import DataEngine
    except Exception:  # pragma: no cover
        return

    if getattr(DataEngine._seed_entity_types, "_sag_api_vi_entity_types", False):
        return
    original = DataEngine._seed_entity_types
    vi_map = {t: (name, desc) for t, name, desc in _VIETNAMESE_DEFAULT_ENTITY_TYPES}

    async def _patched_seed(specs: list[tuple[str, str, str]], only_if_empty: bool = True) -> None:
        await original(specs, only_if_empty=only_if_empty)
        try:
            from sqlalchemy import select

            from zleap.sag.db.base import get_session_factory
            from zleap.sag.db.models import EntityType

            factory = get_session_factory()
            async with factory() as session:
                rows = (
                    (
                        await session.execute(
                            select(EntityType).where(EntityType.scope == "global")
                        )
                    )
                    .scalars()
                    .all()
                )
                existing_types = {et.type: et for et in rows}
                changed = False
                import uuid
                from decimal import Decimal
                for t, name, desc in _VIETNAMESE_DEFAULT_ENTITY_TYPES:
                    if t not in existing_types:
                        session.add(
                            EntityType(
                                id=str(uuid.uuid4()),
                                scope="global",
                                type=t,
                                name=name,
                                description=desc,
                                weight=Decimal("1.00"),
                                similarity_threshold=Decimal("0.800"),
                                is_active=True,
                                is_default=True,
                            )
                        )
                        changed = True
                    else:
                        et = existing_types[t]
                        if et.name != name or et.description != desc:
                            et.name = name
                            et.description = desc
                            changed = True
                if changed:
                    await session.commit()
        except Exception:  # pragma: no cover - best effort
            log.warning("Cập nhật entity types tiếng Việt thất bại", exc_info=True)

    _patched_seed._sag_api_vi_entity_types = True  # type: ignore[attr-defined]
    DataEngine._seed_entity_types = staticmethod(_patched_seed)


def install_zleap_sag_vietnamese() -> None:
    """Localize zleap-sag defaults to 100% Vietnamese.

    - Seed entity types with Vietnamese labels/descriptions on fresh databases.
    - Localize the hardcoded English NER / rerank prompts used by the search
      chain (atomic / multi / multi_es) so retrieval stays in Vietnamese.
    - Thay fallback tiếng Trung ``"{name} thực thể"`` được dùng khi một loại thực thể
      không có mô tả.
    """
    try:
        import zleap.sag.engine as _engine
    except Exception:  # pragma: no cover - import path guard
        return

    if getattr(_engine, "_DEFAULT_ENTITY_TYPES", None) != _VIETNAMESE_DEFAULT_ENTITY_TYPES:
        _engine._DEFAULT_ENTITY_TYPES = _VIETNAMESE_DEFAULT_ENTITY_TYPES
    _install_vietnamese_entity_types_seed()

    for mod_name in (
        "zleap.sag.modules.search.atomic",
        "zleap.sag.modules.search.multi",
        "zleap.sag.modules.search.multi_vector",
    ):
        try:
            import importlib

            _patch_search_module(importlib.import_module(mod_name))
        except Exception:  # pragma: no cover - best effort
            log.warning("Không patch được search prompts: %s", mod_name)

    try:
        from zleap.sag.modules.extract.processor import EventProcessor

        if getattr(EventProcessor._build_input, "_sag_api_vi_fallback", False):
            return
        original = EventProcessor._build_input

        def _patched_build_input(self, items, metadata, source_type, related_events):  # type: ignore[no-untyped-def]
            result = original(self, items, metadata, source_type, related_events)
            meta = (result or {}).get("data", {}).get("meta")
            if isinstance(meta, dict):
                entity_types = meta.get("entity_types")
                if isinstance(entity_types, list):
                    for et in entity_types:
                        if isinstance(et, dict) and not et.get("description"):
                            et["description"] = f"{et.get('name') or 'thực thể'} (thực thể)"
            return result

        _patched_build_input._sag_api_vi_fallback = True  # type: ignore[attr-defined]
        EventProcessor._build_input = _patched_build_input
    except Exception:  # pragma: no cover - best effort
        log.warning("Không patch được extract entity-types fallback")
