export const SEARCH_STRATEGIES = [
  {
    value: "vector",
    labelKey: "vectorLabel",
    descriptionKey: "vectorDescription",
  },
  {
    value: "multi",
    labelKey: "multiLabel",
    descriptionKey: "multiDescription",
  },
] as const;

export type SearchStrategy = (typeof SEARCH_STRATEGIES)[number]["value"];

export const DEFAULT_SEARCH_STRATEGY: SearchStrategy = "vector";

export function isSearchStrategy(value: unknown): value is SearchStrategy {
  return (
    typeof value === "string" &&
    SEARCH_STRATEGIES.some((strategy) => strategy.value === value)
  );
}

export function getSearchStrategy(value: unknown) {
  return (
    SEARCH_STRATEGIES.find((strategy) => strategy.value === value) ??
    SEARCH_STRATEGIES.find((strategy) => strategy.value === DEFAULT_SEARCH_STRATEGY)!
  );
}
