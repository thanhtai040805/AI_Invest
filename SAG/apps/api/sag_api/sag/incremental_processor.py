"""Lớp adapter song song, tiến độ và điểm dừng của zleap-sag.

DataEngine phía trên chỉ lộ extract cả bài; ở đây tách việc trích xuất thành các task chunk độc lập,
mỗi chunk lưu thành công là lưu điểm dừng ngay, khi tạm dừng hoặc thử lại thì tiếp tục từ điểm dừng đã xác nhận gần nhất.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Literal, cast


from zleap.sag import DataEngine
from zleap.sag.modules.extract.config import ExtractConfig
from zleap.sag.modules.extract.extractor import EventExtractor
from zleap.sag.modules.load.config import DocumentLoadConfig
from zleap.sag.modules.load.loader import DocumentLoader
from zleap.sag.modules.load.parser import MarkdownParser

from sag_api.core.logging import get_logger
from sag_api.sag.dto import ProcessCheckpoint, ProcessOutcome
from sag_api.sag.financial_ontology import get_financial_extraction_prompt, infer_doc_type


CheckpointCallback = Callable[[ProcessCheckpoint], Awaitable[None]]
PauseCheck = Callable[[], Awaitable[bool]]
StageCallback = Callable[[str], Awaitable[None]]

log = get_logger("sag.incremental")

_SQLITE_INTEGER_MIN = -(2**63)
_SQLITE_INTEGER_MAX = 2**63 - 1

_KNOWLEDGE_EVENT_REQUIREMENTS = """
Đối với các tài liệu phi tin tức như sách, báo cáo, luận văn, "sự kiện" (观点、事实、定义) cũng bao gồm các quan điểm, sự kiện, định nghĩa,
cơ chế, quan hệ nhân quả, luận chứng và kết luận có thể hiểu độc lập, không bắt buộc phải chứa ngày tháng, hành động nhân vật hay sự kiện tin tức.
Chỉ các đoạn là mục lục, đầu trang/chân trang, quảng cáo, lỗi hiển thị, thuần liên kết, hoặc thực sự không liên quan đến chủ đề tài liệu mới có thể trả về kết quả rỗng;
nội dung chính chỉ cần chứa tri thức dùng lại được là giữ ít nhất một sự kiện cấp cao hợp lệ.
Mỗi thực thể phải dùng đúng {"type":"loại thực thể","name":"tên thực thể","description":"mô tả tác dụng"};
cấm viết loại thực thể thành tên trường, ví dụ không được xuất
{"location":"Trung Đông","name":"Trung Đông","description":"khu vực"}.
""".strip()



class _FallbackTitleMarkdownParser(MarkdownParser):
    """Preserve Muse's logical filename when converted Markdown has no H1."""

    def __init__(self, fallback_title: str) -> None:
        super().__init__()
        self._fallback_title = fallback_title.strip()

    def extract_title(self, content: str) -> str:
        title = super().extract_title(content)
        if title.strip().casefold() == "untitled" and self._fallback_title:
            return self._fallback_title
        return title


def _llm_chat_owner(client: Any) -> Any:
    """Tìm client zleap-sag trong cùng nhất thực sự thực thi chat."""
    current = client
    seen: set[int] = set()
    while id(current) not in seen:
        seen.add(id(current))
        nested = getattr(current, "client", None)
        if nested is None or not callable(getattr(nested, "chat", None)):
            break
        current = nested
    return current


def _usage_value(value: Any, field: str) -> int:
    raw = value.get(field, 0) if isinstance(value, Mapping) else getattr(value, field, 0)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _response_token_usage(response: Any) -> int:
    for value in (
        response,
        getattr(response, "usage", None),
        getattr(response, "usage_metadata", None),
    ):
        if value is None:
            continue
        total = _usage_value(value, "total_tokens")
        if total > 0:
            return total
        input_tokens = _usage_value(value, "prompt_tokens") or _usage_value(value, "input_tokens")
        output_tokens = _usage_value(value, "completion_tokens") or _usage_value(value, "output_tokens")
        if input_tokens + output_tokens > 0:
            return input_tokens + output_tokens
    return 0


def _entity_types_from_messages(messages: object) -> set[str]:
    """Read the current extraction request's explicit entity-type vocabulary."""

    if not isinstance(messages, list):
        return set()
    for message in reversed(messages):
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        if not isinstance(content, str):
            continue
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            continue
        data = payload.get("data") if isinstance(payload, dict) else None
        meta = data.get("meta") if isinstance(data, dict) else None
        entity_types = meta.get("entity_types") if isinstance(meta, dict) else None
        if not isinstance(entity_types, list):
            continue
        return {
            item["type"].strip()
            for item in entity_types
            if isinstance(item, dict) and isinstance(item.get("type"), str) and item["type"].strip()
        }
    return set()


def _normalize_event_entity_aliases(event: object, allowed_types: set[str]) -> int:
    """Chỉ chuẩn hóa một lỗi gõ rõ ràng của mô hình trước khi SAG kiểm tra schema.

    Một số mô hình tương thích OpenAI thỉnh thoảng xuất
    ``{"location": "Trung Đông", "name": "Trung Đông", ...}`` thay vì đặt
    ``location`` vào trường ``type`` bắt buộc.  Chúng ta chỉ viết lại khi có
    đúng một khóa bất ngờ, khóa đó nằm trong từ vựng loại được cho phép của yêu cầu này,
    và giá trị của nó bằng ``name``; các đối tượng mơ hồ hoặc không đầy đủ
    được giữ nguyên và vẫn sẽ trượt kiểm tra của SAG.
    """

    if not isinstance(event, dict):
        return 0
    normalized = 0
    entities = event.get("entities")
    if isinstance(entities, list):
        for entity in entities:
            if not isinstance(entity, dict) or "type" in entity:
                continue
            name = entity.get("name")
            description = entity.get("description")
            if not isinstance(name, str) or not isinstance(description, str):
                continue
            aliases = [key for key in entity if key not in {"name", "description"}]
            if len(aliases) != 1:
                continue
            alias = aliases[0]
            alias_value = entity.get(alias)
            if not isinstance(alias, str) or alias.strip() not in allowed_types:
                continue
            if not isinstance(alias_value, str) or alias_value.strip() != name.strip():
                continue
            entity.pop(alias)
            entity["type"] = alias.strip()
            normalized += 1

    children = event.get("children")
    if isinstance(children, list):
        for child in children:
            normalized += _normalize_event_entity_aliases(child, allowed_types)
    return normalized


def _value_overflows_sqlite_integer(value: object, entity_type: object) -> bool:
    """Match zleap-sag numeric parsing, then check SQLite's signed range."""

    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return not _SQLITE_INTEGER_MIN <= value <= _SQLITE_INTEGER_MAX
    if not isinstance(value, str):
        return False
    from zleap.sag.modules.extract.parser import EntityValueParser

    parse = getattr(EntityValueParser, "_sag_original_parse", EntityValueParser.parse)
    parsed = parse(EntityValueParser(), value, entity_type=entity_type if isinstance(entity_type, str) else None)
    return bool(
        parsed
        and parsed.get("type") == "int"
        and not _SQLITE_INTEGER_MIN <= int(parsed["value"]) <= _SQLITE_INTEGER_MAX
    )


def _install_sqlite_integer_guard() -> None:
    """Guard zleap-sag's parser at the same boundary that persists Entity.int_value."""

    from zleap.sag.modules.extract.parser import EntityValueParser

    if getattr(EntityValueParser, "_sag_sqlite_integer_guard_installed", False):
        return
    original_parse = EntityValueParser.parse

    def guarded_parse(self: Any, text: str, *args: Any, **kwargs: Any):
        result = original_parse(self, text, *args, **kwargs)
        if result and result.get("type") == "int" and _value_overflows_sqlite_integer(result.get("value"), None):
            return {**result, "type": "text", "value": str(text), "unit": None}
        return result

    EntityValueParser.parse = guarded_parse
    EntityValueParser._sag_original_parse = original_parse
    EntityValueParser._sag_sqlite_integer_guard_installed = True
    log.warning("Đã bật bảo vệ tương thích phạm vi số nguyên SQLite của zleap-sag")


_install_sqlite_integer_guard()


def _normalize_event_entity_values(event: object) -> int:
    """Downgrade integer entities that SQLite cannot store without losing their text."""

    if not isinstance(event, dict):
        return 0
    normalized = 0
    entities = event.get("entities")
    if isinstance(entities, list):
        for entity in entities:
            if not isinstance(entity, dict) or entity.get("value_type") == "text":
                continue
            candidate = entity.get("value") if entity.get("value_type") == "int" else entity.get("name")
            if _value_overflows_sqlite_integer(candidate, entity.get("type")):
                entity["value_type"] = "text"
                normalized += 1

    children = event.get("children")
    if isinstance(children, list):
        for child in children:
            normalized += _normalize_event_entity_values(child)
    return normalized


def _normalize_extraction_response(response: Any, allowed_types: set[str]) -> int:
    """Normalize response fields that would otherwise fail upstream persistence."""

    content = getattr(response, "content", None)
    if not isinstance(content, str):
        return 0
    candidate = content.strip()
    fenced = candidate.startswith("```") and candidate.endswith("```")
    if fenced:
        lines = candidate.splitlines()
        if len(lines) < 3 or lines[0].strip().casefold() not in {"```", "```json"}:
            return 0
        candidate = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    data = payload.get("data")
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return 0

    normalized = sum(
        _normalize_event_entity_aliases(item, allowed_types) + _normalize_event_entity_values(item) for item in items
    )
    if normalized:
        response.content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return normalized


def _first_task_error(group: BaseExceptionGroup) -> Exception:
    for error in group.exceptions:
        if isinstance(error, BaseExceptionGroup):
            return _first_task_error(error)
        if isinstance(error, Exception):
            return error
    return RuntimeError(str(group))


class IncrementalDocumentProcessor:
    def __init__(
        self,
        engine: DataEngine,
        source_config_id: str,
        *,
        max_concurrency: int,
        chunk_max_tokens: int = 1_000,
        chunk_mode: Literal["standard", "heading_strict"] = "standard",
        document_title: str | None = None,
        enable_strict_filtering: bool = False,
        doc_type: str | None = None,
    ) -> None:
        self._engine = engine
        self._source_config_id = source_config_id
        self._max_concurrency = max(1, min(100, max_concurrency))
        self._chunk_max_tokens = chunk_max_tokens
        self._chunk_mode = chunk_mode
        self._document_title = (document_title or "").strip()
        self._enable_strict_filtering = enable_strict_filtering
        self._doc_type = doc_type or infer_doc_type(document_title)


    async def process(
        self,
        path: str | Path | None,
        *,
        checkpoint: ProcessCheckpoint,
        on_checkpoint: CheckpointCallback,
        should_pause: PauseCheck,
        on_stage: StageCallback | None = None,
    ) -> ProcessOutcome:
        current = checkpoint.model_copy(deep=True)
        if not current.chunk_ids:
            if path is None:
                raise RuntimeError("Tài liệu chưa được chia chunk, không thể tiếp tục từ điểm dừng")
            if on_stage:
                await on_stage("loading")
            loader = (
                DocumentLoader(parser=_FallbackTitleMarkdownParser(self._document_title))
                if self._document_title
                else DocumentLoader()
            )
            loaded = await loader.load(
                DocumentLoadConfig(
                    path=str(path),
                    source_config_id=self._source_config_id,
                    max_tokens=self._chunk_max_tokens,
                    chunk_mode=self._chunk_mode,
                )
            )
            current.source_id = getattr(loaded, "source_id", None)
            current.chunk_ids = list(getattr(loaded, "chunk_ids", []) or [])
            current.processed_chunk_ids = []
            current.event_count = 0
            current.event_ids = []
            current.eventless_chunk_ids = []
            current.token_usage = 0
            await on_checkpoint(current.model_copy(deep=True))

        if on_stage:
            await on_stage("extracting")

        processed = set(current.processed_chunk_ids)
        remaining = [chunk_id for chunk_id in current.chunk_ids if chunk_id not in processed]
        if remaining and not await should_pause():
            await self._extract_remaining(
                remaining,
                current=current,
                on_checkpoint=on_checkpoint,
                should_pause=should_pause,
            )

        await self._restore_checkpoint_events(current.event_ids)
        paused = len(current.processed_chunk_ids) < len(current.chunk_ids)
        if not paused:
            await self._normalize_event_ranks(current.chunk_ids)
        return ProcessOutcome(
            source_id=current.source_id,
            chunk_count=len(current.chunk_ids),
            event_count=current.event_count,
            chunk_ids=list(current.chunk_ids),
            event_ids=list(current.event_ids),
            processed_chunk_ids=list(current.processed_chunk_ids),
            eventless_chunk_ids=list(current.eventless_chunk_ids),
            token_usage=current.token_usage,
            paused=paused,
        )

    async def _extract_remaining(
        self,
        chunk_ids: list[str],
        *,
        current: ProcessCheckpoint,
        on_checkpoint: CheckpointCallback,
        should_pause: PauseCheck,
    ) -> None:
        queue: asyncio.Queue[str] = asyncio.Queue()
        for chunk_id in chunk_ids:
            queue.put_nowait(chunk_id)
        checkpoint_lock = asyncio.Lock()

        async def worker() -> None:
            while not queue.empty():
                if await should_pause():
                    return
                try:
                    chunk_id = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    event_ids, token_usage = await self._extract_chunk(chunk_id)
                    async with checkpoint_lock:
                        if chunk_id in current.processed_chunk_ids:
                            continue
                        current.processed_chunk_ids.append(chunk_id)
                        current.event_ids.extend(event_ids)
                        current.event_count += len(event_ids)
                        if event_ids:
                            if chunk_id in current.eventless_chunk_ids:
                                current.eventless_chunk_ids.remove(chunk_id)
                        elif chunk_id not in current.eventless_chunk_ids:
                            current.eventless_chunk_ids.append(chunk_id)
                        current.token_usage += token_usage
                        # zleap-sag replaces an article's visible event set on
                        # every chunk save. Restore the complete checkpoint
                        # before publishing its counters so `/graph` can read
                        # every event the document detail has just announced.
                        await self._restore_checkpoint_events(current.event_ids)
                        await on_checkpoint(current.model_copy(deep=True))
                finally:
                    queue.task_done()

        worker_count = min(self._max_concurrency, len(chunk_ids))
        try:
            async with asyncio.TaskGroup() as group:
                for _ in range(worker_count):
                    group.create_task(worker())
        except ExceptionGroup as errors:
            # TaskGroup bọc ngoại lệ SAG/LLM của từng chunk thành ExceptionGroup chung; sau khi giải bọc
            # EngineManager mới ánh xạ được loại có thể thử lại, tài liệu và Job cũng lưu được nguyên nhân lỗi thật.
            raise _first_task_error(errors) from errors

    async def _extract_chunk(self, chunk_id: str) -> tuple[list[str], int]:
        template = getattr(self._engine, "_extractor", None)
        if template is None:
            raise RuntimeError("Engine trích xuất chưa được khởi tạo")
        extractor = EventExtractor(
            prompt_manager=template.prompt_manager,
            model_config=template.model_config,
        )

        token_usage = 0
        chunk_failure: Exception | None = None
        client = await extractor._get_llm_client()
        chat_owner = _llm_chat_owner(client)
        original_chat = chat_owner.chat

        async def tracked_chat(*args: Any, **kwargs: Any):
            nonlocal token_usage
            response = await original_chat(*args, **kwargs)
            used = _response_token_usage(response)
            if used <= 0:
                messages = args[0] if args else kwargs.get("messages", [])
                input_chars = sum(
                    len(
                        str(
                            message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")
                        )
                    )
                    for message in messages
                )
                used = max(1, (input_chars + len(str(getattr(response, "content", ""))) + 2) // 3)
            token_usage += used
            messages = args[0] if args else kwargs.get("messages", [])
            normalized_entities = _normalize_extraction_response(
                response,
                _entity_types_from_messages(messages),
            )
            if normalized_entities:
                log.info(
                    "Đã chuẩn hóa trường loại thực thể của mô hình chunk=%s count=%d",
                    chunk_id,
                    normalized_entities,
                )
            return response

        # Lớp xử lý theo lô zleap-sag 0.7.x ghi nhận ngoại lệ đơn chunk thành thất bại rồi trả về danh sách rỗng, phía gọi
        # do đó không phân biệt được "không có sự kiện bình thường" và "LLM/Schema thất bại". Muse mỗi lần chỉ giao cho
        # extractor này một chunk, có thể ghi nhận ngoại lệ gốc ở ranh giới instance và sau khi extract() trả về
        # ném lại, tránh ghi chunk thất bại vào điểm dừng thành công. Không cần sửa site-packages.
        original_extract_from_chunk: Callable[..., Awaitable[Any]] | None = getattr(
            extractor, "extract_from_chunk", None
        )
        if callable(original_extract_from_chunk):

            async def tracked_extract_from_chunk(*args: Any, **kwargs: Any) -> Any:
                nonlocal chunk_failure
                try:
                    target = cast(Callable[..., Awaitable[Any]], original_extract_from_chunk)
                    return await target(*args, **kwargs)
                except Exception as error:  # noqa: BLE001 - Giữ nguyên loại ngoại lệ gốc SAG
                    chunk_failure = error
                    raise

            extractor.extract_from_chunk = tracked_extract_from_chunk


        chat_owner.chat = tracked_chat
        prompt_requirements = f"{_KNOWLEDGE_EVENT_REQUIREMENTS}\n\n{get_financial_extraction_prompt(self._doc_type)}"
        try:
            events = await extractor.extract(
                ExtractConfig(
                    source_config_id=self._source_config_id,
                    chunk_ids=[chunk_id],
                    max_concurrency=1,
                    custom_requirements=prompt_requirements,
                    enable_strict_filtering=self._enable_strict_filtering,
                )
            )


            if chunk_failure is not None:
                raise chunk_failure
        finally:
            chat_owner.chat = original_chat
        return [event.id for event in events], token_usage

    async def _restore_checkpoint_events(self, event_ids: list[str]) -> None:
        """Sau khi các chunk nộp xong, khôi phục toàn bộ sự kiện mà điểm dừng hiện tại đã tạo ra.

        zleap-sag mỗi lần lưu đều thay thế sự kiện của toàn bài; khi lớp adapter điểm dừng nộp từng chunk,
        chunk nộp sau sẽ đánh dấu sự kiện của chunk trước là đã xóa, vì vậy phải khôi phục thống nhất theo điểm dừng.
        """
        if not event_ids:
            return
        from sqlalchemy import update
        from zleap.sag.db import SourceEvent, get_session_factory

        unique_ids = list(dict.fromkeys(event_ids))
        session_factory = get_session_factory()
        async with session_factory() as session:
            for offset in range(0, len(unique_ids), 500):
                batch = unique_ids[offset : offset + 500]
                await session.execute(
                    update(SourceEvent)
                    .where(
                        SourceEvent.source_config_id == self._source_config_id,
                        SourceEvent.id.in_(batch),
                        SourceEvent.status == "DELETED",
                    )
                    .values(status="COMPLETED")
                )
            await session.commit()

    async def _normalize_event_ranks(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        from sqlalchemy import select
        from zleap.sag.db import SourceEvent, get_session_factory

        chunk_order = {chunk_id: index for index, chunk_id in enumerate(chunk_ids)}
        session_factory = get_session_factory()
        async with session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(SourceEvent).where(
                            SourceEvent.source_config_id == self._source_config_id,
                            SourceEvent.chunk_id.in_(chunk_ids),
                        )
                    )
                ).scalars()
            )
            rows.sort(
                key=lambda event: (
                    chunk_order.get(event.chunk_id or "", len(chunk_order)),
                    int(event.rank or 0),
                    event.id,
                )
            )
            for rank, event in enumerate(rows):
                event.rank = rank
            await session.commit()
