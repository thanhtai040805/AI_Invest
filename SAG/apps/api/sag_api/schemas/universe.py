from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

UniverseNodeKind = Literal["event", "entity"]
UniverseNodeState = Literal["latent", "active"]
UniverseTimelineDirection = Literal["older", "newer"]


class UniverseTimeBucketOut(BaseModel):
    start: datetime
    end: datetime
    count: int = 0


class UniversePartitionOut(BaseModel):
    id: str
    source_id: str
    parent_id: str | None = None
    kind: Literal["source", "topic"]
    key: str
    label: str
    x: float
    y: float
    z: float = 0.0
    radius: float
    node_count: int
    event_count: int = 0
    entity_count: int = 0
    relation_count: int = 0
    density: float = 0.0
    time_buckets: list[UniverseTimeBucketOut] = Field(default_factory=list)
    importance: float


class UniversePolicyOut(BaseModel):
    source_limit: int
    timeline_event_page_size: int
    event_entity_limit: int
    lod_orbit_px: int
    lod_near_px: int
    lod_deep_px: int
    lod_hysteresis_px: int
    lod_debounce_ms: int
    proxy_budget_desktop: int
    proxy_budget_mobile: int
    node_budget_desktop: int
    node_budget_mobile: int
    edge_budget_desktop: int
    edge_budget_mobile: int


class UniverseManifestOut(BaseModel):
    version: str | None = None
    status: Literal["empty", "building", "ready", "stale", "failed"]
    stale: bool = False
    as_of: datetime | None = None
    bounds: dict[str, float] = Field(default_factory=dict)
    partitions: list[UniversePartitionOut] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    policy: UniversePolicyOut


class UniverseRelationOut(BaseModel):
    source_id: str = Field(min_length=1, max_length=64)
    from_id: str = Field(min_length=1, max_length=128)
    to_id: str = Field(min_length=1, max_length=128)
    kind: Literal["mentions", "subevent"] = "mentions"
    weight: float = 1.0
    description: str = ""


class UniverseEvidenceOut(BaseModel):
    source_id: str
    source_name: str
    document_id: str | None = None
    document_name: str | None = None
    chunk_id: str | None = None
    heading: str = ""
    content: str = ""


class UniverseNodeDetailOut(BaseModel):
    id: str
    kind: UniverseNodeKind
    source_id: str
    source_name: str
    label: str
    description: str = ""
    category: str = ""
    start_time: datetime | None = None
    evidence: UniverseEvidenceOut | None = None


class UniverseExpandIn(BaseModel):
    epoch: int = Field(ge=1)
    source_id: str = Field(min_length=1, max_length=64)
    node_kind: UniverseNodeKind
    node_id: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=4, ge=1, le=8)
    cursor: str | None = Field(default=None, max_length=2048)
    snapshot_id: str | None = Field(default=None, max_length=2048)
    after: datetime | None = None
    before: datetime | None = None

    @model_validator(mode="after")
    def validate_time_window(self) -> UniverseExpandIn:
        if self.node_kind == "entity" and self.limit > 4:
            raise ValueError("Khám phá thực thể mỗi trang trả về tối đa bốn gói sự kiện")
        if self.cursor is not None and self.snapshot_id is None:
            raise ValueError("Tiếp trang vùng lân cận phải kèm snapshot_id")
        if self.node_kind == "event" and (self.after is not None or self.before is not None):
            raise ValueError("Mở rộng từ sự kiện sang thực thể không chấp nhận khoảng thời gian")
        if self.after is not None and self.before is not None:
            after = self.after.replace(tzinfo=UTC) if self.after.tzinfo is None else self.after
            before = self.before.replace(tzinfo=UTC) if self.before.tzinfo is None else self.before
            if after.astimezone(UTC) > before.astimezone(UTC):
                raise ValueError("after không được muộn hơn before")
        return self


class UniverseTimelineIn(BaseModel):
    epoch: int = Field(ge=1)
    source_id: str = Field(min_length=1, max_length=64)
    # The product UI deliberately exposes a 10–50 range, while the transport
    # still accepts smaller pages for deterministic pagination probes and
    # internal callers. The public default remains the production page size.
    limit: int = Field(default=20, ge=1, le=50)
    direction: UniverseTimelineDirection = "older"
    cursor: str | None = Field(default=None, max_length=2048)
    snapshot_id: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def validate_snapshot(self) -> UniverseTimelineIn:
        if self.cursor is not None and self.snapshot_id is None:
            raise ValueError("Tiếp trang dòng thời gian phải kèm snapshot_id")
        if self.direction == "newer" and self.cursor is None:
            raise ValueError("Tiếp trang theo hướng mới hơn của dòng thời gian phải kèm cursor")
        return self


class UniversePatchNodeOut(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    kind: UniverseNodeKind
    source_id: str = Field(min_length=1, max_length=64)
    label: str = ""
    description: str = ""
    category: str = ""
    chunk_id: str | None = None
    start_time: datetime | None = None
    importance: float = 0.5
    related_count: int = Field(default=0, ge=0)
    state: UniverseNodeState = "active"


class UniversePageOut(BaseModel):
    returned: int = Field(default=0, ge=0)
    has_more: bool = False
    next_cursor: str | None = Field(default=None, max_length=2048)


class UniverseNeighborPageOut(BaseModel):
    total_unique: int = Field(default=0, ge=0)
    returned_unique: int = Field(default=0, ge=0)
    complete: bool = False
    next_cursor: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def validate_counts(self) -> UniverseNeighborPageOut:
        if self.returned_unique > self.total_unique:
            raise ValueError("returned_unique không thể vượt quá total_unique")
        if self.complete != (self.returned_unique == self.total_unique):
            raise ValueError("complete không khớp với số lượng hàng xóm")
        if self.complete != (self.next_cursor is None):
            raise ValueError("complete không khớp với cursor tiếp trang hàng xóm")
        return self


class UniverseTimelineEventOut(UniversePatchNodeOut):
    kind: Literal["event"]


class UniverseTimelineEntityOut(UniversePatchNodeOut):
    kind: Literal["entity"]


class UniverseTimelineRelationOut(UniverseRelationOut):
    kind: Literal["mentions"] = "mentions"


class UniverseTimelineBundleOut(BaseModel):
    bundle_id: str = Field(min_length=1)
    # Snapshot-stable position in the source's canonical exploration order
    # (newest = 0). The client's counting axis places the event at
    # ordinal × axis-unit, so this must never depend on which page was asked.
    ordinal: int = Field(ge=0)
    event: UniverseTimelineEventOut
    nodes: list[UniverseTimelineEntityOut] = Field(default_factory=list)
    relations: list[UniverseTimelineRelationOut] = Field(default_factory=list)
    neighbor_page: UniverseNeighborPageOut
    cursor_before: str | None = Field(default=None, max_length=2048)
    cursor_after: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def validate_neighborhood(self) -> UniverseTimelineBundleOut:
        entity_ids = [node.id for node in self.nodes]
        if len(set(entity_ids)) != len(entity_ids):
            raise ValueError("Gói sự kiện dòng thời gian chứa thực thể trùng lặp")
        entity_id_set = set(entity_ids)
        relation_keys = {(relation.from_id, relation.to_id) for relation in self.relations}
        if len(relation_keys) != len(self.relations):
            raise ValueError("Gói sự kiện dòng thời gian chứa quan hệ trùng lặp")
        if any(
            relation.source_id != self.event.source_id
            or relation.from_id != self.event.id
            or relation.to_id not in entity_id_set
            for relation in self.relations
        ):
            raise ValueError("Điểm đầu cuối quan hệ của dòng thời gian không thuộc gói sự kiện hiện tại")
        if {relation.to_id for relation in self.relations} != entity_id_set:
            raise ValueError("Các thực thể trả về của dòng thời gian phải mỗi cái có một quan hệ dữ kiện")
        if any(node.source_id != self.event.source_id for node in self.nodes):
            raise ValueError("Gói sự kiện dòng thời gian vượt qua nguồn thông tin")
        if self.neighbor_page.returned_unique != len(entity_id_set):
            raise ValueError("returned_unique không khớp với số thực thể trả về")
        if self.event.related_count != self.neighbor_page.total_unique:
            raise ValueError("Tổng số liên kết của sự kiện không khớp với neighbor_page")
        return self


class UniverseTimelinePageOut(BaseModel):
    returned_bundles: int = Field(default=0, ge=0)
    returned_unique_nodes: int = Field(default=0, ge=0)
    returned_relations: int = Field(default=0, ge=0)
    direction: UniverseTimelineDirection
    has_newer: bool
    newer_cursor: str | None = Field(default=None, max_length=2048)
    has_older: bool
    older_cursor: str | None = Field(default=None, max_length=2048)
    has_more: bool = False
    next_cursor: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def validate_directional_cursors(self) -> UniverseTimelinePageOut:
        if self.has_newer != (self.newer_cursor is not None):
            raise ValueError("has_newer không khớp với newer_cursor")
        if self.has_older != (self.older_cursor is not None):
            raise ValueError("has_older không khớp với older_cursor")
        directional_cursor = self.older_cursor if self.direction == "older" else self.newer_cursor
        if self.has_more != (directional_cursor is not None):
            raise ValueError("has_more không khớp với cursor của hướng yêu cầu")
        if self.next_cursor != directional_cursor:
            raise ValueError("next_cursor không khớp với cursor của hướng yêu cầu")
        return self


class UniverseTimelineSliceOut(BaseModel):
    schema_version: Literal[3] = 3
    epoch: int
    source_id: str = Field(min_length=1, max_length=64)
    source_revision: str = Field(min_length=1, max_length=128)
    snapshot_id: str = Field(min_length=1, max_length=2048)
    request_direction: UniverseTimelineDirection
    request_cursor: str | None = Field(default=None, max_length=2048)
    page_id: str = Field(min_length=1, max_length=128)
    bundles: list[UniverseTimelineBundleOut] = Field(default_factory=list)
    # Snapshot-stable event total of this source: the counting axis' length.
    total_events: int = Field(ge=0)
    page: UniverseTimelinePageOut
    as_of: datetime

    @model_validator(mode="after")
    def validate_page_contract(self) -> UniverseTimelineSliceOut:
        bundle_ids = [bundle.bundle_id for bundle in self.bundles]
        event_ids = [bundle.event.id for bundle in self.bundles]
        after_cursors = [bundle.cursor_after for bundle in self.bundles if bundle.cursor_after is not None]
        before_cursors = [bundle.cursor_before for bundle in self.bundles if bundle.cursor_before is not None]
        if len(set(bundle_ids)) != len(bundle_ids):
            raise ValueError("Trang dòng thời gian chứa gói sự kiện trùng lặp")
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("Trang dòng thời gian chứa sự kiện trùng lặp")
        # Hydration may drop an event inside the page, so ordinals may skip;
        # they must still march strictly older within one page.
        ordinals = [bundle.ordinal for bundle in self.bundles]
        if any(later <= earlier for earlier, later in zip(ordinals, ordinals[1:], strict=False)):
            raise ValueError("Thứ tự gói sự kiện dòng thời gian phải tăng nghiêm ngặt")
        if any(ordinal >= self.total_events for ordinal in ordinals):
            raise ValueError("Thứ tự gói sự kiện dòng thời gian vượt quá tổng số của nguồn")
        if len(set(after_cursors)) != len(after_cursors) or len(set(before_cursors)) != len(before_cursors):
            raise ValueError("Trang dòng thời gian chứa cursor trùng lặp")
        if any(bundle.event.source_id != self.source_id for bundle in self.bundles):
            raise ValueError("Trang dòng thời gian vượt qua nguồn thông tin")
        unique_nodes = {(bundle.event.kind, bundle.event.id) for bundle in self.bundles}
        unique_nodes.update((node.kind, node.id) for bundle in self.bundles for node in bundle.nodes)
        relation_count = sum(len(bundle.relations) for bundle in self.bundles)
        if self.page.returned_bundles != len(self.bundles):
            raise ValueError("returned_bundles không khớp với số gói sự kiện")
        if self.page.returned_unique_nodes != len(unique_nodes):
            raise ValueError("returned_unique_nodes không khớp với số nút")
        if self.page.returned_relations != relation_count:
            raise ValueError("returned_relations không khớp với số quan hệ")
        if self.page.direction != self.request_direction:
            raise ValueError("Hướng trang không khớp với hướng yêu cầu")
        if self.page.has_more and not self.bundles:
            raise ValueError("Trang rỗng không thể khai báo has_more")
        if any(bundle.cursor_after is None for bundle in self.bundles[:-1]):
            raise ValueError("Gói sự kiện không phải cuối thiếu cursor_after")
        if any(bundle.cursor_before is None for bundle in self.bundles[1:]):
            raise ValueError("Gói sự kiện không phải đầu thiếu cursor_before")
        if self.bundles:
            if self.bundles[0].cursor_before != self.page.newer_cursor:
                raise ValueError("Cursor gói sự kiện đầu không khớp với newer_cursor")
            if self.bundles[-1].cursor_after != self.page.older_cursor:
                raise ValueError("Cursor gói sự kiện cuối không khớp với older_cursor")
        if self.request_cursor is not None and self.request_cursor == self.page.next_cursor:
            raise ValueError("Cursor dòng thời gian không tiến tới")
        return self


class UniverseGraphPatchOut(BaseModel):
    schema_version: Literal[2] = 2
    epoch: int
    source_id: str = Field(min_length=1, max_length=64)
    source_revision: str = Field(min_length=1, max_length=128)
    snapshot_id: str = Field(min_length=1, max_length=2048)
    request_cursor: str | None = Field(default=None, max_length=2048)
    page_id: str = Field(min_length=1, max_length=128)
    bundle_id: str = Field(min_length=1, max_length=512)
    anchor: UniversePatchNodeOut
    nodes: list[UniversePatchNodeOut] = Field(default_factory=list)
    relations: list[UniverseTimelineRelationOut] = Field(default_factory=list)
    page: UniversePageOut
    as_of: datetime

    @model_validator(mode="after")
    def validate_page_contract(self) -> UniverseGraphPatchOut:
        if self.anchor.source_id != self.source_id:
            raise ValueError("Điểm neo khám phá không thuộc nguồn thông tin hiện tại")
        node_ids = [node.id for node in self.nodes]
        if self.anchor.id in node_ids or len(set(node_ids)) != len(node_ids):
            raise ValueError("Trang khám phá chứa nút trùng lặp")
        if any(node.source_id != self.source_id for node in self.nodes):
            raise ValueError("Trang khám phá vượt qua nguồn thông tin")

        kinds_by_id = {self.anchor.id: self.anchor.kind}
        kinds_by_id.update((node.id, node.kind) for node in self.nodes)
        relation_keys = [
            (relation.from_id, relation.to_id)
            for relation in self.relations
        ]
        if len(set(relation_keys)) != len(relation_keys):
            raise ValueError("Trang khám phá chứa quan hệ trùng lặp")
        if any(
            relation.source_id != self.source_id
            or kinds_by_id.get(relation.from_id) != "event"
            or kinds_by_id.get(relation.to_id) != "entity"
            for relation in self.relations
        ):
            raise ValueError("Điểm đầu cuối quan hệ khám phá không đầy đủ hoặc sai hướng")

        connected_ids = {
            endpoint
            for relation in self.relations
            for endpoint in (relation.from_id, relation.to_id)
        }
        if any(node.id not in connected_ids for node in self.nodes):
            raise ValueError("Trang khám phá chứa nút không có quan hệ dữ kiện")
        if self.anchor.kind == "event":
            if any(node.kind != "entity" for node in self.nodes):
                raise ValueError("Khám phá sự kiện chỉ được trả về các hàng xóm thực thể")
            if any(relation.from_id != self.anchor.id for relation in self.relations):
                raise ValueError("Quan hệ khám phá sự kiện phải xuất phát từ điểm neo")
            returned = len(self.nodes)
        else:
            event_ids = {node.id for node in self.nodes if node.kind == "event"}
            if any(
                not any(
                    relation.from_id == event_id
                    and relation.to_id == self.anchor.id
                    for relation in self.relations
                )
                for event_id in event_ids
            ):
                raise ValueError("Các sự kiện trả về của khám phá thực thể phải kết nối trực tiếp với điểm neo")
            returned = len(event_ids)
        if self.page.returned != returned:
            raise ValueError("returned không khớp với số lượng hàng xóm chính")
        if self.page.returned > self.anchor.related_count:
            raise ValueError("returned không thể vượt quá tổng số liên kết của điểm neo")
        if self.page.has_more != (self.page.next_cursor is not None):
            raise ValueError("has_more không khớp với next_cursor")
        if self.page.has_more and self.page.returned == 0:
            raise ValueError("Trang khám phá rỗng không thể khai báo has_more")
        if (
            self.request_cursor is not None
            and self.request_cursor == self.page.next_cursor
        ):
            raise ValueError("Cursor khám phá không tiến tới")
        return self


class ExplorationSessionOut(BaseModel):
    id: str
    title: str
    source_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    step_count: int = 0


class ExplorationStepOut(BaseModel):
    id: str
    session_id: str
    query: str
    summary: str
    source_ids: list[str] = Field(default_factory=list)
    event_refs: list[dict] = Field(default_factory=list)
    entity_refs: list[dict] = Field(default_factory=list)
    relation_refs: list[dict] = Field(default_factory=list)
    evidence_refs: list[dict] = Field(default_factory=list)
    camera: dict = Field(default_factory=dict)
    created_at: datetime


class ExplorationDetailOut(BaseModel):
    session: ExplorationSessionOut
    steps: list[ExplorationStepOut] = Field(default_factory=list)
