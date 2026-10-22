"use client"

import { useMemo, useRef, useState, useEffect } from "react"
import { Panel, PercentChange } from "@/components/ui"
import { Link } from "@/lib/router"
import { SECTOR_COLOR_PALETTE, formatSectorName } from "@/lib/sector-weights"
import { useRealtimeMarket } from "@/lib/use-realtime"

export interface TopSectorStock {
  symbol: string
  name: string
  price: number
  changePct: number | null
  rs20d: number
}

export interface SectorStrengthItem {
  sector: string
  count: number
  dailyReturns: number[]
  latestChangePct: number | null
  topStocks: TopSectorStock[]
}

export interface SectorStrengthPayload {
  dates?: string[]
  vnindexReturns?: number[]
  sectors?: SectorStrengthItem[]
  asOf?: string | null
}

const TIMEFRAMES: Record<string, number> = {
  "1 Tháng": 20,
  "3 Tháng": 60,
  "6 Tháng": 120,
  "1 Năm": 240,
}

function formatDateVi(isoDate: string): string {
  const parts = isoDate.slice(0, 10).split("-")
  if (parts.length !== 3) return isoDate
  return `${parts[2]}/${parts[1]}/${parts[0]}`
}

export function SectorStrengthChart({ data, loading }: { data?: SectorStrengthPayload | null; loading?: boolean }) {
  const { heatmap: liveHeatmap, indices: liveIndices } = useRealtimeMarket()
  const [timeframe, setTimeframe] = useState<string>("1 Tháng")
  const [selectedSectors, setSelectedSectors] = useState<string[]>([])
  const [initialized, setInitialized] = useState(false)
  const [hoveredSector, setHoveredSector] = useState<string | null>(null)
  const [hoveredDateIdx, setHoveredDateIdx] = useState<number | null>(null)
  const [popupPos, setPopupPos] = useState<{ x: number; y: number }>({ x: 24, y: 24 })
  const chartContainerRef = useRef<HTMLDivElement>(null)

  const liveSectorChangeMap = useMemo(() => {
    const map = new Map<string, number>()
    const list = Array.isArray(liveHeatmap?.sectors) ? liveHeatmap!.sectors! : []
    for (const s of list) {
      const code = String(s.sector ?? s.name ?? "")
      const rawChg = s.change ?? s.change_pct ?? s.changePct
      if (code && rawChg != null && Number.isFinite(Number(rawChg))) {
        map.set(code, Number(rawChg))
      }
    }
    return map
  }, [liveHeatmap])

  const rawSectors = useMemo(() => {
    const list = Array.isArray(data?.sectors) ? data!.sectors! : []
    return list.filter((s) => s.sector && s.sector !== "OTHER_INDUSTRIALS" && s.count > 0)
  }, [data])

  useEffect(() => {
    if (!initialized && rawSectors.length > 0) {
      setSelectedSectors(rawSectors.map((s) => s.sector))
      setInitialized(true)
    }
  }, [rawSectors, initialized])

  const windowSize = TIMEFRAMES[timeframe] ?? 20

  const computed = useMemo(() => {
    const allDates = Array.isArray(data?.dates) ? data!.dates! : []
    const allVn = Array.isArray(data?.vnindexReturns) ? data!.vnindexReturns! : []
    const sliceLen = Math.min(windowSize, allDates.length)
    if (sliceLen < 2 || rawSectors.length === 0) {
      return {
        dates: [] as string[],
        series: [] as Array<{
          sector: string
          label: string
          color: string
          count: number
          rsSeries: number[]
          cumReturnSeries: number[]
          latestRs: number
          latestCumReturn: number
          latestChangePct: number | null
          rank: number
          topStocks: TopSectorStock[]
        }>,
      }
    }

    const dates = allDates.slice(-sliceLen)
    const vnSlice = [...allVn.slice(-sliceLen)]
    if (liveIndices?.vnIndexPct != null && Number.isFinite(Number(liveIndices.vnIndexPct)) && vnSlice.length > 0) {
      vnSlice[vnSlice.length - 1] = Number(liveIndices.vnIndexPct)
    }

    const baseSeries = rawSectors.map((item, idx) => {
      const secSlice = [...(item.dailyReturns || []).slice(-sliceLen)]
      const liveChg = liveSectorChangeMap.get(item.sector)
      if (liveChg !== undefined && secSlice.length > 0) {
        secSlice[secSlice.length - 1] = liveChg
      }
      let sectorFactor = 1.0
      let vnFactor = 1.0
      const rsSeries: number[] = []
      const cumReturnSeries: number[] = []

      for (let i = 0; i < sliceLen; i++) {
        if (i > 0) {
          const rSec = Number(secSlice[i] ?? 0) / 100
          const rVn = Number(vnSlice[i] ?? 0) / 100
          sectorFactor *= 1 + rSec
          vnFactor *= 1 + rVn
        }
        const cumSecPct = (sectorFactor - 1) * 100
        const cumVnPct = (vnFactor - 1) * 100
        const rs = Number((cumSecPct - cumVnPct).toFixed(2))
        rsSeries.push(rs)
        cumReturnSeries.push(Number(cumSecPct.toFixed(2)))
      }

      const latestRs = rsSeries[rsSeries.length - 1] ?? 0
      const latestCumReturn = cumReturnSeries[cumReturnSeries.length - 1] ?? 0
      return {
        sector: item.sector,
        label: formatSectorName(item.sector),
        color: SECTOR_COLOR_PALETTE[idx % SECTOR_COLOR_PALETTE.length],
        count: item.count,
        rsSeries,
        cumReturnSeries,
        latestRs,
        latestCumReturn,
        latestChangePct: item.latestChangePct,
        topStocks: item.topStocks || [],
      }
    })

    const ranked = [...baseSeries]
      .sort((a, b) => b.latestRs - a.latestRs)
      .map((item, idx) => ({ ...item, rank: idx + 1 }))

    return { dates, series: ranked }
  }, [data, rawSectors, windowSize, liveSectorChangeMap, liveIndices?.vnIndexPct])

  const visibleSeries = useMemo(
    () => computed.series.filter((s) => selectedSectors.includes(s.sector)),
    [computed.series, selectedSectors],
  )

  const allSelected = rawSectors.length > 0 && selectedSectors.length === rawSectors.length
  const noneSelected = selectedSectors.length === 0

  const selectAll = () => setSelectedSectors(rawSectors.map((s) => s.sector))
  const clearAll = () => setSelectedSectors([])
  const toggleSector = (sectorCode: string) => {
    setSelectedSectors((prev) =>
      prev.includes(sectorCode) ? prev.filter((s) => s !== sectorCode) : [...prev, sectorCode],
    )
  }

  // SVG Coordinate System: 1000 x 500
  const SVG_W = 1000
  const SVG_H = 500
  const PAD_L = 48
  const PAD_R = 16
  const PAD_T = 20
  const PAD_B = 34
  const PLOT_W = SVG_W - PAD_L - PAD_R
  const PLOT_H = SVG_H - PAD_T - PAD_B

  const { minVal, maxVal, gridLevels } = useMemo(() => {
    const vals = visibleSeries.flatMap((s) => s.rsSeries)
    vals.push(0) // include VN-Index baseline 0%
    if (vals.length <= 1) return { minVal: -10, maxVal: 10, gridLevels: [-8, -4, 0, 4, 8] }
    const lo = Math.min(...vals)
    const hi = Math.max(...vals)
    const pad = Math.max((hi - lo) * 0.1, 1.5)
    const minV = lo - pad
    const maxV = hi + pad
    const step = (maxV - minV) / 4
    const levels = [minV + step * 3.5, minV + step * 2.5, minV + step * 1.5, minV + step * 0.5].map((v) =>
      Number(v.toFixed(1)),
    )
    return { minVal: minV, maxVal: maxV, gridLevels: levels }
  }, [visibleSeries])

  const xAt = (idx: number, len: number) => {
    if (len <= 1) return PAD_L
    return PAD_L + (idx / (len - 1)) * PLOT_W
  }

  const yAt = (val: number) => {
    const range = maxVal - minVal || 1
    return PAD_T + PLOT_H - ((val - minVal) / range) * PLOT_H
  }

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = chartContainerRef.current
    if (!el || computed.dates.length < 2 || visibleSeries.length === 0) return
    const rect = el.getBoundingClientRect()
    if (!rect.width || !rect.height) return

    const relX = e.clientX - rect.left
    const relY = e.clientY - rect.top
    const svgX = (relX / rect.width) * SVG_W
    const svgY = (relY / rect.height) * SVG_H

    const len = computed.dates.length
    const ratio = Math.min(1, Math.max(0, (svgX - PAD_L) / PLOT_W))
    const dateIdx = Math.round(ratio * (len - 1))
    setHoveredDateIdx(dateIdx)

    let bestSector: string | null = null
    let bestDist = 30
    for (const s of visibleSeries) {
      const val = s.rsSeries[dateIdx]
      if (val === undefined) continue
      const cy = yAt(val)
      const dist = Math.abs(cy - svgY)
      if (dist < bestDist) {
        bestDist = dist
        bestSector = s.sector
      }
    }

    setHoveredSector(bestSector)
    const popX = relX + 18 + 230 > rect.width ? Math.max(12, relX - 240) : relX + 16
    const popY = relY + 18 + 230 > rect.height ? Math.max(12, relY - 220) : relY + 14
    setPopupPos({ x: popX, y: popY })
  }

  const handlePointerLeave = () => {
    setHoveredSector(null)
    setHoveredDateIdx(null)
  }

  const activeSectorObj = useMemo(
    () => computed.series.find((s) => s.sector === hoveredSector) ?? null,
    [computed.series, hoveredSector],
  )

  const xLabels = useMemo(() => {
    const d = computed.dates
    if (d.length < 2) return []
    const count = Math.min(6, d.length)
    const out: Array<{ idx: number; label: string }> = []
    for (let i = 0; i < count; i++) {
      const idx = Math.round((i / (count - 1)) * (d.length - 1))
      const parts = d[idx].split("-")
      out.push({ idx, label: parts.length === 3 ? `${parts[2]}/${parts[1]}` : d[idx] })
    }
    return out
  }, [computed.dates])

  const zeroY = yAt(0)

  return (
    <Panel>
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4 pb-3 border-b border-line">
        <div className="flex flex-wrap items-center gap-4">
          <div>
            <h3 className="text-[15px] font-semibold text-ink tracking-tight">
              Sức mạnh tương đối nhóm ngành (RS vs VN-Index)
            </h3>
            <p className="text-[12px] text-muted mt-0.5">
              Hiệu suất lũy kế của từng ngành trừ đi hiệu suất VN-Index từ đầu kỳ · Dữ liệu thực từ PostgreSQL ({computed.dates[computed.dates.length - 1] ? `đến ${formatDateVi(computed.dates[computed.dates.length - 1])}` : "HOSE"})
            </p>
          </div>
          <div className="inline-flex rounded-[6px] border border-line overflow-hidden text-[12px] font-medium">
            <button
              type="button"
              onClick={selectAll}
              className={`h-7 px-2.5 transition-colors ${
                allSelected ? "bg-ink text-paper" : "bg-surface text-secondary hover:bg-soft"
              }`}
            >
              Chọn tất cả
            </button>
            <button
              type="button"
              onClick={clearAll}
              className={`h-7 px-2.5 border-l border-line transition-colors ${
                noneSelected ? "bg-ink text-paper" : "bg-surface text-secondary hover:bg-soft"
              }`}
            >
              Bỏ chọn
            </button>
          </div>
        </div>

        {/* Timeframe Segmented Selector */}
        <div className="inline-flex rounded-[6px] border border-line overflow-hidden text-[12px] font-medium">
          {Object.keys(TIMEFRAMES).map((tf, idx) => {
            const active = timeframe === tf
            return (
              <button
                key={tf}
                type="button"
                onClick={() => setTimeframe(tf)}
                className={`h-7 px-3 transition-colors ${idx > 0 ? "border-l border-line" : ""} ${
                  active ? "bg-ink text-paper" : "bg-surface text-secondary hover:bg-soft"
                }`}
              >
                {tf}
              </button>
            )
          })}
        </div>
      </div>

      {/* Main Content: Left Ranked List + Right Multi-Line SVG Chart */}
      {loading && computed.series.length === 0 ? (
        <div className="h-[520px] flex items-center justify-center text-[13px] text-muted">
          Đang tải dữ liệu sức mạnh ngành…
        </div>
      ) : computed.series.length === 0 ? (
        <div className="h-[320px] flex items-center justify-center text-[13px] text-muted">
          Chưa có dữ liệu chuỗi lịch sử ngành.
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-[310px_minmax(0,1fr)] gap-4 items-stretch">
          {/* Left Ranked List (Top 1 -> Top 17) — fixed width with zero horizontal clipping */}
          <div className="lg:border-r border-line lg:pr-3 flex flex-col justify-between">
            <div className="flex items-center justify-between px-2 pb-1.5 mb-1 border-b border-line text-[10px] font-semibold uppercase tracking-wider text-muted">
              <span>Hạng · Nhóm ngành</span>
              <span className="text-right">RS ({timeframe})</span>
            </div>
            <div className="divide-y divide-line/50">
              {computed.series.map((item) => {
                const isSelected = selectedSectors.includes(item.sector)
                const isHovered = hoveredSector === item.sector
                return (
                  <button
                    key={item.sector}
                    type="button"
                    onClick={() => toggleSector(item.sector)}
                    onMouseEnter={() => {
                      setHoveredSector(item.sector)
                      setHoveredDateIdx(computed.dates.length - 1)
                      setPopupPos({ x: 64, y: 20 })
                    }}
                    onMouseLeave={() => setHoveredSector(null)}
                    className={`w-full flex items-center justify-between gap-2 px-2 py-1.5 text-left text-[12px] transition-colors rounded-[4px] ${
                      isSelected ? "opacity-100" : "opacity-40"
                    } ${isHovered ? "bg-soft" : "hover:bg-soft/60"}`}
                  >
                    <div className="flex items-center gap-2 min-w-0 flex-1">
                      <span className="w-[42px] shrink-0 font-mono text-[11px] text-muted tnum">
                        Top {item.rank}
                      </span>
                      <span
                        className="w-2 h-2 rounded-full shrink-0"
                        style={{ backgroundColor: item.color }}
                      />
                      <span className="font-medium text-ink truncate" title={item.label}>
                        {item.label}
                      </span>
                    </div>
                    <span
                      className={`w-[64px] shrink-0 text-right font-mono text-[11.5px] font-semibold tnum ${
                        item.latestRs >= 0 ? "text-gain" : "text-loss"
                      }`}
                    >
                      {item.latestRs >= 0 ? "+" : ""}
                      {item.latestRs.toFixed(1)}%
                    </span>
                  </button>
                )
              })}
            </div>
          </div>

          {/* Right Interactive Multi-Line Chart */}
          <div
            ref={chartContainerRef}
            onPointerMove={handlePointerMove}
            onPointerLeave={handlePointerLeave}
            className="relative min-w-0 h-[420px] lg:h-[520px] select-none overflow-hidden rounded-[8px] bg-surface"
          >
            <svg
              viewBox={`0 0 ${SVG_W} ${SVG_H}`}
              preserveAspectRatio="none"
              className="w-full h-full overflow-visible"
              role="img"
              aria-label="Biểu đồ sức mạnh ngành"
            >
              {/* Horizontal Grid Lines */}
              {gridLevels.map((lvl) => {
                const y = yAt(lvl)
                return (
                  <line
                    key={lvl}
                    x1={PAD_L}
                    x2={SVG_W - PAD_R}
                    y1={y}
                    y2={y}
                    stroke="var(--color-line)"
                    strokeWidth="1"
                    strokeDasharray="3 3"
                    vectorEffect="non-scaling-stroke"
                  />
                )
              })}

              {/* VN-Index 0% Baseline */}
              {zeroY >= PAD_T && zeroY <= SVG_H - PAD_B && (
                <line
                  x1={PAD_L}
                  x2={SVG_W - PAD_R}
                  y1={zeroY}
                  y2={zeroY}
                  stroke="var(--color-ink)"
                  strokeOpacity="0.45"
                  strokeWidth="1.2"
                  strokeDasharray="5 4"
                  vectorEffect="non-scaling-stroke"
                />
              )}

              {/* Vertical Crosshair Line */}
              {hoveredDateIdx !== null && computed.dates.length > 1 && (
                <line
                  x1={xAt(hoveredDateIdx, computed.dates.length)}
                  x2={xAt(hoveredDateIdx, computed.dates.length)}
                  y1={PAD_T}
                  y2={SVG_H - PAD_B}
                  stroke="var(--color-mineral)"
                  strokeWidth="1"
                  strokeDasharray="3 3"
                  vectorEffect="non-scaling-stroke"
                />
              )}

              {/* Sector Polylines */}
              {visibleSeries.map((s) => {
                const isHighlighted = hoveredSector === s.sector
                const dimmed = hoveredSector !== null && !isHighlighted
                const points = s.rsSeries
                  .map((val, idx) => `${xAt(idx, s.rsSeries.length).toFixed(1)},${yAt(val).toFixed(1)}`)
                  .join(" ")
                return (
                  <g key={s.sector}>
                    <polyline
                      points={points}
                      fill="none"
                      stroke={s.color}
                      strokeWidth={isHighlighted ? "2.6" : "1.5"}
                      strokeOpacity={dimmed ? 0.18 : 0.92}
                      strokeLinejoin="round"
                      strokeLinecap="round"
                      vectorEffect="non-scaling-stroke"
                    />
                    {isHighlighted && hoveredDateIdx !== null && s.rsSeries[hoveredDateIdx] !== undefined && (
                      <circle
                        cx={xAt(hoveredDateIdx, s.rsSeries.length)}
                        cy={yAt(s.rsSeries[hoveredDateIdx])}
                        r="4.5"
                        fill={s.color}
                        stroke="var(--color-surface)"
                        strokeWidth="2"
                        vectorEffect="non-scaling-stroke"
                      />
                    )}
                  </g>
                )
              })}

              {/* Bottom X-Axis Line */}
              <line
                x1={PAD_L}
                x2={SVG_W - PAD_R}
                y1={SVG_H - PAD_B}
                y2={SVG_H - PAD_B}
                stroke="var(--color-line-strong)"
                strokeWidth="1"
                vectorEffect="non-scaling-stroke"
              />
            </svg>

            {/* Left Y-Axis Labels */}
            <div className="pointer-events-none absolute left-1 inset-y-0">
              {gridLevels.map((lvl) => {
                const topPct = (yAt(lvl) / SVG_H) * 100
                return (
                  <span
                    key={lvl}
                    style={{ top: `${topPct}%` }}
                    className="absolute left-0 -translate-y-1/2 font-mono text-[10px] text-muted tnum"
                  >
                    {lvl > 0 ? `+${lvl}%` : `${lvl}%`}
                  </span>
                )
              })}
            </div>

            {/* Bottom Date Labels */}
            <div className="pointer-events-none absolute left-12 right-4 bottom-1.5 flex justify-between text-[10.5px] font-mono text-muted">
              {xLabels.map((item) => (
                <span key={`${item.idx}-${item.label}`}>{item.label}</span>
              ))}
            </div>

            {/* Floating Hover Card: Sector RS & Top Strongest Stocks */}
            {activeSectorObj && (
              <div
                style={{ transform: `translate(${popupPos.x}px, ${popupPos.y}px)` }}
                className="pointer-events-none absolute top-0 left-0 z-20 min-w-[215px] rounded-[8px] border border-line-strong bg-surface/95 backdrop-blur px-3.5 py-2.5 shadow-[0_10px_28px_rgba(24,32,29,0.14)] text-[12px]"
              >
                <div className="flex items-center justify-between gap-3 pb-1.5 border-b border-line">
                  <div className="flex items-center gap-2 font-semibold text-ink">
                    <span
                      className="w-2 h-2 rounded-full shrink-0"
                      style={{ backgroundColor: activeSectorObj.color }}
                    />
                    <span>{activeSectorObj.label}</span>
                  </div>
                  <span className="text-[11px] font-mono text-muted">Hạng {activeSectorObj.rank}</span>
                </div>

                {hoveredDateIdx !== null && computed.dates[hoveredDateIdx] && (
                  <div className="mt-1.5 space-y-0.5 text-[11px]">
                    <div className="flex items-center justify-between text-secondary">
                      <span>Ngày {formatDateVi(computed.dates[hoveredDateIdx])}</span>
                      <span
                        className={`font-mono font-semibold tnum ${
                          (activeSectorObj.rsSeries[hoveredDateIdx] ?? 0) >= 0 ? "text-gain" : "text-loss"
                        }`}
                      >
                        RS: {(activeSectorObj.rsSeries[hoveredDateIdx] ?? 0) >= 0 ? "+" : ""}
                        {(activeSectorObj.rsSeries[hoveredDateIdx] ?? 0).toFixed(2)}%
                      </span>
                    </div>
                    <div className="flex items-center justify-between text-muted">
                      <span>Hiệu suất ngành</span>
                      <span className="font-mono tnum">
                        {(activeSectorObj.cumReturnSeries[hoveredDateIdx] ?? 0) >= 0 ? "+" : ""}
                        {(activeSectorObj.cumReturnSeries[hoveredDateIdx] ?? 0).toFixed(2)}%
                      </span>
                    </div>
                  </div>
                )}

                <div className="mt-2 pt-1.5 border-t border-line/70 space-y-1">
                  <div className="text-[10px] uppercase tracking-wider text-muted font-semibold">
                    Cổ phiếu dẫn dắt ngành (RS 20P)
                  </div>
                  {activeSectorObj.topStocks.length === 0 ? (
                    <div className="text-[11px] text-muted">Chưa có dữ liệu</div>
                  ) : (
                    activeSectorObj.topStocks.slice(0, 6).map((st, idx) => (
                      <div key={st.symbol} className="flex items-center justify-between gap-3 text-[11.5px] leading-snug">
                        <span className="font-mono text-ink">
                          <span className="text-muted mr-1">{idx + 1}.</span>
                          <span className="font-semibold">{st.symbol}</span>
                        </span>
                        <PercentChange value={st.rs20d} arrow={false} className="text-[11px]" />
                      </div>
                    ))
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Quick Top-3 Sector Leaders Strip at Footer */}
      {computed.series.length >= 3 && (
        <div className="mt-3 pt-3 border-t border-line flex flex-wrap items-center justify-between gap-2 text-[12px]">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-muted">Nhóm dẫn dắt ({timeframe}):</span>
            {computed.series.slice(0, 3).map((s) => (
              <Link
                key={s.sector}
                to={`/discovery?sector=${encodeURIComponent(s.sector)}`}
                className="inline-flex items-center gap-1.5 rounded-[5px] border border-line bg-soft px-2.5 py-0.5 text-[11.5px] font-medium text-ink hover:border-line-strong transition-colors"
              >
                <span className="w-2 h-2 rounded-full" style={{ backgroundColor: s.color }} />
                <span>
                  {s.label}
                </span>
                <span className="font-mono text-gain tnum">
                  {s.latestRs >= 0 ? "+" : ""}
                  {s.latestRs.toFixed(1)}%
                </span>
              </Link>
            ))}
          </div>
          <Link to="/discovery" className="text-[12px] font-medium text-mineral hover:underline">
            Lọc cổ phiếu theo ngành →
          </Link>
        </div>
      )}
    </Panel>
  )
}
