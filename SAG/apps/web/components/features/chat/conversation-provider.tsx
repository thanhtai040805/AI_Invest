"use client";

import * as React from "react";

import {
  ConversationRuntime,
  type ConversationIndexSnapshot,
  type ConversationRuntimeCallbacks,
  type ConversationSessionSnapshot,
  type ConversationTransport,
} from "@/lib/conversation-runtime";

const ConversationRuntimeContext = React.createContext<ConversationRuntime | null>(null);
const ConversationComposerContext = React.createContext<ConversationComposerStore | null>(null);
const EMPTY_CONVERSATION_INDEX: ConversationIndexSnapshot = {
  activeSessionId: null,
  activeRunSessionId: null,
  sessions: [],
};

export interface ConversationComposerScope {
  id: string;
  name: string;
}

export interface ConversationComposerImage {
  file: File;
  url: string;
}

export interface ConversationComposerSnapshot {
  input: string;
  scoped: ConversationComposerScope[];
  images: ConversationComposerImage[];
  webEnabled: boolean;
}

const EMPTY_COMPOSER: ConversationComposerSnapshot = {
  input: "",
  scoped: [],
  images: [],
  webEnabled: false,
};

class ConversationComposerStore {
  private drafts = new Map<string, ConversationComposerSnapshot>();
  private listeners = new Map<string, Set<() => void>>();

  constructor(readonly agentId: string) {}

  getSnapshot = (sessionId: string) => this.drafts.get(sessionId) ?? EMPTY_COMPOSER;

  subscribe = (sessionId: string, listener: () => void) => {
    const listeners = this.listeners.get(sessionId) ?? new Set<() => void>();
    listeners.add(listener);
    this.listeners.set(sessionId, listeners);
    return () => {
      listeners.delete(listener);
      if (listeners.size === 0) this.listeners.delete(sessionId);
    };
  };

  update(
    sessionId: string,
    updater: (current: ConversationComposerSnapshot) => ConversationComposerSnapshot,
  ) {
    const current = this.getSnapshot(sessionId);
    const next = updater(current);
    if (next === current) return;
    this.drafts.set(sessionId, next);
    this.listeners.get(sessionId)?.forEach((listener) => listener());
  }

  dispose() {
    this.drafts.forEach((draft) => {
      draft.images.forEach((image) => URL.revokeObjectURL(image.url));
    });
    this.drafts.clear();
    this.listeners.clear();
  }
}

export interface ConversationProviderProps extends ConversationRuntimeCallbacks {
  agentId: string;
  transport: ConversationTransport;
  flushIntervalMs?: number;
  stopGraceMs?: number;
  maxSessions?: number;
  maxMessagesPerSession?: number;
  children: React.ReactNode;
}

/**
 * Owns one runtime for an authenticated AppShell/agent lifecycle. Individual
 * conversation surfaces merely subscribe, so unmounting a main or mini entry
 * never owns or interrupts the underlying stream.
 */
export function ConversationProvider({
  agentId,
  transport,
  flushIntervalMs,
  stopGraceMs,
  maxSessions,
  maxMessagesPerSession,
  onActivity,
  onUniverseActivation,
  children,
}: ConversationProviderProps) {
  const pendingDisposalsRef = React.useRef(
    new Map<ConversationRuntime, number>(),
  );
  const runtime = React.useMemo(
    () =>
      new ConversationRuntime({
        agentId,
        transport,
        flushIntervalMs,
        stopGraceMs,
        maxSessions,
        maxMessagesPerSession,
      }),
    [
      agentId,
      flushIntervalMs,
      maxMessagesPerSession,
      maxSessions,
      stopGraceMs,
      transport,
    ],
  );
  const composerStore = React.useMemo(() => new ConversationComposerStore(agentId), [agentId]);

  React.useEffect(() => {
    runtime.setCallbacks({ onActivity, onUniverseActivation });
  }, [onActivity, onUniverseActivation, runtime]);

  React.useEffect(() => {
    const pendingDisposals = pendingDisposalsRef.current;
    const pending = pendingDisposals.get(runtime);
    if (pending !== undefined) {
      window.clearTimeout(pending);
      pendingDisposals.delete(runtime);
    }
    return () => {
      const timer = window.setTimeout(() => {
        runtime.dispose();
        pendingDisposals.delete(runtime);
      }, 0);
      pendingDisposals.set(runtime, timer);
    };
  }, [runtime]);

  React.useEffect(() => () => composerStore.dispose(), [composerStore]);

  return (
    <ConversationRuntimeContext.Provider value={runtime}>
      <ConversationComposerContext.Provider value={composerStore}>
        {children}
      </ConversationComposerContext.Provider>
    </ConversationRuntimeContext.Provider>
  );
}

export function useConversationRuntime(): ConversationRuntime {
  const runtime = React.useContext(ConversationRuntimeContext);
  if (!runtime) throw new Error("useConversationRuntime must be used inside ConversationProvider");
  return runtime;
}

export function useOptionalConversationRuntime(): ConversationRuntime | null {
  return React.useContext(ConversationRuntimeContext);
}

function useRuntimeIndex(runtime: ConversationRuntime | null): ConversationIndexSnapshot {
  const subscribe = React.useCallback(
    (listener: () => void) => runtime?.subscribeIndex(listener) ?? (() => {}),
    [runtime],
  );
  const getSnapshot = React.useCallback(
    () => runtime?.getIndexSnapshot() ?? EMPTY_CONVERSATION_INDEX,
    [runtime],
  );
  return React.useSyncExternalStore(
    subscribe,
    getSnapshot,
    getSnapshot,
  );
}

export function useConversationIndex(): ConversationIndexSnapshot {
  return useRuntimeIndex(useConversationRuntime());
}

export function useOptionalConversationIndex(): ConversationIndexSnapshot {
  return useRuntimeIndex(useOptionalConversationRuntime());
}

function useRuntimeSession(
  runtime: ConversationRuntime | null,
  index: ConversationIndexSnapshot,
  sessionId?: string | null,
): ConversationSessionSnapshot | null {
  const resolvedId = sessionId === undefined ? index.activeSessionId : sessionId;
  const subscribe = React.useCallback(
    (listener: () => void) =>
      runtime && resolvedId ? runtime.subscribeSession(resolvedId, listener) : () => {},
    [resolvedId, runtime],
  );
  const getSnapshot = React.useCallback(
    () => (runtime && resolvedId ? runtime.getSessionSnapshot(resolvedId) : null),
    [resolvedId, runtime],
  );
  return React.useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

/** Uses the active session when no explicit session id is supplied. */
export function useConversationSession(
  sessionId?: string | null,
): ConversationSessionSnapshot | null {
  const runtime = useConversationRuntime();
  return useRuntimeSession(runtime, useRuntimeIndex(runtime), sessionId);
}

/** Ambient UI may render outside AppShell; it observes conversations only when one exists. */
export function useOptionalConversationSession(
  sessionId?: string | null,
): ConversationSessionSnapshot | null {
  const runtime = useOptionalConversationRuntime();
  return useRuntimeSession(runtime, useRuntimeIndex(runtime), sessionId);
}

export function useConversationComposer(sessionId: string) {
  const store = React.useContext(ConversationComposerContext);
  if (!store) throw new Error("useConversationComposer must be used inside ConversationProvider");

  const subscribe = React.useCallback(
    (listener: () => void) => store.subscribe(sessionId, listener),
    [sessionId, store],
  );
  const getSnapshot = React.useCallback(() => store.getSnapshot(sessionId), [sessionId, store]);
  const draft = React.useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  const setInput = React.useCallback<React.Dispatch<React.SetStateAction<string>>>(
    (next) => {
      store.update(sessionId, (current) => ({
        ...current,
        input: typeof next === "function" ? next(current.input) : next,
      }));
    },
    [sessionId, store],
  );
  const setScoped = React.useCallback<
    React.Dispatch<React.SetStateAction<ConversationComposerScope[]>>
  >(
    (next) => {
      store.update(sessionId, (current) => ({
        ...current,
        scoped: typeof next === "function" ? next(current.scoped) : next,
      }));
    },
    [sessionId, store],
  );
  const setImages = React.useCallback<
    React.Dispatch<React.SetStateAction<ConversationComposerImage[]>>
  >(
    (next) => {
      store.update(sessionId, (current) => ({
        ...current,
        images: typeof next === "function" ? next(current.images) : next,
      }));
    },
    [sessionId, store],
  );
  const setWebEnabled = React.useCallback<React.Dispatch<React.SetStateAction<boolean>>>(
    (next) => {
      store.update(sessionId, (current) => ({
        ...current,
        webEnabled:
          typeof next === "function" ? next(current.webEnabled) : next,
      }));
    },
    [sessionId, store],
  );

  return { ...draft, setInput, setScoped, setImages, setWebEnabled };
}
