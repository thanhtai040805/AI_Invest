"use client"

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { getSocket, retainOhlc } from "@/lib/socket"
import {
  fetchTimeframeCandles,
  isIndexSymbol,
  normalizeRawCandles,
  roundToHoseTick,
  toVndPrice,
  type RawInputCandle,
} from "../adapters/aiinvest-datafeed"
import { hitTestDrawing } from "../drawings/drawing-renderer"
import { TV_DRAWING_TOOL_MAP } from "../drawings/tool-groups"
import {
  computeChartMetrics,
  fmtPriceVnd,
  PRICE_AXIS_WIDTH,
  renderChartFrame,
  TIME_AXIS_HEIGHT,
  type RenderFrameMetrics,
} from "../engine/canvas-renderer"
import { computeIndicator, transformSeriesForChartStyle } from "../indicators/calculator"
import {
  createIndicatorInstance,
  DEFAULT_ACTIVE_INDICATORS,
  getIndicatorMetadata,
  registerCustomScripts,
  TV_INDICATOR_REGISTRY,
} from "../indicators/registry"
import {
  ChartSettingsModal,
  DEFAULT_TV_CHART_SETTINGS,
} from "../modals/ChartSettingsModal"
import { IndicatorsModal } from "../modals/IndicatorsModal"
import { ObjectTreeModal } from "../modals/ObjectTreeModal"
import { BottomToolbar } from "../toolbars/BottomToolbar"
import { FloatingDrawingToolbar } from "../toolbars/FloatingDrawingToolbar"
import { LeftDrawingToolbar } from "../toolbars/LeftDrawingToolbar"
import { PaneLegend } from "../toolbars/PaneLegend"
import { StrategyTesterPanel } from "../toolbars/StrategyTesterPanel"
import { TopHeaderToolbar } from "../toolbars/TopHeaderToolbar"
import type {
  TvCandle,
  TvChartSettings,
  TvChartStyle,
  TvComparisonSeries,
  TvCustomScriptDef,
  TvDateRange,
  TvDrawingObject,
  TvDrawingPoint,
  TvDrawingToolId,
  TvIndicatorId,
  TvIndicatorInstance,
  TvMagnetMode,
  TvReplayState,
  TvScaleMode,
  TvTimeframe,
  TvTradingOverlay,
  TvViewportState,
} from "../types"

export interface TradingViewChartProps {
  symbol: string
  seedCandles?: RawInputCandle[]
  trading?: TvTradingOverlay
  initialTimeframe?: TvTimeframe
  initialStyle?: TvChartStyle
  height?: number
  precision?: number
  compact?: boolean
  className?: string
}

const STORAGE_SETTINGS_KEY = "aiinvest_tv_settings_v2"
const STORAGE_INDICATORS_KEY = "aiinvest_tv_indicators_v1"
const STORAGE_FAVORITES_KEY = "aiinvest_tv_favorites_v3"
const STORAGE_CUSTOM_SCRIPTS_KEY = "aiinvest_tv_custom_scripts_v1"

const DEFAULT_FAVORITE_IDS: TvIndicatorId[] = [
  "SMA",
  "EMA",
  "BB",
  "ICHIMOKU",
  "VIP_HUD_DE_TE",
  "VP_VISIBLE",
  "AUTO_FIB_RET",
  "PAT_ALL_CHART",
  "STRAT_SUPERTREND",
  "COM_SMC_LUXALGO",
  "RSI",
  "MACD",
]

const VIEWPORT_AWARE_STUDY_IDS = new Set<TvIndicatorId>([
  "VP_VISIBLE",
  "VP_SESSION",
  "VP_SESSION_HD",
  "VP_PERIODIC",
  "TPO_PERIODIC",
  "TPO_SESSION",
  "VOL_FOOTPRINT",
  "VISIBLE_AVG_PRICE",
])

const NON_TIME_CHART_STYLES = new Set<TvChartStyle>([
  "renko",
  "line_break",
  "kagi",
  "point_and_figure",
  "range",
])

function clampViewport(
  rawStart: number,
  rawEnd: number,
  totalBars: number,
  rightOffsetBars: number
): { startIndex: number; endIndex: number } {
  if (totalBars <= 0) return { startIndex: 0, endIndex: 90 }
  const minSpan = Math.min(8, Math.max(3, totalBars))
  const maxSpan = Math.max(30, totalBars + rightOffsetBars + 16)
  const span = Math.max(minSpan, Math.min(maxSpan, rawEnd - rawStart))

  const minVisibleBars = Math.min(6, totalBars)
  const minEnd = minVisibleBars - 1
  const maxStart = Math.max(0, totalBars - minVisibleBars)

  let startIndex = rawStart
  let endIndex = startIndex + span

  if (endIndex < minEnd) {
    endIndex = minEnd
    startIndex = endIndex - span
  }
  if (startIndex > maxStart) {
    startIndex = maxStart
    endIndex = startIndex + span
  }

  const minAllowedStart = -Math.min(20, Math.floor(span * 0.3))
  const maxAllowedEnd = totalBars - 1 + Math.max(rightOffsetBars + 6, Math.floor(span * 0.4))
  if (startIndex < minAllowedStart) {
    startIndex = minAllowedStart
    endIndex = startIndex + span
  }
  if (endIndex > maxAllowedEnd) {
    endIndex = maxAllowedEnd
    startIndex = Math.max(minAllowedStart, endIndex - span)
  }

  return { startIndex, endIndex }
}

export const TradingViewChart: React.FC<TradingViewChartProps> = ({
  symbol,
  seedCandles,
  trading,
  initialTimeframe = "1D",
  initialStyle = "candles",
  height = 610,
  precision,
  compact = false,
  className = "",
}) => {
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasWrapperRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)

  const isIndex = useMemo(
    () => isIndexSymbol(symbol) || (precision != null && precision > 0),
    [symbol, precision]
  )
  const effectivePrecision = precision ?? (isIndex ? 2 : 0)

  // Chart Core State
  const [timeframe, setTimeframe] = useState<TvTimeframe>(initialTimeframe)
  const [activeDateRange, setActiveDateRange] = useState<TvDateRange | null>("6M")
  const [chartStyle, setChartStyle] = useState<TvChartStyle>(initialStyle)
  const [candles, setCandles] = useState<TvCandle[]>(() =>
    seedCandles && seedCandles.length > 0
      ? normalizeRawCandles(seedCandles, initialTimeframe, isIndex)
      : []
  )
  const [loadingData, setLoadingData] = useState(false)

  // Settings & Indicators State
  const [settings, setSettings] = useState<TvChartSettings>(() => {
    if (typeof window !== "undefined") {
      try {
        const raw = window.localStorage.getItem(STORAGE_SETTINGS_KEY)
        if (raw) return { ...DEFAULT_TV_CHART_SETTINGS, ...JSON.parse(raw) }
      } catch {
        // ignore
      }
    }
    return DEFAULT_TV_CHART_SETTINGS
  })

  const [customScripts, setCustomScripts] = useState<TvCustomScriptDef[]>(() => {
    if (typeof window !== "undefined") {
      try {
        const raw = window.localStorage.getItem(STORAGE_CUSTOM_SCRIPTS_KEY)
        if (raw) {
          const parsed = JSON.parse(raw)
          if (Array.isArray(parsed)) {
            registerCustomScripts(parsed)
            return parsed
          }
        }
      } catch {
        // ignore
      }
    }
    return []
  })

  const [favoriteIds, setFavoriteIds] = useState<TvIndicatorId[]>(() => {
    if (typeof window !== "undefined") {
      try {
        const raw = window.localStorage.getItem(STORAGE_FAVORITES_KEY)
        if (raw) {
          const parsed = JSON.parse(raw)
          if (Array.isArray(parsed)) {
            const valid = parsed.filter((id) => Boolean(TV_INDICATOR_REGISTRY[id]))
            if (valid.length >= 6) return valid
          }
        }
      } catch {
        // ignore
      }
    }
    return DEFAULT_FAVORITE_IDS
  })

  // Auto-heal any stale favorite IDs across HMR or catalog upgrades
  useEffect(() => {
    setFavoriteIds((prev) => {
      const valid = prev.filter((id) => Boolean(TV_INDICATOR_REGISTRY[id]))
      if (valid.length === prev.length && valid.length >= 6) return prev
      const merged = [...valid]
      for (const defId of DEFAULT_FAVORITE_IDS) {
        if (!merged.includes(defId)) merged.push(defId)
      }
      return merged
    })
  }, [])

  const [strategyTesterOpen, setStrategyTesterOpen] = useState(true)

  const [indicators, setIndicators] = useState<TvIndicatorInstance[]>(() => {
    if (typeof window !== "undefined") {
      try {
        const raw = window.localStorage.getItem(STORAGE_INDICATORS_KEY)
        if (raw) {
          const parsed = JSON.parse(raw)
          if (Array.isArray(parsed) && parsed.length > 0) return parsed
        }
      } catch {
        // ignore
      }
    }
    return DEFAULT_ACTIVE_INDICATORS
  })

  const [comparisons, setComparisons] = useState<TvComparisonSeries[]>([])

  // Drawings & Toolbar State
  const [activeTool, setActiveTool] = useState<TvDrawingToolId>("cursor_cross")
  const [magnetMode, setMagnetMode] = useState<TvMagnetMode>("off")
  const [keepDrawing, setKeepDrawing] = useState<boolean>(false)
  const [lockAllDrawings, setLockAllDrawings] = useState<boolean>(false)
  const [hideAllDrawings, setHideAllDrawings] = useState<boolean>(false)
  const [drawings, setDrawings] = useState<TvDrawingObject[]>([])
  const [undoStack, setUndoStack] = useState<TvDrawingObject[][]>([])
  const [redoStack, setRedoStack] = useState<TvDrawingObject[][]>([])
  const [draftDrawing, setDraftDrawing] = useState<TvDrawingObject | null>(null)
  const [selectedDrawingId, setSelectedDrawingId] = useState<string | null>(null)
  const [hoveredDrawingId, setHoveredDrawingId] = useState<string | null>(null)

  // Replay State
  const [replay, setReplay] = useState<TvReplayState>({
    active: false,
    selectingStartBar: false,
    playing: false,
    cutoffIndex: 0,
    speedMs: 650,
  })

  // Modals & Fullscreen State
  const [indicatorsModalOpen, setIndicatorsModalOpen] = useState(false)
  const [editingIndicatorId, setEditingIndicatorId] = useState<string | null>(null)
  const [objectTreeOpen, setObjectTreeOpen] = useState(false)
  const [settingsModalOpen, setSettingsModalOpen] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)

  // Viewport & Canvas Size State
  const [canvasSize, setCanvasSize] = useState<{ width: number; height: number }>({
    width: 900,
    height: height - 76,
  })
  const [viewport, setViewport] = useState<TvViewportState>({
    startIndex: 0,
    endIndex: 90,
    autoScale: true,
    manualPriceScaleFactor: 1,
    manualPricePanDelta: 0,
    scaleMode: "normal",
  })

  // Mouse, Box-Zoom & Drag Interaction State
  const [mousePos, setMousePos] = useState<{ x: number; y: number; inside: boolean }>({
    x: 0,
    y: 0,
    inside: false,
  })
  const [isDraggingOrderLine, setIsDraggingOrderLine] = useState(false)
  const [isZoomMode, setIsZoomMode] = useState(false)
  const [zoomBox, setZoomBox] = useState<{ x1: number; y1: number; x2: number; y2: number } | null>(
    null
  )

  const dragRef = useRef<{
    mode:
      | "none"
      | "pan"
      | "price_axis"
      | "time_axis"
      | "order_line"
      | "move_drawing"
      | "move_drawing_point"
      | "freehand_draw"
      | "zoom_box"
      | "pane_resize"
    startX: number
    startY: number
    startViewport: TvViewportState
    drawingId?: string
    pointIndex?: number
    initialPoints?: TvDrawingPoint[]
    anchorBarIndex?: number
    anchorPrice?: number
    resizePaneId?: string
    resizeStartHeight?: number
    resizePrevPaneId?: string
    resizePrevStartHeight?: number
  }>({
    mode: "none",
    startX: 0,
    startY: 0,
    startViewport: viewport,
  })

  // Persist Settings, Indicators, Favorites & Custom Scripts
  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_SETTINGS_KEY, JSON.stringify(settings))
    } catch {
      // ignore
    }
  }, [settings])

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_INDICATORS_KEY, JSON.stringify(indicators))
    } catch {
      // ignore
    }
  }, [indicators])

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_FAVORITES_KEY, JSON.stringify(favoriteIds))
    } catch {
      // ignore
    }
  }, [favoriteIds])

  useEffect(() => {
    registerCustomScripts(customScripts)
    try {
      window.localStorage.setItem(STORAGE_CUSTOM_SCRIPTS_KEY, JSON.stringify(customScripts))
    } catch {
      // ignore
    }
  }, [customScripts])

  // Load & Persist Drawings per Symbol
  useEffect(() => {
    try {
      const key = `aiinvest_tv_drawings_${symbol.toUpperCase()}`
      const saved = window.localStorage.getItem(key)
      setDrawings(saved ? JSON.parse(saved) : [])
      setUndoStack([])
      setRedoStack([])
      setSelectedDrawingId(null)
      setDraftDrawing(null)
    } catch {
      setDrawings([])
    }
  }, [symbol])

  const commitDrawings = useCallback(
    (next: TvDrawingObject[], recordUndo = true) => {
      setDrawings((prev) => {
        if (recordUndo) {
          setUndoStack((u) => [...u.slice(-29), prev])
          setRedoStack([])
        }
        try {
          const key = `aiinvest_tv_drawings_${symbol.toUpperCase()}`
          window.localStorage.setItem(key, JSON.stringify(next))
        } catch {
          // ignore
        }
        return next
      })
    },
    [symbol]
  )

  // Fetch Multi-Timeframe Candles when symbol or timeframe changes
  useEffect(() => {
    let cancelled = false
    setLoadingData(true)

    fetchTimeframeCandles(symbol, timeframe, seedCandles)
      .then((res) => {
        if (cancelled) return
        if (res.length > 0) {
          setCandles(res)
          const defaultBars = Math.min(res.length, timeframe === "1D" ? (compact ? 65 : 95) : 110)
          setViewport((prev) => ({
            ...prev,
            startIndex: Math.max(0, res.length - defaultBars),
            endIndex: res.length - 1 + settings.rightOffsetBars,
            autoScale: true,
            manualPriceScaleFactor: 1,
            manualPricePanDelta: 0,
          }))
          setReplay((r) => ({
            ...r,
            active: false,
            playing: false,
            selectingStartBar: false,
            cutoffIndex: res.length - 1,
          }))
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingData(false)
      })

    return () => {
      cancelled = true
    }
  }, [symbol, timeframe]) // eslint-disable-line react-hooks/exhaustive-deps

  // Sync seedCandles if parent updates 1D candles in real time (never shrink 250-bar history to 130-bar seed)
  useEffect(() => {
    if (timeframe !== "1D" || !seedCandles || seedCandles.length === 0) return
    const normalized = normalizeRawCandles(seedCandles, "1D", isIndex)
    if (normalized.length === 0) return

    setCandles((prev) => {
      if (prev.length === 0) {
        const defaultBars = Math.min(normalized.length, 95)
        setViewport((vp) => ({
          ...vp,
          startIndex: Math.max(0, normalized.length - defaultBars),
          endIndex: normalized.length - 1 + settings.rightOffsetBars,
        }))
        return normalized
      }
      if (normalized.length > prev.length + 5) {
        const defaultBars = Math.min(normalized.length, 95)
        setViewport((vp) => ({
          ...vp,
          startIndex: Math.max(0, normalized.length - defaultBars),
          endIndex: normalized.length - 1 + settings.rightOffsetBars,
        }))
        return normalized
      }
      // Merge only the latest bar tick without clobbering deeper historical candles
      const seedLast = normalized[normalized.length - 1]
      const prevLast = prev[prev.length - 1]
      if (
        prevLast.close === seedLast.close &&
        prevLast.high === seedLast.high &&
        prevLast.low === seedLast.low &&
        prevLast.volume === seedLast.volume
      ) {
        return prev
      }
      const next = [...prev]
      next[next.length - 1] = {
        ...prevLast,
        close: seedLast.close,
        high: Math.max(prevLast.high, seedLast.high),
        low: Math.min(prevLast.low, seedLast.low),
        volume: Math.max(prevLast.volume, seedLast.volume),
      }
      return next
    })
  }, [seedCandles, timeframe, settings.rightOffsetBars, isIndex])

  // Subscribe to Live Socket.IO OHLC Stream (+ market:indices stream for VNINDEX/VN30/VN100)
  useEffect(() => {
    if (!symbol) return
    const sock = getSocket()
    const release = retainOhlc(symbol, "1D")
    const onOhlcUpdate = (payload: {
      symbol?: string
      open?: number
      high?: number
      low?: number
      close?: number
      volume?: number
    }) => {
      if (String(payload?.symbol ?? "").toUpperCase() !== symbol.toUpperCase()) return
      const close = toVndPrice(payload.close, 0, isIndex)
      if (close <= 0) return
      const open = toVndPrice(payload.open, close, isIndex)
      const high = Math.max(toVndPrice(payload.high, close, isIndex), open, close)
      const low = Math.min(toVndPrice(payload.low, close, isIndex), open, close)
      const vol = Math.max(0, Number(payload.volume ?? 0))

      setCandles((prev) => {
        if (prev.length === 0) return prev
        const next = [...prev]
        const lastIdx = next.length - 1
        const last = next[lastIdx]
        next[lastIdx] = {
          ...last,
          close,
          high: Math.max(last.high, high),
          low: Math.min(last.low, low),
          volume: vol > 0 ? Math.max(last.volume, vol) : last.volume,
        }
        return next
      })
    }

    const onIndicesUpdate = (payload: unknown) => {
      if (!isIndex) return
      const rows = Array.isArray(payload)
        ? payload
        : Array.isArray((payload as { indices?: unknown })?.indices)
          ? ((payload as { indices: Array<Record<string, unknown>> }).indices ?? [])
          : []
      const cleanSym = symbol.trim().toUpperCase().replace("-", "")
      const match = rows.find(
        (r: Record<string, unknown>) =>
          String(r?.symbol ?? r?.code ?? r?.name ?? "")
            .toUpperCase()
            .replace("-", "") === cleanSym
      )
      if (!match) return
      const val = toVndPrice(match.value ?? match.price ?? match.close, 0, true)
      if (val <= 0) return
      setCandles((prev) => {
        if (prev.length === 0) return prev
        const last = prev[prev.length - 1]
        if (Math.abs(last.close - val) < 0.005) return prev
        const next = [...prev]
        next[next.length - 1] = {
          ...last,
          close: val,
          high: Math.max(last.high, val),
          low: Math.min(last.low, val),
        }
        return next
      })
    }

    sock.on("ohlc:update", onOhlcUpdate)
    if (isIndex) {
      sock.on("market:indices", onIndicesUpdate)
    }
    return () => {
      sock.off("ohlc:update", onOhlcUpdate)
      if (isIndex) {
        sock.off("market:indices", onIndicesUpdate)
      }
      release()
    }
  }, [symbol, isIndex])

  // Also sync latest candle close with trading.currentPrice
  useEffect(() => {
    const livePrice = trading?.currentPrice ?? 0
    if (livePrice <= 0) return
    const eps = isIndex ? 0.005 : 1
    setCandles((prev) => {
      if (prev.length === 0) return prev
      const last = prev[prev.length - 1]
      if (Math.abs(last.close - livePrice) < eps) return prev
      const next = [...prev]
      next[next.length - 1] = {
        ...last,
        close: livePrice,
        high: Math.max(last.high, livePrice),
        low: Math.min(last.low, livePrice),
      }
      return next
    })
  }, [trading?.currentPrice, isIndex])

  // Bar Replay Timer
  useEffect(() => {
    if (!replay.active || !replay.playing) return
    const timer = window.setInterval(() => {
      setReplay((prev) => {
        if (prev.cutoffIndex >= candles.length - 1) {
          return { ...prev, playing: false }
        }
        return { ...prev, cutoffIndex: prev.cutoffIndex + 1 }
      })
    }, replay.speedMs)
    return () => window.clearInterval(timer)
  }, [replay.active, replay.playing, replay.speedMs, candles.length])

  // Keep React `isFullscreen` strictly synchronized when browser exits Fullscreen natively (e.g. pressing Esc)
  useEffect(() => {
    const syncFullscreen = () => {
      const doc = document as Document & { webkitFullscreenElement?: Element | null }
      const fsEl = document.fullscreenElement ?? doc.webkitFullscreenElement ?? null
      const active = Boolean(fsEl && containerRef.current && fsEl === containerRef.current)
      setIsFullscreen(active)
    }
    document.addEventListener("fullscreenchange", syncFullscreen)
    document.addEventListener("webkitfullscreenchange", syncFullscreen)
    return () => {
      document.removeEventListener("fullscreenchange", syncFullscreen)
      document.removeEventListener("webkitfullscreenchange", syncFullscreen)
    }
  }, [])

  // ResizeObserver for Responsive High-DPI Canvas (with post-fullscreen settle frames)
  useEffect(() => {
    const el = canvasWrapperRef.current
    if (!el) return
    const updateSize = () => {
      const rect = el.getBoundingClientRect()
      if (rect.width > 40 && rect.height > 40) {
        const nextW = Math.floor(rect.width)
        const nextH = Math.floor(rect.height)
        setCanvasSize((prev) =>
          prev.width === nextW && prev.height === nextH ? prev : { width: nextW, height: nextH }
        )
      }
    }
    updateSize()
    const rafId = window.requestAnimationFrame(updateSize)
    const settleTimer = window.setTimeout(updateSize, 90)
    const ro = new ResizeObserver(updateSize)
    ro.observe(el)
    return () => {
      window.cancelAnimationFrame(rafId)
      window.clearTimeout(settleTimer)
      ro.disconnect()
    }
  }, [isFullscreen, height, compact])

  // Compute Display Candles across all 16 Chart Styles (incl. Heikin-Ashi, Renko, Line Break, Kagi, P&F, Range)
  const effectiveCandles = useMemo(() => {
    if (replay.active && replay.cutoffIndex >= 0 && replay.cutoffIndex < candles.length) {
      return candles.slice(0, replay.cutoffIndex + 1)
    }
    return candles
  }, [candles, replay.active, replay.cutoffIndex])

  const displayCandles = useMemo(() => {
    return transformSeriesForChartStyle(effectiveCandles, chartStyle)
  }, [effectiveCandles, chartStyle])

  // Re-frame viewport smoothly when switching to/from non-time-based chart styles (Renko, Kagi, Line Break, P&F, Range)
  const prevChartStyleRef = useRef<TvChartStyle>(chartStyle)
  useEffect(() => {
    const prevStyle = prevChartStyleRef.current
    prevChartStyleRef.current = chartStyle
    if (prevStyle === chartStyle || displayCandles.length === 0) return
    if (NON_TIME_CHART_STYLES.has(chartStyle) || NON_TIME_CHART_STYLES.has(prevStyle)) {
      const defaultBars = Math.min(displayCandles.length, 85)
      setViewport((vp) => ({
        ...vp,
        startIndex: Math.max(0, displayCandles.length - defaultBars),
        endIndex: displayCandles.length - 1 + settings.rightOffsetBars,
        autoScale: true,
        manualPriceScaleFactor: 1,
        manualPricePanDelta: 0,
      }))
    }
  }, [chartStyle, displayCandles.length, settings.rightOffsetBars])

  // Compute all Active Studies, Strategies, Volume Profiles, Patterns, Options, Fundamentals & Custom Scripts
  const hasViewportAwareIndicator = useMemo(
    () => indicators.some((i) => i.visible && VIEWPORT_AWARE_STUDY_IDS.has(i.id)),
    [indicators]
  )
  const vpStartBar = hasViewportAwareIndicator ? Math.floor(viewport.startIndex) : 0
  const vpEndBar = hasViewportAwareIndicator
    ? Math.ceil(viewport.endIndex)
    : Math.max(0, displayCandles.length - 1)

  const computedIndicators = useMemo(() => {
    return indicators.map((inst) =>
      computeIndicator(displayCandles, inst, {
        visibleStart: vpStartBar,
        visibleEnd: vpEndBar,
        symbol,
      })
    )
  }, [displayCandles, indicators, vpStartBar, vpEndBar, symbol])

  const overlayIndicators = useMemo(
    () => computedIndicators.filter((c) => c.meta.overlay),
    [computedIndicators]
  )

  const isEmbeddedCompact = compact && !isFullscreen

  const subPaneIndicators = useMemo(
    () => (isEmbeddedCompact ? [] : computedIndicators.filter((c) => !c.meta.overlay)),
    [computedIndicators, isEmbeddedCompact]
  )

  const strategyIndicators = useMemo(
    () =>
      isEmbeddedCompact
        ? []
        : computedIndicators.filter((c) => c.instance.visible && Boolean(c.strategyReport)),
    [computedIndicators, isEmbeddedCompact]
  )

  // Compute Layout & Coordinate Metrics
  const metrics: RenderFrameMetrics = useMemo(() => {
    return computeChartMetrics(
      canvasSize.width,
      canvasSize.height,
      displayCandles,
      displayCandles,
      viewport,
      settings,
      overlayIndicators,
      subPaneIndicators,
      trading,
      effectivePrecision
    )
  }, [
    canvasSize.width,
    canvasSize.height,
    displayCandles,
    viewport,
    settings,
    overlayIndicators,
    subPaneIndicators,
    trading,
    effectivePrecision,
  ])

  // Determine Hovered Bar Index & Active Candle for Legend/Data Window
  const hoveredBarIndex = useMemo(() => {
    if (displayCandles.length === 0) return 0
    if (mousePos.inside && mousePos.x >= metrics.plotLeft && mousePos.x <= metrics.plotRight) {
      const idx = Math.round(metrics.coordConverter.xToBarIndex(mousePos.x))
      return Math.max(0, Math.min(displayCandles.length - 1, idx))
    }
    return displayCandles.length - 1
  }, [displayCandles.length, mousePos, metrics])

  const activeCandle = displayCandles[hoveredBarIndex] ?? null
  const prevCandle = hoveredBarIndex > 0 ? displayCandles[hoveredBarIndex - 1] : null

  // Apply Magnet Mode Snapping
  const resolveCursorPoint = useCallback(
    (x: number, y: number): TvDrawingPoint => {
      const rawBarIndex = metrics.coordConverter.xToBarIndex(x)
      const clampedIdx = Math.max(
        0,
        Math.min(displayCandles.length - 1, Math.round(rawBarIndex))
      )
      const candle = displayCandles[clampedIdx]
      let price = metrics.coordConverter.yToPrice(y)

      if (magnetMode !== "off" && candle) {
        const candidates = [candle.open, candle.high, candle.low, candle.close]
        let nearest = candidates[0]
        let bestDistPx = Infinity
        for (const cPrice of candidates) {
          const cy = metrics.coordConverter.priceToY(cPrice)
          const d = Math.abs(cy - y)
          if (d < bestDistPx) {
            bestDistPx = d
            nearest = cPrice
          }
        }
        if (magnetMode === "strong" || bestDistPx <= 24) {
          price = nearest
        }
      }

      return {
        barIndex: magnetMode !== "off" ? clampedIdx : Math.round(rawBarIndex * 10) / 10,
        timestamp: candle?.timestamp ?? Date.now(),
        price: roundToHoseTick(price, isIndex),
      }
    },
    [metrics.coordConverter, displayCandles, magnetMode, isIndex]
  )

  // Render High-DPI Canvas Frame
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext("2d")
    if (!ctx) return

    const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1
    const targetW = Math.floor(canvasSize.width * dpr)
    const targetH = Math.floor(canvasSize.height * dpr)

    if (canvas.width !== targetW || canvas.height !== targetH) {
      canvas.width = targetW
      canvas.height = targetH
    }

    ctx.save()
    ctx.scale(dpr, dpr)

    renderChartFrame({
      ctx,
      width: canvasSize.width,
      height: canvasSize.height,
      candles: displayCandles,
      displayCandles,
      chartStyle,
      viewport,
      settings,
      overlayIndicators,
      subPaneIndicators,
      comparisons,
      drawings: hideAllDrawings ? [] : drawings,
      draftDrawing,
      selectedDrawingId,
      hoveredDrawingId,
      trading,
      replay,
      mousePos,
      isDraggingOrderLine,
      zoomBox,
      precision: effectivePrecision,
    })

    ctx.restore()
  }, [
    canvasSize,
    effectiveCandles,
    displayCandles,
    chartStyle,
    viewport,
    settings,
    overlayIndicators,
    subPaneIndicators,
    comparisons,
    drawings,
    hideAllDrawings,
    draftDrawing,
    selectedDrawingId,
    hoveredDrawingId,
    trading,
    replay,
    mousePos,
    isDraggingOrderLine,
    zoomBox,
    effectivePrecision,
  ])

  // Keyboard Shortcuts (Esc, Delete/Backspace, Ctrl+Z / Ctrl+Y)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (
        document.activeElement &&
        ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)
      ) {
        return
      }
      if (e.key === "Escape") {
        if (isZoomMode || zoomBox) {
          setIsZoomMode(false)
          setZoomBox(null)
        } else if (replay.selectingStartBar) {
          setReplay((prev) => ({ ...prev, selectingStartBar: false }))
        } else if (draftDrawing) {
          setDraftDrawing(null)
        } else if (selectedDrawingId) {
          setSelectedDrawingId(null)
        } else if (activeTool !== "cursor_cross") {
          setActiveTool("cursor_cross")
        } else if (isFullscreen) {
          if (document.fullscreenElement) {
            document.exitFullscreen?.().catch(() => {})
          }
          setIsFullscreen(false)
        }
      } else if ((e.key === "Delete" || e.key === "Backspace") && selectedDrawingId) {
        e.preventDefault()
        commitDrawings(drawings.filter((d) => d.id !== selectedDrawingId))
        setSelectedDrawingId(null)
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault()
        if (undoStack.length > 0) {
          const prev = undoStack[undoStack.length - 1]
          setUndoStack((u) => u.slice(0, -1))
          setRedoStack((r) => [...r, drawings])
          commitDrawings(prev, false)
        }
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") {
        e.preventDefault()
        if (redoStack.length > 0) {
          const next = redoStack[redoStack.length - 1]
          setRedoStack((r) => r.slice(0, -1))
          setUndoStack((u) => [...u, drawings])
          commitDrawings(next, false)
        }
      }
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [
    isZoomMode,
    zoomBox,
    replay.selectingStartBar,
    draftDrawing,
    selectedDrawingId,
    activeTool,
    isFullscreen,
    drawings,
    undoStack,
    redoStack,
    commitDrawings,
  ])

  // Native Non-Passive Wheel Listener (Smooth Exponential Zoom + Horizontal Pan, Zero Page Scroll)
  const wheelContextRef = useRef({
    totalBars: displayCandles.length,
    rightOffsetBars: settings.rightOffsetBars,
    metrics,
  })
  wheelContextRef.current = {
    totalBars: displayCandles.length,
    rightOffsetBars: settings.rightOffsetBars,
    metrics,
  }

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const onNativeWheel = (e: WheelEvent) => {
      e.preventDefault()
      e.stopPropagation()

      const { totalBars, rightOffsetBars, metrics: curMetrics } = wheelContextRef.current
      if (totalBars < 5) return

      const rect = canvas.getBoundingClientRect()
      const x = e.clientX - rect.left
      const rawDeltaY = e.deltaMode === 1 ? e.deltaY * 32 : e.deltaY
      const rawDeltaX = e.deltaMode === 1 ? e.deltaX * 32 : e.deltaX

      // Horizontal Trackpad Swipe or Shift + Wheel -> Smooth Horizontal Pan
      if (e.shiftKey || Math.abs(rawDeltaX) > Math.abs(rawDeltaY) * 1.15) {
        const panPx = Math.abs(rawDeltaX) > Math.abs(rawDeltaY) ? rawDeltaX : rawDeltaY
        setViewport((prev) => {
          const span = Math.max(8, prev.endIndex - prev.startIndex)
          const deltaBars = (panPx / Math.max(1, curMetrics.plotWidth)) * span
          const clamped = clampViewport(
            prev.startIndex + deltaBars,
            prev.endIndex + deltaBars,
            totalBars,
            rightOffsetBars
          )
          return { ...prev, ...clamped }
        })
        return
      }

      if (Math.abs(rawDeltaY) < 0.5) return
      const clampedDelta = Math.max(-160, Math.min(160, rawDeltaY))
      const zoomFactor = Math.exp(clampedDelta * 0.0022)

      setViewport((prev) => {
        const span = Math.max(8, prev.endIndex - prev.startIndex)
        const minSpan = Math.min(8, Math.max(3, totalBars))
        const maxSpan = Math.max(30, totalBars + rightOffsetBars + 16)
        const newSpan = Math.max(minSpan, Math.min(maxSpan, span * zoomFactor))

        // If cursor is on or past the latest candle / right price axis, anchor to right edge
        const lastBarX = curMetrics.coordConverter.barIndexToX(totalBars - 1)
        let rawStart: number
        let rawEnd: number
        if (x >= lastBarX - 10) {
          rawEnd = prev.endIndex
          rawStart = rawEnd - newSpan
        } else {
          const cursorRatio = Math.max(
            0,
            Math.min(1, (x - curMetrics.plotLeft) / Math.max(1, curMetrics.plotWidth))
          )
          const anchorBar = prev.startIndex + cursorRatio * span
          rawStart = anchorBar - cursorRatio * newSpan
          rawEnd = rawStart + newSpan
        }

        const clamped = clampViewport(rawStart, rawEnd, totalBars, rightOffsetBars)
        return { ...prev, ...clamped }
      })
    }

    canvas.addEventListener("wheel", onNativeWheel, { passive: false })
    return () => canvas.removeEventListener("wheel", onNativeWheel)
  }, [])

  // Zoom / Pan Button Helpers
  const zoomByFactor = useCallback(
    (factor: number) => {
      if (displayCandles.length < 3) return
      setViewport((prev) => {
        const span = Math.max(8, prev.endIndex - prev.startIndex)
        const newSpan = span * factor
        const clamped = clampViewport(
          prev.endIndex - newSpan,
          prev.endIndex,
          displayCandles.length,
          settings.rightOffsetBars
        )
        return { ...prev, ...clamped }
      })
    },
    [displayCandles.length, settings.rightOffsetBars]
  )

  const panByDirection = useCallback(
    (direction: -1 | 1) => {
      if (displayCandles.length < 3) return
      setViewport((prev) => {
        const span = Math.max(8, prev.endIndex - prev.startIndex)
        const step = Math.max(4, span * 0.22) * direction
        const clamped = clampViewport(
          prev.startIndex + step,
          prev.endIndex + step,
          displayCandles.length,
          settings.rightOffsetBars
        )
        return { ...prev, ...clamped }
      })
    },
    [displayCandles.length, settings.rightOffsetBars]
  )

  // Detect Sub-Pane Splitter Line under Cursor
  const findSplitterAt = useCallback(
    (x: number, y: number) => {
      if (x < metrics.plotLeft || x > metrics.plotRight) return null
      for (let i = 0; i < metrics.subPanes.length; i++) {
        const sp = metrics.subPanes[i]
        if (Math.abs(y - sp.top) <= 5) {
          return { index: i, subPane: sp }
        }
      }
      return null
    },
    [metrics.plotLeft, metrics.plotRight, metrics.subPanes]
  )

  // Check if Y is near the Interactive Order Price Line (ONLY when an explicit draft order exists)
  const isNearOrderLine = useCallback(
    (x: number, y: number): boolean => {
      if (!trading || !trading.hasDraftOrder || !settings.showOrderLine || trading.orderPrice <= 0) {
        return false
      }
      if (x < metrics.plotLeft || x > metrics.plotRight) return false
      if (y < metrics.mainPane.top || y > metrics.mainPane.bottom) return false
      const orderY = metrics.coordConverter.priceToY(trading.orderPrice)
      return Math.abs(y - orderY) <= 8
    },
    [trading, settings.showOrderLine, metrics]
  )

  // Canvas Mouse Down
  const handleMouseDown = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      if (e.button !== 0) return
      const rect = e.currentTarget.getBoundingClientRect()
      const x = e.clientX - rect.left
      const y = e.clientY - rect.top

      // 1. Check if clicking the "+" Quick-Order Button on the Right Price Axis
      if (
        trading &&
        x >= metrics.plotRight - 22 &&
        x <= metrics.plotRight + 2 &&
        y >= metrics.mainPane.top &&
        y <= metrics.mainPane.bottom
      ) {
        const picked = roundToHoseTick(metrics.coordConverter.yToPrice(y))
        if (picked > 0) {
          trading.onPickPriceFromChart(picked)
        }
        return
      }

      // 2. Right Price Axis Vertical Drag (Scale Price manually)
      if (x > metrics.plotRight) {
        dragRef.current = {
          mode: "price_axis",
          startX: x,
          startY: y,
          startViewport: viewport,
        }
        return
      }

      // 3. Bottom Time Axis Horizontal Drag (Scale Time/Bars)
      if (y > metrics.plotBottom) {
        dragRef.current = {
          mode: "time_axis",
          startX: x,
          startY: y,
          startViewport: viewport,
        }
        return
      }

      // 3.5 Sub-Pane Splitter Drag Resize
      const hitSplitter = findSplitterAt(x, y)
      if (hitSplitter && !hitSplitter.subPane.computed?.instance.collapsed) {
        const prevSp = hitSplitter.index > 0 ? metrics.subPanes[hitSplitter.index - 1] : undefined
        dragRef.current = {
          mode: "pane_resize",
          startX: x,
          startY: y,
          startViewport: viewport,
          resizePaneId: hitSplitter.subPane.id,
          resizeStartHeight: hitSplitter.subPane.height,
          resizePrevPaneId:
            prevSp && !prevSp.computed?.instance.collapsed ? prevSp.id : undefined,
          resizePrevStartHeight: prevSp?.height,
        }
        return
      }

      // 4. Bar Replay Cut Selection Mode
      if (replay.active && replay.selectingStartBar) {
        const cutIdx = Math.max(
          5,
          Math.min(candles.length - 1, Math.round(metrics.coordConverter.xToBarIndex(x)))
        )
        setReplay((prev) => ({
          ...prev,
          selectingStartBar: false,
          cutoffIndex: cutIdx,
        }))
        return
      }

      // 4.5 Interactive Box-Zoom Mode
      if (isZoomMode) {
        setZoomBox({ x1: x, y1: y, x2: x, y2: y })
        dragRef.current = {
          mode: "zoom_box",
          startX: x,
          startY: y,
          startViewport: viewport,
        }
        return
      }

      // 5. Active Drawing Tool Creation
      const isCursorTool = activeTool.startsWith("cursor_")
      if (!isCursorTool) {
        const toolDef = TV_DRAWING_TOOL_MAP[activeTool]
        const pt = resolveCursorPoint(x, y)

        // Freehand Brush / Highlighter
        if (activeTool === "LineToolBrush" || activeTool === "LineToolHighlighter") {
          const newId = `draw_${Date.now()}`
          const draft: TvDrawingObject = {
            id: newId,
            tool: activeTool,
            group: toolDef.group,
            points: [pt],
            color: toolDef.defaultColor || "#2962FF",
            fillColor: "rgba(41, 98, 255, 0.14)",
            lineWidth: activeTool === "LineToolHighlighter" ? 4 : 2,
            lineStyle: "solid",
            locked: false,
            visible: true,
            createdAt: Date.now(),
          }
          setDraftDrawing(draft)
          dragRef.current = {
            mode: "freehand_draw",
            startX: x,
            startY: y,
            startViewport: viewport,
          }
          return
        }

        // Single-point tools (HorzLine, VertLine, AnchoredVWAP, Flag, PriceLabel, Text, etc.)
        if (toolDef.requiredPoints === 1) {
          const newObj: TvDrawingObject = {
            id: `draw_${Date.now()}`,
            tool: activeTool,
            group: toolDef.group,
            points: [pt],
            color: toolDef.defaultColor || "#2962FF",
            fillColor: "rgba(41, 98, 255, 0.14)",
            lineWidth: 2,
            lineStyle: "solid",
            text: toolDef.defaultText,
            locked: false,
            visible: true,
            createdAt: Date.now(),
          }
          commitDrawings([...drawings, newObj])
          setSelectedDrawingId(newObj.id)
          if (!keepDrawing) setActiveTool("cursor_cross")
          return
        }

        // Multi-point tools (2, 3, or 5 points)
        if (!draftDrawing) {
          const newDraft: TvDrawingObject = {
            id: `draw_${Date.now()}`,
            tool: activeTool,
            group: toolDef.group,
            points: [pt, pt],
            color: toolDef.defaultColor || "#2962FF",
            fillColor: "rgba(41, 98, 255, 0.14)",
            lineWidth: 2,
            lineStyle: "solid",
            text: toolDef.defaultText,
            locked: false,
            visible: true,
            createdAt: Date.now(),
          }
          setDraftDrawing(newDraft)
        } else {
          const updatedPoints = [...draftDrawing.points.slice(0, -1), pt]
          if (updatedPoints.length >= toolDef.requiredPoints) {
            const finalized: TvDrawingObject = {
              ...draftDrawing,
              points: updatedPoints,
            }
            commitDrawings([...drawings, finalized])
            setSelectedDrawingId(finalized.id)
            setDraftDrawing(null)
            if (!keepDrawing) setActiveTool("cursor_cross")
          } else {
            setDraftDrawing({
              ...draftDrawing,
              points: [...updatedPoints, pt],
            })
          }
        }
        return
      }

      // 6. Eraser Mode
      if (activeTool === "cursor_eraser" && !hideAllDrawings) {
        const hit = hitTestDrawing(x, y, drawings, metrics.coordConverter)
        if (hit) {
          const target = drawings.find((item) => item.id === hit.drawingId)
          if (target && !target.locked) {
            commitDrawings(drawings.filter((item) => item.id !== hit.drawingId))
            if (selectedDrawingId === hit.drawingId) setSelectedDrawingId(null)
            return
          }
        }
      }

      // 7. Hit-test Existing Drawings for Selection / Dragging
      if (!hideAllDrawings) {
        const hit = hitTestDrawing(x, y, drawings, metrics.coordConverter)
        if (hit) {
          const d = drawings.find((item) => item.id === hit.drawingId)
          if (d && d.visible) {
            setSelectedDrawingId(d.id)
            if (!d.locked && !lockAllDrawings) {
              const cursorPt = resolveCursorPoint(x, y)
              dragRef.current = {
                mode: hit.pointIndex !== null ? "move_drawing_point" : "move_drawing",
                startX: x,
                startY: y,
                startViewport: viewport,
                drawingId: d.id,
                pointIndex: hit.pointIndex ?? undefined,
                initialPoints: d.points.map((p) => ({ ...p })),
                anchorBarIndex: cursorPt.barIndex,
                anchorPrice: cursorPt.price,
              }
            }
            return
          }
        }
      }

      // Deselect drawing when clicking empty chart space
      if (selectedDrawingId) {
        setSelectedDrawingId(null)
      }

      // 8. Check if clicking Close [✕] or grabbing the Interactive Order Price Line
      if (isNearOrderLine(x, y)) {
        if (
          trading?.hasDraftOrder &&
          trading.onClearOrderPrice &&
          x >= metrics.plotRight - 108 &&
          x <= metrics.plotRight - 86
        ) {
          trading.onClearOrderPrice()
          return
        }
        setIsDraggingOrderLine(true)
        dragRef.current = {
          mode: "order_line",
          startX: x,
          startY: y,
          startViewport: viewport,
        }
        return
      }

      // 9. Default: Pan Chart Viewport
      dragRef.current = {
        mode: "pan",
        startX: x,
        startY: y,
        startViewport: viewport,
      }
    },
    [
      trading,
      metrics,
      viewport,
      findSplitterAt,
      replay,
      candles.length,
      isZoomMode,
      activeTool,
      resolveCursorPoint,
      keepDrawing,
      commitDrawings,
      drawings,
      draftDrawing,
      hideAllDrawings,
      selectedDrawingId,
      lockAllDrawings,
      isNearOrderLine,
    ]
  )

  // Canvas Mouse Move
  const handleMouseMove = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const rect = e.currentTarget.getBoundingClientRect()
      const x = e.clientX - rect.left
      const y = e.clientY - rect.top
      setMousePos({ x, y, inside: true })

      // Box-Zoom Selection Rectangle Drag
      if (dragRef.current.mode === "zoom_box") {
        setZoomBox({
          x1: dragRef.current.startX,
          y1: dragRef.current.startY,
          x2: x,
          y2: y,
        })
        return
      }

      // Sub-Pane Splitter Height Resize
      if (dragRef.current.mode === "pane_resize" && dragRef.current.resizePaneId) {
        const dy = y - dragRef.current.startY
        const paneId = dragRef.current.resizePaneId
        const startH = dragRef.current.resizeStartHeight ?? 92
        const prevPaneId = dragRef.current.resizePrevPaneId
        const prevStartH = dragRef.current.resizePrevStartHeight ?? 92

        setIndicators((prev) =>
          prev.map((ind) => {
            if (ind.instanceId === paneId) {
              return { ...ind, paneHeight: Math.max(48, Math.min(260, Math.round(startH - dy))) }
            }
            if (prevPaneId && ind.instanceId === prevPaneId) {
              return { ...ind, paneHeight: Math.max(48, Math.min(260, Math.round(prevStartH + dy))) }
            }
            return ind
          })
        )
        return
      }

      // Update live preview of multi-point draft drawing
      if (draftDrawing && dragRef.current.mode !== "freehand_draw") {
        const pt = resolveCursorPoint(x, y)
        setDraftDrawing((prev) => {
          if (!prev) return null
          return {
            ...prev,
            points: [...prev.points.slice(0, -1), pt],
          }
        })
        return
      }

      // Freehand Brush / Highlighter Drag
      if (dragRef.current.mode === "freehand_draw" && draftDrawing) {
        const pt = resolveCursorPoint(x, y)
        setDraftDrawing((prev) => {
          if (!prev) return null
          return {
            ...prev,
            points: [...prev.points, pt],
          }
        })
        return
      }

      // Order Line Dragging (Snaps to HOSE ticks and clamps to Floor/Ceiling)
      if (dragRef.current.mode === "order_line" && trading) {
        let rawPrice = metrics.coordConverter.yToPrice(y)
        if (trading.floorPrice > 0) rawPrice = Math.max(trading.floorPrice, rawPrice)
        if (trading.ceilingPrice > 0) rawPrice = Math.min(trading.ceilingPrice, rawPrice)
        const snapped = roundToHoseTick(rawPrice)
        if (snapped > 0 && snapped !== trading.orderPrice) {
          trading.onPickPriceFromChart(snapped)
        }
        return
      }

      // Move Drawing Anchor Point
      if (
        dragRef.current.mode === "move_drawing_point" &&
        dragRef.current.drawingId &&
        dragRef.current.pointIndex !== undefined
      ) {
        const pt = resolveCursorPoint(x, y)
        const dId = dragRef.current.drawingId
        const pIdx = dragRef.current.pointIndex
        setDrawings((prev) =>
          prev.map((d) => {
            if (d.id !== dId) return d
            const nextPts = d.points.map((p, idx) => (idx === pIdx ? pt : p))
            return { ...d, points: nextPts }
          })
        )
        return
      }

      // Move Whole Drawing
      if (
        dragRef.current.mode === "move_drawing" &&
        dragRef.current.drawingId &&
        dragRef.current.initialPoints &&
        dragRef.current.anchorBarIndex !== undefined &&
        dragRef.current.anchorPrice !== undefined
      ) {
        const pt = resolveCursorPoint(x, y)
        const deltaBars = pt.barIndex - dragRef.current.anchorBarIndex
        const deltaPrice = pt.price - dragRef.current.anchorPrice
        const dId = dragRef.current.drawingId
        const initPts = dragRef.current.initialPoints

        setDrawings((prev) =>
          prev.map((d) => {
            if (d.id !== dId) return d
            return {
              ...d,
              points: initPts.map((p) => ({
                ...p,
                barIndex: Math.round((p.barIndex + deltaBars) * 10) / 10,
                price: roundToHoseTick(p.price + deltaPrice),
              })),
            }
          })
        )
        return
      }

      // Pan Viewport (Bounded with clampViewport so chart never flies into blank space)
      if (dragRef.current.mode === "pan") {
        const dx = x - dragRef.current.startX
        const dy = y - dragRef.current.startY
        const startVp = dragRef.current.startViewport
        const span = Math.max(8, startVp.endIndex - startVp.startIndex)
        const barsDelta = (dx / Math.max(1, metrics.plotWidth)) * span

        const priceSpan = Math.max(1, metrics.maxPrice - metrics.minPrice)
        const priceDelta = !startVp.autoScale
          ? (dy / Math.max(1, metrics.mainPane.height)) * priceSpan
          : 0

        const clamped = clampViewport(
          startVp.startIndex - barsDelta,
          startVp.endIndex - barsDelta,
          displayCandles.length,
          settings.rightOffsetBars
        )

        setViewport((prev) => ({
          ...prev,
          ...clamped,
          manualPricePanDelta: startVp.manualPricePanDelta + priceDelta,
        }))
        return
      }

      // Scale Right Price Axis
      if (dragRef.current.mode === "price_axis") {
        const dy = y - dragRef.current.startY
        const startVp = dragRef.current.startViewport
        const factor = Math.max(
          0.25,
          Math.min(4.0, startVp.manualPriceScaleFactor * (1 + dy * 0.005))
        )
        setViewport((prev) => ({
          ...prev,
          autoScale: false,
          manualPriceScaleFactor: factor,
        }))
        return
      }

      // Scale Bottom Time Axis
      if (dragRef.current.mode === "time_axis") {
        const dx = x - dragRef.current.startX
        const startVp = dragRef.current.startViewport
        const span = Math.max(8, startVp.endIndex - startVp.startIndex)
        const newSpan = span * Math.exp(dx * 0.0035)
        const clamped = clampViewport(
          startVp.endIndex - newSpan,
          startVp.endIndex,
          displayCandles.length,
          settings.rightOffsetBars
        )
        setViewport((prev) => ({
          ...prev,
          ...clamped,
        }))
        return
      }

      // Hover Hit-Test on Drawings
      if (!hideAllDrawings) {
        const hit = hitTestDrawing(x, y, drawings, metrics.coordConverter)
        const foundHover = hit ? hit.drawingId : null
        if (foundHover !== hoveredDrawingId) {
          setHoveredDrawingId(foundHover)
        }
      }
    },
    [
      draftDrawing,
      resolveCursorPoint,
      trading,
      metrics,
      displayCandles.length,
      settings.rightOffsetBars,
      hideAllDrawings,
      drawings,
      hoveredDrawingId,
    ]
  )

  // Canvas Mouse Up
  const handleMouseUp = useCallback(() => {
    if (dragRef.current.mode === "zoom_box") {
      if (zoomBox && Math.abs(zoomBox.x2 - zoomBox.x1) >= 16 && displayCandles.length >= 5) {
        const minX = Math.max(metrics.plotLeft, Math.min(zoomBox.x1, zoomBox.x2))
        const maxX = Math.min(metrics.plotRight, Math.max(zoomBox.x1, zoomBox.x2))
        const bStart = Math.floor(metrics.coordConverter.xToBarIndex(minX))
        const bEnd = Math.ceil(metrics.coordConverter.xToBarIndex(maxX))
        const clamped = clampViewport(
          bStart,
          Math.max(bStart + 8, bEnd),
          displayCandles.length,
          settings.rightOffsetBars
        )
        setViewport((prev) => ({
          ...prev,
          ...clamped,
          autoScale: true,
          manualPriceScaleFactor: 1,
          manualPricePanDelta: 0,
        }))
        setIsZoomMode(false)
      }
      setZoomBox(null)
    } else if (dragRef.current.mode === "freehand_draw" && draftDrawing) {
      commitDrawings([...drawings, draftDrawing])
      setSelectedDrawingId(draftDrawing.id)
      setDraftDrawing(null)
      if (!keepDrawing) setActiveTool("cursor_cross")
    } else if (
      dragRef.current.mode === "move_drawing" ||
      dragRef.current.mode === "move_drawing_point"
    ) {
      commitDrawings(drawings, true)
    }

    setIsDraggingOrderLine(false)
    dragRef.current.mode = "none"
  }, [
    zoomBox,
    displayCandles.length,
    metrics,
    settings.rightOffsetBars,
    draftDrawing,
    commitDrawings,
    drawings,
    keepDrawing,
  ])

  // Double-Click on Canvas: Reset Price Axis if on Right Axis, or Pick Order Price on Main Pane
  const handleDoubleClick = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const rect = e.currentTarget.getBoundingClientRect()
      const x = e.clientX - rect.left
      const y = e.clientY - rect.top

      if (x > metrics.plotRight) {
        setViewport((prev) => ({
          ...prev,
          autoScale: true,
          manualPriceScaleFactor: 1,
          manualPricePanDelta: 0,
        }))
        return
      }

      if (trading && y >= metrics.mainPane.top && y <= metrics.mainPane.bottom) {
        let rawPrice = metrics.coordConverter.yToPrice(y)
        if (trading.floorPrice > 0) rawPrice = Math.max(trading.floorPrice, rawPrice)
        if (trading.ceilingPrice > 0) rawPrice = Math.min(trading.ceilingPrice, rawPrice)
        const picked = roundToHoseTick(rawPrice)
        if (picked > 0) {
          trading.onPickPriceFromChart(picked)
        }
      }
    },
    [metrics, trading]
  )

  // Date Range Selection Handler (1D, 5D, 1M, 3M, 6M, YTD, 1Y, 5Y, ALL)
  const handleSelectDateRange = useCallback(
    (range: TvDateRange) => {
      setActiveDateRange(range)
      const total = displayCandles.length
      if (total === 0) return

      const rangeBarsMap: Record<TvDateRange, number> = {
        "1D": 24,
        "5D": 35,
        "1M": 30,
        "3M": 66,
        "6M": 130,
        YTD: 160,
        "1Y": 250,
        "5Y": total,
        ALL: total,
      }
      const count = Math.min(total, rangeBarsMap[range] ?? 100)
      setViewport((prev) => ({
        ...prev,
        startIndex: Math.max(0, total - count),
        endIndex: total - 1 + settings.rightOffsetBars,
        autoScale: true,
        manualPriceScaleFactor: 1,
        manualPricePanDelta: 0,
      }))
    },
    [displayCandles.length, settings.rightOffsetBars]
  )

  // Comparison Series Toggle
  const handleToggleComparison = useCallback(
    async (compSym: string, compName: string, compColor: string) => {
      const existing = comparisons.find((c) => c.symbol === compSym)
      if (existing) {
        setComparisons((prev) => prev.filter((c) => c.symbol !== compSym))
        return
      }
      const compCandles = await fetchTimeframeCandles(compSym, timeframe)
      if (compCandles.length > 0) {
        setComparisons((prev) => [
          ...prev,
          {
            symbol: compSym,
            name: compName,
            color: compColor,
            candles: compCandles,
            visible: true,
          },
        ])
      }
    },
    [comparisons, timeframe]
  )

  // Snapshot Export (Download PNG)
  const handleTakeSnapshot = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    try {
      const url = canvas.toDataURL("image/png")
      const link = document.createElement("a")
      link.download = `AIInvest_${symbol}_${timeframe}_${new Date().toISOString().slice(0, 10)}.png`
      link.href = url
      link.click()
    } catch {
      // ignore
    }
  }, [symbol, timeframe])

  // Fullscreen Toggle (Synchronized with Browser Fullscreen API + Clean Fallback)
  const handleToggleFullscreen = useCallback(() => {
    const el = containerRef.current
    if (!el) return
    const doc = document as Document & {
      webkitFullscreenElement?: Element | null
      webkitExitFullscreen?: () => Promise<void> | void
    }
    const activeFs = document.fullscreenElement ?? doc.webkitFullscreenElement ?? null
    if (!activeFs && !isFullscreen) {
      const req =
        el.requestFullscreen?.bind(el) ??
        (el as HTMLElement & { webkitRequestFullscreen?: () => Promise<void> })
          .webkitRequestFullscreen?.bind(el)
      if (req) {
        Promise.resolve(req())
          .then(() => setIsFullscreen(true))
          .catch(() => setIsFullscreen(true))
      } else {
        setIsFullscreen(true)
      }
    } else {
      if (activeFs) {
        const exit =
          document.exitFullscreen?.bind(document) ?? doc.webkitExitFullscreen?.bind(doc)
        Promise.resolve(exit?.()).catch(() => {})
      }
      setIsFullscreen(false)
    }
  }, [isFullscreen])

  // Selected Drawing Object
  const selectedDrawing = useMemo(
    () => drawings.find((d) => d.id === selectedDrawingId) ?? null,
    [drawings, selectedDrawingId]
  )

  // Dynamic Cursor CSS
  const canvasCursorClass = useMemo(() => {
    if (mousePos.x > canvasSize.width - PRICE_AXIS_WIDTH) return "cursor-ns-resize"
    if (mousePos.y > canvasSize.height - TIME_AXIS_HEIGHT) return "cursor-ew-resize"
    if (findSplitterAt(mousePos.x, mousePos.y)) return "cursor-row-resize"
    if (isZoomMode) return "cursor-zoom-in"
    if (isDraggingOrderLine || isNearOrderLine(mousePos.x, mousePos.y)) return "cursor-ns-resize"
    if (hoveredDrawingId) return "cursor-move"
    if (activeTool === "cursor_arrow") return "cursor-default"
    if (activeTool === "cursor_dot") return "cursor-cell"
    if (activeTool === "cursor_eraser") return "cursor-pointer"
    return "cursor-crosshair"
  }, [
    mousePos,
    canvasSize.width,
    canvasSize.height,
    findSplitterAt,
    isZoomMode,
    isDraggingOrderLine,
    isNearOrderLine,
    hoveredDrawingId,
    activeTool,
  ])

  const isDark = settings.theme === "dark"

  return (
    <div
      ref={containerRef}
      data-name="tv-chart-root"
      style={{ height: isFullscreen ? "100dvh" : `${height}px` }}
      className={`relative flex flex-col rounded-xl border overflow-hidden select-none transition-colors ${
        isDark
          ? "bg-[#131722] border-[#2A2E39] text-[#D1D4DC]"
          : "bg-white border-[#E0E3EB] text-[#131722]"
      } ${isFullscreen ? "fixed inset-0 z-[120] rounded-none" : ""} ${className}`}
    >
      {/* 1. Top Header Toolbar (or Sleek Compact Bar when embedded in Market Pulse) */}
      {isEmbeddedCompact ? (
        <div
          className={`flex items-center justify-between gap-2 px-2.5 h-8 border-b text-[11px] shrink-0 ${
            isDark
              ? "bg-[#131722] border-[#2A2E39] text-[#D1D4DC]"
              : "bg-[#F8F9FD] border-[#E0E3EB] text-[#131722]"
          }`}
        >
          <div className="flex items-center gap-1">
            {(["15m", "1H", "1D", "1W", "1M"] as TvTimeframe[]).map((tf) => (
              <button
                key={tf}
                type="button"
                onClick={() => {
                  setTimeframe(tf)
                  setActiveDateRange(null)
                }}
                className={`px-1.5 py-0.5 rounded font-mono text-[10.5px] font-semibold transition-colors ${
                  timeframe === tf
                    ? "bg-[#2962FF] text-white"
                    : "opacity-75 hover:opacity-100 hover:bg-black/5 dark:hover:bg-white/10"
                }`}
              >
                {tf}
              </button>
            ))}
            <div className="w-px h-3.5 bg-current/15 mx-0.5" />
            <button
              type="button"
              onClick={() => setChartStyle((s) => (s === "area" ? "candles" : "area"))}
              className="px-1.5 py-0.5 rounded text-[10.5px] font-medium opacity-80 hover:opacity-100 hover:bg-black/5 dark:hover:bg-white/10"
              title="Chuyển đổi Nến / Miền"
            >
              {chartStyle === "area" ? "Miền" : "Nến"}
            </button>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => {
                const defaultBars = Math.min(displayCandles.length, timeframe === "1D" ? 65 : 90)
                setViewport((vp) => ({
                  ...vp,
                  startIndex: Math.max(0, displayCandles.length - defaultBars),
                  endIndex: displayCandles.length - 1 + settings.rightOffsetBars,
                  autoScale: true,
                  manualPriceScaleFactor: 1,
                  manualPricePanDelta: 0,
                }))
              }}
              title="Đặt lại khung nhìn mặc định"
              className="px-1.5 py-0.5 rounded text-[11px] font-medium opacity-75 hover:opacity-100 hover:bg-black/5 dark:hover:bg-white/10"
            >
              ↺
            </button>
            <button
              type="button"
              onClick={() => {
                setEditingIndicatorId(null)
                setIndicatorsModalOpen(true)
              }}
              className="px-1.5 py-0.5 rounded text-[10.5px] font-medium opacity-80 hover:opacity-100 hover:bg-black/5 dark:hover:bg-white/10"
            >
              Chỉ báo
            </button>
            <button
              type="button"
              onClick={handleToggleFullscreen}
              title="Mở rộng toàn bộ công cụ TradingView Pro (Nhấn Esc để thu nhỏ)"
              className="px-1.5 py-0.5 rounded bg-[#2962FF]/10 text-[#2962FF] hover:bg-[#2962FF] hover:text-white font-semibold text-[10.5px] transition-colors"
            >
              ⤢ Pro
            </button>
          </div>
        </div>
      ) : (
        <TopHeaderToolbar
          symbol={symbol}
          timeframe={timeframe}
          chartStyle={chartStyle}
          indicators={indicators}
          favoriteIds={favoriteIds}
          comparisons={comparisons}
          replay={replay}
          canUndo={undoStack.length > 0}
          canRedo={redoStack.length > 0}
          isFullscreen={isFullscreen}
          isDark={isDark}
          onSymbolChange={trading?.onSymbolChange}
          onTimeframeChange={(tf) => {
            setTimeframe(tf)
            setActiveDateRange(null)
          }}
          onChartStyleChange={setChartStyle}
          onOpenIndicatorsModal={() => {
            setEditingIndicatorId(null)
            setIndicatorsModalOpen(true)
          }}
          onQuickToggleIndicator={(id: TvIndicatorId) => {
            setIndicators((prev) => {
              const exists = prev.find((i) => i.id === id)
              if (exists) {
                return prev.filter((i) => i.instanceId !== exists.instanceId)
              }
              const meta = getIndicatorMetadata(id)
              if (meta.category === "strategy") {
                setStrategyTesterOpen(true)
              }
              return [...prev, createIndicatorInstance(id)]
            })
          }}
          onToggleComparison={handleToggleComparison}
          onToggleReplay={() =>
            setReplay((prev) => ({
              ...prev,
              active: !prev.active,
              selectingStartBar: !prev.active,
              playing: false,
              cutoffIndex: Math.max(10, candles.length - 15),
            }))
          }
          onReplaySelectCut={() =>
            setReplay((prev) => ({ ...prev, selectingStartBar: true, playing: false }))
          }
          onReplayPlayPause={() =>
            setReplay((prev) => ({ ...prev, playing: !prev.playing, selectingStartBar: false }))
          }
          onReplayStep={() =>
            setReplay((prev) => ({
              ...prev,
              playing: false,
              cutoffIndex: Math.min(candles.length - 1, prev.cutoffIndex + 1),
            }))
          }
          onReplaySpeedChange={(ms) => setReplay((prev) => ({ ...prev, speedMs: ms }))}
          onUndo={() => {
            if (undoStack.length === 0) return
            const prev = undoStack[undoStack.length - 1]
            setUndoStack((u) => u.slice(0, -1))
            setRedoStack((r) => [...r, drawings])
            commitDrawings(prev, false)
          }}
          onRedo={() => {
            if (redoStack.length === 0) return
            const next = redoStack[redoStack.length - 1]
            setRedoStack((r) => r.slice(0, -1))
            setUndoStack((u) => [...u, drawings])
            commitDrawings(next, false)
          }}
          onOpenObjectTree={() => setObjectTreeOpen(true)}
          onOpenSettings={() => setSettingsModalOpen(true)}
          onToggleFullscreen={handleToggleFullscreen}
          onTakeSnapshot={handleTakeSnapshot}
        />
      )}

      {/* 2. Middle Workspace: Left Drawing Toolbar + Multi-Pane Canvas Stage */}
      <div className="flex-1 flex min-h-0 relative overflow-hidden">
        {!isEmbeddedCompact && (
          <LeftDrawingToolbar
            activeTool={activeTool}
            magnetMode={magnetMode}
            keepDrawing={keepDrawing}
            lockAllDrawings={lockAllDrawings}
            hideAllDrawings={hideAllDrawings}
            drawingsCount={drawings.length}
            isZoomMode={isZoomMode}
            isDark={isDark}
            onSelectTool={(tool) => {
              setIsZoomMode(false)
              setZoomBox(null)
              setActiveTool(tool)
              setDraftDrawing(null)
            }}
            onCycleMagnetMode={() =>
              setMagnetMode((m) => (m === "off" ? "weak" : m === "weak" ? "strong" : "off"))
            }
            onToggleKeepDrawing={() => setKeepDrawing((v) => !v)}
            onToggleLockAll={() => setLockAllDrawings((v) => !v)}
            onToggleHideAll={() => setHideAllDrawings((v) => !v)}
            onClearDrawings={() => {
              commitDrawings([])
              setSelectedDrawingId(null)
            }}
            onClearIndicators={() => setIndicators([])}
            onZoomIn={() => zoomByFactor(0.78)}
            onZoomOut={() => zoomByFactor(1.28)}
            onToggleZoomMode={() => {
              setIsZoomMode((v) => !v)
              setZoomBox(null)
              setDraftDrawing(null)
              setActiveTool("cursor_cross")
            }}
          />
        )}

        {/* Canvas Container */}
        <div
          ref={canvasWrapperRef}
          className="group/tvcanvas flex-1 relative min-w-0 min-h-0 overflow-hidden"
        >
          {/* Floating Contextual Drawing Toolbar (when a drawing is selected) */}
          {selectedDrawing && (
            <FloatingDrawingToolbar
              drawing={selectedDrawing}
              isDark={isDark}
              onUpdate={(patch) => {
                commitDrawings(
                  drawings.map((d) => (d.id === selectedDrawing.id ? { ...d, ...patch } : d))
                )
              }}
              onClone={() => {
                const cloned: TvDrawingObject = {
                  ...selectedDrawing,
                  id: `draw_${Date.now()}`,
                  points: selectedDrawing.points.map((p) => ({
                    ...p,
                    barIndex: p.barIndex + 3,
                  })),
                  createdAt: Date.now(),
                }
                commitDrawings([...drawings, cloned])
                setSelectedDrawingId(cloned.id)
              }}
              onDelete={() => {
                commitDrawings(drawings.filter((d) => d.id !== selectedDrawing.id))
                setSelectedDrawingId(null)
              }}
              onClose={() => setSelectedDrawingId(null)}
            />
          )}

          {/* Top-Left Pane Legend + Sub-Pane Headers + Floating Zoom/Pan Controls */}
          <PaneLegend
            symbol={symbol}
            stockName={trading?.stockName}
            exchange={trading?.exchange || "HOSE"}
            timeframe={timeframe}
            activeCandle={activeCandle}
            prevCandle={prevCandle}
            settings={settings}
            trading={trading}
            overlayIndicators={overlayIndicators}
            subPanes={metrics.subPanes}
            comparisons={comparisons}
            hoveredBarIndex={hoveredBarIndex}
            precision={effectivePrecision}
            compact={isEmbeddedCompact}
            isHovering={mousePos.inside}
            onToggleIndicatorVisible={(instanceId) =>
              setIndicators((prev) =>
                prev.map((i) => (i.instanceId === instanceId ? { ...i, visible: !i.visible } : i))
              )
            }
            onRemoveIndicator={(instanceId) =>
              setIndicators((prev) => prev.filter((i) => i.instanceId !== instanceId))
            }
            onToggleIndicatorCollapsed={(instanceId) =>
              setIndicators((prev) =>
                prev.map((i) =>
                  i.instanceId === instanceId ? { ...i, collapsed: !i.collapsed } : i
                )
              )
            }
            onToggleIndicatorMaximized={(instanceId) =>
              setIndicators((prev) =>
                prev.map((i) =>
                  i.instanceId === instanceId ? { ...i, maximized: !i.maximized } : i
                )
              )
            }
            onOpenIndicatorSettings={(instanceId) => {
              setEditingIndicatorId(instanceId)
              setIndicatorsModalOpen(true)
            }}
            onOpenIndicatorsModal={() => {
              setEditingIndicatorId(null)
              setIndicatorsModalOpen(true)
            }}
            onRemoveComparison={(compSym) =>
              setComparisons((prev) => prev.filter((c) => c.symbol !== compSym))
            }
            onZoomIn={() => zoomByFactor(0.78)}
            onZoomOut={() => zoomByFactor(1.28)}
            onPanLeft={() => panByDirection(-1)}
            onPanRight={() => panByDirection(1)}
            onResetView={() => {
              const defaultBars = Math.min(displayCandles.length, 95)
              setViewport((vp) => ({
                ...vp,
                startIndex: Math.max(0, displayCandles.length - defaultBars),
                endIndex: displayCandles.length - 1 + settings.rightOffsetBars,
                autoScale: true,
                manualPriceScaleFactor: 1,
                manualPricePanDelta: 0,
              }))
            }}
          />

          {/* Floating Bar Replay Control Bar (Keeps TopHeaderToolbar strictly single-row) */}
          {replay.active && (
            <div
              className={`absolute bottom-[68px] left-1/2 -translate-x-1/2 z-20 flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border shadow-lg text-[11.5px] font-medium whitespace-nowrap ${
                isDark
                  ? "bg-[#1E222D]/95 border-[#2962FF]/60 text-[#D1D4DC]"
                  : "bg-white/95 border-[#2962FF]/50 text-[#131722]"
              }`}
            >
              <span className="font-semibold text-[#2962FF] px-1">⏪ Replay</span>
              <div className="w-px h-4 bg-current/15" />
              <button
                type="button"
                onClick={() =>
                  setReplay((prev) => ({ ...prev, selectingStartBar: true, playing: false }))
                }
                className={`px-2 py-0.5 rounded transition-colors ${
                  replay.selectingStartBar
                    ? "bg-[#2962FF] text-white"
                    : "hover:bg-black/5 dark:hover:bg-white/10"
                }`}
              >
                ✂ Chọn điểm cắt
              </button>
              <button
                type="button"
                onClick={() =>
                  setReplay((prev) => ({
                    ...prev,
                    playing: !prev.playing,
                    selectingStartBar: false,
                  }))
                }
                className="px-2.5 py-0.5 rounded bg-[#2962FF] text-white font-semibold hover:bg-[#1E53E5] transition-colors"
              >
                {replay.playing ? "⏸ Tạm dừng" : "▶ Phát"}
              </button>
              <button
                type="button"
                onClick={() =>
                  setReplay((prev) => ({
                    ...prev,
                    playing: false,
                    cutoffIndex: Math.min(candles.length - 1, prev.cutoffIndex + 1),
                  }))
                }
                title="Tiến thêm 1 nến"
                className="px-2 py-0.5 rounded hover:bg-black/5 dark:hover:bg-white/10 transition-colors"
              >
                ⏭ +1 nến
              </button>
              <select
                value={replay.speedMs}
                onChange={(e) =>
                  setReplay((prev) => ({ ...prev, speedMs: Number(e.target.value) }))
                }
                aria-label="Tốc độ phát Replay"
                className={`px-1.5 py-0.5 rounded border text-[11px] font-mono outline-none ${
                  isDark
                    ? "bg-[#131722] border-[#2A2E39] text-[#D1D4DC]"
                    : "bg-[#F8F9FD] border-[#E0E3EB] text-[#131722]"
                }`}
              >
                <option value={1000}>0.5x</option>
                <option value={650}>1x</option>
                <option value={320}>2x</option>
                <option value={140}>4x</option>
              </select>
              <div className="w-px h-4 bg-current/15" />
              <button
                type="button"
                onClick={() =>
                  setReplay((prev) => ({
                    ...prev,
                    active: false,
                    playing: false,
                    selectingStartBar: false,
                  }))
                }
                title="Đóng chế độ Replay"
                className="px-1.5 py-0.5 rounded text-[#F23645] hover:bg-[#F23645]/10 font-semibold transition-colors"
              >
                ✕
              </button>
            </div>
          )}

          {/* Loading Badge */}
          {loadingData && (
            <div className="absolute top-2.5 right-24 z-20 px-2.5 py-1 rounded-full bg-[#2962FF] text-white text-[11px] font-semibold shadow-md animate-pulse">
              Đang tải dữ liệu {symbol} ({timeframe})...
            </div>
          )}

          {/* Box-Zoom Mode Hint Banner */}
          {isZoomMode && (
            <div className="absolute bottom-9 right-24 z-20 px-2.5 py-1 rounded-md bg-[#2962FF]/95 text-white text-[11px] font-medium shadow-sm flex items-center gap-2">
              <span>🔍 Kéo chuột quét vùng trên biểu đồ để phóng to (Box Zoom)</span>
              <button
                type="button"
                onClick={() => {
                  setIsZoomMode(false)
                  setZoomBox(null)
                }}
                className="underline text-[10px] opacity-90 hover:opacity-100"
              >
                Hủy (Esc)
              </button>
            </div>
          )}

          {/* Active Drawing Tool Hint Banner */}
          {!isZoomMode && !activeTool.startsWith("cursor_") && (
            <div className="absolute bottom-9 right-24 z-20 px-2.5 py-1 rounded-md bg-[#2962FF]/90 text-white text-[11px] font-medium shadow-sm flex items-center gap-2">
              <span>
                Đang vẽ: <strong>{TV_DRAWING_TOOL_MAP[activeTool]?.viName}</strong>{" "}
                {draftDrawing
                  ? `(Đã chấm ${draftDrawing.points.length - 1}/${TV_DRAWING_TOOL_MAP[activeTool]?.requiredPoints} điểm)`
                  : "(Nhấp chuột lên biểu đồ để đặt điểm neo)"}
              </span>
              <button
                type="button"
                onClick={() => {
                  setDraftDrawing(null)
                  setActiveTool("cursor_cross")
                }}
                className="underline text-[10px] opacity-90 hover:opacity-100"
              >
                Hủy (Esc)
              </button>
            </div>
          )}

          {/* High-DPI Multi-Pane HTML5 Canvas */}
          <canvas
            ref={canvasRef}
            style={{
              width: `${canvasSize.width}px`,
              height: `${canvasSize.height}px`,
            }}
            className={`absolute inset-0 block touch-none ${canvasCursorClass}`}
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
            onMouseLeave={() => {
              setMousePos((m) => ({ ...m, inside: false }))
              handleMouseUp()
            }}
            onDoubleClick={handleDoubleClick}
          />
        </div>
      </div>

      {/* 2.5 Docked Strategy Tester Backtest Panel (when any TradingView Strategy is active) */}
      <StrategyTesterPanel
        strategyIndicators={strategyIndicators}
        isDark={isDark}
        isOpen={strategyTesterOpen}
        onToggleOpen={() => setStrategyTesterOpen((v) => !v)}
        onOpenSettings={(instanceId) => {
          setEditingIndicatorId(instanceId)
          setIndicatorsModalOpen(true)
        }}
        onRemoveStrategy={(instanceId) =>
          setIndicators((prev) => prev.filter((i) => i.instanceId !== instanceId))
        }
      />

      {/* 3. Bottom Date Ranges & Scale Mode Toolbar */}
      {!isEmbeddedCompact && (
        <BottomToolbar
          activeRange={activeDateRange}
          scaleMode={viewport.scaleMode}
          autoScale={viewport.autoScale}
          isDark={isDark}
          trading={trading}
          onSelectDateRange={handleSelectDateRange}
          onToggleScaleMode={(mode: TvScaleMode) =>
            setViewport((vp) => ({ ...vp, scaleMode: mode }))
          }
          onToggleAutoScale={() =>
            setViewport((vp) => ({
              ...vp,
              autoScale: !vp.autoScale,
              manualPriceScaleFactor: 1,
              manualPricePanDelta: 0,
            }))
          }
          onGoToLatest={() => {
            const span = Math.max(20, viewport.endIndex - viewport.startIndex)
            const end = displayCandles.length - 1 + settings.rightOffsetBars
            setViewport((vp) => ({
              ...vp,
              startIndex: Math.max(0, end - span),
              endIndex: end,
            }))
          }}
        />
      )}

      {/* 4. Modals: Indicators, Metrics & Strategies (287+ Unlocked Items), Object Tree & Data Window, Chart Settings */}
      <IndicatorsModal
        isOpen={indicatorsModalOpen}
        isDark={isDark}
        activeIndicators={indicators}
        initialEditingInstanceId={editingIndicatorId}
        favoriteIds={favoriteIds}
        customScripts={customScripts}
        onClose={() => {
          setIndicatorsModalOpen(false)
          setEditingIndicatorId(null)
        }}
        onAddIndicator={(id, customDef) => {
          const meta = getIndicatorMetadata(id)
          if (meta.category === "strategy") {
            setStrategyTesterOpen(true)
          }
          const existingSame = indicators.filter((i) => i.id === id)
          if (!customDef && id === "VIP_HUD_DE_TE" && existingSame.length > 0) {
            const firstMode = Math.round(existingSame[0].params.mode ?? 0)
            const nextMode = existingSame.length % 2 === 1 ? (firstMode === 0 ? 1 : 0) : firstMode
            const inst = createIndicatorInstance(id, { mode: nextMode })
            setIndicators((prev) => [...prev, inst])
          } else if (
            !customDef &&
            meta.allowMultiple &&
            existingSame.length > 0 &&
            meta.params.some((p) => p.key === "length")
          ) {
            const periods = [20, 50, 100, 200]
            const colors = ["#2962FF", "#FF6D00", "#9C27B0", "#00897B"]
            const idx = existingSame.length % periods.length
            const inst = createIndicatorInstance(id, { length: periods[idx] }, colors[idx])
            setIndicators((prev) => [...prev, inst])
          } else {
            setIndicators((prev) => [
              ...prev,
              createIndicatorInstance(id, undefined, undefined, customDef),
            ])
          }
        }}
        onUpdateIndicator={(instanceId, patch) =>
          setIndicators((prev) =>
            prev.map((i) => (i.instanceId === instanceId ? { ...i, ...patch } : i))
          )
        }
        onRemoveIndicator={(instanceId) =>
          setIndicators((prev) => prev.filter((i) => i.instanceId !== instanceId))
        }
        onResetDefaults={() => setIndicators(DEFAULT_ACTIVE_INDICATORS)}
        onToggleFavorite={(id) =>
          setFavoriteIds((prev) =>
            prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
          )
        }
        onCreateCustomScript={(script) => {
          registerCustomScripts([script])
          setCustomScripts((prev) => [script, ...prev.filter((s) => s.id !== script.id)])
        }}
        onDeleteCustomScript={(scriptId) => {
          setCustomScripts((prev) => prev.filter((s) => s.id !== scriptId))
          setIndicators((prev) => prev.filter((i) => i.id !== scriptId))
        }}
      />

      <ObjectTreeModal
        isOpen={objectTreeOpen}
        isDark={isDark}
        symbol={symbol}
        activeCandle={activeCandle}
        hoveredBarIndex={hoveredBarIndex}
        trading={trading}
        indicators={computedIndicators}
        drawings={drawings}
        selectedDrawingId={selectedDrawingId}
        onClose={() => setObjectTreeOpen(false)}
        onSelectDrawing={(id) => setSelectedDrawingId(id)}
        onUpdateDrawing={(id, patch) =>
          commitDrawings(drawings.map((d) => (d.id === id ? { ...d, ...patch } : d)))
        }
        onDeleteDrawing={(id) => {
          commitDrawings(drawings.filter((d) => d.id !== id))
          if (selectedDrawingId === id) setSelectedDrawingId(null)
        }}
        onToggleIndicatorVisible={(instanceId) =>
          setIndicators((prev) =>
            prev.map((i) => (i.instanceId === instanceId ? { ...i, visible: !i.visible } : i))
          )
        }
        onRemoveIndicator={(instanceId) =>
          setIndicators((prev) => prev.filter((i) => i.instanceId !== instanceId))
        }
        onClearAllDrawings={() => {
          commitDrawings([])
          setSelectedDrawingId(null)
        }}
      />

      <ChartSettingsModal
        isOpen={settingsModalOpen}
        settings={settings}
        onClose={() => setSettingsModalOpen(false)}
        onUpdateSettings={(patch) => setSettings((prev) => ({ ...prev, ...patch }))}
        onResetSettings={() => setSettings(DEFAULT_TV_CHART_SETTINGS)}
      />
    </div>
  )
}
