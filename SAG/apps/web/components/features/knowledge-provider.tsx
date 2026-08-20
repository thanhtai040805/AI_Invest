"use client";

import * as React from "react";
import { useTranslations } from "next-intl";

import { api, ApiError } from "@/lib/api";
import type { Source } from "@/lib/types";

interface KnowledgeContextValue {
  sources: Source[] | null;
  error: string;
  ensureLoaded: () => Promise<void>;
  refresh: () => Promise<void>;
  addSource: (source: Source) => void;
}

const KnowledgeContext = React.createContext<KnowledgeContextValue | null>(null);

export function KnowledgeProvider({ children }: { children: React.ReactNode }) {
  const t = useTranslations("Knowledge");
  const [sources, setSources] = React.useState<Source[] | null>(null);
  const [error, setError] = React.useState("");
  const loadedRef = React.useRef(false);
  const pendingRef = React.useRef<Promise<void> | null>(null);

  const load = React.useCallback(async (force: boolean) => {
    if (!force && loadedRef.current) return;
    if (pendingRef.current) return pendingRef.current;

    const request = api
      .listSources()
      .then((next) => {
        setSources(next);
        setError("");
        loadedRef.current = true;
      })
      .catch((reason) => {
        setSources((current) => current ?? []);
        setError(reason instanceof ApiError ? reason.message : t("loadFailed"));
      })
      .finally(() => {
        pendingRef.current = null;
      });

    pendingRef.current = request;
    return request;
  }, [t]);

  const ensureLoaded = React.useCallback(() => load(false), [load]);
  const refresh = React.useCallback(() => load(true), [load]);
  const addSource = React.useCallback((source: Source) => {
    loadedRef.current = true;
    setSources((current) => {
      const rest = (current ?? []).filter((item) => item.id !== source.id);
      return [source, ...rest];
    });
    setError("");
  }, []);

  const value = React.useMemo<KnowledgeContextValue>(
    () => ({ sources, error, ensureLoaded, refresh, addSource }),
    [addSource, ensureLoaded, error, refresh, sources],
  );

  return <KnowledgeContext.Provider value={value}>{children}</KnowledgeContext.Provider>;
}

export function useKnowledgeWorkspace() {
  const value = React.useContext(KnowledgeContext);
  if (!value) throw new Error("useKnowledgeWorkspace must be used inside KnowledgeProvider");
  return value;
}
