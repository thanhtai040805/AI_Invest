import type { Entity, SourceGraphEvent, SourceGraphResponse } from "@/lib/types";

export type EventEntityGraphKind = "event" | "entity";

export interface EventEntityRelation {
  id: string;
  eventId: string;
  entityId: string;
}

export interface EventEntityGraphSlice {
  events: SourceGraphEvent[];
  entities: Entity[];
  relations: EventEntityRelation[];
}

export function eventEntityNodeId(kind: EventEntityGraphKind, id: string) {
  return `${kind}:${id}`;
}

/** Normalize the API graph to the real event-entity relationships rendered by both graph modes. */
export function sliceEventEntityGraph(graph: SourceGraphResponse): EventEntityGraphSlice {
  const eventIds = new Set(graph.events.map((event) => event.id));
  const entityIds = new Set(graph.entities.map((entity) => entity.id));
  const seen = new Set<string>();
  const relations: EventEntityRelation[] = [];

  graph.relations.forEach((relation) => {
    if (relation.kind !== "mentions") return;

    const eventId =
      relation.source_kind === "event" && relation.target_kind === "entity"
        ? relation.source_id
        : relation.target_kind === "event" && relation.source_kind === "entity"
          ? relation.target_id
          : null;
    const entityId =
      relation.source_kind === "event" && relation.target_kind === "entity"
        ? relation.target_id
        : relation.target_kind === "event" && relation.source_kind === "entity"
          ? relation.source_id
          : null;

    if (!eventId || !entityId || !eventIds.has(eventId) || !entityIds.has(entityId)) return;

    const key = `${eventId}:${entityId}`;
    if (seen.has(key)) return;
    seen.add(key);
    relations.push({ id: `mention:${key}`, eventId, entityId });
  });

  return {
    events: graph.events,
    entities: graph.entities,
    relations,
  };
}
