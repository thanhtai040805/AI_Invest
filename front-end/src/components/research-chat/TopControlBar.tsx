"use client"

import { useState, type FormEvent } from "react"
import { CalendarClock, GitBranch, Layers3, Plus, ShieldCheck, X } from "lucide-react"
import { Button, Pill } from "@/components/ui"
import type { EvalSummaryResponse, ResearchSessionSummary } from "@/lib/api/researchChat"
import { PILOT_TICKERS } from "./types"

interface TopControlBarProps {
  primaryTicker: string
  onPrimaryTickerChange: (ticker: string) => void
  compareTickers: string[]
  onCompareTickersChange: (tickers: string[]) => void
  enableKg: boolean
  onEnableKgChange: (enabled: boolean) => void
  asOfDate: string
  todayDate: string
  onAsOfDateChange: (date: string) => void
  sessions: ResearchSessionSummary[]
  activeSessionId: string | null
  onSelectSession: (sessionId: string) => void
  onCreateNewSession: () => void
  creatingSession: boolean
  evalSummary: EvalSummaryResponse | null
  onOpenGateTab: () => void
  disabled?: boolean
}

function cleanTicker(raw: string): string {
  return raw.trim().toUpperCase().replace(/[^A-Z0-9]/g, "")
}

export function TopControlBar({
  primaryTicker,
  onPrimaryTickerChange,
  compareTickers,
  onCompareTickersChange,
  enableKg,
  onEnableKgChange,
  asOfDate,
  todayDate,
  onAsOfDateChange,
  sessions,
  activeSessionId,
  onSelectSession,
  onCreateNewSession,
  creatingSession,
  evalSummary,
  onOpenGateTab,
  disabled = false,
}: TopControlBarProps) {
  const [tickerInput, setTickerInput] = useState("")
  const [compareInput, setCompareInput] = useState("")

  const totalTickers = 1 + compareTickers.length
  const maxReached = totalTickers >= 6

  const handlePrimarySubmit = (e: FormEvent) => {
    e.preventDefault()
    const sym = cleanTicker(tickerInput)
    if (!sym || sym.length < 2 || sym.length > 8) return
    onPrimaryTickerChange(sym)
    if (compareTickers.includes(sym)) {
      onCompareTickersChange(compareTickers.filter((t) => t !== sym))
    }
    setTickerInput("")
  }

  const handleCompareAdd = (e: FormEvent) => {
    e.preventDefault()
    if (maxReached) return
    const parts = compareInput
      .split(/[\s,;]+/)
      .map(cleanTicker)
      .filter((s) => s.length >= 2 && s.length <= 8 && s !== primaryTicker)
    if (parts.length === 0) return
    const next = [...compareTickers]
    for (const sym of parts) {
      if (1 + next.length >= 6) break
      if (!next.includes(sym)) next.push(sym)
    }
    onCompareTickersChange(next)
    setCompareInput("")
  }

  const g5 = evalSummary?.gates?.g5HumanPassRate
  const g1 = evalSummary?.gates?.g1CitationVerifiedRate
  const gatePassCount = evalSummary
    ? [
        (g1 ?? 0) >= 0.98,
        (evalSummary.gates.g2TableCalcVerifiedRate ?? 0) >= 0.98,
        (evalSummary.gates.g3AbstentionComplianceRate ?? 0) >= 1,
        evalSummary.gates.g4PitZeroLeakCount === 0 && evalSummary.totalAssistantMessages > 0,
        (g5 ?? 0) >= 0.9,
      ].filter(Boolean).length
    : 0

  return (
    <div className="rounded-[10px] border border-line bg-surface p-3.5 space-y-3">
      {/* Row 1: Primary Ticker Quick Chips + Session & Exit Gate Pill */}
      <div className="flex flex-wrap items-center justify-between gap-2.5">
        <div className="flex flex-wrap items-center gap-1.5 min-w-0">
          <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted mr-1">
            Mã chính
          </span>
          {PILOT_TICKERS.map((sym) => {
            const active = primaryTicker === sym
            return (
              <button
                key={sym}
                type="button"
                disabled={disabled}
                onClick={() => {
                  onPrimaryTickerChange(sym)
                  if (compareTickers.includes(sym)) {
                    onCompareTickersChange(compareTickers.filter((t) => t !== sym))
                  }
                }}
                className={`h-7 px-2.5 rounded-[6px] font-mono text-[11.5px] font-semibold transition-colors duration-[120ms] border ${
                  active
                    ? "bg-ink text-paper border-ink"
                    : "bg-paper text-secondary border-line hover:border-line-strong hover:text-ink"
                } disabled:opacity-50`}
              >
                {sym}
              </button>
            )
          })}
          <form onSubmit={handlePrimarySubmit} className="flex items-center gap-1 ml-1">
            <input
              type="text"
              value={tickerInput}
              onChange={(e) => setTickerInput(e.target.value.toUpperCase())}
              placeholder="Mã khác…"
              maxLength={8}
              disabled={disabled}
              aria-label="Nhập mã cổ phiếu chính"
              className="h-7 w-24 rounded-[6px] border border-line bg-paper px-2 font-mono text-[11.5px] text-ink placeholder:text-muted focus:border-mineral focus:outline-none"
            />
            <Button type="submit" size="sm" variant="secondary" disabled={disabled || !tickerInput.trim()} className="!h-7 !px-2 text-[11px]">
              Chọn
            </Button>
          </form>
        </div>

        <div className="flex flex-wrap items-center gap-2 shrink-0">
          <button
            type="button"
            onClick={onOpenGateTab}
            title="Mở bảng chi tiết Exit Gate G1–G5 & chấm điểm kiểm nghiệm"
            className="inline-flex items-center gap-1.5 rounded-full border border-line-strong bg-soft/80 px-2.5 h-7 text-[11.5px] font-medium text-ink hover:bg-raised transition-colors"
          >
            <ShieldCheck className="h-3.5 w-3.5 text-teal" aria-hidden="true" />
            <span className="font-mono tnum">
              Gate G1–G5: {evalSummary ? `${gatePassCount}/5` : "—"}
            </span>
            {g5 != null && (
              <span className="text-muted font-mono tnum">
                ({Math.round(g5 * 100)}% PASS)
              </span>
            )}
          </button>

          <select
            aria-label="Chọn phiên nghiên cứu"
            value={activeSessionId ?? ""}
            onChange={(e) => {
              if (e.target.value) onSelectSession(e.target.value)
            }}
            disabled={disabled || sessions.length === 0}
            className="h-7 max-w-[220px] rounded-[6px] border border-line bg-surface px-2 text-[12px] text-ink focus:border-mineral focus:outline-none"
          >
            {sessions.length === 0 ? (
              <option value="">Chưa có phiên lưu</option>
            ) : (
              sessions.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.ticker ? `[${s.ticker}] ` : ""}
                  {s.title || `Phiên ${s.id.slice(0, 8)}`}
                </option>
              ))
            )}
          </select>

          <Button
            variant="primary"
            size="sm"
            onClick={onCreateNewSession}
            disabled={disabled || creatingSession}
            className="!h-7 !px-2.5 text-[11.5px]"
          >
            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{creatingSession ? "Đang tạo…" : "Phiên mới"}</span>
          </Button>
        </div>
      </div>

      {/* Row 2: Multi-ticker Compare (<=6), 3-Layer KG Toggle, Point-in-Time as_of_date */}
      <div className="flex flex-wrap items-center gap-3 pt-2.5 border-t border-line">
        {/* Compare Tickers */}
        <div className="flex flex-wrap items-center gap-1.5 min-w-0">
          <span className="inline-flex items-center gap-1 text-[11.5px] font-medium text-secondary">
            <GitBranch className="h-3.5 w-3.5 text-muted" aria-hidden="true" />
            So sánh ({totalTickers}/6):
          </span>
          <Pill tone="mineral">
            <span className="font-mono font-semibold">{primaryTicker}</span>
          </Pill>
          {compareTickers.map((sym) => (
            <span
              key={sym}
              className="inline-flex items-center gap-1 h-[22px] pl-2 pr-1 rounded-full border border-line-strong bg-soft font-mono text-[11px] font-medium text-ink"
            >
              {sym}
              <button
                type="button"
                disabled={disabled}
                onClick={() => onCompareTickersChange(compareTickers.filter((t) => t !== sym))}
                aria-label={`Bỏ mã so sánh ${sym}`}
                className="rounded-full p-0.5 text-muted hover:text-loss hover:bg-loss/10"
              >
                <X className="h-3 w-3" aria-hidden="true" />
              </button>
            </span>
          ))}
          <form onSubmit={handleCompareAdd} className="flex items-center gap-1">
            <input
              type="text"
              value={compareInput}
              onChange={(e) => setCompareInput(e.target.value.toUpperCase())}
              placeholder={maxReached ? "Đã đủ 6 mã" : "+ Mã đối thủ (VD: CTG, BID)"}
              disabled={disabled || maxReached}
              aria-label="Thêm mã so sánh"
              className="h-7 w-44 rounded-[6px] border border-line bg-paper px-2 font-mono text-[11.5px] text-ink placeholder:text-muted focus:border-mineral focus:outline-none disabled:opacity-50"
            />
            <Button
              type="submit"
              size="sm"
              variant="secondary"
              disabled={disabled || maxReached || !compareInput.trim()}
              className="!h-7 !px-2 text-[11px]"
            >
              Thêm
            </Button>
          </form>
        </div>

        <div className="h-4 w-px bg-line hidden md:block" aria-hidden="true" />

        {/* Toggle 3-Layer KG */}
        <label className="inline-flex items-center gap-2 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={enableKg}
            disabled={disabled}
            onChange={(e) => onEnableKgChange(e.target.checked)}
            className="h-4 w-4 rounded border-line-strong accent-teal"
          />
          <span className="inline-flex items-center gap-1 text-[12px] font-medium text-ink">
            <Layers3 className="h-3.5 w-3.5 text-mineral" aria-hidden="true" />
            3-Layer Macro & True-Peer KG
          </span>
        </label>

        <div className="h-4 w-px bg-line hidden md:block" aria-hidden="true" />

        {/* Point-in-Time as_of_date */}
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="inline-flex items-center gap-1 text-[12px] font-medium text-secondary">
            <CalendarClock className="h-3.5 w-3.5 text-muted" aria-hidden="true" />
            PIT <code className="font-mono text-[11px]">as_of_date</code>:
          </span>
          <input
            type="date"
            value={asOfDate}
            max={todayDate}
            disabled={disabled}
            onChange={(e) => onAsOfDateChange(e.target.value)}
            aria-label="Ngày Point-in-Time as_of_date"
            className="h-7 rounded-[6px] border border-line bg-paper px-2 font-mono text-[11.5px] text-ink focus:border-mineral focus:outline-none"
          />
          <button
            type="button"
            disabled={disabled}
            onClick={() => onAsOfDateChange(asOfDate === "2025-06-30" ? todayDate : "2025-06-30")}
            title="Chuyển nhanh mốc tua ngược PIT 2025-06-30 để kiểm thử G4"
            className={`h-7 px-2 rounded-[6px] border font-mono text-[11px] transition-colors ${
              asOfDate === "2025-06-30"
                ? "border-warning/40 bg-warning/12 text-warning font-semibold"
                : "border-line bg-soft text-secondary hover:text-ink"
            }`}
          >
            {asOfDate === "2025-06-30" ? "Về hôm nay" : "Test PIT 2025-06-30"}
          </button>
        </div>
      </div>
    </div>
  )
}
