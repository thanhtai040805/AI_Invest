import { useRef, useState, type ReactNode } from "react"

// ── Number formatting ──────────────────────────────────────────
export const metricTone: Record<string, string> = { gain: "text-gain", loss: "text-loss", warning: "text-warning", teal: "text-teal", mineral: "text-mineral", neutral: "text-ink" }
export const fmt = (n: number) => n.toLocaleString("en-US")
export const fmtPct = (n: number) => `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`

// ── Button ─────────────────────────────────────────────────────
type BtnVariant = "primary" | "secondary" | "ghost" | "quiet"
export function Button({
  children, onClick, variant = "secondary", className = "", type = "button", disabled,
}: {
  children: ReactNode; onClick?: () => void; variant?: BtnVariant; className?: string
  type?: "button" | "submit"; disabled?: boolean
}) {
  const base = "inline-flex items-center justify-center gap-1.5 rounded-[7px] text-[13px] font-medium px-3.5 h-9 transition-colors disabled:opacity-45 disabled:pointer-events-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-mineral"
  const styles: Record<BtnVariant, string> = {
    primary: "bg-ink text-paper hover:bg-[#0d1310]",
    secondary: "bg-surface text-ink border border-line-strong hover:border-ink/40 hover:bg-soft",
    ghost: "text-secondary hover:text-ink hover:bg-soft",
    quiet: "text-mineral hover:bg-mineral/8",
  }
  return (
    <button type={type} disabled={disabled} onClick={onClick} className={`${base} ${styles[variant]} ${className}`}>
      {children}
    </button>
  )
}

// ── Panel / Card ───────────────────────────────────────────────
export function Panel({ children, className = "", flush }: { children: ReactNode; className?: string; flush?: boolean }) {
  return (
    <section className={`bg-surface border border-line rounded-[10px] ${flush ? "" : "p-5"} ${className}`}>
      {children}
    </section>
  )
}

export function PanelHead({ title, sub, action }: { title: string; sub?: string; action?: ReactNode }) {
  return (
    <div className="flex items-start justify-between mb-4">
      <div>
        <h3 className="text-[15px] font-semibold text-ink tracking-tight">{title}</h3>
        {sub && <p className="text-[12px] text-muted mt-0.5">{sub}</p>}
      </div>
      {action}
    </div>
  )
}

// ── Percent / Price ────────────────────────────────────────────
export function PercentChange({ value, className = "", arrow = true }: { value: number; className?: string; arrow?: boolean }) {
  const pos = value > 0, neg = value < 0
  const color = pos ? "text-gain" : neg ? "text-loss" : "text-neutral"
  return (
    <span className={`tnum font-mono ${color} ${className}`}>
      {arrow && (pos ? "▲ " : neg ? "▼ " : "· ")}
      {fmtPct(value)}
    </span>
  )
}

export function PriceCell({ value, className = "" }: { value: number; className?: string }) {
  return <span className={`tnum font-mono text-ink ${className}`}>{fmt(value)}</span>
}

// ── Labels / badges ────────────────────────────────────────────
export function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: string }) {
  const map: Record<string, string> = {
    neutral: "bg-soft text-secondary border-line",
    mineral: "bg-mineral/10 text-mineral border-mineral/25",
    teal: "bg-teal/10 text-teal border-teal/25",
    gold: "bg-gold/12 text-gold border-gold/30",
    gain: "bg-gain/10 text-gain border-gain/25",
    loss: "bg-loss/10 text-loss border-loss/25",
    warning: "bg-warning/12 text-warning border-warning/30",
  }
  return <span className={`inline-flex items-center gap-1 text-[11px] font-medium px-2 h-[22px] rounded-full border ${map[tone]}`}>{children}</span>
}

export function RiskLabel({ risk }: { risk: string }) {
  const tone = risk === "Low" ? "teal" : risk === "Moderate" ? "gold" : risk === "Elevated" ? "warning" : "loss"
  const dots = risk === "Low" ? 1 : risk === "Moderate" ? 2 : risk === "Elevated" ? 3 : 4
  return (
    <span className="inline-flex items-center gap-1.5 text-[12px] text-secondary">
      <span className="flex gap-0.5" aria-hidden>
        {[0, 1, 2, 3].map((i) => (
          <span key={i} className={`w-1 h-3 rounded-full ${i < dots ? (tone === "teal" ? "bg-teal" : tone === "gold" ? "bg-gold" : tone === "warning" ? "bg-warning" : "bg-loss") : "bg-line-strong"}`} />
        ))}
      </span>
      {risk}
    </span>
  )
}

export function SignalLabel({ label }: { label: string }) {
  return <span className="text-[12px] text-secondary">{label}</span>
}

export function Conviction({ level }: { level: "Strong" | "Moderate" | "Weak" | "Conflicting" }) {
  const tone = level === "Strong" ? "teal" : level === "Moderate" ? "gold" : level === "Weak" ? "neutral" : "warning"
  return <Pill tone={tone}>{level}</Pill>
}

// ── Sparkline ──────────────────────────────────────────────────
export function Sparkline({ data, up, width = 68, height = 22 }: { data: number[]; up?: boolean; width?: number; height?: number }) {
  const min = Math.min(...data), max = Math.max(...data)
  const range = max - min || 1
  const rising = up ?? data[data.length - 1] >= data[0]
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * width},${height - ((v - min) / range) * (height - 3) - 1.5}`).join(" ")
  const color = rising ? "var(--color-gain)" : "var(--color-loss)"
  return (
    <svg width={width} height={height} className="overflow-visible" aria-hidden>
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

// ── Readable market chart ──────────────────────────────────────
export function MarketLineChart({ series, height = 220 }: { series: { label: string; data: number[]; color: string; dashed?: boolean }[]; height?: number }) {
  const values = series.flatMap((item) => item.data)
  const min = Math.min(...values), max = Math.max(...values), range = max - min || 1
  const len = Math.max(...series.map((s) => s.data.length))
  const yAt = (v: number) => 46 - ((v - min) / range) * 40
  const path = (data: number[]) => data.map((v, i) => `${(i / (data.length - 1)) * 100},${yAt(v)}`).join(" ")
  const ref = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<number | null>(null)
  const dates = ["05 Aug", "12 Aug", "19 Aug", "26 Aug", "05 Sep"]

  const onMove = (e: React.PointerEvent) => {
    const el = ref.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const ratio = Math.min(Math.max((e.clientX - rect.left) / rect.width, 0), 1)
    setHover(Math.round(ratio * (len - 1)))
  }
  const xPct = hover === null ? 0 : (hover / (len - 1)) * 100

  return (
    <div className="relative select-none" style={{ height }} ref={ref} onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
      <div className="absolute inset-x-0 top-[13%] border-t border-dashed border-line" />
      <div className="absolute inset-x-0 top-1/2 border-t border-dashed border-line" />
      <div className="absolute inset-x-0 bottom-[13%] border-t border-dashed border-line" />
      <svg viewBox="0 0 100 50" preserveAspectRatio="none" className="relative w-full h-full overflow-visible" aria-label="One month performance chart" role="img">
        {hover !== null && <line x1={xPct} x2={xPct} y1="2" y2="48" stroke="var(--color-mineral)" strokeWidth="0.5" strokeDasharray="1.5 1.5" vectorEffect="non-scaling-stroke" />}
        {series.map((item) => <polyline key={item.label} points={path(item.data)} fill="none" stroke={item.color} strokeWidth="0.72" strokeDasharray={item.dashed ? "2 1.5" : undefined} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />)}
        {hover !== null && series.map((item) => {
          const v = item.data[Math.min(hover, item.data.length - 1)]
          return <circle key={item.label} cx={xPct} cy={yAt(v)} r="1.4" fill="var(--color-surface)" stroke={item.color} strokeWidth="0.7" vectorEffect="non-scaling-stroke" />
        })}
      </svg>
      {hover !== null && (
        <div className="pointer-events-none absolute top-1 z-10 rounded-[7px] border border-line bg-surface px-2.5 py-1.5 shadow-[0_8px_24px_rgba(24,32,29,.12)]" style={{ left: `${xPct}%`, transform: `translateX(${xPct > 60 ? "-108%" : "8px"})` }}>
          {series.map((item) => (
            <div key={item.label} className="flex items-center gap-2 text-[11px] leading-tight">
              <span className="h-0.5 w-3 rounded-full" style={{ background: item.color }} />
              <span className="text-secondary">{item.label}</span>
              <span className="ml-auto tnum font-mono font-medium text-ink">{fmt(Math.round(item.data[Math.min(hover, item.data.length - 1)]))}</span>
            </div>
          ))}
        </div>
      )}
      <div className="absolute inset-x-0 bottom-0 flex justify-between pt-2 text-[10px] font-mono text-muted">{dates.map((d) => <span key={d}>{d}</span>)}</div>
    </div>
  )
}

// ── Bars ───────────────────────────────────────────────────────
export function FactorBar({ label, value, tone = "mineral" }: { label: string; value: number; tone?: string }) {
  const color = tone === "teal" ? "bg-teal" : tone === "gold" ? "bg-gold" : "bg-mineral"
  return (
    <div className="flex items-center gap-3">
      <span className="text-[12px] text-secondary w-24 shrink-0">{label}</span>
      <div className="flex-1 h-1.5 bg-soft rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full`} style={{ width: `${value}%` }} />
      </div>
      <span className="tnum font-mono text-[12px] text-ink w-8 text-right">{value}</span>
    </div>
  )
}

// ── Market Signal Rail (signature) ─────────────────────────────
export function MarketSignalRail({ compact, values }: { compact?: boolean; values?: { market?: string; change?: string; signal?: string; risk?: string } }) {
  const rows: [string, string][] = [
    ["THỊ TRƯỜNG", values?.market ?? "Chưa có dữ liệu"],
    ["THAY ĐỔI", values?.change ?? "—"],
    ["TÍN HIỆU", values?.signal ?? "Chưa có tín hiệu"],
    ["RỦI RO", values?.risk ?? "Chưa đánh giá"],
  ]
  return (
    <div className={`bg-surface border border-line rounded-[10px] ${compact ? "px-4 py-3" : "p-5"}`}>
      <div className={`grid ${compact ? "grid-cols-4 divide-x divide-line" : "grid-cols-1 divide-y divide-line"}`}>
        {rows.map(([k, v], i) => (
          <div key={k} className={`${compact ? "px-4 first:pl-0 last:pr-0" : "py-3 first:pt-0 last:pb-0"} relative`}>
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] font-semibold tracking-[0.14em] text-muted">{k}</span>
              {!compact && i < 3 && <span className="text-muted">→</span>}
            </div>
            <p className="text-[13px] text-ink mt-1 font-medium leading-tight">{v}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Metric strip ───────────────────────────────────────────────
export function MetricStrip({ items }: { items: { label: string; value: ReactNode; sub?: ReactNode }[] }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 divide-x divide-line border border-line rounded-[10px] bg-surface overflow-hidden">
      {items.map((it) => (
        <div key={it.label} className="px-5 py-4">
          <div className="text-[11px] font-medium tracking-wide text-muted uppercase">{it.label}</div>
          <div className="text-[22px] font-semibold text-ink tnum font-mono mt-1 leading-none">{it.value}</div>
          {it.sub && <div className="text-[12px] mt-1.5">{it.sub}</div>}
        </div>
      ))}
    </div>
  )
}

// ── Reasoning block (universal AI pattern) ─────────────────────
export function ReasoningBlock({ data }: { data: Record<string, string> }) {
  const order: { key: string; label: string }[] = [
    { key: "observation", label: "Observation" },
    { key: "change", label: "Change" },
    { key: "evidence", label: "Evidence" },
    { key: "interpretation", label: "Interpretation" },
    { key: "risk", label: "Risk" },
    { key: "invalidation", label: "Invalidation" },
  ]
  return (
    <div className="divide-y divide-line">
      {order.filter((o) => data[o.key]).map((o) => (
        <div key={o.key} className="py-3.5 grid grid-cols-[110px_1fr] gap-4 first:pt-0">
          <span className="text-[11px] font-semibold tracking-[0.1em] uppercase text-muted pt-0.5">{o.label}</span>
          <p className="text-[13.5px] text-secondary leading-relaxed">{data[o.key]}</p>
        </div>
      ))}
    </div>
  )
}

// ── Section heading ────────────────────────────────────────────
export function SectionEyebrow({ children }: { children: ReactNode }) {
  return <div className="text-[11px] font-semibold tracking-[0.14em] uppercase text-muted mb-3">{children}</div>
}

// ── Empty state ────────────────────────────────────────────────
export function EmptyState({ title, body, action }: { title: string; body: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-16 px-6">
      <div className="w-11 h-11 rounded-full border border-line-strong grid place-items-center mb-4 text-muted">◇</div>
      <h4 className="text-[15px] font-semibold text-ink">{title}</h4>
      <p className="text-[13px] text-muted mt-1.5 max-w-sm leading-relaxed">{body}</p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}

// ── Tabs ───────────────────────────────────────────────────────
export function Tabs({ tabs, active, onChange }: { tabs: string[]; active: string; onChange: (t: string) => void }) {
  return (
    <div className="flex items-center gap-0.5 border-b border-line -mx-0.5" role="tablist">
      {tabs.map((t) => (
        <button
          key={t}
          role="tab"
          aria-selected={active === t}
          onClick={() => onChange(t)}
          className={`relative px-3 h-9 text-[13px] font-medium transition-colors ${active === t ? "text-ink" : "text-muted hover:text-secondary"}`}
        >
          {t}
          {active === t && <span className="absolute left-2 right-2 -bottom-px h-[2px] bg-ink rounded-full" />}
        </button>
      ))}
    </div>
  )
}
