/* ============================================================================
 * TradingView Drawing Tools — Canvas 2D Renderer & Hit-Testing Engine
 * Supports all 68 LineTool* types across Trend, Fib/Gann, Patterns,
 * Risk/Reward Positions, Volume Profile (FRVP/AVP), Anchored VWAP, Shapes & Annotations
 * ============================================================================ */

import type { TvCandle, TvDrawingObject, TvDrawingPoint } from "../types"

export interface DrawingCoordConverter {
  barIndexToX: (barIndex: number) => number
  xToBarIndex: (x: number) => number
  priceToY: (price: number) => number
  yToPrice: (y: number) => number
  plotLeft: number
  plotRight: number
  plotTop: number
  plotBottom: number
  candles: TvCandle[]
}

const FIB_LEVELS = [
  { ratio: 0, color: "#787B86" },
  { ratio: 0.236, color: "#F23645" },
  { ratio: 0.382, color: "#FF9800" },
  { ratio: 0.5, color: "#4CAF50" },
  { ratio: 0.618, color: "#089981" },
  { ratio: 0.786, color: "#00BCD4" },
  { ratio: 1, color: "#787B86" },
  { ratio: 1.618, color: "#2962FF" },
]

function fmtVndShort(price: number): string {
  if (!Number.isFinite(price)) return "—"
  return Math.round(price).toLocaleString("vi-VN")
}

function setCanvasDash(ctx: CanvasRenderingContext2D, style: "solid" | "dashed" | "dotted") {
  if (style === "dashed") ctx.setLineDash([6, 4])
  else if (style === "dotted") ctx.setLineDash([2, 3])
  else ctx.setLineDash([])
}

function hexWithAlpha(hex: string, alpha: number): string {
  if (!hex || !hex.startsWith("#")) return `rgba(41, 98, 255, ${alpha})`
  const clean = hex.slice(1)
  const r = parseInt(clean.slice(0, 2), 16) || 41
  const g = parseInt(clean.slice(2, 4), 16) || 98
  const b = parseInt(clean.slice(4, 6), 16) || 255
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

function drawRoundedBadge(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  text: string,
  bgColor: string,
  textColor = "#FFFFFF",
  align: "left" | "center" | "right" = "center"
) {
  ctx.save()
  ctx.font = "600 10.5px 'JetBrains Mono', monospace"
  const padX = 6
  const h = 19
  const w = ctx.measureText(text).width + padX * 2
  const rx = align === "center" ? x - w / 2 : align === "right" ? x - w : x
  const ry = y - h / 2

  ctx.fillStyle = bgColor
  ctx.beginPath()
  ctx.roundRect(rx, ry, w, h, 4)
  ctx.fill()

  ctx.fillStyle = textColor
  ctx.textAlign = "left"
  ctx.textBaseline = "middle"
  ctx.fillText(text, rx + padX, y + 0.5)
  ctx.restore()
}

function drawArrowHead(
  ctx: CanvasRenderingContext2D,
  fromX: number,
  fromY: number,
  toX: number,
  toY: number,
  size = 9
) {
  const angle = Math.atan2(toY - fromY, toX - fromX)
  ctx.beginPath()
  ctx.moveTo(toX, toY)
  ctx.lineTo(toX - size * Math.cos(angle - Math.PI / 6), toY - size * Math.sin(angle - Math.PI / 6))
  ctx.lineTo(toX - size * Math.cos(angle + Math.PI / 6), toY - size * Math.sin(angle + Math.PI / 6))
  ctx.closePath()
  ctx.fill()
}

export function renderSingleDrawing(
  ctx: CanvasRenderingContext2D,
  drawing: TvDrawingObject,
  conv: DrawingCoordConverter,
  isSelected: boolean,
  isHovered: boolean
) {
  if (!drawing.visible || drawing.points.length === 0) return

  const pts = drawing.points.map((p) => ({
    x: conv.barIndexToX(p.barIndex),
    y: conv.priceToY(p.price),
    barIndex: p.barIndex,
    price: p.price,
  }))

  ctx.save()
  ctx.strokeStyle = drawing.color
  ctx.fillStyle = drawing.color
  ctx.lineWidth = drawing.lineWidth + (isSelected || isHovered ? 0.6 : 0)
  setCanvasDash(ctx, drawing.lineStyle)

  const p0 = pts[0]
  const p1 = pts[1] ?? p0
  const p2 = pts[2] ?? p1

  switch (drawing.tool) {
    /* ----------------------------------------------------------------------
     * 1. TREND LINES & CHANNELS
     * ---------------------------------------------------------------------- */
    case "LineToolTrendLine":
    case "LineToolInfoLine":
    case "LineToolTrendAngle":
    case "LineToolRay":
    case "LineToolExtended": {
      if (pts.length < 2) break
      let xStart = p0.x
      let yStart = p0.y
      let xEnd = p1.x
      let yEnd = p1.y

      const dx = p1.x - p0.x
      const dy = p1.y - p0.y
      if (drawing.tool === "LineToolRay" || drawing.extendRight) {
        if (Math.abs(dx) > 0.1) {
          const slope = dy / dx
          const targetX = dx >= 0 ? conv.plotRight : conv.plotLeft
          xEnd = targetX
          yEnd = p0.y + slope * (targetX - p0.x)
        }
      } else if (drawing.tool === "LineToolExtended") {
        if (Math.abs(dx) > 0.1) {
          const slope = dy / dx
          xStart = conv.plotLeft
          yStart = p0.y + slope * (conv.plotLeft - p0.x)
          xEnd = conv.plotRight
          yEnd = p0.y + slope * (conv.plotRight - p0.x)
        }
      }

      ctx.beginPath()
      ctx.moveTo(xStart, yStart)
      ctx.lineTo(xEnd, yEnd)
      ctx.stroke()

      if (drawing.tool === "LineToolTrendAngle") {
        ctx.save()
        ctx.setLineDash([3, 3])
        ctx.beginPath()
        ctx.moveTo(p0.x, p0.y)
        ctx.lineTo(p0.x + 65, p0.y)
        ctx.stroke()
        const angleRad = Math.atan2(-(p1.y - p0.y), p1.x - p0.x)
        const deg = Math.round((angleRad * 180) / Math.PI)
        ctx.beginPath()
        ctx.arc(p0.x, p0.y, 28, 0, -angleRad, angleRad > 0)
        ctx.stroke()
        drawRoundedBadge(ctx, p0.x + 42, p0.y - 14, `${deg}°`, drawing.color)
        ctx.restore()
      }

      if (drawing.tool === "LineToolInfoLine") {
        const priceDiff = p1.price - p0.price
        const pct = p0.price > 0 ? ((priceDiff / p0.price) * 100).toFixed(2) : "0.00"
        const bars = Math.abs(Math.round(p1.barIndex - p0.barIndex))
        const deg = Math.round((Math.atan2(-(p1.y - p0.y), p1.x - p0.x) * 180) / Math.PI)
        const midX = (p0.x + p1.x) / 2
        const midY = (p0.y + p1.y) / 2 - 18
        const sign = priceDiff >= 0 ? "+" : ""
        drawRoundedBadge(
          ctx,
          midX,
          midY,
          `${sign}${fmtVndShort(priceDiff)} ₫ (${sign}${pct}%) · ${bars} thanh · ${deg}°`,
          drawing.color
        )
      }
      break
    }

    case "LineToolHorzLine": {
      ctx.beginPath()
      ctx.moveTo(conv.plotLeft, p0.y)
      ctx.lineTo(conv.plotRight, p0.y)
      ctx.stroke()
      drawRoundedBadge(
        ctx,
        conv.plotRight - 6,
        p0.y - 11,
        `${fmtVndShort(p0.price)} ₫`,
        drawing.color,
        "#FFFFFF",
        "right"
      )
      break
    }

    case "LineToolHorzRay": {
      ctx.beginPath()
      ctx.moveTo(p0.x, p0.y)
      ctx.lineTo(conv.plotRight, p0.y)
      ctx.stroke()
      drawRoundedBadge(
        ctx,
        conv.plotRight - 6,
        p0.y - 11,
        `${fmtVndShort(p0.price)} ₫`,
        drawing.color,
        "#FFFFFF",
        "right"
      )
      break
    }

    case "LineToolVertLine": {
      ctx.beginPath()
      ctx.moveTo(p0.x, conv.plotTop)
      ctx.lineTo(p0.x, conv.plotBottom)
      ctx.stroke()
      break
    }

    case "LineToolCrossLine": {
      ctx.beginPath()
      ctx.moveTo(conv.plotLeft, p0.y)
      ctx.lineTo(conv.plotRight, p0.y)
      ctx.moveTo(p0.x, conv.plotTop)
      ctx.lineTo(p0.x, conv.plotBottom)
      ctx.stroke()
      break
    }

    case "LineToolParallelChannel":
    case "LineToolFlatBottom":
    case "LineToolDisjointAngle": {
      if (pts.length < 2) break
      const dyOffset = pts.length >= 3 ? p2.y - p0.y : -36
      const p0B = { x: p0.x, y: p0.y + dyOffset }
      const p1B = {
        x: p1.x,
        y:
          drawing.tool === "LineToolFlatBottom"
            ? p0.y + dyOffset
            : drawing.tool === "LineToolDisjointAngle"
              ? p1.y - dyOffset * 0.65
              : p1.y + dyOffset,
      }

      ctx.fillStyle = drawing.fillColor || hexWithAlpha(drawing.color, 0.12)
      ctx.beginPath()
      ctx.moveTo(p0.x, p0.y)
      ctx.lineTo(p1.x, p1.y)
      ctx.lineTo(p1B.x, p1B.y)
      ctx.lineTo(p0B.x, p0B.y)
      ctx.closePath()
      ctx.fill()

      ctx.beginPath()
      ctx.moveTo(p0.x, p0.y)
      ctx.lineTo(p1.x, p1.y)
      ctx.moveTo(p0B.x, p0B.y)
      ctx.lineTo(p1B.x, p1B.y)
      ctx.stroke()

      ctx.save()
      ctx.setLineDash([4, 4])
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(p0.x, (p0.y + p0B.y) / 2)
      ctx.lineTo(p1.x, (p1.y + p1B.y) / 2)
      ctx.stroke()
      ctx.restore()
      break
    }

    case "LineToolRegressionTrend": {
      if (pts.length < 2) break
      const idxA = Math.max(0, Math.min(conv.candles.length - 1, Math.round(Math.min(p0.barIndex, p1.barIndex))))
      const idxB = Math.max(0, Math.min(conv.candles.length - 1, Math.round(Math.max(p0.barIndex, p1.barIndex))))
      if (idxB <= idxA) break

      const n = idxB - idxA + 1
      let sumX = 0
      let sumY = 0
      let sumXY = 0
      let sumX2 = 0
      for (let i = idxA; i <= idxB; i++) {
        const x = i - idxA
        const y = conv.candles[i].close
        sumX += x
        sumY += y
        sumXY += x * y
        sumX2 += x * x
      }
      const denom = n * sumX2 - sumX * sumX
      const slope = denom !== 0 ? (n * sumXY - sumX * sumY) / denom : 0
      const intercept = (sumY - slope * sumX) / n

      let sqErr = 0
      for (let i = idxA; i <= idxB; i++) {
        const x = i - idxA
        const pred = intercept + slope * x
        const diff = conv.candles[i].close - pred
        sqErr += diff * diff
      }
      const stdDev = Math.sqrt(sqErr / n) * 2

      const x1 = conv.barIndexToX(idxA)
      const x2 = conv.barIndexToX(idxB)
      const yMid1 = conv.priceToY(intercept)
      const yMid2 = conv.priceToY(intercept + slope * (n - 1))
      const yUp1 = conv.priceToY(intercept + stdDev)
      const yUp2 = conv.priceToY(intercept + slope * (n - 1) + stdDev)
      const yLow1 = conv.priceToY(intercept - stdDev)
      const yLow2 = conv.priceToY(intercept + slope * (n - 1) - stdDev)

      ctx.fillStyle = hexWithAlpha(drawing.color, 0.11)
      ctx.beginPath()
      ctx.moveTo(x1, yUp1)
      ctx.lineTo(x2, yUp2)
      ctx.lineTo(x2, yLow2)
      ctx.lineTo(x1, yLow1)
      ctx.closePath()
      ctx.fill()

      ctx.beginPath()
      ctx.moveTo(x1, yUp1)
      ctx.lineTo(x2, yUp2)
      ctx.moveTo(x1, yLow1)
      ctx.lineTo(x2, yLow2)
      ctx.stroke()

      ctx.save()
      ctx.strokeStyle = "#F23645"
      ctx.setLineDash([5, 3])
      ctx.beginPath()
      ctx.moveTo(x1, yMid1)
      ctx.lineTo(x2, yMid2)
      ctx.stroke()
      ctx.restore()
      break
    }

    /* ----------------------------------------------------------------------
     * 2. FIBONACCI, GANN & PITCHFORK TOOLS
     * ---------------------------------------------------------------------- */
    case "LineToolFibRetracement":
    case "LineToolTrendBasedFibExtension":
    case "LineToolFibChannel": {
      if (pts.length < 2) break
      const xLeft = Math.min(p0.x, p1.x)
      const xRight = Math.max(p0.x, p1.x, Math.min(conv.plotRight - 10, Math.max(p0.x, p1.x) + 80))
      const basePrice = pts.length >= 3 && drawing.tool === "LineToolTrendBasedFibExtension" ? p2.price : p1.price
      const priceSpan = p0.price - p1.price

      // Connector dashed line
      ctx.save()
      ctx.setLineDash([3, 3])
      ctx.strokeStyle = "#787B86"
      ctx.beginPath()
      ctx.moveTo(p0.x, p0.y)
      ctx.lineTo(p1.x, p1.y)
      ctx.stroke()
      ctx.restore()

      for (let i = 0; i < FIB_LEVELS.length; i++) {
        const lv = FIB_LEVELS[i]
        const lvlPrice = basePrice + priceSpan * lv.ratio
        const y = conv.priceToY(lvlPrice)

        if (i > 0) {
          const prevPrice = basePrice + priceSpan * FIB_LEVELS[i - 1].ratio
          const prevY = conv.priceToY(prevPrice)
          ctx.fillStyle = hexWithAlpha(lv.color, 0.08)
          ctx.fillRect(xLeft, Math.min(y, prevY), xRight - xLeft, Math.abs(y - prevY))
        }

        ctx.strokeStyle = lv.color
        ctx.lineWidth = 1.2
        ctx.setLineDash([])
        ctx.beginPath()
        ctx.moveTo(xLeft, y)
        ctx.lineTo(xRight, y)
        ctx.stroke()

        ctx.fillStyle = lv.color
        ctx.font = "500 10px 'JetBrains Mono', monospace"
        ctx.textAlign = "left"
        ctx.textBaseline = "bottom"
        ctx.fillText(`${lv.ratio} (${fmtVndShort(lvlPrice)})`, xLeft + 4, y - 2)
      }
      break
    }

    case "LineToolFibTimeZone":
    case "LineToolTrendBasedFibTime":
    case "LineToolCircleLines": {
      if (pts.length < 2) break
      const baseStep = Math.max(2, Math.abs(p1.x - p0.x))
      const fibNums =
        drawing.tool === "LineToolCircleLines"
          ? [1, 2, 3, 4, 5, 6, 7, 8]
          : [0, 1, 2, 3, 5, 8, 13, 21, 34]
      for (let i = 0; i < fibNums.length; i++) {
        const x = p0.x + fibNums[i] * baseStep
        if (x > conv.plotRight) break
        ctx.beginPath()
        ctx.moveTo(x, conv.plotTop)
        ctx.lineTo(x, conv.plotBottom)
        ctx.stroke()
        ctx.font = "600 10px 'JetBrains Mono', monospace"
        ctx.fillText(String(fibNums[i]), x + 3, conv.plotBottom - 8)
      }
      break
    }

    case "LineToolFibSpeedResistanceFan":
    case "LineToolGannFan":
    case "LineToolPitchfan": {
      if (pts.length < 2) break
      const ratios =
        drawing.tool === "LineToolGannFan"
          ? [0.125, 0.25, 0.333, 0.5, 1, 2, 3, 4]
          : [0.236, 0.382, 0.5, 0.618, 0.786, 1]
      const dx = p1.x - p0.x || 40
      const dy = p1.y - p0.y
      const colors = ["#F23645", "#FF9800", "#4CAF50", "#089981", "#2962FF", "#9C27B0", "#00ACC1", "#787B86"]

      ratios.forEach((r, idx) => {
        ctx.strokeStyle = colors[idx % colors.length]
        ctx.beginPath()
        ctx.moveTo(p0.x, p0.y)
        const targetX = dx >= 0 ? conv.plotRight : conv.plotLeft
        const slope = (dy * r) / dx
        ctx.lineTo(targetX, p0.y + slope * (targetX - p0.x))
        ctx.stroke()
      })
      break
    }

    case "LineToolFibCircles":
    case "LineToolFibSpeedResistanceArcs":
    case "LineToolFibSpiral":
    case "LineToolFibWedge": {
      if (pts.length < 2) break
      const baseR = Math.hypot(p1.x - p0.x, p1.y - p0.y)
      const ratios = [0.236, 0.382, 0.5, 0.618, 0.786, 1]
      const colors = ["#F23645", "#FF9800", "#4CAF50", "#089981", "#00BCD4", "#2962FF"]

      ctx.beginPath()
      ctx.moveTo(p0.x, p0.y)
      ctx.lineTo(p1.x, p1.y)
      ctx.stroke()

      ratios.forEach((r, idx) => {
        ctx.strokeStyle = colors[idx % colors.length]
        ctx.beginPath()
        if (drawing.tool === "LineToolFibSpeedResistanceArcs") {
          ctx.arc(p0.x, p0.y, baseR * r, Math.PI, 0)
        } else {
          ctx.arc(p0.x, p0.y, baseR * r, 0, Math.PI * 2)
        }
        ctx.stroke()
      })
      break
    }

    case "LineToolPitchfork":
    case "LineToolSchiffPitchfork2":
    case "LineToolInsidePitchfork": {
      if (pts.length < 2) break
      const cA =
        drawing.tool === "LineToolSchiffPitchfork2"
          ? { x: (p0.x + p1.x) / 2, y: (p0.y + p1.y) / 2 }
          : p0
      const cB = p1
      const cC = pts.length >= 3 ? p2 : { x: p1.x, y: p1.y + 50 }
      const midBC = { x: (cB.x + cC.x) / 2, y: (cB.y + cC.y) / 2 }
      const dx = midBC.x - cA.x || 30
      const dy = midBC.y - cA.y
      const slope = dy / dx
      const targetX = conv.plotRight

      const endMidY = cA.y + slope * (targetX - cA.x)
      const endBY = cB.y + slope * (targetX - cB.x)
      const endCY = cC.y + slope * (targetX - cC.x)

      ctx.fillStyle = hexWithAlpha(drawing.color, 0.09)
      ctx.beginPath()
      ctx.moveTo(cB.x, cB.y)
      ctx.lineTo(targetX, endBY)
      ctx.lineTo(targetX, endCY)
      ctx.lineTo(cC.x, cC.y)
      ctx.closePath()
      ctx.fill()

      ctx.beginPath()
      ctx.moveTo(cB.x, cB.y)
      ctx.lineTo(cC.x, cC.y)
      ctx.moveTo(cA.x, cA.y)
      ctx.lineTo(targetX, endMidY)
      ctx.moveTo(cB.x, cB.y)
      ctx.lineTo(targetX, endBY)
      ctx.moveTo(cC.x, cC.y)
      ctx.lineTo(targetX, endCY)
      ctx.stroke()
      break
    }

    case "LineToolGannSquare":
    case "LineToolGannFixed":
    case "LineToolGannComplex": {
      if (pts.length < 2) break
      const x = Math.min(p0.x, p1.x)
      const y = Math.min(p0.y, p1.y)
      const w = Math.abs(p1.x - p0.x)
      const h = Math.abs(p1.y - p0.y)

      ctx.fillStyle = hexWithAlpha(drawing.color, 0.08)
      ctx.fillRect(x, y, w, h)
      ctx.strokeRect(x, y, w, h)

      const gridLevels = [0.25, 0.382, 0.5, 0.618, 0.75]
      ctx.save()
      ctx.setLineDash([3, 3])
      ctx.lineWidth = 1
      for (const g of gridLevels) {
        ctx.beginPath()
        ctx.moveTo(x, y + h * g)
        ctx.lineTo(x + w, y + h * g)
        ctx.moveTo(x + w * g, y)
        ctx.lineTo(x + w * g, y + h)
        ctx.stroke()
      }
      ctx.beginPath()
      ctx.moveTo(x, y + h)
      ctx.lineTo(x + w, y)
      ctx.moveTo(x, y)
      ctx.lineTo(x + w, y + h)
      ctx.stroke()
      ctx.restore()
      break
    }

    /* ----------------------------------------------------------------------
     * 3. PATTERNS (XABCD, ABCD, HEAD & SHOULDERS, ELLIOTT WAVES, CYCLES)
     * ---------------------------------------------------------------------- */
    case "LineTool5PointsPattern":
    case "LineToolCypherPattern":
    case "LineToolABCD":
    case "LineToolTrianglePattern":
    case "LineToolThreeDrivers":
    case "LineToolHeadAndShoulders":
    case "LineToolElliottImpulse":
    case "LineToolElliottCorrection":
    case "LineToolElliottTriangle":
    case "LineToolElliottDoubleCombo":
    case "LineToolElliottTripleCombo": {
      // Shaded harmonic triangles for XABCD / ABCD
      if (
        (drawing.tool === "LineTool5PointsPattern" ||
          drawing.tool === "LineToolCypherPattern" ||
          drawing.tool === "LineToolABCD" ||
          drawing.tool === "LineToolTrianglePattern") &&
        pts.length >= 3
      ) {
        ctx.fillStyle = hexWithAlpha(drawing.color, 0.14)
        ctx.beginPath()
        ctx.moveTo(pts[0].x, pts[0].y)
        ctx.lineTo(pts[1].x, pts[1].y)
        ctx.lineTo(pts[2].x, pts[2].y)
        ctx.closePath()
        ctx.fill()

        if (pts.length >= 5) {
          ctx.beginPath()
          ctx.moveTo(pts[2].x, pts[2].y)
          ctx.lineTo(pts[3].x, pts[3].y)
          ctx.lineTo(pts[4].x, pts[4].y)
          ctx.closePath()
          ctx.fill()
        }
      }

      // Connect all points sequentially
      ctx.beginPath()
      pts.forEach((pt, idx) => {
        if (idx === 0) ctx.moveTo(pt.x, pt.y)
        else ctx.lineTo(pt.x, pt.y)
      })
      ctx.stroke()

      const labelsMap: Record<string, string[]> = {
        LineTool5PointsPattern: ["X", "A", "B", "C", "D"],
        LineToolCypherPattern: ["X", "A", "B", "C", "D"],
        LineToolABCD: ["A", "B", "C", "D"],
        LineToolTrianglePattern: ["A", "B", "C", "D"],
        LineToolThreeDrivers: ["0", "D1", "A", "D2", "B", "D3", "C"],
        LineToolHeadAndShoulders: ["L0", "Vai trái", "N1", "Đầu", "N2", "Vai phải", "R0"],
        LineToolElliottImpulse: ["(0)", "(1)", "(2)", "(3)", "(4)", "(5)"],
        LineToolElliottCorrection: ["(0)", "(A)", "(B)", "(C)"],
        LineToolElliottTriangle: ["(0)", "(A)", "(B)", "(C)", "(D)", "(E)"],
        LineToolElliottDoubleCombo: ["(0)", "(W)", "(X)", "(Y)"],
        LineToolElliottTripleCombo: ["(0)", "(W)", "(X)", "(Y)", "(X)", "(Z)"],
      }
      const labels = labelsMap[drawing.tool] || pts.map((_, i) => String(i + 1))

      pts.forEach((pt, idx) => {
        const lbl = labels[idx] ?? String(idx)
        const isPeak = idx === 0 ? false : pt.y < pts[idx - 1].y
        drawRoundedBadge(ctx, pt.x, pt.y + (isPeak ? -14 : 14), lbl, drawing.color)
      })
      break
    }

    case "LineToolTimeCycles": {
      if (pts.length < 2) break
      const step = Math.max(12, Math.abs(p1.x - p0.x))
      for (let x = p0.x; x < conv.plotRight; x += step) {
        ctx.beginPath()
        ctx.arc(x + step / 2, p0.y, step / 2, Math.PI, 0)
        ctx.stroke()
      }
      break
    }

    case "LineToolSineLine": {
      if (pts.length < 2) break
      const wavelength = Math.max(20, Math.abs(p1.x - p0.x) * 2)
      const amp = Math.abs(p1.y - p0.y)
      const midY = (p0.y + p1.y) / 2
      ctx.beginPath()
      for (let x = conv.plotLeft; x <= conv.plotRight; x += 3) {
        const y = midY + amp * Math.sin(((x - p0.x) / wavelength) * Math.PI * 2)
        if (x === conv.plotLeft) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      }
      ctx.stroke()
      break
    }

    /* ----------------------------------------------------------------------
     * 4. PREDICTION & MEASUREMENT (LONG/SHORT R:R, VOLUME PROFILE, ANCHORED VWAP)
     * ---------------------------------------------------------------------- */
    case "LineToolRiskRewardLong":
    case "LineToolRiskRewardShort": {
      const isLong = drawing.tool === "LineToolRiskRewardLong"
      const x1 = Math.min(p0.x, p1.x)
      const x2 = Math.max(p0.x + 110, p1.x)
      const entryY = p0.y
      const entryPrice = p0.price

      const targetPrice =
        pts.length >= 2 && Math.abs(p1.price - entryPrice) > 10
          ? p1.price
          : isLong
            ? entryPrice * 1.07
            : entryPrice * 0.93
      const stopDist = Math.abs(targetPrice - entryPrice) * 0.5
      const stopPrice =
        pts.length >= 3
          ? p2.price
          : isLong
            ? entryPrice - stopDist
            : entryPrice + stopDist

      const targetY = conv.priceToY(targetPrice)
      const stopY = conv.priceToY(stopPrice)

      // Profit zone (green)
      ctx.fillStyle = "rgba(8, 153, 129, 0.18)"
      ctx.fillRect(x1, Math.min(entryY, targetY), x2 - x1, Math.abs(targetY - entryY))
      ctx.strokeStyle = "#089981"
      ctx.strokeRect(x1, Math.min(entryY, targetY), x2 - x1, Math.abs(targetY - entryY))

      // Stop loss zone (red)
      ctx.fillStyle = "rgba(242, 54, 69, 0.18)"
      ctx.fillRect(x1, Math.min(entryY, stopY), x2 - x1, Math.abs(stopY - entryY))
      ctx.strokeStyle = "#F23645"
      ctx.strokeRect(x1, Math.min(entryY, stopY), x2 - x1, Math.abs(stopY - entryY))

      // Entry line
      ctx.strokeStyle = "#18201D"
      ctx.lineWidth = 1.6
      ctx.beginPath()
      ctx.moveTo(x1, entryY)
      ctx.lineTo(x2, entryY)
      ctx.stroke()

      const reward = Math.abs(targetPrice - entryPrice)
      const risk = Math.max(1, Math.abs(entryPrice - stopPrice))
      const rr = (reward / risk).toFixed(2)
      const profitPct = entryPrice > 0 ? ((reward / entryPrice) * 100).toFixed(2) : "0"
      const lossPct = entryPrice > 0 ? ((risk / entryPrice) * 100).toFixed(2) : "0"

      drawRoundedBadge(
        ctx,
        (x1 + x2) / 2,
        targetY + (targetY < entryY ? -11 : 11),
        `Chốt lời: ${fmtVndShort(targetPrice)} (+${profitPct}%)`,
        "#089981"
      )
      drawRoundedBadge(
        ctx,
        (x1 + x2) / 2,
        entryY,
        `${isLong ? "MUA" : "BÁN"} @ ${fmtVndShort(entryPrice)} · R/R: ${rr}`,
        "#2962FF"
      )
      drawRoundedBadge(
        ctx,
        (x1 + x2) / 2,
        stopY + (stopY > entryY ? 11 : -11),
        `Cắt lỗ: ${fmtVndShort(stopPrice)} (-${lossPct}%)`,
        "#F23645"
      )
      break
    }

    case "LineToolFixedRangeVolumeProfile":
    case "LineToolAnchoredVolumeProfile": {
      const startBar = Math.max(
        0,
        Math.min(
          conv.candles.length - 1,
          Math.round(
            drawing.tool === "LineToolAnchoredVolumeProfile"
              ? p0.barIndex
              : Math.min(p0.barIndex, p1.barIndex)
          )
        )
      )
      const endBar = Math.max(
        startBar,
        Math.min(
          conv.candles.length - 1,
          Math.round(
            drawing.tool === "LineToolAnchoredVolumeProfile"
              ? conv.candles.length - 1
              : Math.max(p0.barIndex, p1.barIndex)
          )
        )
      )
      if (endBar <= startBar) break

      let minP = Infinity
      let maxP = -Infinity
      for (let i = startBar; i <= endBar; i++) {
        if (conv.candles[i].low < minP) minP = conv.candles[i].low
        if (conv.candles[i].high > maxP) maxP = conv.candles[i].high
      }
      if (!Number.isFinite(minP) || maxP <= minP) break

      const rows = 20
      const stepP = (maxP - minP) / rows
      const buyBuckets = new Array(rows).fill(0)
      const sellBuckets = new Array(rows).fill(0)

      for (let i = startBar; i <= endBar; i++) {
        const c = conv.candles[i]
        const isUp = c.close >= c.open
        const bIdx = Math.min(rows - 1, Math.max(0, Math.floor(((c.close - minP) / (maxP - minP)) * rows)))
        if (isUp) buyBuckets[bIdx] += c.volume
        else sellBuckets[bIdx] += c.volume
      }

      let maxBucketVol = 1
      let pocRow = 0
      for (let r = 0; r < rows; r++) {
        const tot = buyBuckets[r] + sellBuckets[r]
        if (tot > maxBucketVol) {
          maxBucketVol = tot
          pocRow = r
        }
      }

      const xStart = conv.barIndexToX(startBar)
      const xEnd = conv.barIndexToX(endBar)
      const maxBarWidth = Math.max(45, (xEnd - xStart) * 0.65)
      const topY = conv.priceToY(maxP)
      const botY = conv.priceToY(minP)

      ctx.fillStyle = "rgba(41, 98, 255, 0.05)"
      ctx.fillRect(xStart, topY, xEnd - xStart, botY - topY)

      for (let r = 0; r < rows; r++) {
        const pLow = minP + r * stepP
        const pHigh = pLow + stepP
        const yTop = conv.priceToY(pHigh)
        const yBot = conv.priceToY(pLow)
        const rowH = Math.max(2, Math.abs(yBot - yTop) - 1)
        const buyW = (buyBuckets[r] / maxBucketVol) * maxBarWidth
        const sellW = (sellBuckets[r] / maxBucketVol) * maxBarWidth

        ctx.fillStyle = r === pocRow ? "rgba(38, 166, 154, 0.78)" : "rgba(41, 98, 255, 0.42)"
        ctx.fillRect(xStart, yTop, buyW, rowH)
        ctx.fillStyle = r === pocRow ? "rgba(239, 83, 80, 0.78)" : "rgba(251, 192, 45, 0.42)"
        ctx.fillRect(xStart + buyW, yTop, sellW, rowH)
      }

      // POC Line
      const pocPrice = minP + (pocRow + 0.5) * stepP
      const pocY = conv.priceToY(pocPrice)
      ctx.strokeStyle = "#F23645"
      ctx.lineWidth = 1.8
      ctx.setLineDash([])
      ctx.beginPath()
      ctx.moveTo(xStart, pocY)
      ctx.lineTo(xEnd, pocY)
      ctx.stroke()
      drawRoundedBadge(ctx, xEnd - 4, pocY - 10, `POC ${fmtVndShort(pocPrice)}`, "#F23645", "#FFF", "right")
      break
    }

    case "LineToolAnchoredVWAP": {
      const startBar = Math.max(0, Math.min(conv.candles.length - 1, Math.round(p0.barIndex)))
      let pvSum = 0
      let vSum = 0
      ctx.beginPath()
      for (let i = startBar; i < conv.candles.length; i++) {
        const c = conv.candles[i]
        const typ = (c.high + c.low + c.close) / 3
        const vol = Math.max(1, c.volume)
        pvSum += typ * vol
        vSum += vol
        const vwap = pvSum / vSum
        const x = conv.barIndexToX(i)
        const y = conv.priceToY(vwap)
        if (i === startBar) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      }
      ctx.stroke()
      drawRoundedBadge(ctx, p0.x, p0.y - 14, "AVWAP", drawing.color)
      break
    }

    case "LineToolPriceRange":
    case "LineToolDateRange":
    case "LineToolDateAndPriceRange": {
      if (pts.length < 2) break
      const rx = Math.min(p0.x, p1.x)
      const ry = Math.min(p0.y, p1.y)
      const rw = Math.abs(p1.x - p0.x)
      const rh = Math.abs(p1.y - p0.y)
      const priceDiff = p1.price - p0.price
      const isGain = priceDiff >= 0
      const boxColor = isGain ? "#2962FF" : "#F23645"

      ctx.fillStyle = isGain ? "rgba(41, 98, 255, 0.14)" : "rgba(242, 54, 69, 0.14)"
      ctx.fillRect(rx, ry, rw, rh)
      ctx.strokeStyle = boxColor
      ctx.strokeRect(rx, ry, rw, rh)

      // Internal crosshair arrows
      const midX = rx + rw / 2
      const midY = ry + rh / 2
      ctx.beginPath()
      ctx.moveTo(midX, ry)
      ctx.lineTo(midX, ry + rh)
      ctx.moveTo(rx, midY)
      ctx.lineTo(rx + rw, midY)
      ctx.stroke()

      const pct = p0.price > 0 ? ((priceDiff / p0.price) * 100).toFixed(2) : "0.00"
      const bars = Math.abs(Math.round(p1.barIndex - p0.barIndex))
      const sign = priceDiff >= 0 ? "+" : ""
      drawRoundedBadge(
        ctx,
        midX,
        ry - 13,
        `${sign}${fmtVndShort(priceDiff)} ₫ (${sign}${pct}%) · ${bars} thanh`,
        boxColor
      )
      break
    }

    case "LineToolPrediction":
    case "LineToolProjection":
    case "LineToolBarsPattern":
    case "LineToolGhostFeed": {
      if (pts.length < 2) break
      ctx.save()
      ctx.setLineDash([5, 4])
      ctx.beginPath()
      ctx.moveTo(p0.x, p0.y)
      ctx.lineTo(p1.x, p1.y)
      ctx.stroke()
      drawArrowHead(ctx, p0.x, p0.y, p1.x, p1.y, 10)
      const pct = p0.price > 0 ? (((p1.price - p0.price) / p0.price) * 100).toFixed(2) : "0.00"
      drawRoundedBadge(
        ctx,
        p1.x,
        p1.y - 14,
        `Mục tiêu: ${fmtVndShort(p1.price)} (${Number(pct) >= 0 ? "+" : ""}${pct}%)`,
        drawing.color
      )
      ctx.restore()
      break
    }

    /* ----------------------------------------------------------------------
     * 5. GEOMETRIC SHAPES (BRUSH, HIGHLIGHTER, RECT, CIRCLE, ELLIPSE, TRIANGLE, ARROW)
     * ---------------------------------------------------------------------- */
    case "LineToolBrush":
    case "LineToolHighlighter": {
      if (pts.length < 2) break
      ctx.save()
      if (drawing.tool === "LineToolHighlighter") {
        ctx.strokeStyle = hexWithAlpha(drawing.color, 0.35)
        ctx.lineWidth = 14
        ctx.lineCap = "round"
        ctx.lineJoin = "round"
      }
      ctx.beginPath()
      pts.forEach((pt, i) => {
        if (i === 0) ctx.moveTo(pt.x, pt.y)
        else ctx.lineTo(pt.x, pt.y)
      })
      ctx.stroke()
      ctx.restore()
      break
    }

    case "LineToolArrow":
    case "LineToolArrowMarker": {
      if (pts.length < 2) break
      ctx.lineWidth = drawing.tool === "LineToolArrowMarker" ? 3.2 : drawing.lineWidth
      ctx.beginPath()
      ctx.moveTo(p0.x, p0.y)
      ctx.lineTo(p1.x, p1.y)
      ctx.stroke()
      drawArrowHead(ctx, p0.x, p0.y, p1.x, p1.y, drawing.tool === "LineToolArrowMarker" ? 13 : 9)
      break
    }

    case "LineToolArrowMarkUp":
    case "LineToolArrowMarkDown": {
      const isUp = drawing.tool === "LineToolArrowMarkUp"
      ctx.fillStyle = isUp ? "#089981" : "#F23645"
      ctx.beginPath()
      if (isUp) {
        ctx.moveTo(p0.x, p0.y - 10)
        ctx.lineTo(p0.x - 8, p0.y + 4)
        ctx.lineTo(p0.x - 3, p0.y + 4)
        ctx.lineTo(p0.x - 3, p0.y + 12)
        ctx.lineTo(p0.x + 3, p0.y + 12)
        ctx.lineTo(p0.x + 3, p0.y + 4)
        ctx.lineTo(p0.x + 8, p0.y + 4)
      } else {
        ctx.moveTo(p0.x, p0.y + 10)
        ctx.lineTo(p0.x - 8, p0.y - 4)
        ctx.lineTo(p0.x - 3, p0.y - 4)
        ctx.lineTo(p0.x - 3, p0.y - 12)
        ctx.lineTo(p0.x + 3, p0.y - 12)
        ctx.lineTo(p0.x + 3, p0.y - 4)
        ctx.lineTo(p0.x + 8, p0.y - 4)
      }
      ctx.closePath()
      ctx.fill()
      break
    }

    case "LineToolRectangle":
    case "LineToolRotatedRectangle": {
      if (pts.length < 2) break
      const rx = Math.min(p0.x, p1.x)
      const ry = Math.min(p0.y, p1.y)
      const rw = Math.abs(p1.x - p0.x)
      const rh = Math.abs(p1.y - p0.y)
      ctx.fillStyle = drawing.fillColor || hexWithAlpha(drawing.color, 0.14)
      ctx.fillRect(rx, ry, rw, rh)
      ctx.strokeRect(rx, ry, rw, rh)
      break
    }

    case "LineToolCircle": {
      if (pts.length < 2) break
      const r = Math.hypot(p1.x - p0.x, p1.y - p0.y)
      ctx.fillStyle = drawing.fillColor || hexWithAlpha(drawing.color, 0.12)
      ctx.beginPath()
      ctx.arc(p0.x, p0.y, r, 0, Math.PI * 2)
      ctx.fill()
      ctx.stroke()
      break
    }

    case "LineToolEllipse": {
      if (pts.length < 2) break
      const cx = (p0.x + p1.x) / 2
      const cy = (p0.y + p1.y) / 2
      const rx = Math.max(4, Math.abs(p1.x - p0.x) / 2)
      const ry = Math.max(4, Math.abs(p1.y - p0.y) / 2)
      ctx.fillStyle = drawing.fillColor || hexWithAlpha(drawing.color, 0.12)
      ctx.beginPath()
      ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2)
      ctx.fill()
      ctx.stroke()
      break
    }

    case "LineToolTriangle":
    case "LineToolPolyline":
    case "LineToolPath": {
      if (pts.length < 2) break
      ctx.fillStyle = drawing.fillColor || hexWithAlpha(drawing.color, 0.12)
      ctx.beginPath()
      pts.forEach((pt, idx) => {
        if (idx === 0) ctx.moveTo(pt.x, pt.y)
        else ctx.lineTo(pt.x, pt.y)
      })
      if (drawing.tool !== "LineToolPath") {
        ctx.closePath()
        ctx.fill()
      }
      ctx.stroke()
      if (drawing.tool === "LineToolPath" && pts.length >= 2) {
        const last = pts[pts.length - 1]
        const prev = pts[pts.length - 2]
        drawArrowHead(ctx, prev.x, prev.y, last.x, last.y, 9)
      }
      break
    }

    case "LineToolArc":
    case "LineToolBezierQuadro":
    case "LineToolBezierCubic": {
      if (pts.length < 2) break
      const ctrl = pts.length >= 3 ? p2 : { x: (p0.x + p1.x) / 2, y: Math.min(p0.y, p1.y) - 40 }
      ctx.beginPath()
      ctx.moveTo(p0.x, p0.y)
      ctx.quadraticCurveTo(ctrl.x, ctrl.y, p1.x, p1.y)
      ctx.stroke()
      break
    }

    /* ----------------------------------------------------------------------
     * 6. ANNOTATION TOOLS (TEXT, CALLOUT, NOTE, PRICE LABEL, FLAG MARK)
     * ---------------------------------------------------------------------- */
    case "LineToolText":
    case "LineToolTextAbsolute":
    case "LineToolNote":
    case "LineToolComment":
    case "LineToolSignpost": {
      const label = drawing.text || "Ghi chú phân tích"
      drawRoundedBadge(ctx, p0.x, p0.y, label, drawing.color)
      break
    }

    case "LineToolCallout":
    case "LineToolTextNote":
    case "LineToolPriceNote": {
      const target = pts.length >= 2 ? p1 : { x: p0.x + 55, y: p0.y - 32, price: p0.price }
      ctx.beginPath()
      ctx.moveTo(p0.x, p0.y)
      ctx.lineTo(target.x, target.y)
      ctx.stroke()
      const label =
        drawing.tool === "LineToolPriceNote"
          ? `${fmtVndShort(p0.price)} ₫`
          : drawing.text || "Vùng chú ý"
      drawRoundedBadge(ctx, target.x, target.y, label, drawing.color)
      break
    }

    case "LineToolPriceLabel": {
      drawRoundedBadge(ctx, p0.x, p0.y - 12, `${fmtVndShort(p0.price)} ₫`, drawing.color)
      break
    }

    case "LineToolFlagMark": {
      ctx.strokeStyle = drawing.color
      ctx.lineWidth = 2
      ctx.beginPath()
      ctx.moveTo(p0.x, p0.y)
      ctx.lineTo(p0.x, p0.y - 22)
      ctx.stroke()
      ctx.fillStyle = drawing.color
      ctx.beginPath()
      ctx.moveTo(p0.x, p0.y - 22)
      ctx.lineTo(p0.x + 15, p0.y - 17)
      ctx.lineTo(p0.x, p0.y - 12)
      ctx.closePath()
      ctx.fill()
      break
    }

    default: {
      if (pts.length >= 2) {
        ctx.beginPath()
        ctx.moveTo(p0.x, p0.y)
        ctx.lineTo(p1.x, p1.y)
        ctx.stroke()
      }
      break
    }
  }

  // Draw TradingView circular anchor handles when selected or hovered
  if ((isSelected || isHovered) && drawing.tool !== "LineToolBrush" && drawing.tool !== "LineToolHighlighter") {
    ctx.setLineDash([])
    for (const pt of pts) {
      ctx.fillStyle = "#FFFFFF"
      ctx.strokeStyle = drawing.color
      ctx.lineWidth = 2
      ctx.beginPath()
      ctx.arc(pt.x, pt.y, 4.5, 0, Math.PI * 2)
      ctx.fill()
      ctx.stroke()
    }
  }

  ctx.restore()
}

function distToSegment(px: number, py: number, x1: number, y1: number, x2: number, y2: number): number {
  const l2 = (x2 - x1) * (x2 - x1) + (y2 - y1) * (y2 - y1)
  if (l2 === 0) return Math.hypot(px - x1, py - y1)
  let t = ((px - x1) * (x2 - x1) + (py - y1) * (y2 - y1)) / l2
  t = Math.max(0, Math.min(1, t))
  return Math.hypot(px - (x1 + t * (x2 - x1)), py - (y1 + t * (y2 - y1)))
}

export function hitTestDrawing(
  mouseX: number,
  mouseY: number,
  drawings: TvDrawingObject[],
  conv: DrawingCoordConverter
): { drawingId: string; pointIndex: number | null } | null {
  for (let i = drawings.length - 1; i >= 0; i--) {
    const d = drawings[i]
    if (!d.visible || d.points.length === 0) continue

    const pts = d.points.map((p: TvDrawingPoint) => ({
      x: conv.barIndexToX(p.barIndex),
      y: conv.priceToY(p.price),
    }))

    // 1. Check anchor handles first
    for (let pIdx = 0; pIdx < pts.length; pIdx++) {
      if (Math.hypot(mouseX - pts[pIdx].x, mouseY - pts[pIdx].y) <= 8) {
        return { drawingId: d.id, pointIndex: pIdx }
      }
    }

    // 2. Check specific geometry
    if (d.tool === "LineToolHorzLine") {
      if (Math.abs(mouseY - pts[0].y) <= 6) return { drawingId: d.id, pointIndex: null }
    } else if (d.tool === "LineToolVertLine") {
      if (Math.abs(mouseX - pts[0].x) <= 6) return { drawingId: d.id, pointIndex: null }
    } else if (d.tool === "LineToolCrossLine") {
      if (Math.abs(mouseY - pts[0].y) <= 6 || Math.abs(mouseX - pts[0].x) <= 6) {
        return { drawingId: d.id, pointIndex: null }
      }
    } else if (pts.length === 1) {
      if (Math.hypot(mouseX - pts[0].x, mouseY - pts[0].y) <= 22) {
        return { drawingId: d.id, pointIndex: null }
      }
    } else if (pts.length >= 2) {
      // Check segments between consecutive points
      for (let k = 0; k < pts.length - 1; k++) {
        if (distToSegment(mouseX, mouseY, pts[k].x, pts[k].y, pts[k + 1].x, pts[k + 1].y) <= 7) {
          return { drawingId: d.id, pointIndex: null }
        }
      }
      // Check bounding box interior for rectangles / Fib / Risk-Reward / Channels
      if (
        d.tool === "LineToolRectangle" ||
        d.tool === "LineToolFibRetracement" ||
        d.tool === "LineToolRiskRewardLong" ||
        d.tool === "LineToolRiskRewardShort" ||
        d.tool === "LineToolDateAndPriceRange" ||
        d.tool === "LineToolFixedRangeVolumeProfile"
      ) {
        const minX = Math.min(pts[0].x, pts[1].x)
        const maxX = Math.max(pts[0].x, pts[1].x)
        const minY = Math.min(pts[0].y, pts[1].y)
        const maxY = Math.max(pts[0].y, pts[1].y)
        if (mouseX >= minX && mouseX <= maxX && mouseY >= minY && mouseY <= maxY) {
          return { drawingId: d.id, pointIndex: null }
        }
      }
    }
  }
  return null
}
