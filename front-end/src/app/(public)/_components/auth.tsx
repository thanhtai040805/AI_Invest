"use client"

import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react"
import { Link, useRouter } from "@/lib/router"
import { Pill } from "@/components/ui"

// ============================================================================
// 1. TYPES & SHARED INTERFACES
// ============================================================================

export type AuthMode = "login" | "signup"

export interface NormalizedAuthError {
  status?: number
  code?: string
  title: string
  message: string
  actionLabel?: string
  actionHref?: string
  isRateLimited?: boolean
  isInvalidCredentials?: boolean
  isConflict?: boolean
  isNetworkError?: boolean
  fieldErrors?: Record<string, string>
}

export interface PasswordStrengthCriterion {
  key: "minLength" | "recommendedLength" | "mixedCase" | "numberOrSymbol"
  label: string
  shortLabel: string
  met: boolean
  required: boolean
}

export interface PasswordStrengthResult {
  score: 0 | 1 | 2 | 3 | 4
  label: string
  tone: "muted" | "loss" | "warning" | "mineral" | "gain"
  meetsMinimum: boolean
  criteria: PasswordStrengthCriterion[]
}

// ============================================================================
// 2. BRAND LOGO (MATCHING LANDING PAGE)
// ============================================================================

export function Logo({ onClick }: { onClick?: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="AIInvest — Về trang chủ"
      className="flex items-center gap-2.5 rounded-[8px] text-left focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-mineral"
    >
      <span className="grid h-8 w-8 place-items-center rounded-[8px] bg-ink font-serif text-[17px] leading-none text-paper">
        A
      </span>
      <span className="text-[17px] font-semibold tracking-tight text-ink">
        AIInvest
      </span>
    </button>
  )
}

// ============================================================================
// 3. FINE-LINE TOPOGRAPHIC & GUILLOCHÉ CANVAS + DAYLIGHT EDITORIAL SHOWCASE
// ============================================================================

const EDITORIAL_PILLARS = [
  {
    num: "12",
    title: "Tác tử chuyên biệt",
    desc: "Phân tích độc lập vĩ mô, chu kỳ ngành và dòng tiền.",
  },
  {
    num: "04",
    title: "Lớp bằng chứng gốc",
    desc: "Đối chiếu trực tiếp từng mục thuyết minh kiểm toán.",
  },
  {
    num: "01",
    title: "Khung rủi ro thống nhất",
    desc: "Xác lập vùng mục tiêu và ngưỡng bảo vệ danh mục.",
  },
] as const

function TopographicGuillocheCanvas() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx =
      canvas.getContext("2d", { alpha: true, desynchronized: true }) ??
      canvas.getContext("2d")
    if (!ctx) return

    let animationFrameId = 0
    let width = 0
    let height = 0
    let dpr = 1
    let canvasLeft = 0
    let canvasTop = 0
    let isPageVisible = !document.hidden
    let typingCooldownUntil = 0
    let pointerActiveUntil = 0
    let lastFrameAt = 0

    const pointer = { x: 0.65, y: 0.55, targetX: 0.65, targetY: 0.55 }

    // Precomputed static lookup tables for Layer 1 (20 rings x 144 angular steps)
    const RING_COUNT = 20
    const RING_STEPS = 144
    const ringVerts = RING_STEPS + 1
    const cosTheta = new Float32Array(ringVerts)
    const sinTheta = new Float32Array(ringVerts)
    const sin5T = new Float32Array(ringVerts)
    const cos5T = new Float32Array(ringVerts)
    const cos9T = new Float32Array(ringVerts)
    const sin9T = new Float32Array(ringVerts)
    const sin3T = new Float32Array(ringVerts)
    const cos3T = new Float32Array(ringVerts)
    for (let s = 0; s <= RING_STEPS; s++) {
      const theta = (s / RING_STEPS) * Math.PI * 2
      cosTheta[s] = Math.cos(theta) * 1.08
      sinTheta[s] = Math.sin(theta) * 0.92
      sin5T[s] = Math.sin(theta * 5)
      cos5T[s] = Math.cos(theta * 5)
      cos9T[s] = Math.cos(theta * 9)
      sin9T[s] = Math.sin(theta * 9)
      sin3T[s] = Math.sin(theta * 3)
      cos3T[s] = Math.cos(theta * 3)
    }

    // Precomputed spatial X lookup tables (rebuilt only on resize -> zero Math.pow in render)
    const X_STEP = 8
    let sampleCount = 0
    let xCoords = new Float32Array(0)
    let goldBaseSpine = new Float32Array(0)
    let mainBaseSpine = new Float32Array(0)
    let cos17 = new Float32Array(0)
    let sin17 = new Float32Array(0)
    let sin21 = new Float32Array(0)
    let cos21 = new Float32Array(0)
    let cos35 = new Float32Array(0)
    let sin35 = new Float32Array(0)
    let sin16 = new Float32Array(0)
    let cos16 = new Float32Array(0)
    let cos22 = new Float32Array(0)
    let sin22 = new Float32Array(0)
    let sin32 = new Float32Array(0)
    let cos32 = new Float32Array(0)
    let cos50 = new Float32Array(0)
    let sin50 = new Float32Array(0)
    let scratchSpine = new Float32Array(0)

    const GOLD_COUNT = 24
    const LINE_COUNT = 46
    const lineSinPi = new Float32Array(LINE_COUNT)
    for (let i = 0; i < LINE_COUNT; i++) {
      lineSinPi[i] = Math.sin((i / (LINE_COUNT - 1)) * Math.PI)
    }

    const rebuildSpatialTables = () => {
      const count = Math.floor((width + 24) / X_STEP) + 2
      sampleCount = count
      xCoords = new Float32Array(count)
      goldBaseSpine = new Float32Array(count)
      mainBaseSpine = new Float32Array(count)
      cos17 = new Float32Array(count)
      sin17 = new Float32Array(count)
      sin21 = new Float32Array(count)
      cos21 = new Float32Array(count)
      cos35 = new Float32Array(count)
      sin35 = new Float32Array(count)
      sin16 = new Float32Array(count)
      cos16 = new Float32Array(count)
      cos22 = new Float32Array(count)
      sin22 = new Float32Array(count)
      sin32 = new Float32Array(count)
      cos32 = new Float32Array(count)
      cos50 = new Float32Array(count)
      sin50 = new Float32Array(count)
      scratchSpine = new Float32Array(count)

      for (let k = 0; k < count; k++) {
        const x = -12 + k * X_STEP
        xCoords[k] = x
        const nx = Math.max(0, x / width)
        goldBaseSpine[k] = height * 0.72 - Math.pow(nx, 1.35) * height * 0.26
        mainBaseSpine[k] = height * 0.7 - Math.pow(nx, 1.4) * height * 0.28

        const a17 = nx * Math.PI * 1.7
        cos17[k] = Math.cos(a17)
        sin17[k] = Math.sin(a17)

        const a21 = nx * Math.PI * 2.1
        sin21[k] = Math.sin(a21)
        cos21[k] = Math.cos(a21)

        const a35 = nx * Math.PI * 3.5
        cos35[k] = Math.cos(a35)
        sin35[k] = Math.sin(a35)

        const a16 = nx * Math.PI * 1.6
        sin16[k] = Math.sin(a16)
        cos16[k] = Math.cos(a16)

        const a22 = nx * Math.PI * 2.2
        cos22[k] = Math.cos(a22)
        sin22[k] = Math.sin(a22)

        const a32 = nx * Math.PI * 3.2
        sin32[k] = Math.sin(a32)
        cos32[k] = Math.cos(a32)

        const a50 = nx * Math.PI * 5.0
        cos50[k] = Math.cos(a50)
        sin50[k] = Math.sin(a50)
      }
    }

    const handleResize = () => {
      const parent = canvas.parentElement
      if (!parent) return
      const rect = parent.getBoundingClientRect()
      canvasLeft = rect.left
      canvasTop = rect.top
      width = Math.max(rect.width, 320)
      height = Math.max(rect.height, 420)
      // Cap DPR at 1.5x: crisp anti-aliased hairlines with 64% fewer pixels than 2.5x
      dpr = Math.min(window.devicePixelRatio || 1, 1.5)
      canvas.width = Math.floor(width * dpr)
      canvas.height = Math.floor(height * dpr)
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`
      rebuildSpatialTables()
    }

    const handleMouseMove = (e: MouseEvent) => {
      if (width <= 0 || height <= 0) return
      const rx = (e.clientX - canvasLeft) / width
      const ry = (e.clientY - canvasTop) / height
      if (rx >= -0.15 && rx <= 1.15 && ry >= -0.1 && ry <= 1.1) {
        pointer.targetX = Math.max(0, Math.min(1, rx))
        pointer.targetY = Math.max(0, Math.min(1, ry))
        pointerActiveUntil = performance.now() + 320
      }
    }

    const handleKeyDown = () => {
      // Yield 100% main-thread frame budget while user types in auth inputs
      typingCooldownUntil = performance.now() + 180
    }

    const handleVisibilityChange = () => {
      isPageVisible = !document.hidden
    }

    handleResize()
    window.addEventListener("resize", handleResize, { passive: true })
    window.addEventListener("mousemove", handleMouseMove, { passive: true })
    window.addEventListener("keydown", handleKeyDown, { passive: true })
    document.addEventListener("visibilitychange", handleVisibilityChange)

    const prefersReducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)"
    ).matches

    const startTime = performance.now()

    const traceRingPath = (rIdx: number, elapsed: number, topoCx: number, topoCy: number, baseRadius: number) => {
      const ru = rIdx / (RING_COUNT - 1)
      const rBase = baseRadius * (0.22 + ru * 1.05)
      const p1 = elapsed * 0.22 + ru * 2.4
      const cP1 = Math.cos(p1) * 14 * ru
      const sP1 = Math.sin(p1) * 14 * ru

      const p2 = elapsed * 0.16 + ru * 3.2
      const amp2 = 7 * (1 - ru * 0.25)
      const cP2 = Math.cos(p2) * amp2
      const sP2 = Math.sin(p2) * amp2

      const p3 = -elapsed * 0.1 + ru * 1.5
      const cP3 = Math.cos(p3) * 10
      const sP3 = Math.sin(p3) * 10

      for (let s = 0; s <= RING_STEPS; s++) {
        const h1 = sin5T[s] * cP1 + cos5T[s] * sP1
        const h2 = cos9T[s] * cP2 + sin9T[s] * sP2
        const h3 = sin3T[s] * cP3 + cos3T[s] * sP3
        const radius = rBase + h1 + h2 + h3
        const px = topoCx + cosTheta[s] * radius
        const py = topoCy + sinTheta[s] * radius
        if (s === 0) ctx.moveTo(px, py)
        else ctx.lineTo(px, py)
      }
      ctx.closePath()
    }

    const render = (now: number) => {
      if (!prefersReducedMotion) {
        animationFrameId = window.requestAnimationFrame(render)
      }
      if (!isPageVisible) return
      if (!prefersReducedMotion && now < typingCooldownUntil) return

      // 60fps when pointer interacts with canvas, 30fps silky ambient drift when idle
      const minInterval = now < pointerActiveUntil ? 15 : 32
      if (!prefersReducedMotion && lastFrameAt > 0 && now - lastFrameAt < minInterval) {
        return
      }
      lastFrameAt = now

      const elapsed = prefersReducedMotion ? 2.8 : (now - startTime) * 0.001

      pointer.x += (pointer.targetX - pointer.x) * 0.05
      pointer.y += (pointer.targetY - pointer.y) * 0.05

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, width, height)

      // ---------------------------------------------------------------------
      // LAYER 1: Batched Upper-Right Topographic Elevation Contours (3 strokes instead of 20)
      // ---------------------------------------------------------------------
      const topoCx = width * 0.86
      const topoCy = height * 0.21
      const baseRadius = Math.min(width, height) * 0.27

      ctx.beginPath()
      for (let rIdx = 0; rIdx < RING_COUNT; rIdx++) {
        if (rIdx % 5 !== 0 && rIdx % 4 !== 0) {
          traceRingPath(rIdx, elapsed, topoCx, topoCy, baseRadius)
        }
      }
      ctx.lineWidth = 0.55
      ctx.strokeStyle = "rgba(63, 98, 89, 0.13)"
      ctx.stroke()

      ctx.beginPath()
      for (let rIdx = 0; rIdx < RING_COUNT; rIdx++) {
        if (rIdx % 4 === 0 && rIdx % 5 !== 0) {
          traceRingPath(rIdx, elapsed, topoCx, topoCy, baseRadius)
        }
      }
      ctx.lineWidth = 0.8
      ctx.strokeStyle = "rgba(63, 98, 89, 0.22)"
      ctx.stroke()

      ctx.beginPath()
      for (let rIdx = 0; rIdx < RING_COUNT; rIdx += 5) {
        traceRingPath(rIdx, elapsed, topoCx, topoCy, baseRadius)
      }
      ctx.lineWidth = 0.85
      ctx.strokeStyle = "rgba(176, 138, 67, 0.19)"
      ctx.stroke()

      // ---------------------------------------------------------------------
      // LAYER 2: Batched Warm-Gold Counter-Harmonic Silk Ribbon (2 strokes instead of 24)
      // ---------------------------------------------------------------------
      const cGoldSpine = Math.cos(elapsed * 0.3) * 32
      const sGoldSpine = Math.sin(elapsed * 0.3) * 32
      for (let k = 0; k < sampleCount; k++) {
        scratchSpine[k] = goldBaseSpine[k] + cos17[k] * cGoldSpine + sin17[k] * sGoldSpine
      }

      const traceGoldLine = (i: number) => {
        const u = i / (GOLD_COUNT - 1)
        const vAmp = (u - 0.5) * 150
        const pTwist = elapsed * 0.24 + u * 1.45
        const cT = Math.cos(pTwist) * vAmp
        const sT = Math.sin(pTwist) * vAmp

        const pRip = -elapsed * 0.36 + u * 3.1
        const cR = Math.cos(pRip) * 18
        const sR = Math.sin(pRip) * 18

        for (let k = 0; k < sampleCount; k++) {
          const y =
            scratchSpine[k] +
            (sin21[k] * cT + cos21[k] * sT) +
            (cos35[k] * cR - sin35[k] * sR)
          if (k === 0) ctx.moveTo(xCoords[k], y)
          else ctx.lineTo(xCoords[k], y)
        }
      }

      ctx.beginPath()
      for (let i = 0; i < GOLD_COUNT; i++) {
        if (i % 5 !== 0) traceGoldLine(i)
      }
      ctx.lineWidth = 0.55
      ctx.strokeStyle = "rgba(176, 138, 67, 0.15)"
      ctx.stroke()

      ctx.beginPath()
      for (let i = 0; i < GOLD_COUNT; i += 5) {
        traceGoldLine(i)
      }
      ctx.lineWidth = 0.9
      ctx.strokeStyle = "rgba(176, 138, 67, 0.26)"
      ctx.stroke()

      // ---------------------------------------------------------------------
      // LAYER 3: Batched Primary Topographic & Guilloché Contour Field (3 strokes instead of 46)
      // ---------------------------------------------------------------------
      const mousePx = pointer.x * width
      const mousePy = pointer.y * height
      const cMainSpine = Math.cos(elapsed * 0.38) * 36
      const sMainSpine = Math.sin(elapsed * 0.38) * 36
      for (let k = 0; k < sampleCount; k++) {
        scratchSpine[k] = mainBaseSpine[k] + (sin16[k] * cMainSpine - cos16[k] * sMainSpine)
      }

      const invBellRadiusSq = 1 / 52000
      const traceMainLine = (i: number) => {
        const u = i / (LINE_COUNT - 1)
        const vAmp = (u - 0.5) * 175
        const liftAmp = 20 * lineSinPi[i]

        const pFold = elapsed * 0.3 + u * 1.3
        const cF = Math.cos(pFold) * vAmp
        const sF = Math.sin(pFold) * vAmp

        const pWave = -elapsed * 0.42 + u * 3.5
        const cW = Math.cos(pWave) * 24
        const sW = Math.sin(pWave) * 24

        const pMicro = elapsed * 0.24 - u * 2.2
        const cM = Math.cos(pMicro) * 7
        const sM = Math.sin(pMicro) * 7

        for (let k = 0; k < sampleCount; k++) {
          const x = xCoords[k]
          const ySpine = scratchSpine[k]
          const dx = x - mousePx
          const dy = ySpine - mousePy
          const distSq = dx * dx + dy * dy
          const mouseLift =
            distSq < 220000 ? (liftAmp / (1 + distSq * invBellRadiusSq)) : 0

          const y =
            ySpine +
            (cos22[k] * cF - sin22[k] * sF) +
            (sin32[k] * cW + cos32[k] * sW) +
            (cos50[k] * cM - sin50[k] * sM) -
            mouseLift

          if (k === 0) ctx.moveTo(x, y)
          else ctx.lineTo(x, y)
        }
      }

      // Soft outer & inner contours batched in 2 alpha bands + 1 crisp index band
      ctx.beginPath()
      for (let i = 0; i < LINE_COUNT; i++) {
        if (i % 6 !== 0 && lineSinPi[i] < 0.65) traceMainLine(i)
      }
      ctx.lineWidth = 0.65
      ctx.strokeStyle = "rgba(63, 98, 89, 0.14)"
      ctx.stroke()

      ctx.beginPath()
      for (let i = 0; i < LINE_COUNT; i++) {
        if (i % 6 !== 0 && lineSinPi[i] >= 0.65) traceMainLine(i)
      }
      ctx.lineWidth = 0.7
      ctx.strokeStyle = "rgba(63, 98, 89, 0.22)"
      ctx.stroke()

      ctx.beginPath()
      for (let i = 0; i < LINE_COUNT; i += 6) {
        traceMainLine(i)
      }
      ctx.lineWidth = 1.05
      ctx.strokeStyle = "rgba(63, 98, 89, 0.38)"
      ctx.stroke()
    }

    animationFrameId = window.requestAnimationFrame(render)

    return () => {
      window.cancelAnimationFrame(animationFrameId)
      window.removeEventListener("resize", handleResize)
      window.removeEventListener("mousemove", handleMouseMove)
      window.removeEventListener("keydown", handleKeyDown)
      document.removeEventListener("visibilitychange", handleVisibilityChange)
    }
  }, [])

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 z-0 h-full w-full will-change-transform [contain:strict]"
    />
  )
}

export function AuthStageShowReel() {
  return (
    <div className="relative flex h-full min-h-0 flex-col justify-between">
      {/* Top Editorial Classification */}
      <div className="flex items-center justify-between border-b border-line pb-3">
        <Pill tone="mineral">Trí tuệ nhân tạo đầu tư chứng khoán · Việt Nam</Pill>
        <span className="font-mono text-[11px] tracking-[0.08em] text-secondary">
          HOSE · HNX · UPCOM
        </span>
      </div>

      {/* Center Editorial Narrative (Zero Sliding Content, 100% Symmetrical & Laptop Viewport-Fit) */}
      <div className="my-auto py-2 xl:py-4">
        <div className="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-muted">
          Không gian nghiên cứu định lượng
        </div>

        <h2 className="mt-2 font-serif text-[clamp(26px,2.35vw,38px)] leading-[1.12] tracking-[-0.025em] text-ink">
          Khoảng lặng lý tính giữa tín hiệu
          <br />
          <span className="text-teal">và quyết định đầu tư.</span>
        </h2>

        <p className="mt-2.5 max-w-lg text-[13.5px] leading-relaxed text-secondary xl:text-[14.5px]">
          AIInvest hợp nhất bối cảnh vĩ mô, dữ liệu cổ phiếu chuẩn hóa, phân tích đa tác tử và trích dẫn báo cáo tài chính nguyên bản trong một mặt phẳng làm việc tĩnh tại.
        </p>

        {/* Crisp Opaque Editorial Plate (Zero GPU backdrop-blur readback over Canvas) */}
        <div className="mt-5 rounded-[14px] border border-line-strong bg-surface/96 p-4 shadow-[0_18px_44px_rgba(24,32,29,0.06)] xl:mt-7 xl:p-5.5">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-2.5">
            <div className="flex items-center gap-2">
              <span className="font-mono text-[12.5px] font-semibold text-ink">
                HPG · Luận điểm mẫu
              </span>
              <span className="text-[12px] text-secondary">
                Tập đoàn Hòa Phát
              </span>
            </div>
            <Pill tone="neutral">BCTC Kiểm toán · Thuyết minh 14.2</Pill>
          </div>

          <blockquote className="mt-3 border-l-2 border-gold pl-3.5 font-serif text-[14.5px] leading-relaxed text-ink xl:text-[16px]">
            “Điều quan trọng không phải câu trả lời nhanh hơn, mà là một luận điểm đầu tư bạn có thể tự mình kiểm chứng đến tận tài liệu gốc.”
          </blockquote>

          {/* 3 Strictly Symmetrical, Equal-Height Pillars */}
          <div className="mt-3.5 grid grid-cols-3 divide-x divide-line border-t border-line pt-3">
            {EDITORIAL_PILLARS.map((pillar) => (
              <div
                key={pillar.title}
                className="group relative flex flex-col justify-between px-3 first:pl-0 last:pr-0 transition-colors duration-300"
              >
                <div>
                  <div className="font-mono text-[18px] font-semibold leading-none text-ink tnum transition-colors duration-300 group-hover:text-teal xl:text-[20px]">
                    {pillar.num}
                  </div>
                  <div className="mt-1 text-[11.5px] font-semibold text-ink">
                    {pillar.title}
                  </div>
                </div>
                <p className="mt-0.5 text-[10.5px] leading-[1.4] text-secondary xl:text-[11px]">
                  {pillar.desc}
                </p>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Bottom Footnote */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-t border-line pt-3 text-[11.5px] text-secondary">
        <span>Công cụ hỗ trợ nghiên cứu độc lập</span>
        <span>Không lưu ký tài sản · Không đặt lệnh hộ</span>
      </div>
    </div>
  )
}

// ============================================================================
// 4. DAYLIGHT EDITORIAL SPLIT-SCREEN SHELL
// ============================================================================

export function AuthShell({
  mode,
  title,
  sub,
  children,
  footer,
  email,
  nextParam,
}: {
  mode: AuthMode
  title: string
  sub: string
  children: ReactNode
  footer?: ReactNode
  email?: string
  nextParam?: string | null
}) {
  const { navigate } = useRouter()
  const loginHref = buildAuthHref("/login", { email, next: nextParam })
  const signupHref = buildAuthHref("/signup", { email, next: nextParam })

  return (
    <div className="min-h-dvh w-full bg-surface text-ink lg:grid lg:h-dvh lg:max-h-dvh lg:grid-cols-[minmax(420px,43%)_1fr] lg:overflow-hidden">
      {/* Skip link for keyboard accessibility */}
      <a
        href="#auth-main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-[8px] focus:bg-ink focus:px-4 focus:py-2.5 focus:text-[13px] focus:font-medium focus:text-paper focus:shadow-lg"
      >
        Bỏ qua đến biểu mẫu xác thực
      </a>

      {/* =====================================================================
          LEFT COLUMN: Pure Surface Form Plane (`bg-surface`)
      ===================================================================== */}
      <main
        id="auth-main"
        className="flex min-h-dvh flex-col justify-between bg-surface px-6 py-4 sm:px-10 lg:h-dvh lg:min-h-0 lg:overflow-y-auto lg:px-12 lg:py-5 xl:px-18 xl:py-6"
      >
        {/* Top Header */}
        <header className="flex shrink-0 items-center justify-between">
          <Logo onClick={() => navigate("/")} />
          <button
            type="button"
            onClick={() => navigate("/")}
            className="inline-flex items-center gap-1.5 rounded-[7px] px-2.5 py-1 text-[12.5px] font-medium text-secondary transition-colors hover:bg-soft hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-mineral"
          >
            <span aria-hidden="true">←</span>
            <span>Trang chủ</span>
          </button>
        </header>

        {/* Center Form */}
        <div className="mx-auto my-auto w-full max-w-[380px] py-2 xl:py-4">
          {/* Subtle Mode Switcher */}
          <nav
            aria-label="Chuyển đổi chế độ xác thực"
            className="inline-flex items-center rounded-[8px] border border-line bg-paper p-0.5"
          >
            <button
              type="button"
              aria-current={mode === "login" ? "page" : undefined}
              onClick={() => {
                if (mode !== "login") navigate(loginHref)
              }}
              className={`rounded-[6px] px-3 py-1 text-[12px] font-medium transition-all focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-mineral ${
                mode === "login"
                  ? "bg-surface text-ink shadow-xs"
                  : "text-secondary hover:text-ink"
              }`}
            >
              Đăng nhập
            </button>
            <button
              type="button"
              aria-current={mode === "signup" ? "page" : undefined}
              onClick={() => {
                if (mode !== "signup") navigate(signupHref)
              }}
              className={`rounded-[6px] px-3 py-1 text-[12px] font-medium transition-all focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-mineral ${
                mode === "signup"
                  ? "bg-surface text-ink shadow-xs"
                  : "text-secondary hover:text-ink"
              }`}
            >
              Tạo tài khoản
            </button>
          </nav>

          {/* Editorial Heading */}
          <div className="mt-3.5 xl:mt-4">
            <h1 className="font-serif text-[27px] font-medium leading-[1.12] tracking-[-0.025em] text-ink xl:text-[31px]">
              {title}
            </h1>
            <p className="mt-1.5 text-[13px] leading-snug text-secondary xl:text-[13.5px]">
              {sub}
            </p>
          </div>

          {/* Form Content */}
          <div className="mt-4 xl:mt-5">{children}</div>

          {/* Footer Switch Link */}
          {footer && (
            <div className="mt-3.5 border-t border-line pt-3 text-[12.5px] text-secondary xl:mt-4 xl:pt-3.5">
              {footer}
            </div>
          )}
        </div>

        {/* Bottom Legal Line */}
        <footer className="flex shrink-0 flex-wrap items-center justify-between gap-2 text-[11px] text-muted">
          <span>© 2026 AIInvest</span>
          <span>Bảo mật TLS 1.3 · Dữ liệu nghiên cứu độc lập</span>
        </footer>
      </main>

      {/* =====================================================================
          RIGHT COLUMN: Warm Paper Editorial Canvas + Fine-Line Guilloché Art
      ===================================================================== */}
      <aside
        aria-label="Giới thiệu nền tảng phân tích định lượng AIInvest"
        className="relative hidden overflow-hidden border-l border-line bg-paper px-9 py-5 lg:flex lg:h-dvh lg:min-h-0 xl:px-14 xl:py-7"
      >
        {/* Exact 58px Architectural Grid from `page.tsx` */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 opacity-50"
          style={{
            backgroundImage:
              "linear-gradient(var(--color-line) 1px,transparent 1px),linear-gradient(90deg,var(--color-line) 1px,transparent 1px)",
            backgroundSize: "58px 58px",
            maskImage:
              "radial-gradient(ellipse 85% 75% at 55% 48%,black,transparent)",
          }}
        />

        {/* Batched Zero-Lag Fine-Line Topographic & Guilloché Wave Canvas */}
        <TopographicGuillocheCanvas />

        <div className="relative z-10 mx-auto flex h-full min-h-0 w-full max-w-[580px] flex-col">
          <AuthStageShowReel />
        </div>
      </aside>
    </div>
  )
}

// ============================================================================
// 5. FORM PRIMITIVES (FIELD, PASSWORD FIELD, STRENGTH METER, ALERT)
// ============================================================================

export interface FieldProps {
  id?: string
  name?: string
  label: string
  type?: string
  placeholder?: string
  value: string
  onChange: (v: string) => void
  onBlur?: () => void
  error?: string
  hint?: string
  autoComplete?: string
  inputMode?: "text" | "email" | "tel" | "url" | "numeric" | "decimal"
  disabled?: boolean
  required?: boolean
  rightLabel?: ReactNode
}

export function Field({
  id: providedId,
  name,
  label,
  type = "text",
  placeholder,
  value,
  onChange,
  onBlur,
  error,
  hint,
  autoComplete,
  inputMode,
  disabled = false,
  required = true,
  rightLabel,
}: FieldProps) {
  const generatedId = useId()
  const inputId = providedId ?? `field-${generatedId}`
  const errorId = `${inputId}-error`
  const hintId = `${inputId}-hint`

  const describedBy =
    [error ? errorId : null, hint ? hintId : null].filter(Boolean).join(" ") ||
    undefined

  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <label
          htmlFor={inputId}
          className="block text-[12.5px] font-medium text-ink"
        >
          {label}
        </label>
        {rightLabel}
      </div>

      <input
        id={inputId}
        name={name}
        type={type}
        value={value}
        placeholder={placeholder}
        autoComplete={autoComplete}
        inputMode={inputMode}
        disabled={disabled}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        className={`mt-1 h-10 w-full rounded-[8px] border bg-surface px-3.5 text-[13.5px] text-ink outline-none transition-all duration-150 placeholder:text-disabled disabled:cursor-not-allowed disabled:bg-soft disabled:text-secondary ${
          error
            ? "border-loss focus:border-loss focus:ring-3 focus:ring-loss/12"
            : "border-line-strong hover:border-ink/35 focus:border-mineral focus:ring-3 focus:ring-mineral/12"
        }`}
      />

      {error && (
        <p
          id={errorId}
          role="alert"
          className="mt-1 flex items-center gap-1.5 text-[11.5px] font-medium text-loss"
        >
          <span aria-hidden="true">●</span>
          <span>{error}</span>
        </p>
      )}

      {!error && hint && (
        <p id={hintId} className="mt-1 text-[11.5px] text-secondary">
          {hint}
        </p>
      )}
    </div>
  )
}

export interface PasswordFieldProps {
  id?: string
  name?: string
  label: string
  placeholder?: string
  value: string
  onChange: (v: string) => void
  onBlur?: () => void
  error?: string
  hint?: string
  autoComplete?: "current-password" | "new-password"
  disabled?: boolean
  required?: boolean
  describedByIds?: string
  rightLabel?: ReactNode
}

export function PasswordField({
  id: providedId,
  name = "password",
  label,
  placeholder = "Nhập mật khẩu của bạn",
  value,
  onChange,
  onBlur,
  error,
  hint,
  autoComplete = "current-password",
  disabled = false,
  required = true,
  describedByIds,
  rightLabel,
}: PasswordFieldProps) {
  const generatedId = useId()
  const inputId = providedId ?? `password-${generatedId}`
  const errorId = `${inputId}-error`
  const hintId = `${inputId}-hint`
  const capsId = `${inputId}-capslock`

  const [revealed, setRevealed] = useState(false)
  const [capsLockOn, setCapsLockOn] = useState(false)

  const handleKeyEvent = (e: KeyboardEvent<HTMLInputElement>) => {
    if (typeof e.getModifierState === "function") {
      setCapsLockOn(e.getModifierState("CapsLock"))
    }
  }

  const describedBy =
    [
      error ? errorId : null,
      hint ? hintId : null,
      capsLockOn ? capsId : null,
      describedByIds ?? null,
    ]
      .filter(Boolean)
      .join(" ") || undefined

  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <label
          htmlFor={inputId}
          className="block text-[12.5px] font-medium text-ink"
        >
          {label}
        </label>
        {rightLabel}
      </div>

      <div className="relative mt-1">
        <input
          id={inputId}
          name={name}
          type={revealed ? "text" : "password"}
          value={value}
          placeholder={placeholder}
          autoComplete={autoComplete}
          disabled={disabled}
          required={required}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          onChange={(e) => onChange(e.target.value)}
          onBlur={() => {
            setCapsLockOn(false)
            onBlur?.()
          }}
          onKeyDown={handleKeyEvent}
          onKeyUp={handleKeyEvent}
          className={`h-10 w-full rounded-[8px] border bg-surface pl-3.5 pr-10 text-[13.5px] text-ink outline-none transition-all duration-150 placeholder:text-disabled disabled:cursor-not-allowed disabled:bg-soft disabled:text-secondary ${
            error
              ? "border-loss focus:border-loss focus:ring-3 focus:ring-loss/12"
              : "border-line-strong hover:border-ink/35 focus:border-mineral focus:ring-3 focus:ring-mineral/12"
          }`}
        />

        <button
          type="button"
          disabled={disabled}
          onClick={() => setRevealed((r) => !r)}
          aria-label={revealed ? "Ẩn mật khẩu" : "Hiện mật khẩu"}
          aria-pressed={revealed}
          className="absolute right-1.5 top-1/2 -translate-y-1/2 grid h-8 w-8 place-items-center rounded-[6px] text-secondary transition-colors hover:bg-soft hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-mineral disabled:opacity-50"
        >
          {revealed ? (
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
              <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
              <line x1="1" y1="1" x2="23" y2="23" />
            </svg>
          ) : (
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
              <circle cx="12" cy="12" r="3" />
            </svg>
          )}
        </button>
      </div>

      {capsLockOn && (
        <p
          id={capsId}
          role="status"
          aria-live="polite"
          className="mt-1.5 flex items-center gap-1.5 text-[12px] font-medium text-warning"
        >
          <span aria-hidden="true">▲</span>
          <span>Phím Caps Lock đang bật</span>
        </p>
      )}

      {error && (
        <p
          id={errorId}
          role="alert"
          className="mt-1.5 flex items-center gap-1.5 text-[12px] font-medium text-loss"
        >
          <span aria-hidden="true">●</span>
          <span>{error}</span>
        </p>
      )}

      {!error && hint && (
        <p id={hintId} className="mt-1.5 text-[12px] text-secondary">
          {hint}
        </p>
      )}
    </div>
  )
}

export function evaluatePasswordStrength(password: string): PasswordStrengthResult {
  const minLength = password.length >= 6
  const recommendedLength = password.length >= 10
  const mixedCase = /[a-z]/.test(password) && /[A-Z]/.test(password)
  const numberOrSymbol = /\d/.test(password) || /[^A-Za-z0-9]/.test(password)

  const criteria: PasswordStrengthCriterion[] = [
    {
      key: "minLength",
      label: "Tối thiểu 6 ký tự",
      shortLabel: "6+ ký tự",
      met: minLength,
      required: true,
    },
    {
      key: "recommendedLength",
      label: "Từ 10 ký tự trở lên",
      shortLabel: "10+ ký tự",
      met: recommendedLength,
      required: false,
    },
    {
      key: "mixedCase",
      label: "Chữ hoa & chữ thường",
      shortLabel: "Hoa & thường",
      met: mixedCase,
      required: false,
    },
    {
      key: "numberOrSymbol",
      label: "Số hoặc ký tự đặc biệt",
      shortLabel: "Số / Ký tự",
      met: numberOrSymbol,
      required: false,
    },
  ]

  if (!password) {
    return {
      score: 0,
      label: "Chưa nhập mật khẩu",
      tone: "muted",
      meetsMinimum: false,
      criteria,
    }
  }

  if (!minLength) {
    return {
      score: 1,
      label: "Cần ít nhất 6 ký tự",
      tone: "loss",
      meetsMinimum: false,
      criteria,
    }
  }

  const bonusCount = [recommendedLength, mixedCase, numberOrSymbol].filter(Boolean).length
  const rawScore = (1 + bonusCount) as 1 | 2 | 3 | 4

  const scoreMap: Record<
    1 | 2 | 3 | 4,
    { label: string; tone: PasswordStrengthResult["tone"] }
  > = {
    1: { label: "Đạt mức cơ bản", tone: "warning" },
    2: { label: "Trung bình", tone: "warning" },
    3: { label: "Khá mạnh", tone: "mineral" },
    4: { label: "Rất mạnh", tone: "gain" },
  }

  return {
    score: rawScore,
    label: scoreMap[rawScore].label,
    tone: scoreMap[rawScore].tone,
    meetsMinimum: true,
    criteria,
  }
}

export function PasswordStrengthMeter({
  password,
  id,
}: {
  password: string
  id?: string
}) {
  const result = evaluatePasswordStrength(password)

  if (!password) {
    return <div id={id} className="sr-only" aria-live="polite" />
  }

  const activeBarClass =
    result.score === 1
      ? "bg-loss"
      : result.score === 2
        ? "bg-warning"
        : result.score === 3
          ? "bg-mineral"
          : "bg-gain"

  const textToneClass =
    result.tone === "loss"
      ? "text-loss"
      : result.tone === "warning"
        ? "text-warning"
        : result.tone === "mineral"
          ? "text-mineral"
          : "text-gain"

  return (
    <div id={id} className="mt-1.5 space-y-1" aria-live="polite">
      <div className="grid grid-cols-4 gap-1.5" aria-hidden="true">
        {[1, 2, 3, 4].map((level) => (
          <div
            key={level}
            className={`h-1 rounded-full transition-colors duration-200 ${
              result.score >= level ? activeBarClass : "bg-line"
            }`}
          />
        ))}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
        <div className="flex flex-wrap items-center gap-2 text-secondary">
          {result.criteria
            .filter((c) => c.key !== "recommendedLength")
            .map((c) => (
              <span
                key={c.key}
                className={`inline-flex items-center gap-1 transition-colors ${
                  c.met ? "font-medium text-teal" : "text-secondary"
                }`}
              >
                <span aria-hidden="true">{c.met ? "✓" : "·"}</span>
                <span>{c.shortLabel}</span>
              </span>
            ))}
        </div>
        <span className={`font-medium ${textToneClass}`}>{result.label}</span>
      </div>
    </div>
  )
}

export function AuthAlert({
  title,
  message,
  tone = "error",
  actionLabel,
  onAction,
}: {
  title: string
  message: string
  tone?: "error" | "warning" | "info" | "success"
  actionLabel?: string
  onAction?: () => void
}) {
  const styles = {
    error: "border-loss/30 bg-loss/[0.06] text-ink",
    warning: "border-warning/35 bg-warning/[0.07] text-ink",
    info: "border-mineral/30 bg-mineral/[0.06] text-ink",
    success: "border-gain/30 bg-gain/[0.06] text-ink",
  }[tone]

  const badgeTone = {
    error: "text-loss",
    warning: "text-warning",
    info: "text-mineral",
    success: "text-gain",
  }[tone]

  return (
    <div
      role="alert"
      aria-live="assertive"
      className={`rounded-[10px] border p-3.5 text-[13px] ${styles}`}
    >
      <div className="flex items-start gap-2.5">
        <span className={`mt-0.5 font-mono text-[13px] font-bold ${badgeTone}`} aria-hidden="true">
          !
        </span>
        <div className="min-w-0 flex-1">
          <div className="font-semibold text-ink">{title}</div>
          <p className="mt-0.5 text-[12.5px] leading-relaxed text-secondary">
            {message}
          </p>
          {actionLabel && onAction && (
            <button
              type="button"
              onClick={onAction}
              className="mt-2 inline-flex items-center gap-1 text-[12.5px] font-semibold text-mineral underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-mineral"
            >
              <span>{actionLabel}</span>
              <span aria-hidden="true">→</span>
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ============================================================================
// 5B. GOOGLE OAUTH BUTTON, EDITORIAL DIVIDER & 6-DIGIT OTP CODE INPUT
// ============================================================================

export function AuthDivider({ label = "hoặc tiếp tục với email" }: { label?: string }) {
  return (
    <div className="relative flex items-center py-0.5" aria-hidden="true">
      <div className="flex-grow border-t border-line" />
      <span className="mx-3 shrink-0 text-[11px] font-medium text-secondary">
        {label}
      </span>
      <div className="flex-grow border-t border-line" />
    </div>
  )
}

const DEV_GOOGLE_ACCOUNTS = [
  {
    email: "minhquan.nguyen@gmail.com",
    displayName: "Nguyễn Minh Quân",
    role: "Nhà đầu tư Cá nhân · HOSE/HNX",
    initials: "MQ",
    avatarTone: "bg-[#19362E] text-[#F5F6F3]",
  },
  {
    email: "quocbao.research@gmail.com",
    displayName: "Trần Quốc Bảo",
    role: "Chuyên viên Phân tích Định lượng",
    initials: "QB",
    avatarTone: "bg-[#2C4C43] text-[#F5F6F3]",
  },
] as const

const GOOGLE_OAUTH_CLIENT_ID =
  process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID ||
  "129415586818-d5ltg3s4h4oev29caea6bi7elh8mu31v.apps.googleusercontent.com"

type GoogleOAuthWindow = {
  google?: {
    accounts?: {
      oauth2?: {
        initTokenClient: (config: {
          client_id: string
          scope: string
          prompt?: string
          callback: (response: {
            access_token?: string
            error?: string
          }) => void | Promise<void>
          error_callback?: (error: { type?: string; message?: string }) => void
        }) => { requestAccessToken: (overrideConfig?: { prompt?: string }) => void }
      }
    }
  }
}

export function GoogleAuthButton({
  mode,
  disabled = false,
  defaultEmail = "",
  onAuthenticate,
}: {
  mode: AuthMode
  disabled?: boolean
  defaultEmail?: string
  onAuthenticate: (payload: {
    credential?: string
    accessToken?: string
    email?: string
    displayName?: string
  }) => Promise<void>
}) {
  const [modalOpen, setModalOpen] = useState(false)
  const [selectedIdx, setSelectedIdx] = useState(0)
  const [customMode, setCustomMode] = useState(false)
  const [customEmail, setCustomEmail] = useState(defaultEmail)
  const [customName, setCustomName] = useState("")
  const [customError, setCustomError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const selectedAccount = DEV_GOOGLE_ACCOUNTS[selectedIdx] ?? DEV_GOOGLE_ACCOUNTS[0]

  useEffect(() => {
    if (typeof window === "undefined") return
    const existing = document.getElementById("google-gsi-client-script")
    if (existing) return
    const script = document.createElement("script")
    script.id = "google-gsi-client-script"
    script.src = "https://accounts.google.com/gsi/client"
    script.async = true
    script.defer = true
    document.head.appendChild(script)
  }, [])

  const launchNativeGooglePopup = (): boolean => {
    const win =
      typeof window !== "undefined"
        ? (window as unknown as GoogleOAuthWindow)
        : null

    if (GOOGLE_OAUTH_CLIENT_ID && win?.google?.accounts?.oauth2) {
      setLoading(true)
      const tokenClient = win.google.accounts.oauth2.initTokenClient({
        client_id: GOOGLE_OAUTH_CLIENT_ID,
        scope: "email profile openid",
        prompt: "select_account",
        callback: async (response: { access_token?: string; error?: string }) => {
          if (response?.access_token) {
            try {
              await onAuthenticate({ accessToken: response.access_token })
            } finally {
              setLoading(false)
            }
          } else {
            setLoading(false)
          }
        },
        error_callback: () => {
          setLoading(false)
        },
      })
      tokenClient.requestAccessToken({ prompt: "select_account" })
      return true
    }
    return false
  }

  const handleGoogleClick = () => {
    if (disabled || loading) return

    if (launchNativeGooglePopup()) {
      return
    }

    setCustomEmail(defaultEmail || "")
    setCustomError(null)
    setCustomMode(false)
    setSelectedIdx(0)
    setModalOpen(true)
  }

  const handleSelectAccount = async (accEmail: string, accName: string) => {
    if (loading) return
    setLoading(true)
    setCustomError(null)
    try {
      await onAuthenticate({ email: accEmail, displayName: accName })
      setModalOpen(false)
    } catch {
      setLoading(false)
    }
  }

  const handleCustomSubmit = async () => {
    const err = validateEmail(customEmail)
    if (err) {
      setCustomError(err)
      return
    }
    const cleanEmail = customEmail.trim().toLowerCase()
    const cleanName =
      customName.trim().replace(/\s+/g, " ") || cleanEmail.split("@")[0]
    await handleSelectAccount(cleanEmail, cleanName)
  }

  return (
    <>
      <button
        type="button"
        disabled={disabled || loading}
        onClick={handleGoogleClick}
        className="flex h-10 w-full items-center justify-center gap-2.5 rounded-[9px] border border-line-strong bg-surface px-4 text-[13px] font-medium text-ink shadow-2xs transition-all hover:border-ink/40 hover:bg-paper active:scale-[0.997] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-mineral disabled:cursor-not-allowed disabled:opacity-60"
      >
        {loading ? (
          <span
            aria-hidden="true"
            className="h-4 w-4 animate-spin rounded-full border-2 border-ink/25 border-t-ink"
          />
        ) : (
          <svg className="h-[18px] w-[18px] shrink-0" viewBox="0 0 24 24" aria-hidden="true">
            <path
              fill="#4285F4"
              d="M23.49 12.275c0-.85-.075-1.675-.215-2.475H12v4.69h6.45c-.28 1.5-1.125 2.775-2.395 3.63v3.015h3.875c2.265-2.085 3.56-5.16 3.56-8.86z"
            />
            <path
              fill="#34A853"
              d="M12 24c3.24 0 5.955-1.07 7.94-2.91l-3.875-3.015c-1.075.72-2.45 1.15-4.065 1.15-3.125 0-5.77-2.11-6.715-4.945H1.28v3.11C3.255 21.315 7.31 24 12 24z"
            />
            <path
              fill="#FBBC05"
              d="M5.285 14.28A7.23 7.23 0 0 1 4.91 12c0-.795.14-1.565.375-2.28V6.61H1.28A11.99 11.99 0 0 0 0 12c0 1.935.465 3.765 1.28 5.39l4.005-3.11z"
            />
            <path
              fill="#EA4335"
              d="M12 4.775c1.765 0 3.345.605 4.59 1.795l3.44-3.44C17.95 1.19 15.235 0 12 0 7.31 0 3.255 2.685 1.28 6.61l4.005 3.11C6.23 6.885 8.875 4.775 12 4.775z"
            />
          </svg>
        )}
        <span>
          {mode === "login" ? "Tiếp tục với Google" : "Đăng ký nhanh với Google"}
        </span>
      </button>

      {modalOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="google-chooser-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-ink/45 p-4 backdrop-blur-[3px]"
          onClick={(e) => {
            if (e.target === e.currentTarget && !loading) setModalOpen(false)
          }}
        >
          <div className="w-full max-w-[780px] overflow-hidden rounded-[20px] border border-line-strong bg-surface shadow-[0_32px_90px_rgba(18,26,23,0.28)]">
            {/* Top OAuth Browser/Identity Header */}
            <div className="flex items-center justify-between border-b border-line bg-paper px-6 py-3.5">
              <div className="flex items-center gap-2.5">
                <svg className="h-[18px] w-[18px]" viewBox="0 0 24 24" aria-hidden="true">
                  <path
                    fill="#4285F4"
                    d="M23.49 12.275c0-.85-.075-1.675-.215-2.475H12v4.69h6.45c-.28 1.5-1.125 2.775-2.395 3.63v3.015h3.875c2.265-2.085 3.56-5.16 3.56-8.86z"
                  />
                  <path
                    fill="#34A853"
                    d="M12 24c3.24 0 5.955-1.07 7.94-2.91l-3.875-3.015c-1.075.72-2.45 1.15-4.065 1.15-3.125 0-5.77-2.11-6.715-4.945H1.28v3.11C3.255 21.315 7.31 24 12 24z"
                  />
                  <path
                    fill="#FBBC05"
                    d="M5.285 14.28A7.23 7.23 0 0 1 4.91 12c0-.795.14-1.565.375-2.28V6.61H1.28A11.99 11.99 0 0 0 0 12c0 1.935.465 3.765 1.28 5.39l4.005-3.11z"
                  />
                  <path
                    fill="#EA4335"
                    d="M12 4.775c1.765 0 3.345.605 4.59 1.795l3.44-3.44C17.95 1.19 15.235 0 12 0 7.31 0 3.255 2.685 1.28 6.61l4.005 3.11C6.23 6.885 8.875 4.775 12 4.775z"
                  />
                </svg>
                <span className="text-[13px] font-semibold tracking-[-0.01em] text-ink">
                  Đăng nhập bằng Google
                </span>
              </div>

              <div className="flex items-center gap-3">
                <span className="hidden items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-1 font-mono text-[11px] text-secondary sm:inline-flex">
                  <svg className="h-3 w-3 text-mineral" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                    <path
                      d="M4.5 7V5a3.5 3.5 0 0 1 7 0v2M3.5 7h9a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1Z"
                      stroke="currentColor"
                      strokeWidth="1.3"
                      strokeLinecap="round"
                    />
                  </svg>
                  <span>accounts.google.com · OAuth 2.0</span>
                </span>

                <button
                  type="button"
                  disabled={loading}
                  onClick={() => setModalOpen(false)}
                  aria-label="Đóng cửa sổ"
                  className="grid h-7 w-7 place-items-center rounded-[7px] text-[13px] text-secondary transition-colors hover:bg-soft hover:text-ink"
                >
                  ✕
                </button>
              </div>
            </div>

            {/* 2-Column Editorial & Material 3 Split Architecture */}
            <div className="grid grid-cols-1 md:grid-cols-12">
              {/* Left Column: Brand Handshake & OAuth Scope Guarantee */}
              <div className="flex flex-col justify-between border-b border-line bg-paper/60 p-6 md:col-span-5 md:border-r md:border-b-0 md:p-7">
                <div>
                  {/* Dual-Brand Handshake Emblem */}
                  <div className="flex items-center gap-2.5">
                    <span className="grid h-11 w-11 place-items-center rounded-[12px] bg-teal font-serif text-[16px] font-semibold tracking-tight text-paper shadow-2xs">
                      AI
                    </span>
                    <span className="flex items-center gap-1 text-muted" aria-hidden="true">
                      <span className="h-[1px] w-2.5 bg-line-strong" />
                      <svg className="h-3.5 w-3.5 text-mineral" viewBox="0 0 16 16" fill="none">
                        <path
                          d="M3 6h10l-2.5-2.5M13 10H3l2.5 2.5"
                          stroke="currentColor"
                          strokeWidth="1.3"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      </svg>
                      <span className="h-[1px] w-2.5 bg-line-strong" />
                    </span>
                    <span className="grid h-11 w-11 place-items-center rounded-[12px] border border-line-strong bg-surface shadow-2xs">
                      <svg className="h-5 w-5" viewBox="0 0 24 24" aria-hidden="true">
                        <path
                          fill="#4285F4"
                          d="M23.49 12.275c0-.85-.075-1.675-.215-2.475H12v4.69h6.45c-.28 1.5-1.125 2.775-2.395 3.63v3.015h3.875c2.265-2.085 3.56-5.16 3.56-8.86z"
                        />
                        <path
                          fill="#34A853"
                          d="M12 24c3.24 0 5.955-1.07 7.94-2.91l-3.875-3.015c-1.075.72-2.45 1.15-4.065 1.15-3.125 0-5.77-2.11-6.715-4.945H1.28v3.11C3.255 21.315 7.31 24 12 24z"
                        />
                        <path
                          fill="#FBBC05"
                          d="M5.285 14.28A7.23 7.23 0 0 1 4.91 12c0-.795.14-1.565.375-2.28V6.61H1.28A11.99 11.99 0 0 0 0 12c0 1.935.465 3.765 1.28 5.39l4.005-3.11z"
                        />
                        <path
                          fill="#EA4335"
                          d="M12 4.775c1.765 0 3.345.605 4.59 1.795l3.44-3.44C17.95 1.19 15.235 0 12 0 7.31 0 3.255 2.685 1.28 6.61l4.005 3.11C6.23 6.885 8.875 4.775 12 4.775z"
                        />
                      </svg>
                    </span>
                  </div>

                  <h3
                    id="google-chooser-title"
                    className="mt-5 font-serif text-[27px] leading-[1.16] tracking-[-0.02em] text-ink"
                  >
                    {!customMode ? "Chọn một tài khoản" : "Đăng nhập Google"}
                  </h3>
                  <p className="mt-2 text-[13.5px] leading-[1.55] text-secondary">
                    để tiếp tục tới{" "}
                    <span className="font-semibold text-ink">AIInvest</span>{" "}
                    — Không gian Nghiên cứu &amp; Phân tích Định lượng.
                  </p>
                </div>

                {/* Scope & Trust Guarantee Box */}
                <div className="mt-6 rounded-[12px] border border-line bg-surface p-4 shadow-2xs">
                  <p className="font-mono text-[10.5px] font-semibold tracking-[0.08em] text-mineral uppercase">
                    Cơ chế bảo mật OAuth 2.0
                  </p>
                  <ul className="mt-2.5 space-y-2 text-[12px] leading-[1.45] text-secondary">
                    <li className="flex items-start gap-2">
                      <span className="mt-0.5 text-mineral" aria-hidden="true">✓</span>
                      <span>Đồng bộ họ tên, địa chỉ email đã xác minh và ảnh đại diện.</span>
                    </li>
                    <li className="flex items-start gap-2">
                      <span className="mt-0.5 text-mineral" aria-hidden="true">✓</span>
                      <span>AIInvest không bao giờ truy cập mật khẩu Google của bạn.</span>
                    </li>
                    <li className="flex items-start gap-2">
                      <span className="mt-0.5 text-mineral" aria-hidden="true">✓</span>
                      <span>Có thể thu hồi quyền kết nối bất kỳ lúc nào trong Google Account.</span>
                    </li>
                  </ul>
                </div>
              </div>

              {/* Right Column: Account List / Custom Form + Disclosure + Action Bar */}
              <div className="flex flex-col justify-between bg-surface p-6 md:col-span-7 md:p-7">
                {!customMode ? (
                  <>
                    <div>
                      <div className="mb-2.5 flex items-center justify-between">
                        <span className="font-mono text-[11px] font-medium tracking-[0.06em] text-secondary uppercase">
                          Tài khoản đã đăng nhập trên thiết bị
                        </span>
                        <span className="font-mono text-[11px] text-mineral">
                          {DEV_GOOGLE_ACCOUNTS.length} phiên hoạt động
                        </span>
                      </div>

                      {/* Unified Account & Action List */}
                      <div className="divide-y divide-line overflow-hidden rounded-[14px] border border-line-strong bg-surface">
                        {DEV_GOOGLE_ACCOUNTS.map((acc, idx) => {
                          const isSelected = selectedIdx === idx
                          return (
                            <button
                              key={acc.email}
                              type="button"
                              disabled={loading}
                              onClick={() => setSelectedIdx(idx)}
                              onDoubleClick={() =>
                                void handleSelectAccount(acc.email, acc.displayName)
                              }
                              className={`flex w-full items-center gap-3.5 px-4 py-3.5 text-left transition-colors ${
                                isSelected
                                  ? "bg-paper/90"
                                  : "bg-surface hover:bg-paper/50"
                              }`}
                            >
                              {/* Avatar with Google corner badge */}
                              <span className="relative shrink-0">
                                <span
                                  className={`grid h-10 w-10 place-items-center rounded-full font-serif text-[13.5px] font-semibold ${acc.avatarTone}`}
                                >
                                  {acc.initials}
                                </span>
                                <span className="absolute -right-0.5 -bottom-0.5 grid h-4 w-4 place-items-center rounded-full border border-line bg-surface shadow-2xs">
                                  <svg className="h-2.5 w-2.5" viewBox="0 0 24 24" aria-hidden="true">
                                    <path
                                      fill="#4285F4"
                                      d="M23.49 12.275c0-.85-.075-1.675-.215-2.475H12v4.69h6.45c-.28 1.5-1.125 2.775-2.395 3.63v3.015h3.875c2.265-2.085 3.56-5.16 3.56-8.86z"
                                    />
                                    <path
                                      fill="#34A853"
                                      d="M12 24c3.24 0 5.955-1.07 7.94-2.91l-3.875-3.015c-1.075.72-2.45 1.15-4.065 1.15-3.125 0-5.77-2.11-6.715-4.945H1.28v3.11C3.255 21.315 7.31 24 12 24z"
                                    />
                                    <path
                                      fill="#FBBC05"
                                      d="M5.285 14.28A7.23 7.23 0 0 1 4.91 12c0-.795.14-1.565.375-2.28V6.61H1.28A11.99 11.99 0 0 0 0 12c0 1.935.465 3.765 1.28 5.39l4.005-3.11z"
                                    />
                                    <path
                                      fill="#EA4335"
                                      d="M12 4.775c1.765 0 3.345.605 4.59 1.795l3.44-3.44C17.95 1.19 15.235 0 12 0 7.31 0 3.255 2.685 1.28 6.61l4.005 3.11C6.23 6.885 8.875 4.775 12 4.775z"
                                    />
                                  </svg>
                                </span>
                              </span>

                              <span className="min-w-0 flex-1">
                                <span className="flex items-center gap-2">
                                  <span className="truncate text-[14px] font-semibold text-ink">
                                    {acc.displayName}
                                  </span>
                                  <span className="hidden rounded-full border border-line bg-surface px-2 py-0.5 font-mono text-[10px] text-secondary sm:inline-block">
                                    Đã đăng nhập
                                  </span>
                                </span>
                                <span className="mt-0.5 block truncate font-mono text-[12px] text-secondary">
                                  {acc.email}
                                </span>
                              </span>

                              {/* Selection indicator */}
                              <span
                                className={`grid h-5 w-5 shrink-0 place-items-center rounded-full border transition-colors ${
                                  isSelected
                                    ? "border-teal bg-teal text-paper"
                                    : "border-line-strong bg-surface text-transparent"
                                }`}
                                aria-hidden="true"
                              >
                                <svg className="h-3 w-3" viewBox="0 0 12 12" fill="none">
                                  <path
                                    d="M2.5 6.2 4.8 8.5 9.5 3.5"
                                    stroke="currentColor"
                                    strokeWidth="1.6"
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                  />
                                </svg>
                              </span>
                            </button>
                          )
                        })}

                        {/* Standard Google Row: Use another account */}
                        <button
                          type="button"
                          disabled={loading}
                          onClick={() => setCustomMode(true)}
                          className="flex w-full items-center gap-3.5 bg-surface px-4 py-3 text-left transition-colors hover:bg-paper/60"
                        >
                          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full border border-line-strong bg-paper text-ink">
                            <svg className="h-4 w-4" viewBox="0 0 20 20" fill="none" aria-hidden="true">
                              <path
                                d="M10 10a3.25 3.25 0 1 0 0-6.5 3.25 3.25 0 0 0 0 6.5ZM4.5 16.25a5.5 5.5 0 0 1 9.2-4.07M15.5 12.5v4M13.5 14.5h4"
                                stroke="currentColor"
                                strokeWidth="1.4"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                              />
                            </svg>
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block text-[13.5px] font-medium text-ink">
                              Sử dụng một tài khoản khác
                            </span>
                            <span className="block text-[11.5px] text-secondary">
                              Đăng nhập bằng Gmail hoặc Google Workspace khác
                            </span>
                          </span>
                          <span className="text-[13px] text-secondary">→</span>
                        </button>
                      </div>

                      {/* OAuth Data-Sharing & Privacy Disclosure */}
                      <div className="mt-4 rounded-[10px] border border-line bg-paper/65 px-3.5 py-2.5 text-[11.5px] leading-[1.58] text-secondary">
                        Để tiếp tục, Google sẽ chia sẻ tên, địa chỉ email, tùy chọn ngôn ngữ và ảnh hồ sơ của bạn với{" "}
                        <span className="font-medium text-ink">AIInvest</span>. Trước khi sử dụng ứng dụng này, bạn có thể xem lại{" "}
                        <Link to="/privacy" className="font-medium text-ink underline decoration-line-strong underline-offset-3 hover:decoration-ink">
                          Chính sách quyền riêng tư
                        </Link>{" "}
                        và{" "}
                        <Link to="/terms" className="font-medium text-ink underline decoration-line-strong underline-offset-3 hover:decoration-ink">
                          Điều khoản dịch vụ
                        </Link>{" "}
                        của AIInvest.
                      </div>
                    </div>

                    {/* Solid Bottom Action Bar */}
                    <div className="mt-5 flex items-center justify-between gap-3 border-t border-line pt-4">
                      <button
                        type="button"
                        disabled={loading}
                        onClick={() => setModalOpen(false)}
                        className="h-10 rounded-[10px] border border-line-strong bg-surface px-4 text-[13px] font-medium text-secondary transition-colors hover:border-ink/40 hover:text-ink"
                      >
                        Hủy bỏ
                      </button>

                      <button
                        type="button"
                        disabled={loading}
                        onClick={() =>
                          void handleSelectAccount(
                            selectedAccount.email,
                            selectedAccount.displayName
                          )
                        }
                        className="flex h-10 items-center justify-center gap-2 rounded-[10px] bg-ink px-5 text-[13px] font-semibold text-paper shadow-2xs transition-all hover:bg-teal disabled:opacity-60"
                      >
                        {loading ? (
                          <span>Đang xác thực OAuth…</span>
                        ) : (
                          <>
                            <span>Tiếp tục dưới tên {selectedAccount.displayName}</span>
                            <span aria-hidden="true">→</span>
                          </>
                        )}
                      </button>
                    </div>
                  </>
                ) : (
                  <div className="flex h-full flex-col justify-between">
                    <div className="space-y-4">
                      <div>
                        <label className="block text-[12.5px] font-medium text-ink">
                          Địa chỉ Gmail hoặc Google Workspace
                        </label>
                        <input
                          type="email"
                          value={customEmail}
                          onChange={(e) => {
                            setCustomEmail(e.target.value)
                            if (customError) setCustomError(null)
                          }}
                          placeholder="ten.ban@gmail.com"
                          className="mt-1.5 h-11 w-full rounded-[10px] border border-line-strong bg-surface px-3.5 text-[14px] text-ink outline-none transition-colors focus:border-mineral"
                        />
                        {customError && (
                          <p className="mt-1.5 text-[12px] text-loss">{customError}</p>
                        )}
                      </div>

                      <div>
                        <label className="block text-[12.5px] font-medium text-ink">
                          Họ và tên hiển thị trên hồ sơ
                        </label>
                        <input
                          type="text"
                          value={customName}
                          onChange={(e) => setCustomName(e.target.value)}
                          placeholder="Nguyễn Minh Quân"
                          className="mt-1.5 h-11 w-full rounded-[10px] border border-line-strong bg-surface px-3.5 text-[14px] text-ink outline-none transition-colors focus:border-mineral"
                        />
                      </div>

                      <div className="rounded-[10px] border border-line bg-paper/65 px-3.5 py-2.5 text-[11.5px] leading-[1.58] text-secondary">
                        Không phải máy tính của bạn? Hãy sử dụng cửa sổ duyệt web riêng tư để đăng nhập. Tìm hiểu thêm về cách sử dụng{" "}
                        <span className="font-medium text-ink">Chế độ khách của Google OAuth</span>.
                      </div>
                    </div>

                    <div className="mt-6 flex items-center justify-between gap-3 border-t border-line pt-4">
                      <button
                        type="button"
                        disabled={loading}
                        onClick={() => setCustomMode(false)}
                        className="h-10 rounded-[10px] border border-line-strong bg-surface px-4 text-[13px] font-medium text-secondary transition-colors hover:border-ink/40 hover:text-ink"
                      >
                        ← Quay lại danh sách
                      </button>
                      <button
                        type="button"
                        disabled={loading}
                        onClick={() => void handleCustomSubmit()}
                        className="flex h-10 items-center justify-center gap-2 rounded-[10px] bg-ink px-5 text-[13px] font-semibold text-paper shadow-2xs transition-all hover:bg-teal disabled:opacity-60"
                      >
                        <span>{loading ? "Đang xác thực…" : "Tiếp theo →"}</span>
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Bottom System Bar (Google OAuth Standard Footer) */}
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line bg-paper px-6 py-2.5 text-[11.5px] text-secondary">
              <div className="flex items-center gap-1.5">
                <span>Tiếng Việt (Việt Nam)</span>
                <span className="text-[10px]" aria-hidden="true">▾</span>
              </div>
              <div className="flex items-center gap-4">
                <Link to="/help" className="hover:text-ink">
                  Trợ giúp
                </Link>
                <Link to="/privacy" className="hover:text-ink">
                  Quyền riêng tư
                </Link>
                <Link to="/terms" className="hover:text-ink">
                  Điều khoản
                </Link>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

export function OtpCodeInput({
  value,
  onChange,
  disabled = false,
  error,
  devCode,
  resendCooldown,
  onResend,
  recipientEmail,
  onEditEmail,
}: {
  value: string
  onChange: (code: string) => void
  disabled?: boolean
  error?: string
  devCode?: string
  resendCooldown: number
  onResend: () => void
  recipientEmail?: string
  onEditEmail?: () => void
}) {
  const [focusedIdx, setFocusedIdx] = useState<number | null>(null)
  const [pastedNotice, setPastedNotice] = useState(false)
  const inputRefs = useRef<Array<HTMLInputElement | null>>([
    null,
    null,
    null,
    null,
    null,
    null,
  ])

  const digits = Array.from({ length: 6 }, (_, idx) => value[idx] ?? "")
  const filledCount = digits.filter((d) => d.length > 0).length
  const isComplete = filledCount === 6
  const cleanRecipient = recipientEmail?.trim().toLowerCase() ?? ""
  const isGmail =
    cleanRecipient.endsWith("@gmail.com") ||
    cleanRecipient.endsWith("@googlemail.com")

  const handleDigitChange = (idx: number, raw: string) => {
    const clean = raw.replace(/\D/g, "")
    if (!clean) {
      const next = [...digits]
      next[idx] = ""
      onChange(next.join(""))
      return
    }

    if (clean.length > 1) {
      const sliced = clean.slice(0, 6)
      onChange(sliced)
      const focusIdx = Math.min(sliced.length, 5)
      inputRefs.current[focusIdx]?.focus()
      return
    }

    const next = [...digits]
    next[idx] = clean
    onChange(next.join(""))
    if (idx < 5) {
      inputRefs.current[idx + 1]?.focus()
    }
  }

  const handleKeyDown = (idx: number, e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Backspace") {
      if (digits[idx]) {
        const next = [...digits]
        next[idx] = ""
        onChange(next.join(""))
      } else if (idx > 0) {
        const next = [...digits]
        next[idx - 1] = ""
        onChange(next.join(""))
        inputRefs.current[idx - 1]?.focus()
      }
      e.preventDefault()
    } else if (e.key === "ArrowLeft" && idx > 0) {
      e.preventDefault()
      inputRefs.current[idx - 1]?.focus()
    } else if (e.key === "ArrowRight" && idx < 5) {
      e.preventDefault()
      inputRefs.current[idx + 1]?.focus()
    }
  }

  const handleClipboardPasteClick = async () => {
    if (disabled || typeof navigator === "undefined" || !navigator.clipboard) return
    try {
      const text = await navigator.clipboard.readText()
      const clean = text.replace(/\D/g, "").slice(0, 6)
      if (clean) {
        onChange(clean)
        setPastedNotice(true)
        window.setTimeout(() => setPastedNotice(false), 1800)
        inputRefs.current[Math.min(clean.length, 5)]?.focus()
      }
    } catch {
      inputRefs.current[0]?.focus()
    }
  }

  const renderSlot = (idx: number) => {
    const digit = digits[idx] ?? ""
    const isFocused = focusedIdx === idx
    return (
      <div key={idx} className="relative flex-1">
        <input
          ref={(el) => {
            inputRefs.current[idx] = el
          }}
          type="text"
          inputMode="numeric"
          autoComplete={idx === 0 ? "one-time-code" : "off"}
          maxLength={6}
          disabled={disabled}
          aria-label={`Chữ số thứ ${idx + 1}`}
          value={digit}
          onFocus={() => setFocusedIdx(idx)}
          onBlur={() => setFocusedIdx((prev) => (prev === idx ? null : prev))}
          onChange={(e) => handleDigitChange(idx, e.target.value)}
          onKeyDown={(e) => handleKeyDown(idx, e)}
          className={`h-[56px] w-full rounded-[12px] border pb-1.5 text-center font-mono text-[22px] font-bold tracking-tight text-ink shadow-2xs outline-none transition-all ${
            error
              ? "border-loss bg-loss/[0.02] text-loss focus:border-loss focus:ring-3 focus:ring-loss/15"
              : isFocused
                ? "border-teal bg-surface shadow-[0_8px_22px_rgba(25,54,46,0.10)] ring-3 ring-teal/15"
                : digit
                  ? "border-teal/65 bg-surface"
                  : "border-line-strong bg-surface/90 hover:border-ink/35"
          }`}
        />
        <span
          aria-hidden="true"
          className={`pointer-events-none absolute bottom-2 left-1/2 h-[2.5px] w-4 -translate-x-1/2 rounded-full transition-colors ${
            error
              ? "bg-loss"
              : isFocused
                ? "bg-mineral"
                : digit
                  ? "bg-teal"
                  : "bg-line-strong"
          }`}
        />
      </div>
    )
  }

  return (
    <div className="space-y-3.5">
      {/* Recipient Dispatch Envelope Card */}
      {cleanRecipient && (
        <div className="flex items-center justify-between gap-3 rounded-[14px] border border-line-strong bg-surface p-3.5 shadow-2xs">
          <div className="flex min-w-0 items-center gap-3">
            <span className="relative grid h-10 w-10 shrink-0 place-items-center rounded-[10px] border border-line bg-paper text-teal">
              <svg className="h-[18px] w-[18px]" viewBox="0 0 20 20" fill="none" aria-hidden="true">
                <rect
                  x="2.25"
                  y="4.25"
                  width="15.5"
                  height="11.5"
                  rx="2.5"
                  stroke="currentColor"
                  strokeWidth="1.4"
                />
                <path
                  d="m3.5 6 6.5 4.5L16.5 6"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              <span className="absolute -top-1 -right-1 h-2.5 w-2.5 rounded-full border-2 border-surface bg-mineral" />
            </span>

            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                <span className="font-mono text-[10.5px] font-semibold tracking-[0.08em] text-mineral uppercase">
                  Đã gửi mã OTP tới hộp thư
                </span>
              </div>
              <div className="mt-0.5 truncate font-mono text-[13.5px] font-semibold text-ink">
                {cleanRecipient}
              </div>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-1.5">
            {isGmail && (
              <a
                href="https://mail.google.com/mail/u/0/#inbox"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex h-8 items-center gap-1 rounded-[8px] border border-line-strong bg-paper px-2.5 text-[11.5px] font-medium text-ink transition-colors hover:border-mineral hover:bg-surface"
              >
                <span>Mở Gmail</span>
                <span aria-hidden="true" className="text-[10px] text-secondary">↗</span>
              </a>
            )}
            {onEditEmail && (
              <button
                type="button"
                disabled={disabled}
                onClick={onEditEmail}
                className="h-8 rounded-[8px] px-2.5 text-[12px] font-medium text-secondary transition-colors hover:bg-paper hover:text-ink"
              >
                Đổi email
              </button>
            )}
          </div>
        </div>
      )}

      {/* 3 — 3 Architectural OTP Console */}
      <div className="rounded-[16px] border border-line-strong bg-paper/75 p-4 shadow-2xs">
        <div className="mb-3 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <label className="text-[12.5px] font-semibold tracking-[-0.01em] text-ink">
              Mã xác thực 6 chữ số
            </label>
            <span
              className={`rounded-full px-2 py-0.5 font-mono text-[10.5px] font-semibold transition-colors ${
                isComplete
                  ? "bg-teal text-paper"
                  : "border border-line bg-surface text-secondary"
              }`}
            >
              {isComplete ? "✓ Đủ 6 số" : `${filledCount} / 6`}
            </span>
          </div>

          <button
            type="button"
            disabled={disabled}
            onClick={() => void handleClipboardPasteClick()}
            className="inline-flex items-center gap-1.5 rounded-[7px] border border-line bg-surface px-2.5 py-1 font-mono text-[11px] font-medium text-secondary transition-colors hover:border-mineral hover:text-ink disabled:opacity-50"
          >
            <svg className="h-3 w-3" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <rect
                x="5"
                y="3"
                width="8"
                height="10"
                rx="1.5"
                stroke="currentColor"
                strokeWidth="1.3"
              />
              <path
                d="M3 5.5v6A1.5 1.5 0 0 0 4.5 13H10"
                stroke="currentColor"
                strokeWidth="1.3"
                strokeLinecap="round"
              />
            </svg>
            <span>{pastedNotice ? "Đã dán mã!" : "Dán mã nhanh"}</span>
          </button>
        </div>

        <div
          className="flex items-center justify-between gap-1.5 sm:gap-2"
          onPaste={(e) => {
            const pasted = e.clipboardData.getData("text").replace(/\D/g, "").slice(0, 6)
            if (pasted) {
              e.preventDefault()
              onChange(pasted)
              setPastedNotice(true)
              window.setTimeout(() => setPastedNotice(false), 1800)
              inputRefs.current[Math.min(pasted.length, 5)]?.focus()
            }
          }}
        >
          {[0, 1, 2].map(renderSlot)}
          <span
            aria-hidden="true"
            className="px-0.5 font-serif text-[18px] font-semibold text-gold select-none sm:px-1"
          >
            —
          </span>
          {[3, 4, 5].map(renderSlot)}
        </div>

        {error && (
          <p
            role="alert"
            className="mt-3 flex items-center gap-1.5 rounded-[8px] border border-loss/25 bg-loss/[0.04] px-3 py-2 text-[12px] font-medium text-loss"
          >
            <span aria-hidden="true">●</span>
            <span>{error}</span>
          </p>
        )}

        <div className="mt-3.5 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3 text-[11.5px] text-secondary">
          <span>⏱ Mã có hiệu lực trong 10 phút</span>

          <button
            type="button"
            disabled={disabled || resendCooldown > 0}
            onClick={onResend}
            className="font-mono text-[11.5px] font-semibold text-teal underline-offset-4 transition-colors hover:text-ink hover:underline disabled:cursor-not-allowed disabled:text-muted disabled:no-underline"
          >
            {resendCooldown > 0
              ? `Gửi lại mã sau ${resendCooldown}s`
              : "↻ Gửi lại mã xác minh mới"}
          </button>
        </div>
      </div>

      {devCode && (
        <div className="flex items-center justify-between rounded-[10px] border border-line bg-paper px-3.5 py-2.5 text-[12px]">
          <span className="text-secondary">
            Mã xác minh môi trường Dev:{" "}
            <strong className="font-mono font-semibold text-ink">{devCode}</strong>
          </span>
          <button
            type="button"
            onClick={() => onChange(devCode)}
            className="font-mono text-[11.5px] font-semibold text-teal underline-offset-4 hover:underline"
          >
            Điền tự động →
          </button>
        </div>
      )}
    </div>
  )
}

// ============================================================================
// 6. VALIDATION, ERROR NORMALIZATION & SAFE ROUTING HELPERS
// ============================================================================

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/

export function validateEmail(rawEmail: string): string | null {
  const trimmed = rawEmail.trim()
  if (!trimmed) {
    return "Vui lòng nhập địa chỉ email của bạn."
  }
  if (!EMAIL_REGEX.test(trimmed)) {
    return "Địa chỉ email chưa đúng định dạng (ví dụ: ten@tochuc.vn)."
  }
  return null
}

export function extractAuthError(
  error: unknown,
  mode: AuthMode
): NormalizedAuthError {
  const errObj = (error ?? {}) as {
    status?: number
    code?: string
    message?: string
    details?: Array<{ path?: string; message?: string }>
  }

  const status = typeof errObj.status === "number" ? errObj.status : undefined
  const code = typeof errObj.code === "string" ? errObj.code : undefined

  if (status === 0 || code === "NETWORK_ERROR") {
    return {
      status: 0,
      code: "NETWORK_ERROR",
      title: "Không thể kết nối đến máy chủ",
      message:
        "Vui lòng kiểm tra kết nối mạng của bạn. Dữ liệu bạn vừa nhập vẫn được giữ nguyên.",
      isNetworkError: true,
    }
  }

  if (status === 429) {
    return {
      status: 429,
      code: code ?? "RATE_LIMITED",
      title: "Tạm khóa yêu cầu để bảo vệ tài khoản",
      message:
        "Hệ thống giới hạn tối đa 20 lần thử trong 15 phút. Vui lòng đợi ít phút trước khi thử lại.",
      isRateLimited: true,
    }
  }

  if (code === "INVALID_OTP") {
    return {
      status: 400,
      code: "INVALID_OTP",
      title: "Mã xác minh chưa chính xác",
      message:
        errObj.message ||
        "Mã xác minh 6 chữ số không khớp hoặc đã quá hạn 10 phút. Vui lòng kiểm tra lại hoặc yêu cầu gửi mã mới.",
    }
  }

  if (status === 404 || code === "USER_NOT_FOUND") {
    return {
      status: 404,
      code: "USER_NOT_FOUND",
      title: "Chưa tìm thấy tài khoản với email này",
      message:
        "Địa chỉ email bạn nhập chưa được đăng ký trên AIInvest. Bạn có thể tạo tài khoản mới ngay.",
      actionLabel: "Tạo tài khoản với email này",
      actionHref: "/signup",
    }
  }

  if (status === 401 && mode === "login") {
    return {
      status: 401,
      code: code ?? "UNAUTHORIZED",
      title: "Thông tin đăng nhập chưa chính xác",
      message:
        "Email hoặc mật khẩu chưa khớp với hồ sơ hệ thống. Vui lòng kiểm tra lại hoặc tạo tài khoản mới.",
      actionLabel: "Chuyển sang tạo tài khoản mới",
      actionHref: "/signup",
      isInvalidCredentials: true,
    }
  }

  if (status === 409 && mode === "signup") {
    return {
      status: 409,
      code: code ?? "CONFLICT",
      title: "Email này đã được đăng ký trên AIInvest",
      message:
        "Hồ sơ nghiên cứu gắn với địa chỉ email này đã tồn tại. Bạn có thể chuyển sang đăng nhập ngay.",
      actionLabel: "Đăng nhập với email này",
      actionHref: "/login",
      isConflict: true,
    }
  }

  if (status === 400) {
    const fieldErrors: Record<string, string> = {}
    if (Array.isArray(errObj.details)) {
      for (const item of errObj.details) {
        if (item?.path && item?.message) {
          fieldErrors[item.path] = item.message
        }
      }
    }
    return {
      status: 400,
      code: code ?? "VALIDATION_ERROR",
      title: "Thông tin gửi lên chưa hợp lệ",
      message:
        "Vui lòng kiểm tra lại định dạng email (ví dụ: ten@tochuc.vn) và mật khẩu tối thiểu 6 ký tự.",
      fieldErrors: Object.keys(fieldErrors).length > 0 ? fieldErrors : undefined,
    }
  }

  return {
    status,
    code: code ?? "INTERNAL_ERROR",
    title:
      mode === "login"
        ? "Không thể đăng nhập vào lúc này"
        : "Không thể khởi tạo tài khoản vào lúc này",
    message:
      "Hệ thống đang bận hoặc bảo trì ngắn hạn. Vui lòng thử lại sau ít phút.",
  }
}

export function getSafeRedirectTarget(rawNext: string | null | undefined): string {
  if (!rawNext || typeof rawNext !== "string") return "/dashboard"
  const trimmed = rawNext.trim()
  if (!trimmed.startsWith("/") || trimmed.startsWith("//") || trimmed.includes("\\")) {
    return "/dashboard"
  }
  const lower = trimmed.toLowerCase()
  if (
    lower.startsWith("/login") ||
    lower.startsWith("/signup") ||
    lower.startsWith("/register") ||
    lower.startsWith("/auth")
  ) {
    return "/dashboard"
  }
  return trimmed
}

export function buildAuthHref(
  basePath: "/login" | "/signup",
  options?: { email?: string; next?: string | null }
): string {
  const params = new URLSearchParams()
  const cleanEmail = options?.email?.trim().toLowerCase()
  if (cleanEmail) {
    params.set("email", cleanEmail)
  }
  const rawNext = options?.next?.trim()
  if (
    rawNext &&
    rawNext.startsWith("/") &&
    !rawNext.startsWith("//") &&
    !rawNext.includes("\\")
  ) {
    const lower = rawNext.toLowerCase()
    if (
      !lower.startsWith("/login") &&
      !lower.startsWith("/signup") &&
      !lower.startsWith("/register") &&
      !lower.startsWith("/auth")
    ) {
      params.set("next", rawNext)
    }
  }
  const qs = params.toString()
  return qs ? `${basePath}?${qs}` : basePath
}
