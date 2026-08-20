"use client";

import * as React from "react";

import { normalizeAvatar } from "./avatar";

export const PET_APPEARANCE_PREFERENCES_VERSION = 1;
export const PET_APPEARANCE_PREFERENCES_STORAGE_KEY =
  "sag:pet-appearance-preferences";

const PET_APPEARANCE_CHANGE_EVENT = "sag:pet-appearance-change";
export const MAX_PET_FACE_PRESETS = 24;

export const DEFAULT_PET_FACE_PRESETS = [
  "@_@",
  "^_^",
  "-_-",
  "o_o",
  "._.",
  ">_<",
  "x_x",
  "AI",
  "01",
  "S",
  "Z",
] as const;

export const PET_APPEARANCE_LIMITS = {
  size: { min: 0.72, max: 1.35, step: 0.01, default: 1 },
  floatStrength: { min: 0.4, max: 1.8, step: 0.05, default: 1 },
  actionRate: { min: 0, max: 2, step: 0.05, default: 1 },
  expressionDelay: { min: 0.5, max: 2, step: 0.1, default: 1 },
} as const;

export type PetFaceMode = "agent" | "custom";

export interface PetAppearancePreferences {
  version: number;
  faceMode: PetFaceMode;
  /** Empty text is a valid custom face and leaves the visor blank. */
  face: string;
  facePresets: string[];
  size: number;
  floatStrength: number;
  actionRate: number;
  expressionDelay: number;
  reduceMotion: boolean;
}

export const DEFAULT_PET_APPEARANCE_PREFERENCES: Readonly<PetAppearancePreferences> =
  Object.freeze({
    version: PET_APPEARANCE_PREFERENCES_VERSION,
    faceMode: "agent",
    face: "",
    facePresets: Object.freeze([...DEFAULT_PET_FACE_PRESETS]) as unknown as string[],
    size: PET_APPEARANCE_LIMITS.size.default,
    floatStrength: PET_APPEARANCE_LIMITS.floatStrength.default,
    actionRate: PET_APPEARANCE_LIMITS.actionRate.default,
    expressionDelay: PET_APPEARANCE_LIMITS.expressionDelay.default,
    reduceMotion: false,
  });

const LEGACY_KEYS = {
  face: "sag:pet-face",
  faceMode: "sag:pet-face-mode",
  facePresets: "sag:pet-face-presets",
  size: "sag:pet-size",
  floatStrength: "sag:pet-float-strength",
  actionRate: "sag:pet-action-rate",
  expressionDelay: "sag:pet-expression-delay",
  reduceMotion: "sag:pet-reduce-motion",
  emoji: "sag:pet-emoji",
} as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function clampNumber(
  value: unknown,
  limits: { min: number; max: number; default: number },
) {
  const numeric =
    typeof value === "number"
      ? value
      : typeof value === "string" && value.trim()
        ? Number(value)
        : Number.NaN;
  return Number.isFinite(numeric)
    ? Math.min(limits.max, Math.max(limits.min, numeric))
    : limits.default;
}

export function normalizePetFace(value: string) {
  const next = normalizeAvatar(value);
  return next === "s" ? "S" : next;
}

function normalizeFacePresets(value: unknown) {
  if (!Array.isArray(value)) return [...DEFAULT_PET_FACE_PRESETS];
  return Array.from(
    new Set(
      value
        .filter((item): item is string => typeof item === "string")
        .map(normalizePetFace)
        .filter(Boolean),
    ),
  ).slice(0, MAX_PET_FACE_PRESETS);
}

export function normalizePetAppearancePreferences(
  value: unknown,
): PetAppearancePreferences {
  const input = isRecord(value) ? value : {};
  return {
    version: PET_APPEARANCE_PREFERENCES_VERSION,
    faceMode: input.faceMode === "custom" ? "custom" : "agent",
    face: normalizePetFace(typeof input.face === "string" ? input.face : ""),
    facePresets: normalizeFacePresets(input.facePresets),
    size: clampNumber(input.size, PET_APPEARANCE_LIMITS.size),
    floatStrength: clampNumber(
      input.floatStrength,
      PET_APPEARANCE_LIMITS.floatStrength,
    ),
    actionRate: clampNumber(input.actionRate, PET_APPEARANCE_LIMITS.actionRate),
    expressionDelay: clampNumber(
      input.expressionDelay,
      PET_APPEARANCE_LIMITS.expressionDelay,
    ),
    reduceMotion: input.reduceMotion === true,
  };
}

export function resolvePetFace(
  preferences: Pick<PetAppearancePreferences, "face" | "faceMode">,
  agentFace: string,
) {
  return preferences.faceMode === "custom"
    ? normalizePetFace(preferences.face)
    : normalizePetFace(agentFace);
}

function cloneDefaults() {
  return normalizePetAppearancePreferences(DEFAULT_PET_APPEARANCE_PREFERENCES);
}

function parseLegacyPresets(raw: string | null) {
  if (raw === null) return undefined;
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return undefined;
  }
}

/** Reads the former per-field keys once so existing users keep their choices. */
export function migrateLegacyPetAppearancePreferences(
  getItem: (key: string) => string | null,
): PetAppearancePreferences {
  const face = getItem(LEGACY_KEYS.face) ?? getItem(LEGACY_KEYS.emoji);
  const faceMode = getItem(LEGACY_KEYS.faceMode);
  const storedPresets = parseLegacyPresets(getItem(LEGACY_KEYS.facePresets));
  return normalizePetAppearancePreferences({
    faceMode: faceMode === "custom" || face !== null ? "custom" : "agent",
    face: face ?? "",
    facePresets: storedPresets ?? DEFAULT_PET_FACE_PRESETS,
    size: getItem(LEGACY_KEYS.size) ?? PET_APPEARANCE_LIMITS.size.default,
    floatStrength:
      getItem(LEGACY_KEYS.floatStrength) ?? PET_APPEARANCE_LIMITS.floatStrength.default,
    actionRate:
      getItem(LEGACY_KEYS.actionRate) ?? PET_APPEARANCE_LIMITS.actionRate.default,
    expressionDelay:
      getItem(LEGACY_KEYS.expressionDelay)
      ?? PET_APPEARANCE_LIMITS.expressionDelay.default,
    reduceMotion: getItem(LEGACY_KEYS.reduceMotion) === "true",
  });
}

let cachedSnapshot: PetAppearancePreferences = cloneDefaults();
let initialized = false;

function readPreferences() {
  if (typeof window === "undefined") return cachedSnapshot;
  const raw = window.localStorage.getItem(PET_APPEARANCE_PREFERENCES_STORAGE_KEY);
  if (raw !== null) {
    try {
      return normalizePetAppearancePreferences(JSON.parse(raw));
    } catch {
      // A malformed new record should not discard valid legacy preferences.
    }
  }
  return migrateLegacyPetAppearancePreferences((key) =>
    window.localStorage.getItem(key));
}

function getSnapshot() {
  if (!initialized && typeof window !== "undefined") {
    cachedSnapshot = readPreferences();
    initialized = true;
  }
  return cachedSnapshot;
}

function getServerSnapshot() {
  return DEFAULT_PET_APPEARANCE_PREFERENCES as PetAppearancePreferences;
}

function refreshSnapshot() {
  cachedSnapshot = readPreferences();
  initialized = true;
}

function subscribe(listener: () => void) {
  const onChange = () => {
    refreshSnapshot();
    listener();
  };
  const onStorage = (event: StorageEvent) => {
    if (
      event.key === PET_APPEARANCE_PREFERENCES_STORAGE_KEY
      || (!window.localStorage.getItem(PET_APPEARANCE_PREFERENCES_STORAGE_KEY)
        && Object.values(LEGACY_KEYS).includes(event.key as never))
    ) {
      onChange();
    }
  };
  window.addEventListener(PET_APPEARANCE_CHANGE_EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(PET_APPEARANCE_CHANGE_EVENT, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

export function setPetAppearancePreferences(
  update:
    | Partial<PetAppearancePreferences>
    | ((current: PetAppearancePreferences) => PetAppearancePreferences),
) {
  if (typeof window === "undefined") return;
  const current = getSnapshot();
  const next = normalizePetAppearancePreferences(
    typeof update === "function" ? update(current) : { ...current, ...update },
  );
  cachedSnapshot = next;
  initialized = true;
  window.localStorage.setItem(
    PET_APPEARANCE_PREFERENCES_STORAGE_KEY,
    JSON.stringify(next),
  );
  window.dispatchEvent(new Event(PET_APPEARANCE_CHANGE_EVENT));
}

export function resetPetAppearancePreferences() {
  if (typeof window === "undefined") return;
  for (const key of Object.values(LEGACY_KEYS)) window.localStorage.removeItem(key);
  setPetAppearancePreferences(cloneDefaults());
}

export function usePetAppearancePreferences() {
  const preferences = React.useSyncExternalStore(
    subscribe,
    getSnapshot,
    getServerSnapshot,
  );
  const update = React.useCallback(setPetAppearancePreferences, []);
  const reset = React.useCallback(resetPetAppearancePreferences, []);
  return { preferences, update, reset };
}
