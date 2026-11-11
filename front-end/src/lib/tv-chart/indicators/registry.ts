/* ============================================================================
 * TradingView Complete Indicators, Strategies, Profiles, Patterns, Options,
 * Fundamentals, Community, Store & Custom Scripts Registry (280+ Unlocked Items)
 * ============================================================================ */

import type {
  TvCustomScriptDef,
  TvIndicatorId,
  TvIndicatorInstance,
  TvIndicatorMetadata,
} from "../types"
import { ALL_CATALOG_INDICATORS } from "./catalog-data"

export const TV_INDICATOR_REGISTRY: Record<TvIndicatorId, TvIndicatorMetadata> = Object.fromEntries(
  ALL_CATALOG_INDICATORS.map((item) => [item.id, item])
)

export const TV_INDICATOR_LIST: TvIndicatorMetadata[] = [...ALL_CATALOG_INDICATORS]

export function registerCustomScripts(scripts: TvCustomScriptDef[]): void {
  for (const s of scripts) {
    const meta: TvIndicatorMetadata = {
      id: s.id,
      name: s.name,
      shortName: s.shortName || s.name.slice(0, 12),
      viName: s.name,
      description: s.description || `Công thức tùy chỉnh: ${s.formula}`,
      category: "custom",
      section: "my_scripts",
      formula: s.formula,
      allowMultiple: true,
      overlay: s.overlay,
      defaultColor: s.color || "#2962FF",
      params: [],
    }
    TV_INDICATOR_REGISTRY[s.id] = meta
    const idx = TV_INDICATOR_LIST.findIndex((m) => m.id === s.id)
    if (idx >= 0) {
      TV_INDICATOR_LIST[idx] = meta
    } else {
      TV_INDICATOR_LIST.unshift(meta)
    }
  }
}

export function getIndicatorMetadata(
  id: TvIndicatorId,
  instance?: TvIndicatorInstance
): TvIndicatorMetadata {
  const existing = TV_INDICATOR_REGISTRY[id]
  if (existing) return existing
  return {
    id,
    name: instance?.customName || id,
    shortName: instance?.customName?.slice(0, 12) || id.slice(0, 12),
    viName: instance?.customName || id,
    description: instance?.customFormula
      ? `Tập lệnh tùy chỉnh: ${instance.customFormula}`
      : "Tập lệnh phân tích kỹ thuật tùy chỉnh.",
    category: "custom",
    section: "my_scripts",
    formula: instance?.customFormula || "ema(close, 13) - ema(close, 34)",
    overlay: instance?.customOverlay ?? false,
    defaultColor: instance?.color || "#2962FF",
    params: [],
  }
}

export function createIndicatorInstance(
  id: TvIndicatorId,
  customParams?: Partial<Record<string, number>>,
  customColor?: string,
  customDef?: TvCustomScriptDef
): TvIndicatorInstance {
  if (customDef) {
    registerCustomScripts([customDef])
  }
  const meta = getIndicatorMetadata(id)
  const params: Record<string, number> = {}
  for (const p of meta.params) {
    params[p.key] = customParams?.[p.key] ?? p.defaultValue
  }
  return {
    instanceId: `${id}_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
    id,
    visible: true,
    collapsed: false,
    maximized: false,
    paneHeight: meta.overlay ? undefined : id === "VOL" ? 95 : 115,
    color: customColor || meta.defaultColor,
    secondaryColor: meta.secondaryColor,
    tertiaryColor: meta.tertiaryColor,
    lineWidth: 1.6,
    params,
    customFormula: customDef?.formula ?? meta.formula,
    customName: customDef?.name,
    customOverlay: customDef?.overlay,
  }
}

export const DEFAULT_ACTIVE_INDICATORS: TvIndicatorInstance[] = [
  {
    instanceId: "default_sma20",
    id: "SMA",
    visible: true,
    color: "#2962FF",
    lineWidth: 1.75,
    params: { length: 20 },
  },
  {
    instanceId: "default_ema50",
    id: "EMA",
    visible: true,
    color: "#FF6D00",
    lineWidth: 1.5,
    params: { length: 50 },
  },
  {
    instanceId: "default_vol",
    id: "VOL",
    visible: true,
    paneHeight: 92,
    color: "#26A69A",
    secondaryColor: "#F57C00",
    lineWidth: 1.4,
    params: { maLength: 20 },
  },
]
