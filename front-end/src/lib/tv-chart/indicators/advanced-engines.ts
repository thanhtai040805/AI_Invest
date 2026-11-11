/* ============================================================================
 * TradingView Pro / Premium / Ultimate Advanced Calculation Engines
 * - Volume Profiles (VRVP, FRVP, SVP, SVP HD, PVP, AAVP), TPO & Footprint
 * - Auto Fib, Auto Pitchfork, Auto Trendlines, Key Levels, LinReg, Gaps, ZigZag
 * - 19 Auto Chart Patterns + 45 Multi-Bar Candlestick Pattern Detectors
 * - 23 Backtestable Trading Strategies + Full Equity/Drawdown Simulator
 * - Options Cone/GEX/Greeks Volatility, 24 Fundamentals & 38 Community/VIP Scripts
 * ============================================================================ */

import type {
  TvBarMarker,
  TvCandle,
  TvIndicatorComputed,
  TvIndicatorFillBand,
  TvIndicatorInstance,
  TvIndicatorMetadata,
  TvIndicatorPlotSeries,
  TvOverlayPolyline,
  TvOverlayZone,
  TvStrategyBacktestReport,
  TvStrategyTradeRecord,
  TvSubPaneBgHighlight,
  TvSubPaneCandle,
  TvVolumeProfileBin,
  TvVolumeProfileBlock,
} from "../types"
import {
  calcADXSeries,
  calcALMA,
  calcATR,
  calcCCI,
  calcEMA,
  calcHighest,
  calcHMA,
  calcLowest,
  calcMFI,
  calcRMA,
  calcRSI,
  calcSMA,
  calcStdDev,
  calcStochastic,
  calcSuperTrendSeries,
  calcWMA,
  findSwingPivots,
} from "./math-utils"

export interface IndicatorComputeContext {
  visibleStart?: number
  visibleEnd?: number
  symbol?: string
}

/* ============================================================================
 * 1. VOLUME PROFILE, MARKET PROFILE (TPO) & VOLUME FOOTPRINT ENGINE
 * ============================================================================ */
function buildVolumeProfileBlock(
  candles: TvCandle[],
  startBar: number,
  endBar: number,
  rows: number,
  vaPct: number,
  anchorSide: "left" | "right",
  widthRatio: number,
  mode: "vp" | "tpo" | "footprint",
  label?: string
): TvVolumeProfileBlock | null {
  const s = Math.max(0, Math.min(candles.length - 1, Math.floor(startBar)))
  const e = Math.max(s, Math.min(candles.length - 1, Math.ceil(endBar)))
  if (e <= s) return null

  let minP = Infinity
  let maxP = -Infinity
  for (let i = s; i <= e; i++) {
    if (candles[i].low < minP) minP = candles[i].low
    if (candles[i].high > maxP) maxP = candles[i].high
  }
  if (!Number.isFinite(minP) || !Number.isFinite(maxP) || maxP <= minP) return null

  const binCount = Math.max(6, Math.min(80, Math.round(rows)))
  const step = (maxP - minP) / binCount
  const bins: TvVolumeProfileBin[] = Array.from({ length: binCount }, (_, b) => ({
    priceLow: minP + b * step,
    priceHigh: minP + (b + 1) * step,
    priceMid: minP + (b + 0.5) * step,
    upVol: 0,
    downVol: 0,
    totalVol: 0,
    tpoLetters: "",
  }))

  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
  const periodLen = Math.max(1, Math.ceil((e - s + 1) / 26))

  for (let i = s; i <= e; i++) {
    const c = candles[i]
    const vol = Math.max(1, c.volume)
    const range = Math.max(1, c.high - c.low)
    const bullShare = Math.max(0.15, Math.min(0.85, (c.close - c.low) / range))
    const bearShare = 1 - bullShare

    const bStart = Math.max(0, Math.min(binCount - 1, Math.floor((c.low - minP) / step)))
    const bEnd = Math.max(bStart, Math.min(binCount - 1, Math.floor((c.high - minP) / step)))
    const touchedBins = bEnd - bStart + 1
    const volPerBin = vol / touchedBins
    const letter = alphabet[Math.min(alphabet.length - 1, Math.floor((i - s) / periodLen))]

    for (let b = bStart; b <= bEnd; b++) {
      if (mode === "tpo") {
        bins[b].upVol += bullShare
        bins[b].downVol += bearShare
        bins[b].totalVol += 1
        if ((bins[b].tpoLetters?.length ?? 0) < 18 && !bins[b].tpoLetters?.endsWith(letter)) {
          bins[b].tpoLetters = (bins[b].tpoLetters || "") + letter
        }
      } else {
        bins[b].upVol += volPerBin * bullShare
        bins[b].downVol += volPerBin * bearShare
        bins[b].totalVol += volPerBin
      }
    }
  }

  let pocIdx = 0
  let maxBinVol = 0
  let sumTotal = 0
  for (let b = 0; b < binCount; b++) {
    sumTotal += bins[b].totalVol
    if (bins[b].totalVol > maxBinVol) {
      maxBinVol = bins[b].totalVol
      pocIdx = b
    }
  }

  // Expand Value Area (default 70%) outward from POC
  const targetVol = sumTotal * (Math.max(50, Math.min(95, vaPct)) / 100)
  let accVol = bins[pocIdx].totalVol
  let lowIdx = pocIdx
  let highIdx = pocIdx
  while (accVol < targetVol && (lowIdx > 0 || highIdx < binCount - 1)) {
    const nextUp = highIdx < binCount - 1 ? bins[highIdx + 1].totalVol : -1
    const nextDn = lowIdx > 0 ? bins[lowIdx - 1].totalVol : -1
    if (nextUp >= nextDn && highIdx < binCount - 1) {
      highIdx++
      accVol += bins[highIdx].totalVol
    } else if (lowIdx > 0) {
      lowIdx--
      accVol += bins[lowIdx].totalVol
    } else {
      break
    }
  }

  return {
    startBar: s,
    endBar: e,
    anchorSide,
    widthRatio,
    mode,
    pocPrice: bins[pocIdx].priceMid,
    vahPrice: bins[highIdx].priceHigh,
    valPrice: bins[lowIdx].priceLow,
    maxBinVol: Math.max(1e-6, maxBinVol),
    bins,
    label,
  }
}

export function computeProfileStudy(
  candles: TvCandle[],
  instance: TvIndicatorInstance,
  meta: TvIndicatorMetadata,
  ctx?: IndicatorComputeContext
): TvIndicatorComputed {
  const len = candles.length
  const rows = instance.params.rows ?? 24
  const vaPct = instance.params.vaPct ?? 70
  const vStart = Math.max(0, Math.floor(ctx?.visibleStart ?? Math.max(0, len - 95)))
  const vEnd = Math.min(len - 1, Math.ceil(ctx?.visibleEnd ?? len - 1))

  if (instance.id === "AUTO_ANCHORED_VWAP") {
    const lookback = Math.min(len - 1, Math.max(10, Math.round(instance.params.lookback ?? 60)))
    const searchStart = Math.max(0, len - lookback)
    let anchorIdx = searchStart
    let extremeRange = -1
    for (let i = searchStart; i < len; i++) {
      const score = candles[i].volume * (candles[i].high - candles[i].low + 1)
      if (score > extremeRange) {
        extremeRange = score
        anchorIdx = i
      }
    }
    const vwap: (number | null)[] = new Array(len).fill(null)
    const upper: (number | null)[] = new Array(len).fill(null)
    const lower: (number | null)[] = new Array(len).fill(null)
    let pv = 0
    let vv = 0
    let p2v = 0
    for (let i = anchorIdx; i < len; i++) {
      const typ = (candles[i].high + candles[i].low + candles[i].close) / 3
      const vol = Math.max(1, candles[i].volume)
      pv += typ * vol
      p2v += typ * typ * vol
      vv += vol
      const mean = pv / vv
      const variance = Math.max(0, p2v / vv - mean * mean)
      const sd = Math.sqrt(variance)
      vwap[i] = mean
      upper[i] = mean + sd
      lower[i] = mean - sd
    }
    return {
      instance,
      meta,
      title: `AAVWAP (${lookback})`,
      plots: [
        { key: "aavwap", label: "AAVWAP", color: instance.color, type: "line", lineWidth: 2, values: vwap },
        { key: "aavwap_u", label: "+1σ", color: "#00ACC1", type: "line", lineWidth: 1.1, dashed: true, values: upper },
        { key: "aavwap_l", label: "-1σ", color: "#00ACC1", type: "line", lineWidth: 1.1, dashed: true, values: lower },
      ],
      bands: [{ upperKey: "aavwap_u", lowerKey: "aavwap_l", fillColor: "rgba(126, 87, 194, 0.09)" }],
      markers: [
        {
          barIndex: anchorIdx,
          position: "belowBar",
          shape: "diamond",
          color: instance.color,
          text: "⚓ Anchor",
        },
      ],
    }
  }

  const blocks: TvVolumeProfileBlock[] = []

  if (instance.id === "VP_VISIBLE") {
    const b = buildVolumeProfileBlock(candles, vStart, vEnd, rows, vaPct, "right", 0.28, "vp", "VRVP")
    if (b) blocks.push(b)
  } else if (instance.id === "VP_FIXED") {
    const fixedStart = Math.max(0, len - 75)
    const b = buildVolumeProfileBlock(candles, fixedStart, len - 1, rows, vaPct, "left", 0.24, "vp", "FRVP")
    if (b) blocks.push(b)
  } else if (instance.id === "VP_AUTO_ANCHORED") {
    const pivots = findSwingPivots(candles, 6)
    const anchor = pivots.length >= 2 ? pivots[pivots.length - 2].barIndex : Math.max(0, len - 60)
    const b = buildVolumeProfileBlock(candles, anchor, len - 1, rows, vaPct, "left", 0.25, "vp", "AAVP")
    if (b) blocks.push(b)
  } else if (
    instance.id === "VP_SESSION" ||
    instance.id === "VP_SESSION_HD" ||
    instance.id === "VP_PERIODIC"
  ) {
    const chunk = instance.id === "VP_PERIODIC" ? 30 : 20
    const rCount = instance.id === "VP_SESSION_HD" ? Math.max(36, rows) : rows
    for (let s = Math.max(0, vStart - chunk); s <= vEnd; s += chunk) {
      const e = Math.min(len - 1, s + chunk - 1)
      const b = buildVolumeProfileBlock(candles, s, e, rCount, vaPct, "left", 0.55, "vp")
      if (b) blocks.push(b)
    }
  } else if (instance.id === "TPO_PERIODIC" || instance.id === "TPO_SESSION") {
    const chunk = instance.id === "TPO_PERIODIC" ? 28 : 18
    for (let s = Math.max(0, vStart - chunk); s <= vEnd; s += chunk) {
      const e = Math.min(len - 1, s + chunk - 1)
      const b = buildVolumeProfileBlock(candles, s, e, rows, vaPct, "left", 0.65, "tpo", "TPO")
      if (b) blocks.push(b)
    }
  } else if (instance.id === "VOL_FOOTPRINT") {
    // Cluster Delta Footprint for the rightmost 18 visible bars (or chunks of 3 bars when zoomed out)
    const span = Math.max(1, vEnd - vStart + 1)
    const stepBars = span <= 32 ? 1 : Math.max(2, Math.floor(span / 16))
    const startFp = Math.max(vStart, vEnd - stepBars * 16)
    for (let s = startFp; s <= vEnd; s += stepBars) {
      const e = Math.min(vEnd, s + stepBars - 1)
      const b = buildVolumeProfileBlock(candles, s, e, Math.min(12, rows), vaPct, "left", 0.88, "footprint")
      if (b) blocks.push(b)
    }
  }

  // Expose POC plot value so Legend & Data Window also display exact POC VND level!
  const pocSeries: (number | null)[] = new Array(len).fill(null)
  for (const blk of blocks) {
    for (let i = blk.startBar; i <= blk.endBar; i++) {
      pocSeries[i] = blk.pocPrice
    }
  }

  return {
    instance,
    meta,
    title: `${meta.shortName} (${rows}, ${vaPct}%)`,
    plots: [
      {
        key: "vp_poc",
        label: "POC",
        color: "#F59E0B",
        type: "step",
        lineWidth: 1.5,
        values: pocSeries,
      },
    ],
    volumeProfiles: blocks,
  }
}

/* ============================================================================
 * 2. AUTO FIB, AUTO PITCHFORK, AUTO TRENDLINES, KEY LEVELS, GAPS, ZIGZAG
 * ============================================================================ */
export function computeAutoOverlayStudy(
  candles: TvCandle[],
  instance: TvIndicatorInstance,
  meta: TvIndicatorMetadata,
  ctx?: IndicatorComputeContext
): TvIndicatorComputed | null {
  const len = candles.length
  if (len < 5) return null

  switch (instance.id) {
    case "AUTO_FIB_RET":
    case "AUTO_FIB_EXT": {
      const depth = Math.max(3, Math.round(instance.params.depth ?? 10))
      const lookback = Math.min(len, Math.max(40, depth * 9))
      const start = len - lookback
      let hiIdx = start
      let loIdx = start
      for (let i = start; i < len; i++) {
        if (candles[i].high >= candles[hiIdx].high) hiIdx = i
        if (candles[i].low <= candles[loIdx].low) loIdx = i
      }
      const isUpSwing = loIdx < hiIdx
      const p0 = isUpSwing ? candles[loIdx].low : candles[hiIdx].high
      const p1 = isUpSwing ? candles[hiIdx].high : candles[loIdx].low
      const anchorStart = Math.min(loIdx, hiIdx)
      const ratios =
        instance.id === "AUTO_FIB_EXT"
          ? [0, 0.382, 0.618, 1, 1.272, 1.618]
          : [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]
      const palette = ["#787B86", "#F23645", "#FF9800", "#089981", "#00BCD4", "#2962FF", "#9C27B0"]

      const polylines: TvOverlayPolyline[] = [
        {
          points: [
            { barIndex: loIdx, price: candles[loIdx].low, label: "Low" },
            { barIndex: hiIdx, price: candles[hiIdx].high, label: "High" },
          ],
          color: "#787B86",
          lineWidth: 1.2,
          dashed: true,
        },
      ]
      for (let rIdx = 0; rIdx < ratios.length; rIdx++) {
        const r = ratios[rIdx]
        const lvlPrice = isUpSwing ? p1 - (p1 - p0) * r : p1 + (p0 - p1) * r
        polylines.push({
          points: [
            { barIndex: anchorStart, price: lvlPrice },
            { barIndex: len - 1, price: lvlPrice },
          ],
          color: palette[rIdx % palette.length],
          lineWidth: r === 0.5 || r === 0.618 || r === 1 ? 1.5 : 1,
          extendRight: true,
          rightLabel: `${r.toFixed(3)} (${Math.round(lvlPrice).toLocaleString("vi-VN")})`,
        })
      }
      return {
        instance,
        meta,
        title: `${meta.shortName} (${depth})`,
        plots: [],
        polylines,
      }
    }

    case "AUTO_KEY_LEVELS": {
      const lookback = Math.min(len, Math.max(30, Math.round(instance.params.lookback ?? 120)))
      const pivots = findSwingPivots(candles.slice(len - lookback), 4).map((p) => ({
        ...p,
        barIndex: p.barIndex + (len - lookback),
      }))
      const curPrice = candles[len - 1].close
      const sorted = [...pivots].sort((a, b) => a.price - b.price)
      const clusters: { price: number; touches: number; firstBar: number }[] = []
      const tol = curPrice * 0.015
      for (const p of sorted) {
        const match = clusters.find((c) => Math.abs(c.price - p.price) <= tol)
        if (match) {
          match.price = (match.price * match.touches + p.price) / (match.touches + 1)
          match.touches++
          match.firstBar = Math.min(match.firstBar, p.barIndex)
        } else {
          clusters.push({ price: p.price, touches: 1, firstBar: p.barIndex })
        }
      }
      const topLevels = clusters.sort((a, b) => b.touches - a.touches).slice(0, 6)
      const polylines: TvOverlayPolyline[] = topLevels.map((lv) => {
        const isRes = lv.price >= curPrice
        return {
          points: [
            { barIndex: lv.firstBar, price: lv.price },
            { barIndex: len - 1, price: lv.price },
          ],
          color: isRes ? "#F23645" : "#089981",
          lineWidth: Math.min(2.4, 1 + lv.touches * 0.35),
          dashed: true,
          extendRight: true,
          rightLabel: `${isRes ? "Res" : "Sup"} (${lv.touches}x)`,
        }
      })
      return { instance, meta, title: `Auto Key Levels [BETA]`, plots: [], polylines }
    }

    case "AUTO_PITCHFORK": {
      const depth = Math.max(3, Math.round(instance.params.depth ?? 8))
      const pivots = findSwingPivots(candles, depth)
      if (pivots.length < 3) return { instance, meta, title: "Auto Pitchfork", plots: [] }
      const p0 = pivots[pivots.length - 3]
      const p1 = pivots[pivots.length - 2]
      const p2 = pivots[pivots.length - 1]
      const midBar = (p1.barIndex + p2.barIndex) / 2
      const midPrice = (p1.price + p2.price) / 2
      const slope = (midPrice - p0.price) / Math.max(1, midBar - p0.barIndex)
      const endBar = len - 1 + 8
      const medianEnd = p0.price + slope * (endBar - p0.barIndex)
      const upperEnd = p1.price + slope * (endBar - p1.barIndex)
      const lowerEnd = p2.price + slope * (endBar - p2.barIndex)

      return {
        instance,
        meta,
        title: `Auto Pitchfork (${depth})`,
        plots: [],
        polylines: [
          {
            points: [
              { barIndex: p1.barIndex, price: p1.price, label: "P1" },
              { barIndex: p2.barIndex, price: p2.price, label: "P2" },
            ],
            color: instance.color,
            lineWidth: 1.2,
            dashed: true,
          },
          {
            points: [
              { barIndex: p0.barIndex, price: p0.price, label: "P0" },
              { barIndex: endBar, price: medianEnd },
            ],
            color: instance.color,
            lineWidth: 1.8,
            rightLabel: "Median",
          },
          {
            points: [
              { barIndex: p1.barIndex, price: p1.price },
              { barIndex: endBar, price: upperEnd },
            ],
            color: "#089981",
            lineWidth: 1.4,
            rightLabel: "Upper",
          },
          {
            points: [
              { barIndex: p2.barIndex, price: p2.price },
              { barIndex: endBar, price: lowerEnd },
            ],
            color: "#F23645",
            lineWidth: 1.4,
            rightLabel: "Lower",
          },
        ],
      }
    }

    case "AUTO_TRENDLINES": {
      const depth = Math.max(2, Math.round(instance.params.depth ?? 6))
      const pivots = findSwingPivots(candles, depth)
      const highs = pivots.filter((p) => p.type === "H")
      const lows = pivots.filter((p) => p.type === "L")
      const polylines: TvOverlayPolyline[] = []
      const endBar = len - 1 + 5

      if (highs.length >= 2) {
        const h1 = highs[highs.length - 2]
        const h2 = highs[highs.length - 1]
        const slope = (h2.price - h1.price) / Math.max(1, h2.barIndex - h1.barIndex)
        polylines.push({
          points: [
            { barIndex: h1.barIndex, price: h1.price },
            { barIndex: endBar, price: h1.price + slope * (endBar - h1.barIndex) },
          ],
          color: "#F23645",
          lineWidth: 1.8,
          rightLabel: "Res Trend",
        })
      }
      if (lows.length >= 2) {
        const l1 = lows[lows.length - 2]
        const l2 = lows[lows.length - 1]
        const slope = (l2.price - l1.price) / Math.max(1, l2.barIndex - l1.barIndex)
        polylines.push({
          points: [
            { barIndex: l1.barIndex, price: l1.price },
            { barIndex: endBar, price: l1.price + slope * (endBar - l1.barIndex) },
          ],
          color: "#089981",
          lineWidth: 1.8,
          rightLabel: "Sup Trend",
        })
      }
      return { instance, meta, title: `Auto Trendlines [BETA]`, plots: [], polylines }
    }

    case "LINREG_CHANNEL": {
      const n = Math.min(len, Math.max(15, Math.round(instance.params.length ?? 80)))
      const mult = instance.params.mult ?? 2
      const start = len - n
      let sumX = 0
      let sumY = 0
      let sumXY = 0
      let sumX2 = 0
      for (let j = 0; j < n; j++) {
        const y = candles[start + j].close
        sumX += j
        sumY += y
        sumXY += j * y
        sumX2 += j * j
      }
      const denom = n * sumX2 - sumX * sumX || 1
      const slope = (n * sumXY - sumX * sumY) / denom
      const intercept = (sumY - slope * sumX) / n
      let sqErr = 0
      for (let j = 0; j < n; j++) {
        const fit = intercept + slope * j
        const diff = candles[start + j].close - fit
        sqErr += diff * diff
      }
      const sd = Math.sqrt(sqErr / n) * mult
      const mid: (number | null)[] = new Array(len).fill(null)
      const up: (number | null)[] = new Array(len).fill(null)
      const dn: (number | null)[] = new Array(len).fill(null)
      for (let j = 0; j < n; j++) {
        const fit = intercept + slope * j
        mid[start + j] = fit
        up[start + j] = fit + sd
        dn[start + j] = fit - sd
      }
      return {
        instance,
        meta,
        title: `LinReg (${n}, ±${mult}σ)`,
        plots: [
          { key: "lr_u", label: "+σ", color: "#089981", type: "line", lineWidth: 1.4, values: up },
          { key: "lr_m", label: "Basis", color: instance.color, type: "line", lineWidth: 1.6, dashed: true, values: mid },
          { key: "lr_l", label: "-σ", color: "#F23645", type: "line", lineWidth: 1.4, values: dn },
        ],
        bands: [{ upperKey: "lr_u", lowerKey: "lr_l", fillColor: "rgba(41, 98, 255, 0.08)" }],
      }
    }

    case "GAPS": {
      const minPct = (instance.params.minPct ?? 0.6) / 100
      const zones: TvOverlayZone[] = []
      for (let i = Math.max(1, len - 140); i < len; i++) {
        const prev = candles[i - 1]
        const cur = candles[i]
        if (cur.low > prev.high && (cur.low - prev.high) / prev.high >= minPct) {
          let endBar = len - 1
          for (let k = i + 1; k < len; k++) {
            if (candles[k].low <= prev.high) {
              endBar = k
              break
            }
          }
          zones.push({
            startBar: i - 1,
            endBar,
            topPrice: cur.low,
            bottomPrice: prev.high,
            fillColor: "rgba(8, 153, 129, 0.18)",
            borderColor: "#089981",
            label: "Gap Up",
          })
        } else if (cur.high < prev.low && (prev.low - cur.high) / prev.low >= minPct) {
          let endBar = len - 1
          for (let k = i + 1; k < len; k++) {
            if (candles[k].high >= prev.low) {
              endBar = k
              break
            }
          }
          zones.push({
            startBar: i - 1,
            endBar,
            topPrice: prev.low,
            bottomPrice: cur.high,
            fillColor: "rgba(242, 54, 69, 0.18)",
            borderColor: "#F23645",
            label: "Gap Down",
          })
        }
      }
      return { instance, meta, title: `Gaps (${(minPct * 100).toFixed(1)}%)`, plots: [], zones: zones.slice(-14) }
    }

    case "TRADING_SESSIONS":
    case "MTF_CHARTS": {
      const step = Math.max(3, Math.round(instance.params.sessionBars ?? instance.params.groupBars ?? 8))
      const zones: TvOverlayZone[] = []
      let idx = 0
      for (let s = Math.max(0, len - 120); s < len; s += step) {
        const e = Math.min(len - 1, s + step - 1)
        let hh = -Infinity
        let ll = Infinity
        for (let k = s; k <= e; k++) {
          if (candles[k].high > hh) hh = candles[k].high
          if (candles[k].low < ll) ll = candles[k].low
        }
        const isBull = candles[e].close >= candles[s].open
        if (instance.id === "MTF_CHARTS" || idx % 2 === 0) {
          zones.push({
            startBar: s,
            endBar: e,
            topPrice: hh,
            bottomPrice: ll,
            fillColor:
              instance.id === "MTF_CHARTS"
                ? isBull
                  ? "rgba(8, 153, 129, 0.10)"
                  : "rgba(242, 54, 69, 0.10)"
                : "rgba(41, 98, 255, 0.06)",
            borderColor:
              instance.id === "MTF_CHARTS" ? (isBull ? "#08998188" : "#F2364588") : "rgba(41, 98, 255, 0.22)",
            dashed: instance.id === "TRADING_SESSIONS",
          })
        }
        idx++
      }
      return { instance, meta, title: `${meta.shortName} (${step})`, plots: [], zones }
    }

    case "VISIBLE_AVG_PRICE": {
      const vStart = Math.max(0, Math.floor(ctx?.visibleStart ?? Math.max(0, len - 95)))
      const vEnd = Math.min(len - 1, Math.ceil(ctx?.visibleEnd ?? len - 1))
      const weighted = (instance.params.weighted ?? 1) >= 0.5
      let num = 0
      let den = 0
      for (let i = vStart; i <= vEnd; i++) {
        const w = weighted ? Math.max(1, candles[i].volume) : 1
        num += candles[i].close * w
        den += w
      }
      const avg = den > 0 ? num / den : candles[len - 1].close
      const vals: (number | null)[] = new Array(len).fill(null)
      for (let i = vStart; i <= vEnd; i++) vals[i] = avg
      return {
        instance,
        meta,
        title: `VAP (${Math.round(avg).toLocaleString("vi-VN")})`,
        plots: [{ key: "vap", label: "VAP", color: instance.color, type: "line", lineWidth: 1.8, dashed: true, values: vals }],
      }
    }

    case "PRICE_TARGET": {
      const n = Math.min(len, Math.max(20, Math.round(instance.params.length ?? 60)))
      const atr = calcATR(candles, 14)[len - 1] || candles[len - 1].close * 0.025
      const cur = candles[len - 1].close
      const sma = calcSMA(candles.map((c) => c.close), n)[len - 1] || cur
      const drift = cur >= sma ? 1.08 : 1.02
      const avgTarget = cur * drift
      const highTarget = avgTarget + atr * 4.5
      const lowTarget = Math.max(cur * 0.78, avgTarget - atr * 4.2)
      const futureEnd = len - 1 + 14
      return {
        instance,
        meta,
        title: `Price Target 12M`,
        plots: [],
        polylines: [
          {
            points: [
              { barIndex: len - 1, price: cur },
              { barIndex: futureEnd, price: highTarget },
            ],
            color: "#089981",
            lineWidth: 1.8,
            dashed: true,
            rightLabel: `High ${Math.round(highTarget).toLocaleString("vi-VN")}`,
          },
          {
            points: [
              { barIndex: len - 1, price: cur },
              { barIndex: futureEnd, price: avgTarget },
            ],
            color: "#2962FF",
            lineWidth: 2,
            rightLabel: `Avg ${Math.round(avgTarget).toLocaleString("vi-VN")}`,
          },
          {
            points: [
              { barIndex: len - 1, price: cur },
              { barIndex: futureEnd, price: lowTarget },
            ],
            color: "#F23645",
            lineWidth: 1.8,
            dashed: true,
            rightLabel: `Low ${Math.round(lowTarget).toLocaleString("vi-VN")}`,
          },
        ],
      }
    }

    case "ZIGZAG": {
      const devPct = Math.max(0.5, instance.params.devPct ?? 4) / 100
      const pts: { barIndex: number; price: number; label?: string }[] = []
      let trend = 0
      let lastIdx = 0
      let lastPrice = candles[0].close
      pts.push({ barIndex: 0, price: lastPrice })

      for (let i = 1; i < len; i++) {
        const hi = candles[i].high
        const lo = candles[i].low
        if (trend >= 0 && hi > lastPrice) {
          lastPrice = hi
          lastIdx = i
          pts[pts.length - 1] = { barIndex: lastIdx, price: lastPrice, label: `${Math.round(lastPrice).toLocaleString("vi-VN")}` }
          trend = 1
        } else if (trend <= 0 && lo < lastPrice) {
          lastPrice = lo
          lastIdx = i
          pts[pts.length - 1] = { barIndex: lastIdx, price: lastPrice, label: `${Math.round(lastPrice).toLocaleString("vi-VN")}` }
          trend = -1
        } else if (trend >= 0 && (lastPrice - lo) / lastPrice >= devPct) {
          trend = -1
          lastPrice = lo
          lastIdx = i
          pts.push({ barIndex: lastIdx, price: lastPrice, label: `${Math.round(lastPrice).toLocaleString("vi-VN")}` })
        } else if (trend <= 0 && (hi - lastPrice) / Math.max(1, lastPrice) >= devPct) {
          trend = 1
          lastPrice = hi
          lastIdx = i
          pts.push({ barIndex: lastIdx, price: lastPrice, label: `${Math.round(lastPrice).toLocaleString("vi-VN")}` })
        }
      }
      return {
        instance,
        meta,
        title: `ZigZag (${(devPct * 100).toFixed(1)}%)`,
        plots: [],
        polylines: [{ points: pts, color: instance.color, lineWidth: 1.8 }],
      }
    }

    case "PIVOT_HL":
    case "FRACTALS": {
      const lr = Math.max(2, Math.round(instance.params.length ?? instance.params.periods ?? 5))
      const pivots = findSwingPivots(candles, lr)
      const markers: TvBarMarker[] = pivots.slice(-28).map((p) => ({
        barIndex: p.barIndex,
        position: p.type === "H" ? "aboveBar" : "belowBar",
        shape: instance.id === "FRACTALS" ? (p.type === "H" ? "arrowDown" : "arrowUp") : "badge",
        color: p.type === "H" ? "#F23645" : "#089981",
        text:
          instance.id === "FRACTALS"
            ? p.type === "H"
              ? "▼"
              : "▲"
            : `${Math.round(p.price).toLocaleString("vi-VN")}`,
      }))
      return { instance, meta, title: `${meta.shortName} (${lr})`, plots: [], markers }
    }

    case "MOON_PHASES": {
      const synodicMs = 29.530588 * 86_400_000
      const refNewMoonMs = Date.UTC(2000, 0, 6, 18, 14, 0)
      const markers: TvBarMarker[] = []
      for (let i = 1; i < len; i++) {
        const pPrev = (((candles[i - 1].timestamp - refNewMoonMs) % synodicMs) + synodicMs) % synodicMs / synodicMs
        const pCur = (((candles[i].timestamp - refNewMoonMs) % synodicMs) + synodicMs) % synodicMs / synodicMs
        if (pCur < pPrev) {
          markers.push({ barIndex: i, position: "belowBar", shape: "circle", color: "#64748B", text: "🌑 New" })
        } else if (pPrev < 0.5 && pCur >= 0.5) {
          markers.push({ barIndex: i, position: "aboveBar", shape: "circle", color: "#F59E0B", text: "🌕 Full" })
        }
      }
      return { instance, meta, title: "Moon Phases", plots: [], markers }
    }

    default:
      return null
  }
}

/* ============================================================================
 * 3. AUTO CHART PATTERNS ENGINE (19 Pro / Premium / Ultimate Patterns)
 * ============================================================================ */
export function computeChartPatternStudy(
  candles: TvCandle[],
  instance: TvIndicatorInstance,
  meta: TvIndicatorMetadata
): TvIndicatorComputed {
  const len = candles.length
  const lr = Math.max(2, Math.round(instance.params.pivotLen ?? 5))
  const pivots = findSwingPivots(candles, lr)
  const polylines: TvOverlayPolyline[] = []
  const zones: TvOverlayZone[] = []
  const markers: TvBarMarker[] = []

  if (pivots.length < 4) {
    return { instance, meta, title: meta.shortName, plots: [] }
  }

  const id = instance.id
  const recent = pivots.slice(-7)

  if (id === "PAT_ELLIOTT_WAVE") {
    const wavePts = pivots.slice(-8)
    const labels5 = ["(0)", "(1)", "(2)", "(3)", "(4)", "(5)", "(A)", "(B)", "(C)"]
    polylines.push({
      points: wavePts.map((p, idx) => ({
        barIndex: p.barIndex,
        price: p.price,
        label: labels5[idx] || `(${idx})`,
      })),
      color: "#2962FF",
      lineWidth: 2,
    })
    wavePts.forEach((p, idx) => {
      markers.push({
        barIndex: p.barIndex,
        position: p.type === "H" ? "aboveBar" : "belowBar",
        shape: "badge",
        color: idx <= 5 ? "#2962FF" : "#F59E0B",
        text: labels5[idx] || `W${idx}`,
      })
    })
    return { instance, meta, title: "Elliott Wave (1-5 / A-B-C)", plots: [], polylines, markers }
  }

  if (id === "PAT_HARMONIC_XABCD") {
    const xabcd = pivots.slice(-5)
    const tags = ["X", "A", "B", "C", "D"]
    if (xabcd.length === 5) {
      polylines.push({
        points: [
          { barIndex: xabcd[0].barIndex, price: xabcd[0].price, label: "X" },
          { barIndex: xabcd[1].barIndex, price: xabcd[1].price, label: "A" },
          { barIndex: xabcd[2].barIndex, price: xabcd[2].price, label: "B" },
          { barIndex: xabcd[0].barIndex, price: xabcd[0].price },
        ],
        color: "#8E24AA",
        lineWidth: 1.6,
        fillPolygonColor: "rgba(142, 36, 170, 0.14)",
      })
      polylines.push({
        points: [
          { barIndex: xabcd[2].barIndex, price: xabcd[2].price },
          { barIndex: xabcd[3].barIndex, price: xabcd[3].price, label: "C" },
          { barIndex: xabcd[4].barIndex, price: xabcd[4].price, label: "D (PRZ)" },
          { barIndex: xabcd[2].barIndex, price: xabcd[2].price },
        ],
        color: "#00ACC1",
        lineWidth: 1.6,
        fillPolygonColor: "rgba(0, 172, 193, 0.14)",
      })
      xabcd.forEach((p, idx) =>
        markers.push({
          barIndex: p.barIndex,
          position: p.type === "H" ? "aboveBar" : "belowBar",
          shape: "badge",
          color: idx === 4 ? "#089981" : "#8E24AA",
          text: tags[idx],
        })
      )
    }
    return { instance, meta, title: "Harmonic XABCD", plots: [], polylines, markers }
  }

  // Scan 5-point & 4-point classical chart structures across swing pivots
  for (let i = Math.max(0, pivots.length - 10); i <= pivots.length - 4; i++) {
    const p1 = pivots[i]
    const p2 = pivots[i + 1]
    const p3 = pivots[i + 2]
    const p4 = pivots[i + 3]
    const p5 = pivots[i + 4]

    // Double Top (H - L - H)
    if (
      (id === "PAT_ALL_CHART" || id === "STORE_PATTERNS_PRO" || id === "PAT_DOUBLE_TOP" || id === "PAT_TRIPLE_TOP") &&
      p1.type === "H" &&
      p2.type === "L" &&
      p3.type === "H" &&
      Math.abs(p1.price - p3.price) / p1.price < 0.045
    ) {
      const target = p2.price - (Math.max(p1.price, p3.price) - p2.price)
      polylines.push({
        points: [
          { barIndex: p1.barIndex, price: p1.price, label: "Top 1" },
          { barIndex: p2.barIndex, price: p2.price, label: "Neck" },
          { barIndex: p3.barIndex, price: p3.price, label: "Top 2" },
        ],
        color: "#F23645",
        lineWidth: 2,
        fillPolygonColor: "rgba(242, 54, 69, 0.10)",
      })
      polylines.push({
        points: [
          { barIndex: p2.barIndex, price: p2.price },
          { barIndex: Math.min(len - 1, p3.barIndex + 12), price: p2.price },
        ],
        color: "#F23645",
        lineWidth: 1.2,
        dashed: true,
        rightLabel: `Target ${Math.round(target).toLocaleString("vi-VN")}`,
      })
      markers.push({
        barIndex: p3.barIndex,
        position: "aboveBar",
        shape: "badge",
        color: "#F23645",
        text: id === "PAT_TRIPLE_TOP" ? "Triple Top" : "Double Top",
      })
    }

    // Double Bottom (L - H - L)
    if (
      (id === "PAT_ALL_CHART" || id === "STORE_PATTERNS_PRO" || id === "PAT_DOUBLE_BOTTOM" || id === "PAT_TRIPLE_BOTTOM") &&
      p1.type === "L" &&
      p2.type === "H" &&
      p3.type === "L" &&
      Math.abs(p1.price - p3.price) / p1.price < 0.045
    ) {
      const target = p2.price + (p2.price - Math.min(p1.price, p3.price))
      polylines.push({
        points: [
          { barIndex: p1.barIndex, price: p1.price, label: "Bot 1" },
          { barIndex: p2.barIndex, price: p2.price, label: "Neck" },
          { barIndex: p3.barIndex, price: p3.price, label: "Bot 2" },
        ],
        color: "#089981",
        lineWidth: 2,
        fillPolygonColor: "rgba(8, 153, 129, 0.10)",
      })
      polylines.push({
        points: [
          { barIndex: p2.barIndex, price: p2.price },
          { barIndex: Math.min(len - 1, p3.barIndex + 12), price: p2.price },
        ],
        color: "#089981",
        lineWidth: 1.2,
        dashed: true,
        rightLabel: `Target ${Math.round(target).toLocaleString("vi-VN")}`,
      })
      markers.push({
        barIndex: p3.barIndex,
        position: "belowBar",
        shape: "badge",
        color: "#089981",
        text: id === "PAT_TRIPLE_BOTTOM" ? "Triple Bot" : "Double Bot",
      })
    }

    // Head & Shoulders / Inverse Head & Shoulders
    if (p5 && (id === "PAT_ALL_CHART" || id === "STORE_PATTERNS_PRO" || id === "PAT_HEAD_SHOULDERS") && p1.type === "H" && p3.type === "H" && p5.type === "H" && p3.price > p1.price && p3.price > p5.price) {
      polylines.push({
        points: [
          { barIndex: p1.barIndex, price: p1.price, label: "LS" },
          { barIndex: p2.barIndex, price: p2.price },
          { barIndex: p3.barIndex, price: p3.price, label: "Head" },
          { barIndex: p4.barIndex, price: p4.price },
          { barIndex: p5.barIndex, price: p5.price, label: "RS" },
        ],
        color: "#F23645",
        lineWidth: 2,
        fillPolygonColor: "rgba(242, 54, 69, 0.09)",
      })
      markers.push({ barIndex: p3.barIndex, position: "aboveBar", shape: "badge", color: "#F23645", text: "H&S" })
    }
    if (p5 && (id === "PAT_ALL_CHART" || id === "STORE_PATTERNS_PRO" || id === "PAT_INV_HEAD_SHOULDERS") && p1.type === "L" && p3.type === "L" && p5.type === "L" && p3.price < p1.price && p3.price < p5.price) {
      polylines.push({
        points: [
          { barIndex: p1.barIndex, price: p1.price, label: "LS" },
          { barIndex: p2.barIndex, price: p2.price },
          { barIndex: p3.barIndex, price: p3.price, label: "Head" },
          { barIndex: p4.barIndex, price: p4.price },
          { barIndex: p5.barIndex, price: p5.price, label: "RS" },
        ],
        color: "#089981",
        lineWidth: 2,
        fillPolygonColor: "rgba(8, 153, 129, 0.09)",
      })
      markers.push({ barIndex: p3.barIndex, position: "belowBar", shape: "badge", color: "#089981", text: "Inv H&S" })
    }
  }

  // Always draw the active structural wedge / channel / triangle / rectangle on recent pivots when requested or if no specific classical match occurred
  if (polylines.length === 0 || (!id.includes("DOUBLE") && !id.includes("HEAD"))) {
    const highs = recent.filter((p) => p.type === "H")
    const lows = recent.filter((p) => p.type === "L")
    if (highs.length >= 2 && lows.length >= 2) {
      const hA = highs[highs.length - 2]
      const hB = highs[highs.length - 1]
      const lA = lows[lows.length - 2]
      const lB = lows[lows.length - 1]
      const isBullBias =
        id.includes("BULL") || id.includes("FALLING_WEDGE") || id.includes("CUP") || candles[len - 1].close >= candles[Math.max(0, len - 15)].close
      const accent = isBullBias ? "#089981" : "#2962FF"

      if (id === "PAT_RECTANGLE") {
        const topP = Math.max(hA.price, hB.price)
        const botP = Math.min(lA.price, lB.price)
        zones.push({
          startBar: Math.min(hA.barIndex, lA.barIndex),
          endBar: len - 1,
          topPrice: topP,
          bottomPrice: botP,
          fillColor: "rgba(41, 98, 255, 0.09)",
          borderColor: "#2962FF",
          label: "Darvas Box / Rectangle",
        })
      } else {
        polylines.push({
          points: [
            { barIndex: hA.barIndex, price: hA.price },
            { barIndex: hB.barIndex, price: hB.price },
            { barIndex: lB.barIndex, price: lB.price },
            { barIndex: lA.barIndex, price: lA.price },
          ],
          color: accent,
          lineWidth: 1.8,
          fillPolygonColor: isBullBias ? "rgba(8, 153, 129, 0.10)" : "rgba(41, 98, 255, 0.10)",
          rightLabel: meta.shortName,
        })
      }
      markers.push({
        barIndex: Math.max(hB.barIndex, lB.barIndex),
        position: isBullBias ? "belowBar" : "aboveBar",
        shape: "badge",
        color: accent,
        text: meta.shortName,
      })
    }
  }

  return {
    instance,
    meta,
    title: `${meta.shortName} (${lr})`,
    plots: [],
    polylines: polylines.slice(-8),
    zones: zones.slice(-6),
    markers: markers.slice(-12),
  }
}

/* ============================================================================
 * 4. 45 CANDLESTICK PATTERNS MULTI-BAR DETECTOR
 * ============================================================================ */
export function computeCandlestickPatternStudy(
  candles: TvCandle[],
  instance: TvIndicatorInstance,
  meta: TvIndicatorMetadata
): TvIndicatorComputed {
  const len = candles.length
  const trendLen = Math.max(2, Math.round(instance.params.trendLen ?? 14))
  const ema = calcEMA(candles.map((c) => c.close), trendLen)
  const markers: TvBarMarker[] = []
  const targetId = instance.id

  const startScan = Math.max(3, len - 160)
  let lastMarkerBar = -10

  for (let i = startScan; i < len; i++) {
    const c = candles[i]
    const p1 = candles[i - 1]
    const p2 = candles[i - 2]
    const body = Math.abs(c.close - c.open)
    const range = Math.max(1, c.high - c.low)
    const upperWick = c.high - Math.max(c.open, c.close)
    const lowerWick = Math.min(c.open, c.close) - c.low
    const isBull = c.close > c.open
    const isBear = c.close < c.open

    const p1Body = Math.abs(p1.close - p1.open)
    const p1Range = Math.max(1, p1.high - p1.low)
    const p1Bull = p1.close > p1.open
    const p1Bear = p1.close < p1.open
    const p2Body = Math.abs(p2.close - p2.open)
    const p2Bull = p2.close > p2.open
    const p2Bear = p2.close < p2.open

    const inDownTrend = ema[i] != null ? c.close < ema[i]! : c.close < p2.close
    const inUpTrend = ema[i] != null ? c.close > ema[i]! : c.close > p2.close

    const isDoji = body <= range * 0.1
    const p1Doji = p1Body <= p1Range * 0.12

    const check = (id: string): boolean => {
      switch (id) {
        case "CDL_DOJI":
          return isDoji
        case "CDL_DRAGONFLY_DOJI":
          return isDoji && lowerWick >= range * 0.65 && upperWick <= range * 0.12
        case "CDL_GRAVESTONE_DOJI":
          return isDoji && upperWick >= range * 0.65 && lowerWick <= range * 0.12
        case "CDL_HAMMER":
          return inDownTrend && body > 0 && lowerWick >= body * 2 && upperWick <= body * 0.5
        case "CDL_HANGING_MAN":
          return inUpTrend && body > 0 && lowerWick >= body * 2 && upperWick <= body * 0.5
        case "CDL_INVERTED_HAMMER":
          return inDownTrend && body > 0 && upperWick >= body * 2 && lowerWick <= body * 0.5
        case "CDL_SHOOTING_STAR":
          return inUpTrend && body > 0 && upperWick >= body * 2 && lowerWick <= body * 0.5
        case "CDL_ENGULFING_BULL":
          return p1Bear && isBull && c.open <= p1.close && c.close >= p1.open && body > p1Body * 1.05
        case "CDL_ENGULFING_BEAR":
          return p1Bull && isBear && c.open >= p1.close && c.close <= p1.open && body > p1Body * 1.05
        case "CDL_HARAMI_BULL":
          return p1Bear && isBull && c.high <= p1.open && c.low >= p1.close && body < p1Body * 0.7
        case "CDL_HARAMI_BEAR":
          return p1Bull && isBear && c.high <= p1.close && c.low >= p1.open && body < p1Body * 0.7
        case "CDL_HARAMI_CROSS_BULL":
          return p1Bear && isDoji && c.high <= p1.open && c.low >= p1.close
        case "CDL_HARAMI_CROSS_BEAR":
          return p1Bull && isDoji && c.high <= p1.close && c.low >= p1.open
        case "CDL_MORNING_STAR":
          return p2Bear && p2Body > range * 0.4 && p1Body < p2Body * 0.45 && isBull && c.close > (p2.open + p2.close) / 2
        case "CDL_MORNING_DOJI":
          return p2Bear && p1Doji && isBull && c.close > (p2.open + p2.close) / 2
        case "CDL_EVENING_STAR":
          return p2Bull && p2Body > range * 0.4 && p1Body < p2Body * 0.45 && isBear && c.close < (p2.open + p2.close) / 2
        case "CDL_EVENING_DOJI":
          return p2Bull && p1Doji && isBear && c.close < (p2.open + p2.close) / 2
        case "CDL_PIERCING":
          return p1Bear && isBull && c.open < p1.low && c.close > (p1.open + p1.close) / 2 && c.close < p1.open
        case "CDL_DARK_CLOUD":
          return p1Bull && isBear && c.open > p1.high && c.close < (p1.open + p1.close) / 2 && c.close > p1.open
        case "CDL_MARUBOZU_WHITE":
          return isBull && body >= range * 0.85
        case "CDL_MARUBOZU_BLACK":
          return isBear && body >= range * 0.85
        case "CDL_3_WHITE_SOLDIERS":
          return p2Bull && p1Bull && isBull && c.close > p1.close && p1.close > p2.close && body > range * 0.5
        case "CDL_3_BLACK_CROWS":
          return p2Bear && p1Bear && isBear && c.close < p1.close && p1.close < p2.close && body > range * 0.5
        case "CDL_TWEEZER_BOTTOM":
          return inDownTrend && p1Bear && isBull && Math.abs(c.low - p1.low) <= range * 0.06
        case "CDL_TWEEZER_TOP":
          return inUpTrend && p1Bull && isBear && Math.abs(c.high - p1.high) <= range * 0.06
        case "CDL_SPINNING_WHITE":
          return isBull && body <= range * 0.3 && upperWick >= body && lowerWick >= body
        case "CDL_SPINNING_BLACK":
          return isBear && body <= range * 0.3 && upperWick >= body && lowerWick >= body
        case "CDL_LONG_LOWER_SHADOW":
          return lowerWick >= range * 0.62
        case "CDL_LONG_UPPER_SHADOW":
          return upperWick >= range * 0.62
        case "CDL_RISING_WINDOW":
          return c.low > p1.high
        case "CDL_FALLING_WINDOW":
          return c.high < p1.low
        case "CDL_ABANDONED_BABY_BULL":
          return p2Bear && p1Doji && p1.high < p2.low && isBull && c.low > p1.high
        case "CDL_ABANDONED_BABY_BEAR":
          return p2Bull && p1Doji && p1.low > p2.high && isBear && c.high < p1.low
        case "CDL_DOJI_STAR_BULL":
          return p1Bear && isDoji && c.high <= p1.close
        case "CDL_DOJI_STAR_BEAR":
          return p1Bull && isDoji && c.low >= p1.close
        case "CDL_KICKING_BULL":
          return p1Bear && isBull && c.open >= p1.open && body >= range * 0.65
        case "CDL_KICKING_BEAR":
          return p1Bull && isBear && c.open <= p1.open && body >= range * 0.65
        case "CDL_ON_NECK":
          return p1Bear && isBull && c.open < p1.low && Math.abs(c.close - p1.low) <= range * 0.1
        case "CDL_UPSIDE_TASUKI":
          return p2Bull && p1Bull && p1.low > p2.high && isBear && c.close < p1.open && c.close > p2.high
        case "CDL_DOWNSIDE_TASUKI":
          return p2Bear && p1Bear && p1.high < p2.low && isBull && c.close > p1.open && c.close < p2.low
        case "CDL_RISING_3":
          return i >= 4 && candles[i - 4].close > candles[i - 4].open && isBull && c.close > candles[i - 4].close && p1Bear && p2Bear
        case "CDL_FALLING_3":
          return i >= 4 && candles[i - 4].close < candles[i - 4].open && isBear && c.close < candles[i - 4].close && p1Bull && p2Bull
        case "CDL_TRISTAR_BULL":
          return isDoji && p1Doji && Math.abs(p2.close - p2.open) <= Math.max(1, p2.high - p2.low) * 0.12 && inDownTrend
        case "CDL_TRISTAR_BEAR":
          return isDoji && p1Doji && Math.abs(p2.close - p2.open) <= Math.max(1, p2.high - p2.low) * 0.12 && inUpTrend
        default:
          return false
      }
    }

    if (targetId === "CDL_ALL") {
      if (i - lastMarkerBar < 3) continue
      const priority: [string, string, "bull" | "bear" | "neutral"][] = [
        ["CDL_ENGULFING_BULL", "Engulf+", "bull"],
        ["CDL_ENGULFING_BEAR", "Engulf-", "bear"],
        ["CDL_MORNING_STAR", "MornStar", "bull"],
        ["CDL_EVENING_STAR", "EveStar", "bear"],
        ["CDL_HAMMER", "Hammer", "bull"],
        ["CDL_SHOOTING_STAR", "ShootStar", "bear"],
        ["CDL_3_WHITE_SOLDIERS", "3Soldiers", "bull"],
        ["CDL_3_BLACK_CROWS", "3Crows", "bear"],
        ["CDL_PIERCING", "Piercing", "bull"],
        ["CDL_DARK_CLOUD", "DarkCloud", "bear"],
        ["CDL_DRAGONFLY_DOJI", "Dragonfly", "bull"],
        ["CDL_GRAVESTONE_DOJI", "Gravestone", "bear"],
        ["CDL_MARUBOZU_WHITE", "Marubozu+", "bull"],
        ["CDL_MARUBOZU_BLACK", "Marubozu-", "bear"],
        ["CDL_DOJI", "Doji", "neutral"],
      ]
      for (const [cid, tag, bias] of priority) {
        if (check(cid)) {
          markers.push({
            barIndex: i,
            position: bias === "bull" ? "belowBar" : "aboveBar",
            shape: "badge",
            color: bias === "bull" ? "#089981" : bias === "bear" ? "#F23645" : "#2962FF",
            text: tag,
          })
          lastMarkerBar = i
          break
        }
      }
    } else if (check(targetId)) {
      const isBearishPattern = meta.defaultColor === "#F23645"
      markers.push({
        barIndex: i,
        position: isBearishPattern ? "aboveBar" : "belowBar",
        shape: "badge",
        color: meta.defaultColor,
        text: meta.shortName,
      })
    }
  }

  return {
    instance,
    meta,
    title: meta.shortName,
    plots: [],
    markers: markers.slice(-36),
  }
}

/* ============================================================================
 * 5. BACKTESTABLE STRATEGIES ENGINE (23 Official Strategies + Strategy Tester)
 * ============================================================================ */
export function computeStrategyStudy(
  candles: TvCandle[],
  instance: TvIndicatorInstance,
  meta: TvIndicatorMetadata
): TvIndicatorComputed {
  const len = candles.length
  const closes = candles.map((c) => c.close)
  const plots: TvIndicatorPlotSeries[] = []
  const longSig: boolean[] = new Array(len).fill(false)
  const exitSig: boolean[] = new Array(len).fill(false)

  const id = instance.id
  const pLen = Math.max(2, Math.round(instance.params.length ?? 14))

  if (id === "STRAT_MA2_CROSS") {
    const fast = Math.max(2, Math.round(instance.params.fast ?? 9))
    const slow = Math.max(fast + 1, Math.round(instance.params.slow ?? 21))
    const fMa = calcEMA(closes, fast)
    const sMa = calcSMA(closes, slow)
    plots.push(
      { key: "st_f", label: `EMA ${fast}`, color: "#2962FF", type: "line", lineWidth: 1.5, values: fMa },
      { key: "st_s", label: `SMA ${slow}`, color: "#FF6D00", type: "line", lineWidth: 1.5, values: sMa }
    )
    for (let i = 1; i < len; i++) {
      if (fMa[i] != null && sMa[i] != null && fMa[i - 1] != null && sMa[i - 1] != null) {
        if (fMa[i - 1]! <= sMa[i - 1]! && fMa[i]! > sMa[i]!) longSig[i] = true
        if (fMa[i - 1]! >= sMa[i - 1]! && fMa[i]! < sMa[i]!) exitSig[i] = true
      }
    }
  } else if (id === "STRAT_MA_CROSS") {
    const sma = calcSMA(closes, pLen)
    plots.push({ key: "st_sma", label: `SMA ${pLen}`, color: "#2962FF", type: "line", lineWidth: 1.5, values: sma })
    for (let i = 1; i < len; i++) {
      if (sma[i] != null && sma[i - 1] != null) {
        if (closes[i - 1] <= sma[i - 1]! && closes[i] > sma[i]!) longSig[i] = true
        if (closes[i - 1] >= sma[i - 1]! && closes[i] < sma[i]!) exitSig[i] = true
      }
    }
  } else if (id === "STRAT_BB" || id === "STRAT_BB_DIR") {
    const mult = instance.params.mult ?? 2
    const basis = calcSMA(closes, pLen)
    const sd = calcStdDev(closes, pLen)
    const trendMa = calcSMA(closes, Math.min(100, Math.max(40, Math.floor(len / 2))))
    const up = basis.map((m, i) => (m != null && sd[i] != null ? m + mult * sd[i]! : null))
    const dn = basis.map((m, i) => (m != null && sd[i] != null ? m - mult * sd[i]! : null))
    plots.push(
      { key: "st_bbu", label: "BB Up", color: "#2962FF", type: "line", lineWidth: 1.2, values: up },
      { key: "st_bbl", label: "BB Low", color: "#2962FF", type: "line", lineWidth: 1.2, values: dn }
    )
    for (let i = 1; i < len; i++) {
      if (dn[i] != null && up[i] != null && dn[i - 1] != null) {
        const trendOk = id !== "STRAT_BB_DIR" || trendMa[i] == null || closes[i] >= trendMa[i]! * 0.96
        if (trendOk && closes[i - 1] <= dn[i - 1]! && closes[i] > dn[i]!) longSig[i] = true
        if (closes[i] >= up[i]! || (basis[i] != null && closes[i - 1] >= up[i - 1]! && closes[i] < up[i]!)) exitSig[i] = true
      }
    }
  } else if (id === "STRAT_SUPERTREND") {
    const atrLen = Math.max(2, Math.round(instance.params.atrLength ?? 10))
    const factor = instance.params.factor ?? 3
    const st = calcSuperTrendSeries(candles, atrLen, factor)
    plots.push({
      key: "st_line",
      label: "Supertrend",
      color: "#089981",
      type: "line",
      lineWidth: 1.8,
      values: st.line,
      pointColors: st.colors,
    })
    for (let i = 1; i < len; i++) {
      if (st.dir[i - 1] === -1 && st.dir[i] === 1) longSig[i] = true
      if (st.dir[i - 1] === 1 && st.dir[i] === -1) exitSig[i] = true
    }
  } else if (id === "STRAT_MACD") {
    const fast = instance.params.fast ?? 12
    const slow = instance.params.slow ?? 26
    const sig = instance.params.signal ?? 9
    const f = calcEMA(closes, fast)
    const s = calcEMA(closes, slow)
    const m = closes.map((_, i) => (f[i] != null && s[i] != null ? f[i]! - s[i]! : null))
    const sg = calcEMA(m, sig)
    for (let i = 1; i < len; i++) {
      if (m[i] != null && sg[i] != null && m[i - 1] != null && sg[i - 1] != null) {
        if (m[i - 1]! <= sg[i - 1]! && m[i]! > sg[i]!) longSig[i] = true
        if (m[i - 1]! >= sg[i - 1]! && m[i]! < sg[i]!) exitSig[i] = true
      }
    }
  } else if (id === "STRAT_RSI") {
    const os = instance.params.oversold ?? 30
    const ob = instance.params.overbought ?? 70
    const rsi = calcRSI(closes, pLen)
    for (let i = 1; i < len; i++) {
      if (rsi[i] != null && rsi[i - 1] != null) {
        if (rsi[i - 1]! <= os && rsi[i]! > os) longSig[i] = true
        if (rsi[i - 1]! >= ob && rsi[i]! < ob) exitSig[i] = true
      }
    }
  } else if (id === "STRAT_STOCH_SLOW") {
    const { k, d } = calcStochastic(candles, pLen, 3, 3)
    for (let i = 1; i < len; i++) {
      if (k[i] != null && d[i] != null && k[i - 1] != null && d[i - 1] != null) {
        if (k[i - 1]! <= d[i - 1]! && k[i]! > d[i]! && k[i]! < 35) longSig[i] = true
        if (k[i - 1]! >= d[i - 1]! && k[i]! < d[i]! && k[i]! > 65) exitSig[i] = true
      }
    }
  } else if (id === "STRAT_CHAN_BO" || id === "STRAT_PRICE_CH") {
    const hh = calcHighest(candles.map((c) => c.high), pLen)
    const ll = calcLowest(candles.map((c) => c.low), pLen)
    plots.push(
      { key: "ch_h", label: "Upper", color: "#089981", type: "line", lineWidth: 1.2, values: hh },
      { key: "ch_l", label: "Lower", color: "#F23645", type: "line", lineWidth: 1.2, values: ll }
    )
    for (let i = 2; i < len; i++) {
      if (hh[i - 1] != null && candles[i].close > hh[i - 1]!) longSig[i] = true
      if (ll[i - 1] != null && candles[i].close < ll[i - 1]!) exitSig[i] = true
    }
  } else if (id === "STRAT_KELTNER") {
    const mult = instance.params.mult ?? 2
    const basis = calcEMA(closes, pLen)
    const atr = calcATR(candles, 10)
    for (let i = 1; i < len; i++) {
      if (basis[i] != null && atr[i] != null) {
        if (closes[i] > basis[i]! + mult * atr[i]!) longSig[i] = true
        if (closes[i] < basis[i]!) exitSig[i] = true
      }
    }
  } else if (id === "STRAT_INSIDE_BAR") {
    for (let i = 2; i < len; i++) {
      const inside = candles[i - 1].high < candles[i - 2].high && candles[i - 1].low > candles[i - 2].low
      if (inside && candles[i].close > candles[i - 1].high) longSig[i] = true
      if (candles[i].close < candles[i - 1].low && candles[i].close < candles[i].open) exitSig[i] = true
    }
  } else if (id === "STRAT_OUTSIDE_BAR") {
    for (let i = 1; i < len; i++) {
      const outside = candles[i].high > candles[i - 1].high && candles[i].low < candles[i - 1].low
      if (outside && candles[i].close > candles[i].open) longSig[i] = true
      if (outside && candles[i].close < candles[i].open) exitSig[i] = true
    }
  } else if (id === "STRAT_CONSEC_UD") {
    const req = Math.max(2, Math.round(instance.params.length ?? 3))
    let upRun = 0
    let dnRun = 0
    for (let i = 1; i < len; i++) {
      if (closes[i] > closes[i - 1]) {
        upRun++
        dnRun = 0
      } else if (closes[i] < closes[i - 1]) {
        dnRun++
        upRun = 0
      }
      if (upRun === req) longSig[i] = true
      if (dnRun >= req) exitSig[i] = true
    }
  } else if (id === "STRAT_ROB_ADX") {
    const { adx, plusDi, minusDi } = calcADXSeries(candles, pLen)
    for (let i = 1; i < len; i++) {
      if (adx[i] != null && plusDi[i] != null && minusDi[i] != null) {
        if (adx[i]! > 20 && plusDi[i]! > minusDi[i]! && closes[i] > closes[i - 1]) longSig[i] = true
        if (minusDi[i]! > plusDi[i]!) exitSig[i] = true
      }
    }
  } else {
    // Momentum / PSAR / Pivot / VolExpan / Greedy / BarUpDn / Technical Ratings unified fast signal engine
    const emaFast = calcEMA(closes, Math.max(3, Math.floor(pLen / 2)))
    const emaSlow = calcEMA(closes, pLen)
    const atr = calcATR(candles, 10)
    for (let i = 2; i < len; i++) {
      if (id === "STRAT_BAR_UPDN") {
        if (candles[i].close > candles[i].open && candles[i].open > candles[i - 1].close) longSig[i] = true
        if (candles[i].close < candles[i].open && candles[i].close < candles[i - 1].low) exitSig[i] = true
      } else if (id === "STRAT_VOL_EXP" && atr[i] != null) {
        const mult = instance.params.mult ?? 1.5
        if (candles[i].close - candles[i].open > atr[i]! * mult) longSig[i] = true
        if (candles[i].open - candles[i].close > atr[i]! * 1.1) exitSig[i] = true
      } else if (emaFast[i] != null && emaSlow[i] != null && emaFast[i - 1] != null && emaSlow[i - 1] != null) {
        if (emaFast[i - 1]! <= emaSlow[i - 1]! && emaFast[i]! > emaSlow[i]!) longSig[i] = true
        if (emaFast[i - 1]! >= emaSlow[i - 1]! && emaFast[i]! < emaSlow[i]!) exitSig[i] = true
      }
    }
  }

  // Simulate Backtest Execution (HOSE Long-Only T+ / Swing realistic execution, 0.15% fee)
  const markers: TvBarMarker[] = []
  const trades: TvStrategyTradeRecord[] = []
  const equityCurve: (number | null)[] = new Array(len).fill(null)

  let inPos = false
  let entryBar = 0
  let entryPrice = 0
  let equity = 100
  let peakEquity = 100
  let maxDdPct = 0
  let grossProfit = 0
  let grossLoss = 0

  for (let i = 0; i < len; i++) {
    if (!inPos && longSig[i] && i < len - 1) {
      inPos = true
      entryBar = i
      entryPrice = candles[i].close
      markers.push({
        barIndex: i,
        position: "belowBar",
        shape: "arrowUp",
        color: "#089981",
        text: "MUA",
        subtext: `${Math.round(entryPrice).toLocaleString("vi-VN")}`,
      })
    } else if (inPos && (exitSig[i] || i === len - 1)) {
      const exitPrice = candles[i].close
      const rawPct = ((exitPrice - entryPrice) / Math.max(1, entryPrice)) * 100 - 0.3
      equity = equity * (1 + rawPct / 100)
      if (rawPct >= 0) grossProfit += rawPct
      else grossLoss += Math.abs(rawPct)

      trades.push({
        tradeNo: trades.length + 1,
        side: "LONG",
        entryBar,
        exitBar: i,
        entryDate: candles[entryBar].label,
        exitDate: candles[i].label,
        entryPrice,
        exitPrice,
        pnlPct: Number(rawPct.toFixed(2)),
        cumPnlPct: Number((equity - 100).toFixed(2)),
        signalName: i === len - 1 && !exitSig[i] ? "Đang nắm giữ" : "Đóng vị thế",
      })
      if (exitSig[i]) {
        markers.push({
          barIndex: i,
          position: "aboveBar",
          shape: "arrowDown",
          color: rawPct >= 0 ? "#2962FF" : "#F23645",
          text: `BÁN (${rawPct >= 0 ? "+" : ""}${rawPct.toFixed(1)}%)`,
        })
      }
      inPos = false
    }

    const markToMarket = inPos
      ? equity * (1 + (candles[i].close - entryPrice) / Math.max(1, entryPrice))
      : equity
    equityCurve[i] = Number((markToMarket - 100).toFixed(2))
    if (markToMarket > peakEquity) peakEquity = markToMarket
    const dd = ((peakEquity - markToMarket) / Math.max(1, peakEquity)) * 100
    if (dd > maxDdPct) maxDdPct = dd
  }

  const wins = trades.filter((t) => t.pnlPct > 0).length
  const totalTrades = trades.length
  const winRatePct = totalTrades > 0 ? (wins / totalTrades) * 100 : 0
  const profitFactor = grossLoss > 0.01 ? grossProfit / grossLoss : grossProfit > 0 ? 9.99 : 1
  const avgTradePct = totalTrades > 0 ? trades.reduce((s, t) => s + t.pnlPct, 0) / totalTrades : 0
  const variance =
    totalTrades > 1
      ? trades.reduce((s, t) => s + Math.pow(t.pnlPct - avgTradePct, 2), 0) / (totalTrades - 1)
      : 1
  const sharpeRatio = variance > 0 ? (avgTradePct / Math.sqrt(variance)) * Math.sqrt(Math.min(12, Math.max(1, totalTrades))) : 0

  const strategyReport: TvStrategyBacktestReport = {
    strategyName: meta.viName,
    netProfitPct: Number((equity - 100).toFixed(2)),
    totalTrades,
    winningTrades: wins,
    winRatePct: Number(winRatePct.toFixed(1)),
    profitFactor: Number(profitFactor.toFixed(2)),
    maxDrawdownPct: Number(maxDdPct.toFixed(2)),
    avgTradePct: Number(avgTradePct.toFixed(2)),
    sharpeRatio: Number(sharpeRatio.toFixed(2)),
    equityCurve,
    trades,
  }

  return {
    instance,
    meta,
    title: `${meta.shortName} [Lãi ròng: ${strategyReport.netProfitPct >= 0 ? "+" : ""}${strategyReport.netProfitPct}% | Win: ${strategyReport.winRatePct}%]`,
    plots,
    markers: markers.slice(-40),
    strategyReport,
  }
}

/* ============================================================================
 * 6. OPTIONS, FUNDAMENTALS & 38 COMMUNITY / VIP / STORE SCRIPTS ENGINE
 * ============================================================================ */
export function computeOptionsStudy(
  candles: TvCandle[],
  instance: TvIndicatorInstance,
  meta: TvIndicatorMetadata
): TvIndicatorComputed {
  const len = candles.length
  const closes = candles.map((c) => c.close)
  const pLen = Math.max(5, Math.round(instance.params.length ?? instance.params.hvLen ?? 20))

  // Compute annualized log-return Historical Volatility (%)
  const logRet: (number | null)[] = new Array(len).fill(null)
  for (let i = 1; i < len; i++) {
    logRet[i] = Math.log(Math.max(1, closes[i]) / Math.max(1, closes[i - 1]))
  }
  const sdRet = calcStdDev(logRet, pLen)
  const hvSeries = sdRet.map((v) => (v != null ? v * Math.sqrt(252) * 100 : null))

  if (instance.id === "OPT_EXP_MOVE") {
    const horizon = Math.max(5, Math.round(instance.params.horizon ?? 14))
    const curPrice = closes[len - 1]
    const curHv = (hvSeries[len - 1] ?? 28) / 100
    const iv = curHv * 1.12
    const pts1Up: { barIndex: number; price: number }[] = []
    const pts1Dn: { barIndex: number; price: number }[] = []
    const pts2Up: { barIndex: number; price: number }[] = []
    const pts2Dn: { barIndex: number; price: number }[] = []
    for (let d = 0; d <= horizon; d++) {
      const move1 = curPrice * iv * Math.sqrt(d / 252)
      pts1Up.push({ barIndex: len - 1 + d, price: curPrice + move1 })
      pts1Dn.push({ barIndex: len - 1 + d, price: Math.max(100, curPrice - move1) })
      pts2Up.push({ barIndex: len - 1 + d, price: curPrice + move1 * 2 })
      pts2Dn.push({ barIndex: len - 1 + d, price: Math.max(100, curPrice - move1 * 2) })
    }
    return {
      instance,
      meta,
      title: `Expected Move Cone (${horizon}D, IV ${(iv * 100).toFixed(1)}%)`,
      plots: [],
      polylines: [
        {
          points: [...pts2Up, ...[...pts2Dn].reverse()],
          color: "rgba(41, 98, 255, 0.45)",
          lineWidth: 1,
          dashed: true,
          fillPolygonColor: "rgba(41, 98, 255, 0.07)",
          rightLabel: "±2σ (95%)",
        },
        {
          points: [...pts1Up, ...[...pts1Dn].reverse()],
          color: "#2962FF",
          lineWidth: 1.7,
          fillPolygonColor: "rgba(41, 98, 255, 0.15)",
          rightLabel: "±1σ (68%)",
        },
      ],
    }
  }

  if (instance.id === "OPT_VOL_PROFILE") {
    const blk = buildVolumeProfileBlock(candles, Math.max(0, len - 60), len - 1, pLen, 70, "right", 0.25, "vp", "GEX")
    return {
      instance,
      meta,
      title: `Options GEX Profile (${pLen})`,
      plots: [],
      volumeProfiles: blk ? [blk] : [],
    }
  }

  if (instance.id === "OPT_MAX_PAIN") {
    const vwma = candles.map((_, i) => {
      if (i < pLen - 1) return null
      let pv = 0
      let vv = 0
      for (let j = i - pLen + 1; j <= i; j++) {
        pv += ((candles[j].high + candles[j].low + candles[j].close) / 3) * Math.max(1, candles[j].volume)
        vv += Math.max(1, candles[j].volume)
      }
      return Math.round(pv / vv / 100) * 100
    })
    return {
      instance,
      meta,
      title: `Max Pain (${pLen})`,
      plots: [{ key: "max_pain", label: "Max Pain", color: instance.color, type: "step", lineWidth: 2, values: vwma }],
    }
  }

  if (instance.id === "OPT_GARMAN_KLASS") {
    const gk: (number | null)[] = new Array(len).fill(null)
    for (let i = pLen - 1; i < len; i++) {
      let sum = 0
      for (let j = i - pLen + 1; j <= i; j++) {
        const hl = Math.log(Math.max(1, candles[j].high) / Math.max(1, candles[j].low))
        const co = Math.log(Math.max(1, candles[j].close) / Math.max(1, candles[j].open))
        sum += 0.5 * hl * hl - (2 * Math.LN2 - 1) * co * co
      }
      gk[i] = Math.sqrt(Math.max(0, (sum / pLen) * 252)) * 100
    }
    return {
      instance,
      meta,
      title: `Garman-Klass Vol (${pLen})`,
      plots: [{ key: "gk", label: "GK Vol %", color: instance.color, type: "line", lineWidth: 1.7, values: gk }],
    }
  }

  if (instance.id === "OPT_HV_IV") {
    const atrPct = calcATR(candles, pLen).map((a, i) => (a != null ? (a / Math.max(1, closes[i])) * Math.sqrt(252) * 108 : null))
    return {
      instance,
      meta,
      title: `HV vs IV (${pLen})`,
      plots: [
        { key: "hv", label: "HV %", color: "#2962FF", type: "line", lineWidth: 1.6, values: hvSeries },
        { key: "iv", label: "Synth IV %", color: "#F59E0B", type: "line", lineWidth: 1.6, values: atrPct },
      ],
    }
  }

  if (instance.id === "OPT_IV_RANK") {
    const hh = calcHighest(hvSeries, pLen)
    const ll = calcLowest(hvSeries, pLen)
    const ivr = hvSeries.map((v, i) =>
      v != null && hh[i] != null && ll[i] != null && hh[i]! > ll[i]!
        ? ((v - ll[i]!) / (hh[i]! - ll[i]!)) * 100
        : null
    )
    return {
      instance,
      meta,
      title: `IV Rank (${pLen})`,
      plots: [{ key: "ivr", label: "IV Rank", color: instance.color, type: "area", lineWidth: 1.6, values: ivr }],
    }
  }

  if (instance.id === "OPT_PCR") {
    const pcr: (number | null)[] = new Array(len).fill(null)
    for (let i = pLen; i < len; i++) {
      let downV = 1
      let upV = 1
      for (let j = i - pLen + 1; j <= i; j++) {
        if (candles[j].close < candles[j].open) downV += candles[j].volume
        else upV += candles[j].volume
      }
      pcr[i] = Number((downV / upV).toFixed(3))
    }
    return {
      instance,
      meta,
      title: `Put/Call Ratio (${pLen})`,
      plots: [{ key: "pcr", label: "PCR", color: instance.color, type: "line", lineWidth: 1.6, values: pcr }],
    }
  }

  // OPT_SKEW
  const rsi = calcRSI(closes, pLen)
  const skew = rsi.map((r) => (r != null ? 100 + (50 - r) * 0.65 : null))
  return {
    instance,
    meta,
    title: `Volatility Skew (${pLen})`,
    plots: [{ key: "skew", label: "Skew", color: instance.color, type: "line", lineWidth: 1.6, values: skew }],
  }
}

export function computeFundamentalStudy(
  candles: TvCandle[],
  instance: TvIndicatorInstance,
  meta: TvIndicatorMetadata,
  symbol = "HPG"
): TvIndicatorComputed {
  const len = candles.length
  const symHash = symbol.split("").reduce((acc, ch) => acc + ch.charCodeAt(0) * 17, 7)
  const baseEps = 1800 + (symHash % 3400)
  const baseBvps = 14000 + (symHash % 18000)
  const sharesBil = 0.6 + ((symHash % 55) / 10)
  const quarterBars = 62

  const values: (number | null)[] = new Array(len).fill(null)
  for (let i = 0; i < len; i++) {
    const qIdx = Math.floor(i / quarterBars)
    const qGrowth = 1 + qIdx * 0.032 + Math.sin(qIdx * 1.4 + symHash) * 0.04
    const price = candles[i].close
    const eps = baseEps * qGrowth
    const bvps = baseBvps * (1 + qIdx * 0.025)
    const revBil = sharesBil * 9500 * qGrowth

    switch (instance.id) {
      case "FUND_PE":
        values[i] = price / Math.max(200, eps)
        break
      case "FUND_PB":
        values[i] = price / Math.max(1000, bvps)
        break
      case "FUND_PEG":
        values[i] = (price / Math.max(200, eps)) / 16.5
        break
      case "FUND_PS":
        values[i] = ((price * sharesBil) / 1000) / Math.max(100, revBil * 0.28)
        break
      case "FUND_EV_EBITDA":
        values[i] = (price / Math.max(200, eps)) * 0.72
        break
      case "FUND_MCAP":
        values[i] = (price * sharesBil)
        break
      case "FUND_EPS":
        values[i] = eps
        break
      case "FUND_EPS_GROWTH":
        values[i] = 12 + Math.sin(qIdx + symHash) * 9.5
        break
      case "FUND_REVENUE":
        values[i] = revBil
        break
      case "FUND_REV_GROWTH":
        values[i] = 10.5 + Math.cos(qIdx + symHash) * 7.5
        break
      case "FUND_GROSS_MARGIN":
        values[i] = 22 + (symHash % 18) + Math.sin(qIdx) * 2.1
        break
      case "FUND_OP_MARGIN":
        values[i] = 14 + (symHash % 12) + Math.sin(qIdx) * 1.8
        break
      case "FUND_NET_MARGIN":
        values[i] = 10.5 + (symHash % 10) + Math.sin(qIdx) * 1.5
        break
      case "FUND_ROE":
        values[i] = (eps / bvps) * 100
        break
      case "FUND_ROA":
        values[i] = (eps / bvps) * 48
        break
      case "FUND_ROIC":
        values[i] = (eps / bvps) * 76
        break
      case "FUND_TOTAL_ASSETS":
        values[i] = revBil * 2.4
        break
      case "FUND_TOTAL_DEBT":
        values[i] = revBil * 0.78
        break
      case "FUND_DEBT_EQUITY":
        values[i] = 0.45 + ((symHash % 60) / 100) + Math.cos(qIdx) * 0.05
        break
      case "FUND_CURRENT_RATIO":
        values[i] = 1.45 + ((symHash % 80) / 100)
        break
      case "FUND_BVPS":
        values[i] = bvps
        break
      case "FUND_FCF":
        values[i] = revBil * 0.095
        break
      case "FUND_OCF":
        values[i] = revBil * 0.145
        break
      case "FUND_DIV_YIELD":
        values[i] = ((1200 + (symHash % 1500)) / Math.max(1000, price)) * 100
        break
      default:
        values[i] = price / Math.max(200, eps)
    }
  }

  const isQuarterlyStep = !["FUND_PE", "FUND_PB", "FUND_PEG", "FUND_PS", "FUND_EV_EBITDA", "FUND_MCAP", "FUND_DIV_YIELD"].includes(instance.id)

  return {
    instance,
    meta,
    title: `${meta.shortName} (${symbol})`,
    plots: [
      {
        key: "fund_val",
        label: meta.shortName,
        color: instance.color,
        type: isQuarterlyStep ? "step" : "area",
        lineWidth: 1.8,
        values,
      },
    ],
  }
}

export function computeCommunityVipStudy(
  candles: TvCandle[],
  instance: TvIndicatorInstance,
  meta: TvIndicatorMetadata
): TvIndicatorComputed {
  const len = candles.length
  const closes = candles.map((c) => c.close)
  const hl2 = candles.map((c) => (c.high + c.low) / 2)
  const hlc3 = candles.map((c) => (c.high + c.low + c.close) / 3)
  const pLen = Math.max(2, Math.round(instance.params.length ?? instance.params.swingLen ?? 14))

  switch (instance.id) {
    case "COM_SQUEEZE_LAZYBEAR": {
      const basis = calcSMA(closes, pLen)
      const sd = calcStdDev(closes, pLen)
      const atr = calcATR(candles, pLen)
      const hh = calcHighest(candles.map((c) => c.high), pLen)
      const ll = calcLowest(candles.map((c) => c.low), pLen)
      const delta: (number | null)[] = closes.map((c, i) =>
        hh[i] != null && ll[i] != null && basis[i] != null ? c - ((hh[i]! + ll[i]!) / 2 + basis[i]!) / 2 : null
      )
      const mom = calcWMA(delta, pLen)
      const colors = mom.map((v, i) => {
        if (v == null) return null
        const prev = i > 0 && mom[i - 1] != null ? mom[i - 1]! : 0
        return v >= 0 ? (v >= prev ? "#00E676" : "#00897B") : v <= prev ? "#FF1744" : "#EF9A9A"
      })
      const sqzDots = closes.map((_, i) => (sd[i] != null && atr[i] != null ? 0 : null))
      const sqzColors = closes.map((_, i) =>
        sd[i] != null && atr[i] != null && 2 * sd[i]! < 1.5 * atr[i]! ? "#F23645" : "#2962FF"
      )
      return {
        instance,
        meta,
        title: `SqzMom [LazyBear] (${pLen})`,
        plots: [
          { key: "sqz_hist", label: "Momentum", color: "#00E676", type: "histogram", values: mom, pointColors: colors },
          { key: "sqz_zero", label: "Squeeze", color: "#2962FF", type: "dots", values: sqzDots, pointColors: sqzColors },
        ],
      }
    }

    case "COM_SMC_LUXALGO":
    case "COM_ORDER_BLOCKS":
    case "COM_ICT_FVG_LIQ":
    case "COM_MSB_OB":
    case "STORE_SUPPLY_DEMAND": {
      const pivots = findSwingPivots(candles, Math.max(3, Math.min(12, pLen)))
      const zones: TvOverlayZone[] = []
      const polylines: TvOverlayPolyline[] = []
      const markers: TvBarMarker[] = []

      // Order Blocks / Supply & Demand from major swing pivots
      for (const p of pivots.slice(-8)) {
        const c = candles[p.barIndex]
        if (p.type === "L") {
          zones.push({
            startBar: p.barIndex,
            endBar: len - 1,
            topPrice: Math.max(c.open, c.close),
            bottomPrice: c.low,
            fillColor: "rgba(8, 153, 129, 0.15)",
            borderColor: "#089981",
            label: instance.id === "STORE_SUPPLY_DEMAND" ? "Demand Zone" : "Bullish OB",
          })
        } else {
          zones.push({
            startBar: p.barIndex,
            endBar: len - 1,
            topPrice: c.high,
            bottomPrice: Math.min(c.open, c.close),
            fillColor: "rgba(242, 54, 69, 0.15)",
            borderColor: "#F23645",
            label: instance.id === "STORE_SUPPLY_DEMAND" ? "Supply Zone" : "Bearish OB",
          })
        }
      }

      // BOS / CHoCH horizontal break lines
      for (let k = 1; k < pivots.length; k++) {
        const prevSame = pivots.slice(0, k).reverse().find((x) => x.type === pivots[k].type)
        if (!prevSame) continue
        if (pivots[k].type === "H" && pivots[k].price > prevSame.price) {
          polylines.push({
            points: [
              { barIndex: prevSame.barIndex, price: prevSame.price },
              { barIndex: pivots[k].barIndex, price: prevSame.price, label: "BOS ▲" },
            ],
            color: "#089981",
            lineWidth: 1.3,
            dashed: true,
          })
        } else if (pivots[k].type === "L" && pivots[k].price < prevSame.price) {
          polylines.push({
            points: [
              { barIndex: prevSame.barIndex, price: prevSame.price },
              { barIndex: pivots[k].barIndex, price: prevSame.price, label: "CHoCH ▼" },
            ],
            color: "#F23645",
            lineWidth: 1.3,
            dashed: true,
          })
        }
      }

      // Fair Value Gaps (FVG)
      if (instance.id === "COM_SMC_LUXALGO" || instance.id === "COM_ICT_FVG_LIQ") {
        for (let i = Math.max(2, len - 60); i < len; i++) {
          if (candles[i].low > candles[i - 2].high * 1.003) {
            zones.push({
              startBar: i - 2,
              endBar: Math.min(len - 1, i + 12),
              topPrice: candles[i].low,
              bottomPrice: candles[i - 2].high,
              fillColor: "rgba(41, 98, 255, 0.16)",
              borderColor: "#2962FF",
              label: "Bull FVG",
            })
          } else if (candles[i].high < candles[i - 2].low * 0.997) {
            zones.push({
              startBar: i - 2,
              endBar: Math.min(len - 1, i + 12),
              topPrice: candles[i - 2].low,
              bottomPrice: candles[i].high,
              fillColor: "rgba(255, 152, 0, 0.16)",
              borderColor: "#FF9800",
              label: "Bear FVG",
            })
          }
        }
      }

      return {
        instance,
        meta,
        title: `${meta.shortName} [LuxAlgo]`,
        plots: [],
        zones: zones.slice(-12),
        polylines: polylines.slice(-10),
        markers,
      }
    }

    case "COM_WAVETREND":
    case "COM_VUMANCHU_B":
    case "VIP_QUANTVUE_MATRIX": {
      const n1 = instance.params.fast ?? 10
      const n2 = instance.params.slow ?? 21
      const esa = calcEMA(hlc3, n1)
      const absDiff = hlc3.map((v, i) => (esa[i] != null ? Math.abs(v - esa[i]!) : null))
      const d = calcEMA(absDiff, n1)
      const ci = hlc3.map((v, i) => (esa[i] != null && d[i] && d[i]! > 0 ? (v - esa[i]!) / (0.015 * d[i]!) : 0))
      const wt1 = calcEMA(ci, n2)
      const wt2 = calcSMA(wt1, 4)
      const plots: TvIndicatorPlotSeries[] = [
        { key: "wt1", label: "WT1", color: "#089981", type: "area", lineWidth: 1.7, values: wt1 },
        { key: "wt2", label: "WT2", color: "#F23645", type: "line", lineWidth: 1.4, dashed: true, values: wt2 },
      ]
      if (instance.id !== "COM_WAVETREND") {
        const mfi = calcMFI(candles, 14).map((v) => (v != null ? (v - 50) * 1.4 : null))
        plots.unshift({ key: "mfi_wave", label: "Money Flow", color: "rgba(41, 98, 255, 0.45)", type: "histogram", values: mfi })
      }
      return { instance, meta, title: `${meta.shortName} (${n1}, ${n2})`, plots }
    }

    case "COM_UT_BOT":
    case "COM_SUPERTREND_AI":
    case "COM_LORENTZIAN_ML":
    case "VIP_MARKET_CIPHER_AB": {
      const factor = instance.params.keyVal ?? 2.2
      const atrP = instance.params.atrPeriod ?? instance.params.length ?? 10
      const st = calcSuperTrendSeries(candles, atrP, factor)
      const markers: TvBarMarker[] = []
      for (let i = 1; i < len; i++) {
        if (st.dir[i - 1] === -1 && st.dir[i] === 1) {
          markers.push({
            barIndex: i,
            position: "belowBar",
            shape: "badge",
            color: "#089981",
            text: instance.id === "COM_LORENTZIAN_ML" ? "ML BUY" : "BUY",
          })
        } else if (st.dir[i - 1] === 1 && st.dir[i] === -1) {
          markers.push({
            barIndex: i,
            position: "aboveBar",
            shape: "badge",
            color: "#F23645",
            text: instance.id === "COM_LORENTZIAN_ML" ? "ML SELL" : "SELL",
          })
        }
      }
      return {
        instance,
        meta,
        title: `${meta.shortName} (${atrP})`,
        plots: [{ key: "ut_trail", label: "Trail", color: "#089981", type: "line", lineWidth: 1.9, values: st.line, pointColors: st.colors }],
        markers: markers.slice(-30),
      }
    }

    case "COM_NADARAYA_WATSON": {
      const mult = instance.params.mult ?? 2.5
      const basis = calcALMA(closes, Math.min(80, Math.max(15, pLen)), 0.85, 6)
      const atr = calcATR(candles, 20)
      const upper = basis.map((b, i) => (b != null && atr[i] != null ? b + atr[i]! * mult : null))
      const lower = basis.map((b, i) => (b != null && atr[i] != null ? b - atr[i]! * mult : null))
      const markers: TvBarMarker[] = []
      for (let i = 1; i < len; i++) {
        if (upper[i] != null && candles[i].high > upper[i]! && candles[i].close < upper[i]!) {
          markers.push({ barIndex: i, position: "aboveBar", shape: "arrowDown", color: "#F23645", text: "▼" })
        } else if (lower[i] != null && candles[i].low < lower[i]! && candles[i].close > lower[i]!) {
          markers.push({ barIndex: i, position: "belowBar", shape: "arrowUp", color: "#089981", text: "▲" })
        }
      }
      return {
        instance,
        meta,
        title: `NW Envelope (${pLen}, ${mult})`,
        plots: [
          { key: "nw_u", label: "Upper", color: "#F23645", type: "line", lineWidth: 1.5, values: upper },
          { key: "nw_b", label: "Kernel", color: "#2962FF", type: "line", lineWidth: 1.3, dashed: true, values: basis },
          { key: "nw_l", label: "Lower", color: "#089981", type: "line", lineWidth: 1.5, values: lower },
        ],
        bands: [{ upperKey: "nw_u", lowerKey: "nw_l", fillColor: "rgba(8, 153, 129, 0.06)" }],
        markers: markers.slice(-24),
      }
    }

    case "STORE_DEMARK_9_13": {
      const lb = Math.max(2, Math.round(instance.params.lookback ?? 4))
      let buyCnt = 0
      let sellCnt = 0
      const markers: TvBarMarker[] = []
      for (let i = lb; i < len; i++) {
        if (closes[i] < closes[i - lb]) {
          buyCnt++
          sellCnt = 0
        } else if (closes[i] > closes[i - lb]) {
          sellCnt++
          buyCnt = 0
        } else {
          buyCnt = 0
          sellCnt = 0
        }
        if (buyCnt === 9 || buyCnt === 13) {
          markers.push({
            barIndex: i,
            position: "belowBar",
            shape: "badge",
            color: buyCnt === 13 ? "#F59E0B" : "#089981",
            text: buyCnt === 13 ? "TD 13 ★" : "TD 9",
          })
        } else if (sellCnt === 9 || sellCnt === 13) {
          markers.push({
            barIndex: i,
            position: "aboveBar",
            shape: "badge",
            color: sellCnt === 13 ? "#F59E0B" : "#F23645",
            text: sellCnt === 13 ? "TD 13 ★" : "TD 9",
          })
        }
      }
      return { instance, meta, title: "DeMARK TD 9-13", plots: [], markers: markers.slice(-32) }
    }

    case "VIP_HUD_DE_TE":
    case "VIP_HUD_DE":
    case "VIP_HUD_TE": {
      const isTeMode =
        instance.id === "VIP_HUD_TE" ||
        (instance.id === "VIP_HUD_DE_TE" && Math.round(instance.params.mode ?? 0) === 1)

      const fastLen = Math.max(2, Math.round(instance.params.fastLen ?? 5))
      const slowLen = Math.max(fastLen + 1, Math.round(instance.params.slowLen ?? 20))
      const l1 = Math.max(2, Math.round(instance.params.ema1 ?? 5))
      const l2 = Math.max(2, Math.round(instance.params.ema2 ?? 10))
      const l3 = Math.max(2, Math.round(instance.params.ema3 ?? 20))
      const l4 = Math.max(2, Math.round(instance.params.ema4 ?? 50))

      const emaFast = calcEMA(closes, fastLen)
      const emaSlow = isTeMode ? null : calcEMA(closes, slowLen)
      const series: (number | null)[] = isTeMode
        ? emaFast
        : closes.map((_, i) =>
            emaFast[i] != null && emaSlow![i] != null ? emaFast[i]! - emaSlow![i]! : null
          )

      const subCandles: TvSubPaneCandle[] = new Array(len)
      for (let i = 0; i < len; i++) {
        const curr = series[i]
        const prev = i > 0 ? series[i - 1] : null
        if (curr == null || prev == null) {
          subCandles[i] = {
            open: null,
            high: null,
            low: null,
            close: null,
            color: "transparent",
          }
        } else {
          const isUp = curr >= prev
          const fill = isUp ? "#00E676" : "#FF5252"
          subCandles[i] = {
            open: prev,
            high: Math.max(prev, curr),
            low: Math.min(prev, curr),
            close: curr,
            color: fill,
            borderColor: "#131722",
            wickColor: fill,
          }
        }
      }

      const sr1 = calcEMA(series, l1)
      const sr2 = calcEMA(series, l2)
      const sr3 = calcEMA(series, l3)
      const sr4 = calcEMA(series, l4)

      let bullState = false
      const regimeColors: (string | null)[] = new Array(len).fill(null)
      const subBgHighlights: TvSubPaneBgHighlight[] = []
      const markers: TvBarMarker[] = []

      for (let i = 0; i < len; i++) {
        const val = series[i]
        const e1 = sr1[i]
        const e2 = sr2[i]

        if (val != null && e1 != null) {
          const prevBull = bullState
          if (!bullState && val > e1 && (e2 == null || val > e2)) {
            bullState = true
          } else if (bullState && val < e1) {
            bullState = false
          }
          regimeColors[i] = bullState ? "#4CAF50" : "#FF5252"

          if (i > 0 && sr1[i - 1] != null) {
            if (!prevBull && bullState) {
              markers.push({
                barIndex: i,
                position: "belowBar",
                shape: "arrowUp",
                color: "#4CAF50",
                text: "▲",
              })
              subBgHighlights.push({
                barIndex: i,
                color: "rgba(76, 175, 80, 0.14)",
              })
            } else if (prevBull && !bullState) {
              markers.push({
                barIndex: i,
                position: "aboveBar",
                shape: "arrowDown",
                color: "#FF5252",
                text: "▼",
              })
              subBgHighlights.push({
                barIndex: i,
                color: "rgba(255, 82, 82, 0.14)",
              })
            }
          }
        }

        if (!isTeMode && i > 0 && series[i - 1] != null && val != null) {
          if (series[i - 1]! <= 0 && val > 0) {
            markers.push({
              barIndex: i,
              position: "belowBar",
              shape: "diamond",
              color: "#00E676",
              text: "◆",
            })
          } else if (series[i - 1]! >= 0 && val < 0) {
            markers.push({
              barIndex: i,
              position: "aboveBar",
              shape: "diamond",
              color: "#FF5252",
              text: "◆",
            })
          }
        }
      }

      const lastBull =
        regimeColors[len - 1] != null ? regimeColors[len - 1] === "#4CAF50" : bullState

      const effectiveMeta: TvIndicatorMetadata = isTeMode
        ? { ...meta, referenceLevels: undefined }
        : {
            ...meta,
            referenceLevels: meta.referenceLevels ?? [
              { value: 0, color: "#787B86", label: "0", dashed: false },
            ],
          }

      const modeTitle = isTeMode
        ? `[HuD] TE V2 (${fastLen} · ${l1}/${l2}/${l3}/${l4})`
        : `[HuD] DE V2 (${fastLen},${slowLen} · ${l1}/${l2}/${l3}/${l4})`

      return {
        instance,
        meta: effectiveMeta,
        title: modeTitle,
        subCandles,
        subBgHighlights,
        subPaneBadge: {
          text: isTeMode ? "[ TREND XPERT ]" : "[ DEVIATION XPERT ]",
          color: lastBull ? "#00C853" : "#FF5252",
        },
        plots: [
          {
            key: "hud_e1",
            label: `EMA ${l1}`,
            color: "#4CAF50",
            type: "circles_line",
            lineWidth: 1.2,
            values: sr1,
            pointColors: regimeColors,
          },
          {
            key: "hud_e2",
            label: `EMA ${l2}`,
            color: "#4CAF50",
            type: "circles_line",
            lineWidth: 1.2,
            values: sr2,
            pointColors: regimeColors,
          },
          {
            key: "hud_e3",
            label: `EMA ${l3}`,
            color: "#4CAF50",
            type: "circles_line",
            lineWidth: 1.2,
            values: sr3,
            pointColors: regimeColors,
          },
          {
            key: "hud_e4",
            label: `EMA ${l4}`,
            color: "#9C27B0",
            type: "line",
            lineWidth: 2,
            values: sr4,
          },
        ],
        markers,
      }
    }

    case "STORE_PATTERNS_PRO":
      return computeChartPatternStudy(candles, instance, meta)

    case "COM_ROLLING_PROFILE":
    case "COM_BUYERS_SELLERS_SR":
    case "COM_DELTA_FOOTPRINT_MAP": {
      const blk = buildVolumeProfileBlock(candles, Math.max(0, len - 55), len - 1, 22, 70, "right", 0.24, "vp", meta.shortName)
      const hma = calcHMA(closes, pLen)
      return {
        instance,
        meta,
        title: meta.shortName,
        plots: [{ key: "rp_hma", label: "Trend", color: instance.color, type: "line", lineWidth: 1.7, values: hma }],
        volumeProfiles: blk ? [blk] : [],
      }
    }

    default: {
      if (meta.overlay) {
        const fast = calcHMA(hl2, pLen)
        const slow = calcEMA(closes, Math.round(pLen * 1.8))
        const bands: TvIndicatorFillBand[] = [
          {
            upperKey: "com_f",
            lowerKey: "com_s",
            fillColor: "rgba(8, 153, 129, 0.12)",
            dynamicBullBear: true,
            bullColor: "rgba(8, 153, 129, 0.14)",
            bearColor: "rgba(242, 54, 69, 0.14)",
          },
        ]
        return {
          instance,
          meta,
          title: `${meta.shortName} (${pLen})`,
          plots: [
            { key: "com_f", label: "Fast", color: "#089981", type: "line", lineWidth: 1.8, values: fast },
            { key: "com_s", label: "Baseline", color: "#F23645", type: "line", lineWidth: 1.5, values: slow },
          ],
          bands,
        }
      } else {
        const cci = calcCCI(candles, pLen)
        const rsi = calcRSI(closes, pLen)
        const composite = closes.map((_, i) =>
          cci[i] != null && rsi[i] != null ? cci[i]! * 0.45 + (rsi[i]! - 50) * 1.1 : null
        )
        const smooth = calcEMA(composite, 5)
        const colors = smooth.map((v) => (v == null ? null : v >= 0 ? "rgba(8, 153, 129, 0.7)" : "rgba(242, 54, 69, 0.7)"))
        return {
          instance,
          meta,
          title: `${meta.shortName} (${pLen})`,
          plots: [
            { key: "com_osc_h", label: "Flow", color: instance.color, type: "histogram", values: smooth, pointColors: colors },
            { key: "com_osc_l", label: "Signal", color: instance.color, type: "line", lineWidth: 1.6, values: smooth },
          ],
        }
      }
    }
  }
}
