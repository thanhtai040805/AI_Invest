"use client"

import { useId, useRef, useState } from "react"
import { displayDate, validDate, vietnamDate } from "@/lib/financial-date"

export function FinancialCalendar({ value, onChange, dates = [], label = "Chọn ngày", allowAll = true, availableOnly = false }: {
  value: string; onChange: (date: string) => void; dates?: string[]; label?: string; allowAll?: boolean; availableOnly?: boolean
}) {
  const id = useId()
  const popup = useRef<HTMLDivElement>(null)
  const today = vietnamDate()
  const dataDates = dates.filter(date => validDate(date) && date <= today)
  const [month, setMonth] = useState((value || dataDates[0] || today).slice(0, 7))
  const [draft, setDraft] = useState("")
  const [error, setError] = useState("")
  const [year, mon] = month.split("-").map(Number)
  const offset = (new Date(Date.UTC(year, mon - 1, 1)).getUTCDay() + 6) % 7
  const count = new Date(Date.UTC(year, mon, 0)).getUTCDate()
  const available = new Set(dataDates)
  function select(date: string) {
    if (date && availableOnly && !available.has(date)) {
      setError(dataDates[0] ? `Ngày này chưa có bản lưu. Ngày mới nhất: ${displayDate(dataDates[0])}.` : "Chưa có ngày nào có bản lưu cho bộ lọc này.")
      return
    }
    onChange(date)
    popup.current?.hidePopover()
  }
  function move(delta: number) {
    setMonth(new Date(Date.UTC(year, mon - 1 + delta, 1)).toISOString().slice(0, 7))
  }
  return <div className="inline-flex">
    <button type="button" popoverTarget={id} aria-label={label} onClick={() => {
      setMonth((value || dataDates[0] || today).slice(0, 7)); setDraft(value ? displayDate(value) : ""); setError("")
    }} className="inline-flex h-9 items-center gap-2 rounded-[7px] border border-line-strong bg-surface px-3 text-[12px] text-ink focus-visible:outline-2 focus-visible:outline-mineral">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18"/></svg>
      <span className="font-mono tnum">{value ? displayDate(value) : allowAll ? "Tất cả ngày" : "Chọn ngày"}</span><span className="text-muted">▾</span>
    </button>
    <div id={id} ref={popup} popover="auto" role="dialog" aria-label={label}
      className="fixed inset-0 m-auto w-[320px] max-w-[calc(100vw-24px)] rounded-xl border border-line-strong bg-surface p-4 text-ink shadow-[0_16px_64px_rgba(24,32,29,.18)] backdrop:bg-ink/15">
      <div className="flex items-center justify-between"><div><div className="text-[13px] font-semibold">{label}</div><div className="text-[10px] text-muted">VIỆT NAM · UTC+7</div></div><button type="button" aria-label="Đóng lịch" onClick={() => popup.current?.hidePopover()} className="h-8 w-8 rounded hover:bg-soft">×</button></div>
      <form className="mt-3" onSubmit={e => {
        e.preventDefault()
        const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(draft)
        const date = match ? `${match[3]}-${match[2]}-${match[1]}` : ""
        if (!validDate(date) || date > today) { setError("Nhập ngày hợp lệ, không vượt hôm nay."); return }
        select(date)
      }}>
        <div className="flex gap-2"><input aria-label="Nhập ngày cụ thể" placeholder="dd/mm/yyyy" value={draft} onChange={e => setDraft(e.target.value)} className="min-w-0 flex-1 rounded border border-line bg-paper px-2 py-2 text-[12px] font-mono"/><button type="submit" className="rounded bg-ink px-3 text-[12px] text-paper">Chọn</button></div>
        {error && <p role="alert" className="mt-1 text-[11px] text-loss">{error}</p>}
      </form>
      <div className="my-3 flex items-center justify-between"><button type="button" aria-label="Tháng trước" onClick={() => move(-1)} className="h-8 w-8 rounded hover:bg-soft">‹</button>
        <div className="flex gap-1"><select aria-label="Tháng" value={mon} onChange={e => setMonth(`${year}-${e.target.value.padStart(2, "0")}`)} className="bg-surface text-[12px]">{Array.from({length: 12}, (_, i) => <option key={i} value={i + 1}>Tháng {i + 1}</option>)}</select><input aria-label="Năm" type="number" min={1900} max={Number(today.slice(0, 4))} value={year} onChange={e => { const y = Number(e.target.value); if (y >= 1900 && y <= Number(today.slice(0, 4))) setMonth(`${y}-${String(mon).padStart(2, "0")}`) }} className="w-16 bg-surface text-[12px] font-mono"/></div>
        <button type="button" aria-label="Tháng sau" disabled={month >= today.slice(0, 7)} onClick={() => move(1)} className="h-8 w-8 rounded hover:bg-soft disabled:opacity-30">›</button></div>
      <div className="grid grid-cols-7 gap-1 text-center">{["T2", "T3", "T4", "T5", "T6", "T7", "CN"].map((d, i) => <span key={d} className={`py-1 text-[10px] ${i > 4 ? "text-muted" : "text-secondary"}`}>{d}</span>)}
        {Array.from({length: offset}, (_, i) => <span key={`blank-${i}`} />)}
        {Array.from({length: count}, (_, i) => {
          const date = `${month}-${String(i + 1).padStart(2, "0")}`
          const weekend = (offset + i) % 7 > 4
          return <button key={date} type="button" disabled={date > today || (availableOnly && !available.has(date))} aria-pressed={date === value} aria-label={`${displayDate(date)}${available.has(date) ? ", có dữ liệu" : availableOnly ? ", chưa có bản lưu" : ""}${weekend ? ", cuối tuần" : ""}`} onClick={() => select(date)}
            className={`relative h-9 rounded-md text-[12px] font-mono disabled:opacity-25 focus-visible:outline-2 focus-visible:outline-mineral ${date === value ? "bg-ink text-paper" : weekend ? "bg-soft text-muted hover:bg-line" : "hover:bg-soft"} ${date === today ? "ring-1 ring-inset ring-mineral" : ""}`}>
            {i + 1}{available.has(date) && <span className={`absolute bottom-1 left-1/2 h-1 w-1 -translate-x-1/2 rounded-full ${date === value ? "bg-paper" : "bg-mineral"}`} />}
          </button>
        })}
      </div>
      <div className="mt-3 flex flex-wrap gap-2 border-t border-line pt-3 text-[11px]">{allowAll && <button type="button" onClick={() => select("")} className="rounded border border-line px-2 py-1">Tất cả ngày</button>}<button type="button" disabled={availableOnly && !available.has(today)} onClick={() => select(today)} className="rounded border border-line px-2 py-1 disabled:opacity-30">Hôm nay</button>{dataDates[0] && <button type="button" onClick={() => select(dataDates[0])} className="rounded border border-line px-2 py-1">Có dữ liệu mới nhất</button>}</div>
      {availableOnly && <p className="mt-2 text-[11px] text-secondary">Chỉ chọn được ngày có bản lưu phù hợp với bộ lọc mã cổ phiếu.</p>}
      <p className="mt-3 text-[10px] leading-relaxed text-muted">● Có dữ liệu · Nền xám: cuối tuần.<br/>Ngày lưu phân tích có thể ngoài phiên giao dịch. Dấu dữ liệu không xác nhận sàn mở cửa hay ngày thanh toán T+.</p>
    </div>
  </div>
}
