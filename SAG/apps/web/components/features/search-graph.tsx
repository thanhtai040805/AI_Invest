"use client";

import * as React from "react";
import { useTranslations } from "next-intl";

import type {
  Entity,
  SearchEvent,
  SourceGraphRelation,
  SourceGraphResponse,
} from "@/lib/types";
import { useDetailPanel } from "@/components/features/detail-panel";
import { EventEntityGraph } from "@/components/features/source-graph";

export default function SearchGraph({
  events,
  entities,
  relations,
  onOpenEvent,
}: {
  events: SearchEvent[];
  entities: Entity[];
  relations: SourceGraphRelation[];
  onOpenEvent?: (event: SearchEvent) => void;
}) {
  const t = useTranslations("Search");
  const { open } = useDetailPanel();
  const eventsById = React.useMemo(
    () => new Map(events.map((event) => [event.id, event])),
    [events],
  );
  const graph = React.useMemo<SourceGraphResponse>(
    () => ({
      documents: [],
      events,
      entities,
      relations,
      counts: {
        documents: 0,
        events: events.length,
        entities: entities.length,
        shown_documents: 0,
        shown_events: events.length,
        shown_entities: entities.length,
        shown_relations: relations.length,
      },
      truncated: false,
    }),
    [entities, events, relations],
  );

  return (
    <EventEntityGraph
      graph={graph}
      refreshKey={`${events.length}-${relations.length}`}
      emptyTitle={t("emptyGraph")}
      emptyDescription={t("emptyGraphDescription")}
      onOpenEvent={(event) => {
        const result = eventsById.get(event.id);
        if (!result) return;
        if (onOpenEvent) {
          onOpenEvent(result);
          return;
        }
        if (!result.chunk_id || !result.source_id) return;
        open({
          kind: "chunk",
          sourceId: result.source_id,
          chunkId: result.chunk_id,
          heading: result.title,
          sourceName: result.source_name ?? undefined,
        });
      }}
    />
  );
}
