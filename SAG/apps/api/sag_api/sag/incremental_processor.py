"""zleap-sag 0.8.2 管线适配层。

0.8.2 将 load/extract 收敛为显式管线,本模块是 SAG 文档处理(ingest+extract)
与 zleap 管线公开 API 之间的适配:

- ``engine.ingest(...)``:Parse → Chunk → Index,返回 ``ChunkSetRef``
  (含 source_id / chunk_ids / generation_id / chunk_version);
- ``engine.extract(ref, ExtractionOptions, observer, cancellation)``:
  整批抽取,返回 ``EventSetRef``(event_ids / entity_ids / stats)。

SAG 职责(保留):断点持久化、暂停/取消、进度映射、与 Job 系统的对接。
zleap 职责(0.8.2 已内置,不再由 SAG 实现):实体数契约
(``ExtractionLimits.min_entities_per_event=1``)、修复重试(``max_retries``)、
无效事项过滤(``is_valid``)、代际替换语义。

临时行为差异(与 0.7.1 相比,带 REQ 标记,待 zleap 需求落地后消除):

- REQ-1/2(事件粒度过滤与修复):0.8.2 的契约校验与修复重试是批次粒度,
  SAG 不再做逐事件过滤(0.7.1 的 ``_require_event_entities`` /
  ``guarded_save_events`` monkeypatch 已删除);一个 chunk 修复耗尽会拒绝整批。
- REQ-3(逐块断点):0.8.2 一次 extract 覆盖全部 chunks(单代提交),
  SAG 不再逐块持久化断点;暂停/取消后恢复会整批重跑(LLM 成本回归)。
  zleap 提供代际 durable prepare/commit 公开入口后恢复逐块断点。
- REQ-4/5(schema 强化与 SQLite int64 防护):0.7.1 的
  ``_strengthen_event_entity_schema`` / ``_install_sqlite_integer_guard``
  monkeypatch 目标在 0.8.2 已不可达,shim 删除,待 zleap 内置。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from hashlib import sha256
from pathlib import Path
from typing import Any

from zleap.sag import DataEngine
from zleap.sag.pipeline import (
    CancellationToken,
    ChunkItem,
    ChunkOptions,
    ChunkSet,
    ChunkSetRef,
    ChunkStats,
    DocumentSource,
    ExtractionExecutionOptions,
    ExtractionLimits,
    ExtractionOptions,
    FileSource,
    IndexOptions,
    RelatedEventContextOptions,
    SourceDescriptor,
    SourceReference,
    SourceType,
    WriteStatus,
)
from zleap.sag.pipeline.errors import PipelineCancelledError
from zleap.sag.pipeline.events import StageEvent, StageEventType, StageName

from sag_api.core.logging import get_logger
from sag_api.sag.dto import ProcessCheckpoint, ProcessOutcome

CheckpointCallback = Callable[[ProcessCheckpoint], Awaitable[None]]
PauseCheck = Callable[[], Awaitable[bool]]
StageCallback = Callable[[str], Awaitable[None]]

log = get_logger("sag.incremental")

def detect_document_language(text: str, configured: str | None = None) -> str:
    """Resolve extraction prompt language. Chinese ('zh') is disabled to prevent language drift."""
    return "en"


_KNOWLEDGE_EVENT_REQUIREMENTS = {
    "en": (
        'For books, reports, papers, and other non-news documents, an "event" also includes\n'
        "independently understandable viewpoints, facts, definitions, mechanisms, causal\n"
        "relationships, arguments, and conclusions. It does not need to include a date, a person's\n"
        "action, or a news event.\n"
        "Only tables of contents, headers, footers, advertisements, corrupted text, standalone links,\n"
        "or fragments genuinely unrelated to the document topic may return an empty result. Retain at\n"
        "least one valid top-level event whenever the main text contains reusable knowledge.\n"
        "Keep each event's content concise (1 to 3 factual sentences preserving exact numbers, metrics,\n"
        "and identifiers) rather than copying the entire chunk verbatim.\n"
        "Every entity must strictly use\n"
        '{"type":"entity type","name":"entity name","description":"role description"}.\n'
        "Valid entity types are: person, organization, location, topic, product, time, event, other "
        "(use 'topic' or 'other' for laws, regulations, tasks, metrics, or concepts).\n"
        "Do not use an entity type as a field name. For example, do not output\n"
        '{"location":"Middle East","name":"Middle East","description":"region"}.'
    ),
}

# 进度观察节流:避免把 zleap 的每个 progress 事件都转换成一次 DB 断点写入。
_PROGRESS_COMMIT_EVERY = 5


class IncrementalDocumentProcessor:
    def __init__(
        self,
        engine: DataEngine,
        source_config_id: str,
        *,
        max_concurrency: int,
        chunk_max_tokens: int = 1_000,
        chunk_mode: str = "standard",
        document_title: str | None = None,
        max_entities_per_event: int = 20,
        write_event_entity_vectors: bool = False,
        enable_strict_filtering: bool = False,
        event_entity_attempts: int = 2,
        extraction_profile: str = "concise",
        document_version_id: str | None = None,
        extract_events: bool = False,
    ) -> None:
        self._engine = engine
        self._source_config_id = source_config_id
        self._max_concurrency = max(1, min(100, max_concurrency))
        self._chunk_max_tokens = chunk_max_tokens
        self._chunk_mode = chunk_mode if chunk_mode in {"standard", "heading_strict"} else "standard"
        self._document_title = (document_title or "").strip()
        self._max_entities_per_event = max(1, min(20, max_entities_per_event))
        self._write_event_entity_vectors = bool(write_event_entity_vectors)
        self._extraction_profile = extraction_profile if extraction_profile in {"standard", "concise"} else "concise"
        self._document_version_id = document_version_id
        self._extract_events = extract_events
        self._detected_language: str | None = None

        # 0.8.2 已内置契约与修复重试,以下 0.7.1 参数仅保留接口兼容:
        # - enable_strict_filtering → zleap 无对应开关(REQ 待对齐),忽略并记录;
        # - event_entity_attempts → zleap ExtractionOptions.max_retries(默认 5)接管。
        if enable_strict_filtering:
            log.warning("0.8.2 无严格过滤开关,忽略 enable_strict_filtering(REQ 待对齐)")
        self._event_entity_attempts = max(1, min(3, event_entity_attempts))

    async def _sample_language(self, path: str | Path | None) -> None:
        if path is not None:
            file_path = Path(path)
            try:
                if file_path.is_file():
                    def _read_head() -> str:
                        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
                            return f.read(4096)

                    sample = await asyncio.to_thread(_read_head)
                    if sample.strip():
                        self._detected_language = detect_document_language(sample)
                        return
            except Exception:  # noqa: BLE001
                pass
        if self._document_title:
            self._detected_language = detect_document_language(self._document_title)

    # ── 主流程 ────────────────────────────────────────────────────────────
    async def process(
        self,
        path: str | Path | None,
        *,
        checkpoint: ProcessCheckpoint,
        on_checkpoint: CheckpointCallback,
        should_pause: PauseCheck,
        on_stage: StageCallback | None = None,
        original_path: str | Path | None = None,
    ) -> ProcessOutcome:
        current = checkpoint.model_copy(deep=True)
        await self._sample_language(path)

        # 阶段 1:解析 → 切块 → 落库(仅在断点尚未建立时)
        if not current.chunk_ids:
            if path is None:
                raise RuntimeError("文档尚未切片，无法从断点继续")
            if on_stage:
                await on_stage("loading")
            await self._ingest(current, path, on_checkpoint, original_path=original_path)

        chunk_set = self._chunk_set_ref(current)

        if not self._extract_events:
            return ProcessOutcome(
                source_id=current.source_id,
                chunk_count=len(current.chunk_ids),
                chunk_ids=list(current.chunk_ids),
            )

        # 阶段 2:整批抽取(0.8.2 批次粒度;REQ-3 落地前不逐块断点)
        if on_stage:
            await on_stage("extracting")
        cancelled, events = await self._extract(current, chunk_set, should_pause, on_checkpoint)
        paused = cancelled or bool(current.processed_chunk_ids) and len(current.processed_chunk_ids) < len(
            current.chunk_ids
        )

        if not cancelled and events is not None:
            current.event_ids = list(events.event_ids)
            current.event_count = events.event_count
            current.processed_chunk_ids = list(chunk_set.chunk_ids)
            await on_checkpoint(current.model_copy(deep=True))

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

    async def _ingest_from_canonical_search_units(
        self,
        current: ProcessCheckpoint,
        descriptor: SourceDescriptor,
        on_checkpoint: CheckpointCallback,
    ) -> bool:
        """Reuse canonical SearchUnits from Phase 2C so the document is never chunked twice."""
        if not self._document_version_id or not hasattr(self._engine, "_index"):
            return False
        relational = getattr(getattr(self._engine, "resources", None), "relational", None)
        if relational is None or not callable(getattr(relational, "session_factory", None)):
            return False

        from sqlalchemy import select
        from sag_api.db.models.routing_rag import CanonicalBlock, SearchUnit

        session_factory = relational.session_factory()
        async with session_factory() as session:
            blocks = list(
                (
                    await session.scalars(
                        select(CanonicalBlock)
                        .where(CanonicalBlock.document_version_id == self._document_version_id)
                        .order_by(CanonicalBlock.ordinal)
                    )
                ).all()
            )
            units = list(
                (
                    await session.scalars(
                        select(SearchUnit).where(
                            SearchUnit.document_version_id == self._document_version_id
                        )
                    )
                ).all()
            )
        if not blocks or not units:
            return False

        block_index_by_id = {b.id: idx for idx, b in enumerate(blocks)}
        units.sort(key=lambda u: block_index_by_id.get(u.block_from_id, 0))

        chunk_items: list[ChunkItem] = []
        for idx, unit in enumerate(units):
            start_idx = block_index_by_id.get(unit.block_from_id, 0)
            end_idx = block_index_by_id.get(unit.block_to_id, start_idx)
            unit_blocks = blocks[start_idx : end_idx + 1]
            combined_text = "\n\n".join(b.normalized_text for b in unit_blocks if b.normalized_text).strip()
            if not combined_text:
                continue
            heading = (unit.section_path or self._document_title or f"Section {idx + 1}").strip()[:500]
            rank = len(chunk_items)
            chunk_items.append(
                ChunkItem(
                    client_key=f"chunk-{rank}",
                    rank=rank,
                    heading=heading,
                    content=combined_text,
                    raw_content=combined_text,
                    chunk_type="TEXT",
                    references=(
                        SourceReference(
                            reference_type="block",
                            key=str(unit.id),
                            order=rank,
                            metadata={},
                        ),
                    ),
                    sections=(),
                    token_count=unit.token_count,
                    metadata={
                        "search_unit_id": str(unit.id),
                        "section_path": unit.section_path or "",
                    },
                )
            )

        if not chunk_items:
            return False

        index_descriptor = descriptor.model_copy(
            update={"data_source_id": self._source_config_id}
        )
        if getattr(self._engine, "_components", None) is None and callable(
            getattr(self._engine, "_ensure_source", None)
        ):
            self._engine._data_source_id = self._source_config_id
            await self._engine._ensure_source()

        source_version = sha256(
            "\n".join(u.content_hash for u in units).encode("utf-8")
        ).hexdigest()[:32]
        chunk_version = sha256(
            f"canonical-search-units:{self._document_version_id}:{len(chunk_items)}".encode("utf-8")
        ).hexdigest()[:32]

        chunk_set_input = ChunkSet(
            descriptor=index_descriptor,
            source_version=source_version,
            chunk_version=chunk_version,
            chunks=tuple(chunk_items),
            units=(),
            stats=ChunkStats(
                input_units=len(blocks),
                chunk_count=len(chunk_items),
                token_count=sum(item.token_count or 0 for item in chunk_items),
            ),
            metadata={"chunker_implementation": "canonical_search_units_v1"},
        )
        chunk_set = await self._engine._index(
            chunk_set_input,
            index_descriptor,
            IndexOptions(
                embedding_batch_size=100,
                vector_batch_size=500,
            ),
        )
        current.source_id = chunk_set.source_id
        current.chunk_ids = list(chunk_set.chunk_ids)
        current.generation_id = chunk_set.generation_id
        current.chunk_version = chunk_set.chunk_version
        current.source_version = chunk_set.source_version
        current.processed_chunk_ids = []
        current.event_count = 0
        current.event_ids = []
        current.eventless_chunk_ids = []
        current.token_usage = 0
        if current.source_id:
            try:
                from sqlalchemy import update
                from sag_api.db.models import Document

                async with session_factory() as session:
                    ver = await session.get(
                        __import__("sag_api.db.models", fromlist=["DocumentVersion"]).DocumentVersion,
                        self._document_version_id,
                    )
                    if ver and ver.document_id:
                        await session.execute(
                            update(Document)
                            .where(Document.id == ver.document_id, Document.sag_source_id.is_(None))
                            .values(sag_source_id=str(current.source_id))
                        )
                        await session.commit()
            except Exception:  # noqa: BLE001
                pass
        await on_checkpoint(current.model_copy(deep=True))
        log.info(
            "Reused canonical SearchUnits for SAG index source_config_id=%s source_id=%s chunks=%d",
            self._source_config_id,
            current.source_id,
            len(current.chunk_ids),
        )
        return True

    async def _ingest(
        self,
        current: ProcessCheckpoint,
        path: str | Path,
        on_checkpoint: CheckpointCallback,
        *,
        original_path: str | Path | None = None,
    ) -> None:
        """Parse → Chunk → Index;把 ChunkSetRef 的定位信息固化进断点。"""
        descriptor = SourceDescriptor(
            source_type=SourceType.ARTICLE,
            title=self._document_title or None,
        )
        is_spreadsheet = original_path is not None and Path(original_path).suffix.lower() in {".xls", ".xlsx"}
        if not is_spreadsheet and await self._ingest_from_canonical_search_units(
            current, descriptor, on_checkpoint
        ):
            return

        source: str | DocumentSource = str(path)
        if is_spreadsheet and original_path is not None:
            original = Path(original_path)
            # Canonical Markdown alone cannot recover merged cells or record boundaries.
            content = await asyncio.to_thread(Path(path).read_text, encoding="utf-8")
            original_bytes = await asyncio.to_thread(original.read_bytes)
            source = DocumentSource(
                content=content,
                original_file=FileSource(path=str(original), format_hint=original.suffix.lower().lstrip(".")),
                original_sha256=sha256(original_bytes).hexdigest(),
                descriptor=descriptor,
                format_hint="markdown",
            )
        chunk_set = await self._engine.ingest(
            source,
            descriptor=descriptor,
            chunk_options=ChunkOptions(
                # heading_strict in 0.12.0 drops record-group metadata.
                strategy="standard" if isinstance(source, DocumentSource) else self._chunk_mode,  # type: ignore[arg-type]
                max_tokens=self._chunk_max_tokens,
            ),
            index_options=IndexOptions(
                embedding_batch_size=100,
                vector_batch_size=500,
            ),
        )
        current.source_id = chunk_set.source_id
        current.chunk_ids = list(chunk_set.chunk_ids)
        current.generation_id = chunk_set.generation_id
        current.chunk_version = chunk_set.chunk_version
        current.source_version = chunk_set.source_version
        current.processed_chunk_ids = []
        current.event_count = 0
        current.event_ids = []
        current.eventless_chunk_ids = []
        current.token_usage = 0
        if current.source_id and self._document_version_id:
            try:
                from sqlalchemy import update
                from sag_api.core.db import SessionLocal
                from sag_api.db.models import Document, DocumentVersion

                async with SessionLocal() as session:
                    ver = await session.get(DocumentVersion, self._document_version_id)
                    if ver and ver.document_id:
                        await session.execute(
                            update(Document)
                            .where(Document.id == ver.document_id, Document.sag_source_id.is_(None))
                            .values(sag_source_id=str(current.source_id))
                        )
                        await session.commit()
            except Exception:  # noqa: BLE001
                pass
        await on_checkpoint(current.model_copy(deep=True))
        log.info(
            "文档切片完成 source_config_id=%s source_id=%s chunks=%d generation=%s",
            self._source_config_id,
            current.source_id,
            len(current.chunk_ids),
            current.generation_id,
        )

    def _chunk_set_ref(self, current: ProcessCheckpoint) -> ChunkSetRef:
        """从断点重建 ChunkSetRef；普通 ingest 的 generation_id 可为空。"""
        if not current.chunk_version:
            raise RuntimeError("断点缺少 chunk_version，无法重建 ChunkSetRef；请重新处理文档")
        if current.source_id is None:
            raise RuntimeError("断点缺少 source_id，无法重建 ChunkSetRef")
        return ChunkSetRef(
            data_source_id=self._source_config_id,
            source_type=SourceType.ARTICLE,
            source_id=current.source_id,
            source_version=current.source_version or "",
            chunk_version=current.chunk_version,
            generation_id=current.generation_id,
            chunk_ids=tuple(current.chunk_ids),
            client_key_to_chunk_id={},
            relation_status=WriteStatus.SUCCEEDED,
            vector_status=WriteStatus.SUCCEEDED,
        )

    async def _extract(
        self,
        current: ProcessCheckpoint,
        chunk_set: ChunkSetRef,
        should_pause: PauseCheck,
        on_checkpoint: CheckpointCallback,
    ) -> tuple[bool, Any]:
        """整批抽取;暂停由 CancellationToken + 后台轮询驱动,进度经 observer 透出。"""
        prompt_manager = getattr(self._engine.resources, "prompts", None)
        if (
            prompt_manager is not None
            and getattr(prompt_manager, "language", None) != "en"
            and hasattr(prompt_manager, "templates")
            and hasattr(prompt_manager, "template_data")
            and hasattr(prompt_manager, "load_templates")
        ):
            prompt_manager.language = "en"
            prompt_manager.templates.clear()
            prompt_manager.template_data.clear()
            prompt_manager.load_templates()
        requirements = _KNOWLEDGE_EVENT_REQUIREMENTS["en"]

        is_concise = self._extraction_profile == "concise"
        concise_rule = (
            "Extract at most 3 high-signal events per chunk and at most 8 core entities per event; "
            "keep event content brief (1-2 sentences) and entity descriptions concise (under 15 words)."
        )
        rules = (requirements, concise_rule) if is_concise else (requirements,)
        options = ExtractionOptions(
            source_type="article",
            contract="minimal" if is_concise else "rich",
            max_retries=self._event_entity_attempts,
            # Keep (20, 1, 20) so zleap.sag's ExtractionPromptComposer preserves few-shot JSON examples
            limits=ExtractionLimits(
                max_events_per_chunk=20,
                min_entities_per_event=1,
                max_entities_per_event=20,
            ),
            related_events=RelatedEventContextOptions(
                enabled=not is_concise,
            ),
            execution=ExtractionExecutionOptions(
                max_concurrency=self._max_concurrency,
                embedding_batch_size=100,
                vector_batch_size=500,
                write_entity_vectors=self._write_event_entity_vectors,
                write_event_entity_vectors=self._write_event_entity_vectors,
            ),
            guidance_rules=rules,
        )
        cancellation = CancellationToken()

        async def poll_pause() -> None:
            while not cancellation.is_cancelled:
                try:
                    if await should_pause():
                        cancellation.cancel()
                        return
                except Exception:  # noqa: BLE001 - 暂停探测失败按不暂停处理
                    pass
                await asyncio.sleep(1.0)

        poller = asyncio.create_task(poll_pause())
        last_committed_progress = -1

        async def observer(event: StageEvent) -> None:
            nonlocal last_committed_progress
            if event.stage != StageName.EXTRACT or event.type != StageEventType.PROGRESS:
                return
            completed = event.completed or 0
            total = event.total or len(chunk_set.chunk_ids)
            if completed < last_committed_progress:
                return
            current.processed_chunk_ids = list(chunk_set.chunk_ids[:completed])
            if total and (completed % _PROGRESS_COMMIT_EVERY == 0 or completed >= total):
                last_committed_progress = completed
                await on_checkpoint(current.model_copy(deep=True))

        try:
            events = await self._engine.extract(
                chunk_set,
                options,
                observer=observer,
                cancellation=cancellation,
            )
        except PipelineCancelledError:
            log.info("抽取已取消 source_config_id=%s", self._source_config_id)
            return True, None
        except asyncio.CancelledError:
            raise
        finally:
            poller.cancel()
            try:
                await poller
            except BaseException:  # noqa: BLE001 - 取消后的 CancelledError 也属预期
                pass

        stats = dict(getattr(events, "stats", {}) or {})
        current.token_usage = int(stats.get("token_usage", 0) or 0)
        # 0.8.2 统计含 zero_event_chunks(整批抽取下语义与 0.7.1 的逐块 eventless 对齐)
        zero_chunks = stats.get("zero_event_chunks", [])
        if isinstance(zero_chunks, (list, tuple)):
            current.eventless_chunk_ids = [str(item) for item in zero_chunks]
        return False, events
