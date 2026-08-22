"""Compatibility shims for dependency-owned zleap-sag behavior.

These patches live at the application boundary so we can keep user workflows
working while waiting for upstream package releases.
"""

from __future__ import annotations

import copy
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

    current = EventProcessor._call_llm_with_retry
    if getattr(current, "_sag_api_extract_meta_compat", False):
        return

    async def _patched_call_llm_with_retry(self, messages, schema):  # type: ignore[no-untyped-def]
        active_schema = schema
        if _looks_like_extract_response_schema(schema):
            active_schema = _relax_extract_schema(schema)
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
_VIETNAMESE_DEFAULT_ENTITY_TYPES: list[tuple[str, str, str]] = [
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
                changed = False
                for et in rows:
                    target = vi_map.get(et.type)
                    if target and (et.name != target[0] or et.description != target[1]):
                        et.name = target[0]
                        et.description = target[1]
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
