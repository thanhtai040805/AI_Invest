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


class UniverseTreeNodeOut(BaseModel):
    id: str
    parent_id: str | None = None
    depth: int = Field(ge=0)
    label: str
    is_leaf: bool
    unit_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    document_count: int = Field(default=0, ge=0)
    top_entities: list[str] = Field(default_factory=list)


class UniverseTreeOut(BaseModel):
    projection: Literal["routing", "source"] = "routing"
    project_id: str
    tree_version: str
    search_epoch: int | None = Field(ge=1)
    active_slot: str | None = None
    routing_recall_at_k: float | None = None
    escape_win_rate: float | None = None
    acl_blackhole_rate: float | None = None
    node_count: int = Field(ge=0)
    unit_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    nodes: list[UniverseTreeNodeOut] = Field(default_factory=list)


class UniverseManifestOut(BaseModel):
    version: str | None = None
    status: Literal["empty", "building", "ready", "stale", "failed"]
    stale: bool = False
    as_of: datetime | None = None
    bounds: dict[str, float] = Field(default_factory=dict)
    partitions: list[UniversePartitionOut] = Field(default_factory=list)
    trees: list[UniverseTreeOut] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    policy: UniversePolicyOut


class UniverseKnowledgeUnitOut(BaseModel):
    id: str
    label: str
    preview: str
    source_id: str
    source_name: str
    document_id: str
    document_version_id: str
    document_name: str
    version_no: int
    block_id: str
    heading: str
    page_from: int
    page_to: int
    anchor: str
    neural_tier: Literal["E0_E1", "E2"] = "E0_E1"
    importance: float = 0.0
    local_confidence: float = 0.0
    e2_reason: str | None = None
    entities: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    cross_doc_synapse_count: int = 0
    checksum: str = ""


class UniverseKnowledgeEdgeOut(BaseModel):
    source: str
    target: str
    weight: float
    kind: Literal["similarity"] = "similarity"
    cross_document: bool = False
    source_document_id: str | None = None
    target_document_id: str | None = None
    source_document_name: str | None = None
    target_document_name: str | None = None
    shared_entities: list[str] = Field(default_factory=list)
    signals: dict[str, float | None] = Field(default_factory=dict)


class UniverseKnowledgePageOut(BaseModel):
    project_id: str
    tree_version: str
    node_id: str
    scope_key: str
    units: list[UniverseKnowledgeUnitOut]
    edges: list[UniverseKnowledgeEdgeOut]
    total: int
    next_cursor: str | None = None


class UniverseKnowledgeEvidenceOut(BaseModel):
    id: str
    tier: str
    kind: str
    quote: str
    confidence: float
    candidate: bool
    start: int
    end: int
    label: str | None = None
    subject: str | None = None
    predicate: str | None = None
    object: str | None = None
    extractor_version: str = ""


class UniverseKnowledgeSynapseOut(BaseModel):
    target_unit_id: str
    target_node_id: str | None = None
    target_label: str
    target_preview: str
    target_heading: str
    target_document_id: str
    target_document_name: str
    target_source_id: str
    target_source_name: str
    target_page_from: int
    target_neural_tier: Literal["E0_E1", "E2"] = "E0_E1"
    weight: float
    cross_document: bool = False
    shared_entities: list[str] = Field(default_factory=list)
    signals: dict[str, float | None] = Field(default_factory=dict)


class UniverseKnowledgeDetailOut(UniverseKnowledgeUnitOut):
    project_id: str
    tree_version: str
    scope_key: str
    content: str
    evidence: list[UniverseKnowledgeEvidenceOut]
    synapses: list[UniverseKnowledgeSynapseOut] = Field(default_factory=list)


class UniverseKnowledgeReferenceIn(BaseModel):
    source_id: str = Field(min_length=1, max_length=128)
    document_version_id: str | None = Field(default=None, max_length=128)
    search_unit_id: str | None = Field(default=None, max_length=128)
    block_id: str | None = Field(default=None, max_length=128)


class UniverseKnowledgeResolveIn(BaseModel):
    references: list[UniverseKnowledgeReferenceIn] = Field(max_length=100)


class UniverseKnowledgeMatchOut(BaseModel):
    project_id: str
    tree_version: str
    node_id: str
    unit_ids: list[str]


class UniverseKnowledgeResolveOut(BaseModel):
    matches: list[UniverseKnowledgeMatchOut]


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
            raise ValueError("实体探索每页最多返回四个事件包")
        if self.cursor is not None and self.snapshot_id is None:
            raise ValueError("邻域续页必须携带 snapshot_id")
        if self.node_kind == "event" and (self.after is not None or self.before is not None):
            raise ValueError("事件到实体的扩展不接受时间范围")
        if self.after is not None and self.before is not None:
            after = self.after.replace(tzinfo=UTC) if self.after.tzinfo is None else self.after
            before = self.before.replace(tzinfo=UTC) if self.before.tzinfo is None else self.before
            if after.astimezone(UTC) > before.astimezone(UTC):
                raise ValueError("after 不能晚于 before")
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
            raise ValueError("时间轴续页必须携带 snapshot_id")
        if self.direction == "newer" and self.cursor is None:
            raise ValueError("向新时间轴续页必须携带 cursor")
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
            raise ValueError("returned_unique 不能超过 total_unique")
        if self.complete != (self.returned_unique == self.total_unique):
            raise ValueError("complete 与邻居计数不一致")
        if self.complete != (self.next_cursor is None):
            raise ValueError("complete 与邻居续页游标不一致")
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
            raise ValueError("时间轴事件包包含重复实体")
        entity_id_set = set(entity_ids)
        relation_keys = {(relation.from_id, relation.to_id) for relation in self.relations}
        if len(relation_keys) != len(self.relations):
            raise ValueError("时间轴事件包包含重复关系")
        if any(
            relation.source_id != self.event.source_id
            or relation.from_id != self.event.id
            or relation.to_id not in entity_id_set
            for relation in self.relations
        ):
            raise ValueError("时间轴关系端点不属于当前事件包")
        if {relation.to_id for relation in self.relations} != entity_id_set:
            raise ValueError("时间轴返回实体必须各有一条事实关系")
        if any(node.source_id != self.event.source_id for node in self.nodes):
            raise ValueError("时间轴事件包跨越了信息源")
        if self.neighbor_page.returned_unique != len(entity_id_set):
            raise ValueError("returned_unique 与返回实体数不一致")
        if self.event.related_count != self.neighbor_page.total_unique:
            raise ValueError("事件关联总数与 neighbor_page 不一致")
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
            raise ValueError("has_newer 与 newer_cursor 不一致")
        if self.has_older != (self.older_cursor is not None):
            raise ValueError("has_older 与 older_cursor 不一致")
        directional_cursor = self.older_cursor if self.direction == "older" else self.newer_cursor
        if self.has_more != (directional_cursor is not None):
            raise ValueError("has_more 与请求方向的游标不一致")
        if self.next_cursor != directional_cursor:
            raise ValueError("next_cursor 与请求方向的游标不一致")
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
            raise ValueError("时间轴页面包含重复事件包")
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("时间轴页面包含重复事件")
        # Hydration may drop an event inside the page, so ordinals may skip;
        # they must still march strictly older within one page.
        ordinals = [bundle.ordinal for bundle in self.bundles]
        if any(later <= earlier for earlier, later in zip(ordinals, ordinals[1:], strict=False)):
            raise ValueError("时间轴事件包序数必须严格递增")
        if any(ordinal >= self.total_events for ordinal in ordinals):
            raise ValueError("时间轴事件包序数超出来源总量")
        if len(set(after_cursors)) != len(after_cursors) or len(set(before_cursors)) != len(before_cursors):
            raise ValueError("时间轴页面包含重复游标")
        if any(bundle.event.source_id != self.source_id for bundle in self.bundles):
            raise ValueError("时间轴页面跨越了信息源")
        unique_nodes = {(bundle.event.kind, bundle.event.id) for bundle in self.bundles}
        unique_nodes.update((node.kind, node.id) for bundle in self.bundles for node in bundle.nodes)
        relation_count = sum(len(bundle.relations) for bundle in self.bundles)
        if self.page.returned_bundles != len(self.bundles):
            raise ValueError("returned_bundles 与事件包数不一致")
        if self.page.returned_unique_nodes != len(unique_nodes):
            raise ValueError("returned_unique_nodes 与节点数不一致")
        if self.page.returned_relations != relation_count:
            raise ValueError("returned_relations 与关系数不一致")
        if self.page.direction != self.request_direction:
            raise ValueError("页面方向与请求方向不一致")
        if self.page.has_more and not self.bundles:
            raise ValueError("空页面不能声明 has_more")
        if any(bundle.cursor_after is None for bundle in self.bundles[:-1]):
            raise ValueError("非末尾事件包缺少 cursor_after")
        if any(bundle.cursor_before is None for bundle in self.bundles[1:]):
            raise ValueError("非首部事件包缺少 cursor_before")
        if self.bundles:
            if self.bundles[0].cursor_before != self.page.newer_cursor:
                raise ValueError("首部事件包游标与 newer_cursor 不一致")
            if self.bundles[-1].cursor_after != self.page.older_cursor:
                raise ValueError("末尾事件包游标与 older_cursor 不一致")
        if self.request_cursor is not None and self.request_cursor == self.page.next_cursor:
            raise ValueError("时间轴游标没有前进")
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
            raise ValueError("探索锚点不属于当前信息源")
        node_ids = [node.id for node in self.nodes]
        if self.anchor.id in node_ids or len(set(node_ids)) != len(node_ids):
            raise ValueError("探索页面包含重复节点")
        if any(node.source_id != self.source_id for node in self.nodes):
            raise ValueError("探索页面跨越了信息源")

        kinds_by_id = {self.anchor.id: self.anchor.kind}
        kinds_by_id.update((node.id, node.kind) for node in self.nodes)
        relation_keys = [
            (relation.from_id, relation.to_id)
            for relation in self.relations
        ]
        if len(set(relation_keys)) != len(relation_keys):
            raise ValueError("探索页面包含重复关系")
        if any(
            relation.source_id != self.source_id
            or kinds_by_id.get(relation.from_id) != "event"
            or kinds_by_id.get(relation.to_id) != "entity"
            for relation in self.relations
        ):
            raise ValueError("探索关系端点不完整或方向错误")

        connected_ids = {
            endpoint
            for relation in self.relations
            for endpoint in (relation.from_id, relation.to_id)
        }
        if any(node.id not in connected_ids for node in self.nodes):
            raise ValueError("探索页面包含没有事实关系的节点")
        if self.anchor.kind == "event":
            if any(node.kind != "entity" for node in self.nodes):
                raise ValueError("事件探索只能返回实体邻居")
            if any(relation.from_id != self.anchor.id for relation in self.relations):
                raise ValueError("事件探索关系必须来自锚点")
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
                raise ValueError("实体探索返回的事件必须直连锚点")
            returned = len(event_ids)
        if self.page.returned != returned:
            raise ValueError("returned 与主邻居数不一致")
        if self.page.returned > self.anchor.related_count:
            raise ValueError("returned 不能超过锚点关联总数")
        if self.page.has_more != (self.page.next_cursor is not None):
            raise ValueError("has_more 与 next_cursor 不一致")
        if self.page.has_more and self.page.returned == 0:
            raise ValueError("空探索页面不能声明 has_more")
        if (
            self.request_cursor is not None
            and self.request_cursor == self.page.next_cursor
        ):
            raise ValueError("探索游标没有前进")
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
