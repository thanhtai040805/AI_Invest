export type WindowMode = "full" | "window";
export type WindowSize = { width: number; height: number };

export interface WindowLayoutStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export const WINDOW_MODE_STORAGE_KEY = "sag:window";
export const WINDOW_SIZE_STORAGE_KEY = "sag:window-size";
export const DEFAULT_WINDOW_MODE: WindowMode = "window";
export const DEFAULT_WINDOW_SIZE: WindowSize = { width: 1360, height: 860 };
export const MIN_WINDOW_SIZE: WindowSize = { width: 900, height: 560 };

const DISABLED_FEATURE_VALUES = new Set(["0", "false", "no", "off"]);

/** Public build flag. Window scaling is enabled unless a clear false value is supplied. */
export function resolveWindowScalingEnabled(value: string | undefined): boolean {
  if (value === undefined || value.trim() === "") return true;
  return !DISABLED_FEATURE_VALUES.has(value.trim().toLowerCase());
}

export function readWindowMode(
  storage: WindowLayoutStorage | null | undefined,
): WindowMode {
  try {
    const saved = storage?.getItem(WINDOW_MODE_STORAGE_KEY);
    if (saved === "full" || saved === "window") return saved;
    storage?.setItem(WINDOW_MODE_STORAGE_KEY, DEFAULT_WINDOW_MODE);
  } catch {
    /* Fall through to the stable default when storage is unavailable. */
  }
  return DEFAULT_WINDOW_MODE;
}

export function persistWindowMode(
  storage: WindowLayoutStorage | null | undefined,
  mode: WindowMode,
) {
  try {
    storage?.setItem(WINDOW_MODE_STORAGE_KEY, mode);
  } catch {
    /* The in-memory preference still applies for this session. */
  }
}

export function clampWindowSize(
  size: WindowSize,
  viewport: WindowSize,
): WindowSize {
  const maxWidth = Math.max(360, viewport.width - 32);
  const maxHeight = Math.max(420, viewport.height - 32);
  const minWidth = Math.min(MIN_WINDOW_SIZE.width, maxWidth);
  const minHeight = Math.min(MIN_WINDOW_SIZE.height, maxHeight);
  return {
    width: Math.min(Math.max(size.width, minWidth), maxWidth),
    height: Math.min(Math.max(size.height, minHeight), maxHeight),
  };
}

export function readWindowSize(
  storage: WindowLayoutStorage | null | undefined,
  viewport: WindowSize,
): WindowSize {
  try {
    const raw = storage?.getItem(WINDOW_SIZE_STORAGE_KEY);
    if (!raw) return clampWindowSize(DEFAULT_WINDOW_SIZE, viewport);
    const parsed = JSON.parse(raw) as Partial<WindowSize>;
    if (typeof parsed.width !== "number" || typeof parsed.height !== "number") {
      return clampWindowSize(DEFAULT_WINDOW_SIZE, viewport);
    }
    return clampWindowSize({ width: parsed.width, height: parsed.height }, viewport);
  } catch {
    return clampWindowSize(DEFAULT_WINDOW_SIZE, viewport);
  }
}

export function persistWindowSize(
  storage: WindowLayoutStorage | null | undefined,
  size: WindowSize,
) {
  try {
    storage?.setItem(WINDOW_SIZE_STORAGE_KEY, JSON.stringify(size));
  } catch {
    /* The in-memory size still applies for this session. */
  }
}
