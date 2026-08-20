import type { SearchStrategy } from "@/lib/retrieval-config";
import type { SearchResponse } from "@/lib/types";

export const DEFAULT_SEARCH_TOP_K = 12;
export const MAX_SEARCH_TOP_K = 50;

export type SearchRunIntent = "replace" | "load-more";
export type SearchPhase = "idle" | "searching" | "streaming" | "loading-more";

export interface SearchIdentity {
  query: string;
  strategy: SearchStrategy;
  sourceIds?: string[];
}

export interface SearchRunResolution {
  intent: SearchRunIntent;
  topK: number;
}

export interface SearchLifecycleState {
  result: SearchResponse | null;
  busy: boolean;
  phase: SearchPhase;
  summaryStreaming: boolean;
  error: string;
  committedSearchKey: string;
  lastQuery: string;
  lastStrategy: SearchStrategy;
  topK: number;
  hasMore: boolean;
}

function clampTopK(value: number): number {
  return Math.max(1, Math.min(Math.trunc(value), MAX_SEARCH_TOP_K));
}

/** A stable identity for the inputs that determine a result set. */
export function createSearchKey({ query, strategy, sourceIds }: SearchIdentity): string {
  const normalizedSourceIds = [...new Set((sourceIds ?? []).filter(Boolean))].sort();
  return JSON.stringify([query.trim(), strategy, normalizedSourceIds]);
}

/**
 * Loading more may retain the committed result only when it expands that exact
 * result set. Any ambiguous request safely becomes a fresh, 12-result search.
 */
export function resolveSearchRun({
  intent = "replace",
  requestedKey,
  requestedTopK,
  committedKey,
  committedTopK,
  hasResult,
  idle,
}: {
  intent?: SearchRunIntent;
  requestedKey: string;
  requestedTopK?: number;
  committedKey: string;
  committedTopK: number;
  hasResult: boolean;
  idle: boolean;
}): SearchRunResolution {
  if (intent === "load-more") {
    const topK = clampTopK(requestedTopK ?? committedTopK + DEFAULT_SEARCH_TOP_K);
    if (
      idle
      && hasResult
      && Boolean(committedKey)
      && requestedKey === committedKey
      && topK > committedTopK
    ) {
      return { intent, topK };
    }
    return { intent: "replace", topK: DEFAULT_SEARCH_TOP_K };
  }
  return {
    intent: "replace",
    topK: clampTopK(requestedTopK ?? DEFAULT_SEARCH_TOP_K),
  };
}

export function canLoadMoreSearch({
  phase,
  hasResult,
  hasMore,
  topK,
  committedKey,
  draftKey,
}: {
  phase: SearchPhase;
  hasResult: boolean;
  hasMore: boolean;
  topK: number;
  committedKey: string;
  draftKey: string;
}): boolean {
  return phase === "idle"
    && hasResult
    && hasMore
    && topK < MAX_SEARCH_TOP_K
    && Boolean(committedKey)
    && draftKey === committedKey;
}

export function beginSearchLifecycle(
  current: SearchLifecycleState,
  run: SearchRunResolution & { strategy: SearchStrategy },
): SearchLifecycleState {
  if (run.intent === "load-more") {
    return {
      ...current,
      busy: true,
      phase: "loading-more",
      summaryStreaming: false,
      error: "",
    };
  }
  return {
    ...current,
    result: null,
    busy: true,
    phase: "searching",
    summaryStreaming: false,
    error: "",
    committedSearchKey: "",
    lastQuery: "",
    lastStrategy: run.strategy,
    topK: run.topK,
    hasMore: false,
  };
}

export function receiveSearchResult(
  current: SearchLifecycleState,
  result: SearchResponse,
): SearchLifecycleState {
  return {
    ...current,
    result,
    busy: true,
    phase: "streaming",
    summaryStreaming: true,
    error: "",
  };
}

export function appendSearchSummary(
  current: SearchLifecycleState,
  delta: string,
): SearchLifecycleState {
  if (!delta || current.phase !== "streaming" || current.result === null) return current;
  return {
    ...current,
    result: {
      ...current.result,
      summary: current.result.summary + delta,
    },
  };
}

export function completeSearchLifecycle(
  current: SearchLifecycleState,
  result: SearchResponse,
  committed: {
    key: string;
    query: string;
    strategy: SearchStrategy;
    topK: number;
    hasMore: boolean;
  },
): SearchLifecycleState {
  const accumulatedSummary = current.result?.summary ?? "";
  return {
    ...current,
    result: result.summary || !accumulatedSummary
      ? result
      : { ...result, summary: accumulatedSummary },
    busy: false,
    phase: "idle",
    summaryStreaming: false,
    error: "",
    committedSearchKey: committed.key,
    lastQuery: committed.query,
    lastStrategy: committed.strategy,
    topK: committed.topK,
    hasMore: committed.hasMore,
  };
}

export function failSearchLifecycle(
  current: SearchLifecycleState,
  message: string,
  rollback: SearchLifecycleState | null,
): SearchLifecycleState {
  if (rollback) {
    return {
      ...rollback,
      busy: false,
      phase: "idle",
      summaryStreaming: false,
      error: message,
    };
  }
  return {
    ...current,
    result: null,
    busy: false,
    phase: "idle",
    summaryStreaming: false,
    error: message,
    committedSearchKey: "",
    lastQuery: "",
    hasMore: false,
  };
}
