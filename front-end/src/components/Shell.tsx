import { useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import {
  BookOpenText,
  BrainCircuit,
  BriefcaseBusiness,
  ChartCandlestick,
  Compass,
  Cpu,
  Layers,
  PanelLeftOpen,
  type LucideIcon,
} from "lucide-react"
import { Link, STOCK_MODAL_EVENT, useRouter, type StockModalEventDetail } from "../lib/router"
import { useAuth } from "../lib/auth"
import { marketApi } from "../lib/api"
import { useResource } from "../lib/api/use-resource"
import { StockDetailModal } from "./StockDetailView"
import { PercentChange } from "./ui"

interface NavItem {
  label: string
  route: string
  icon: LucideIcon
}

interface NavGroup {
  group: string
  items: NavItem[]
}

const nav: NavGroup[] = [
  {
    group: "Không gian làm việc",
    items: [
      { label: "AI War Room", route: "/agent", icon: Cpu },
      { label: "Danh mục", route: "/portfolio", icon: BriefcaseBusiness },
      { label: "ML Tự hành", route: "/ml-fund", icon: BrainCircuit },
    ],
  },
  {
    group: "Thị trường",
    items: [
      { label: "Khám phá", route: "/discovery", icon: Compass },
      { label: "Giao dịch", route: "/trade", icon: ChartCandlestick },
      { label: "Ngành", route: "/sectors", icon: Layers },
    ],
  },
  {
    group: "Thông tin",
    items: [
      { label: "Nghiên cứu", route: "/research", icon: BookOpenText },
    ],
  },
]

const numberValue = (value: unknown) => value == null || value === "" || !Number.isFinite(Number(value)) ? null : Number(value)

function UserMenu() {
  const { name, logout } = useAuth()
  const { navigate } = useRouter()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const initials = name.trim().split(/\s+/).slice(-2).map((w) => w[0]?.toUpperCase()).join("") || "AI"
  useEffect(() => {
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener("mousedown", onDoc)
    return () => document.removeEventListener("mousedown", onDoc)
  }, [])
  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen((o) => !o)} className="w-9 h-9 rounded-full bg-teal/15 text-teal grid place-items-center text-[12px] font-semibold border border-teal/25 hover:border-teal/50 transition-colors">{initials}</button>
      {open && (
        <div className="absolute right-0 mt-2 w-52 bg-surface border border-line-strong rounded-[10px] shadow-xl shadow-ink/10 py-1.5 z-40">
          <div className="px-3 py-2 border-b border-line">
            <div className="text-[13px] font-medium text-ink truncate">{name}</div>
            <div className="text-[11px] text-muted">Không gian cá nhân</div>
          </div>
          {[["Hồ sơ", "/profile/me"]].map(([l, r]) => (
            <button key={l} onClick={() => { navigate(r); setOpen(false) }} className="w-full text-left px-3 h-8 text-[13px] text-secondary hover:bg-soft hover:text-ink transition-colors">{l}</button>
          ))}
          <div className="border-t border-line mt-1 pt-1">
            <button onClick={logout} className="w-full text-left px-3 h-8 text-[13px] text-loss hover:bg-loss/8 transition-colors">Đăng xuất</button>
          </div>
        </div>
      )}
    </div>
  )
}

function NotificationBell() {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const onDoc = (event: MouseEvent) => { if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false) }
    document.addEventListener("mousedown", onDoc)
    return () => document.removeEventListener("mousedown", onDoc)
  }, [])
  return (
    <div ref={ref} className="relative">
      <button aria-label="Thông báo" aria-expanded={open} onClick={() => setOpen((value) => !value)} className="grid h-9 w-9 place-items-center rounded-[7px] border border-line bg-surface text-secondary transition-colors hover:border-ink/30 hover:text-ink">
        <span className="text-[17px] leading-none">♢</span>
      </button>
      {open && (
        <div className="absolute right-0 z-40 mt-2 w-[320px] rounded-[10px] border border-line-strong bg-surface p-4 shadow-xl shadow-ink/10">
          <h2 className="text-[13px] font-semibold text-ink">Thông báo</h2>
          <p className="mt-2 text-[12px] leading-relaxed text-secondary">Chưa có thông báo từ hệ thống.</p>
        </div>
      )}
    </div>
  )
}

function TopBar({ onSearch }: { onSearch: () => void }) {
  const { navigate } = useRouter()
  const market = useResource(() => marketApi.indices(), [])
  const indices = Array.isArray(market.data) ? market.data : (market.data?.data ?? market.data?.indices ?? [])
  const findIndex = (symbol: string) => indices.find((item: Record<string, unknown>) => String(item.symbol ?? item.code ?? item.name).toUpperCase().includes(symbol)) ?? {}
  const vn = findIndex("VNINDEX")
  const rawIndexValue = numberValue(vn.value ?? vn.close ?? vn.price)
  const indexValue = rawIndexValue != null && rawIndexValue > 0 ? rawIndexValue : null
  const changePct = numberValue(vn.changePct ?? vn.changePercent ?? vn.change_pct ?? vn.change_percent)
  let receivedAt = Number(vn.receivedAt ?? 0)
  if (receivedAt > 0 && receivedAt < 1_000_000_000_000) receivedAt *= 1000
  const age = Date.now() - receivedAt
  const isLive = Number.isFinite(receivedAt) && receivedAt > 0 && age >= 0 && age < 30_000
  const stat = (label: string, value: string, sub?: ReactNode) => (
    <div className="flex flex-col justify-center px-4 border-l border-line first:border-l-0">
      <span className="text-[10px] font-medium tracking-wide text-muted uppercase leading-none">{label}</span>
      <span className="tnum font-mono text-[13px] text-ink mt-1 leading-none flex items-baseline gap-1.5">{value}{sub}</span>
    </div>
  )
  return (
    <header className="h-14 bg-surface border-b border-line flex items-center gap-2 px-4 md:pl-5 shrink-0">
      <select aria-label="Điều hướng" value="" onChange={event => navigate(event.target.value)} className="h-9 min-w-0 rounded-[7px] border border-line bg-surface px-2 text-sm text-ink md:hidden">
        <option value="" disabled>Điều hướng</option>
        {nav.map(section => <optgroup key={section.group} label={section.group}>{section.items.map(item => <option key={item.route} value={item.route}>{item.label}</option>)}</optgroup>)}
      </select>
      <div className="hidden items-center min-w-0 md:flex">
        {stat("VN-Index", indexValue === null ? "—" : indexValue.toLocaleString("vi-VN", { minimumFractionDigits: 2 }), changePct === null ? <span className="text-muted">—</span> : <PercentChange value={changePct} className="text-[11px]" arrow={false} />)}
      </div>
      <div className="ml-auto flex items-center gap-2">
        <button
          onClick={onSearch}
          aria-label="Tìm mã cổ phiếu hoặc luận điểm"
          className="flex items-center gap-2 h-9 px-3 md:pr-2 rounded-[7px] border border-line-strong text-muted hover:border-ink/30 hover:text-secondary transition-colors text-[13px] md:w-56"
        >
          <span className="md:hidden">Tìm kiếm</span><span className="hidden md:inline">Tìm mã CP, luận điểm…</span>
          <kbd className="ml-auto hidden md:inline text-[10px] font-mono bg-soft border border-line rounded px-1.5 py-0.5 text-secondary">⌘K</kbd>
        </button>
        <NotificationBell />
        <div className="hidden md:flex items-center gap-1.5 h-9 px-2.5 rounded-[7px] bg-soft text-[11px] text-secondary">
          <span className={`w-1.5 h-1.5 rounded-full ${market.error ? "bg-loss" : isLive ? "bg-gain animate-pulse" : "bg-muted"}`} /> {market.error ? "Mất kết nối" : isLive ? "Trực tiếp" : "Dữ liệu gần nhất"}
        </div>
        <UserMenu />
      </div>
    </header>
  )
}

function Sidebar() {
  const { path } = useRouter()
  const [collapsed, setCollapsed] = useState(true)
  const [hoveredTip, setHoveredTip] = useState<{ label: string; top: number; left: number } | null>(null)

  const showTip = (label: string, el: HTMLElement) => {
    if (!collapsed) return
    const rect = el.getBoundingClientRect()
    setHoveredTip({
      label,
      top: rect.top + rect.height / 2,
      left: rect.right + 10,
    })
  }

  const hideTip = () => setHoveredTip(null)

  return (
    <aside
      aria-label="Thanh điều hướng không gian làm việc"
      className={`hidden md:flex shrink-0 bg-surface border-r border-line flex-col h-full transition-[width] duration-200 ease-out ${
        collapsed ? "w-[64px]" : "w-[224px]"
      }`}
    >
      <div
        className="border-b border-line shrink-0"
        onMouseEnter={(e) => showTip("AIInvest · AI War Room", e.currentTarget)}
        onMouseLeave={hideTip}
        onFocusCapture={(e) => showTip("AIInvest · AI War Room", e.currentTarget)}
        onBlurCapture={hideTip}
      >
        <Link
          to="/agent"
          onClick={hideTip}
          title="AIInvest — Về AI War Room"
          className={`h-14 flex items-center ${
            collapsed ? "justify-center px-2" : "gap-2.5 px-5"
          }`}
        >
          <span className="w-7 h-7 rounded-[7px] bg-ink text-paper grid place-items-center font-serif text-[15px] leading-none shrink-0">
            A
          </span>
          {!collapsed && (
            <>
              <span className="font-semibold text-[15px] tracking-tight text-ink">AIInvest</span>
              <span className="ml-auto text-[10px] font-mono text-muted">v5.1</span>
            </>
          )}
        </Link>
      </div>

      <nav
        onScroll={hideTip}
        className={`flex-1 overflow-y-auto overflow-x-hidden ${collapsed ? "py-3 px-2" : "py-3 px-3"}`}
      >
        {nav.map((section, groupIdx) => (
          <div key={section.group} className={collapsed ? (groupIdx > 0 ? "mt-2.5 pt-2.5 border-t border-line" : "") : "mb-4"}>
            {!collapsed && (
              <div className="text-[10px] font-semibold tracking-[0.14em] uppercase text-muted px-2 mb-1.5">
                {section.group}
              </div>
            )}
            <div className={collapsed ? "flex flex-col items-center gap-1" : "space-y-0.5"}>
              {section.items.map((item) => {
                const active = path === item.route || path.startsWith(`${item.route}/`)
                const Icon = item.icon
                return (
                  <div
                    key={item.route}
                    className={collapsed ? "flex justify-center" : undefined}
                    onMouseEnter={(e) => showTip(item.label, e.currentTarget)}
                    onMouseLeave={hideTip}
                    onFocusCapture={(e) => showTip(item.label, e.currentTarget)}
                    onBlurCapture={hideTip}
                  >
                    <Link
                      to={item.route}
                      onClick={hideTip}
                      className={
                        collapsed
                          ? `relative flex items-center justify-center h-10 w-10 rounded-[8px] transition-colors ${
                              active
                                ? "bg-soft text-ink border border-line-strong/75"
                                : "text-secondary hover:text-ink hover:bg-soft/70"
                            }`
                          : `flex w-full items-center h-8 px-2 rounded-[6px] text-[13px] transition-colors ${
                              active
                                ? "bg-soft text-ink font-medium"
                                : "text-secondary hover:text-ink hover:bg-soft/60"
                            }`
                      }
                    >
                      {collapsed ? (
                        <span className="grid h-full w-full place-items-center" aria-label={item.label}>
                          {active && (
                            <span className="absolute left-0.5 top-2 bottom-2 w-[3px] rounded-full bg-mineral" />
                          )}
                          <Icon className="h-[19px] w-[19px] shrink-0" strokeWidth={1.75} aria-hidden="true" />
                        </span>
                      ) : (
                        <>
                          <span className={`w-1 h-4 rounded-full mr-2.5 transition-colors ${active ? "bg-mineral" : "bg-transparent"}`} />
                          <span>{item.label}</span>
                        </>
                      )}
                    </Link>
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </nav>

      <div className="border-t border-line p-2 shrink-0">
        <button
          type="button"
          onClick={() => {
            hideTip()
            setCollapsed((prev) => !prev)
          }}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Mở rộng thanh điều hướng" : "Thu gọn thanh điều hướng"}
          onMouseEnter={(e) => collapsed && showTip("Mở rộng thanh điều hướng", e.currentTarget)}
          onMouseLeave={hideTip}
          onFocus={(e) => collapsed && showTip("Mở rộng thanh điều hướng", e.currentTarget)}
          onBlur={hideTip}
          className={
            collapsed
              ? "mx-auto grid h-10 w-10 place-items-center rounded-[8px] text-secondary hover:bg-soft hover:text-ink transition-colors"
              : "flex h-9 w-full items-center justify-between rounded-[7px] px-3 text-[12.5px] font-medium text-secondary hover:bg-soft hover:text-ink transition-colors"
          }
        >
          {collapsed ? (
            <PanelLeftOpen className="h-[19px] w-[19px]" strokeWidth={1.75} aria-hidden="true" />
          ) : (
            <>
              <span>Thu gọn menu</span>
              <span className="font-mono text-[11px] text-muted">«</span>
            </>
          )}
        </button>
      </div>

      {collapsed && hoveredTip && (
        <div
          role="tooltip"
          style={{ top: hoveredTip.top, left: hoveredTip.left }}
          className="fixed z-[90] -translate-y-1/2 pointer-events-none rounded-[7px] bg-ink px-2.5 py-1.5 text-[12.5px] font-medium text-paper shadow-lg shadow-ink/15 whitespace-nowrap"
        >
          {hoveredTip.label}
        </div>
      )}
    </aside>
  )
}

function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { navigate } = useRouter()
  const [q, setQ] = useState("")
  const tickerCandidate = useMemo(() => {
    const trimmed = q.trim().toUpperCase()
    return /^[A-Z0-9]{3,5}$/.test(trimmed) ? trimmed : null
  }, [q])

  const allItems = useMemo(() => {
    const routes = nav.flatMap((s) => s.items.map((i) => ({ type: "Điều hướng", label: i.label, route: i.route, hint: s.group })))
    const commands = [
      { type: "Lệnh thao tác", label: "So sánh HPG và HSG", route: "/discovery", hint: "So sánh" },
      { type: "Lệnh thao tác", label: "Kiểm tra rủi ro danh mục", route: "/portfolio", hint: "Danh mục" },
      { type: "Lệnh thao tác", label: "Kiểm tra sổ lệnh đang chờ", route: "/trade", hint: "Giao dịch" },
    ]
    return [...commands, ...routes]
  }, [])

  const results = useMemo(() => {
    const filtered = q ? allItems.filter((i) => i.label.toLowerCase().includes(q.toLowerCase())) : allItems.slice(0, 10)
    if (tickerCandidate) {
      return [
        {
          type: "Cổ phiếu",
          label: `Mở bảng phân tích ${tickerCandidate} ngay tại trang`,
          route: `/stock/${tickerCandidate}`,
          hint: "Popup nghiên cứu",
        },
        {
          type: "Cổ phiếu",
          label: `Đặt lệnh giao dịch ${tickerCandidate}`,
          route: `/trade?symbol=${tickerCandidate}`,
          hint: "Khớp lệnh LIVE",
        },
        ...filtered,
      ]
    }
    return filtered
  }, [allItems, q, tickerCandidate])

  const grouped = results.reduce<Record<string, typeof results>>((acc, r) => {
    (acc[r.type] ??= []).push(r)
    return acc
  }, {})

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[12vh] px-4" role="dialog" aria-modal>
      <div className="absolute inset-0 bg-ink/25 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-full max-w-xl bg-surface border border-line-strong rounded-[12px] shadow-2xl shadow-ink/10 overflow-hidden">
        <div className="flex items-center gap-3 px-4 min-h-[52px] border-b border-line py-3.5">
          <span className="text-muted">⌘</span>
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && results[0]) {
                e.preventDefault()
                navigate(results[0].route)
                setQ("")
                onClose()
              }
            }}
            placeholder="Nhập mã CP (VD: HPG, FPT, VCB) hoặc gõ lệnh… (⌘K)"
            className="flex-1 bg-transparent outline-none text-[14px] text-ink placeholder:text-muted"
          />
          <kbd className="text-[10px] font-mono bg-soft border border-line rounded px-1.5 py-0.5 text-muted">ESC</kbd>
        </div>
        <div className="max-h-[52vh] overflow-y-auto py-2">
          {Object.entries(grouped).map(([type, items]) => (
            <div key={type} className="px-2 mb-1">
              <div className="text-[10px] font-semibold tracking-[0.12em] uppercase text-muted px-2 py-1.5">{type}</div>
              {items.map((r) => (
                <button
                  key={r.type + r.label}
                  onClick={() => { navigate(r.route); setQ(""); onClose() }}
                  className="w-full flex items-center gap-3 px-2 h-9 rounded-[6px] text-left hover:bg-soft transition-colors group"
                >
                  <span className="text-[13px] text-ink">{r.label}</span>
                  <span className="ml-auto text-[11px] text-muted">{r.hint}</span>
                </button>
              ))}
            </div>
          ))}
          {results.length === 0 && <div className="px-4 py-8 text-center text-[13px] text-muted">Không tìm thấy kết quả phù hợp. Hãy thử gõ mã CP như HPG.</div>}
        </div>
      </div>
    </div>
  )
}

export function Shell({ children }: { children: ReactNode }) {
  const { path } = useRouter()
  const [cmd, setCmd] = useState(false)
  const [modalState, setModalState] = useState<{ symbol: string | null; symbols: string[] }>({
    symbol: null,
    symbols: [],
  })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setCmd((v) => !v) }
      if (e.key === "Escape") setCmd(false)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  useEffect(() => {
    const onOpenModal = (e: Event) => {
      const detail = (e as CustomEvent<StockModalEventDetail>).detail
      if (!detail?.symbol) return
      setModalState({
        symbol: detail.symbol.trim().toUpperCase(),
        symbols: Array.isArray(detail.symbols) && detail.symbols.length > 0 ? detail.symbols : [],
      })
    }
    window.addEventListener(STOCK_MODAL_EVENT, onOpenModal)
    return () => window.removeEventListener(STOCK_MODAL_EVENT, onOpenModal)
  }, [])

  useEffect(() => {
    setModalState((prev) => (prev.symbol ? { symbol: null, symbols: [] } : prev))
  }, [path])

  const activeIdx = modalState.symbol ? modalState.symbols.indexOf(modalState.symbol) : -1

  return (
    <div className="flex h-full bg-paper">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <TopBar onSearch={() => setCmd(true)} />
        <main className="flex-1 overflow-y-auto">{children}</main>
      </div>
      <CommandPalette open={cmd} onClose={() => setCmd(false)} />
      {modalState.symbol && (
        <StockDetailModal
          open={Boolean(modalState.symbol)}
          symbol={modalState.symbol}
          onClose={() => setModalState({ symbol: null, symbols: [] })}
          onSelectPrev={
            activeIdx > 0
              ? () => setModalState((s) => ({ ...s, symbol: s.symbols[activeIdx - 1] ?? s.symbol }))
              : undefined
          }
          onSelectNext={
            activeIdx >= 0 && activeIdx < modalState.symbols.length - 1
              ? () => setModalState((s) => ({ ...s, symbol: s.symbols[activeIdx + 1] ?? s.symbol }))
              : undefined
          }
        />
      )}
    </div>
  )
}

export function Page({ title, sub, actions, children }: { title: string; sub?: string; actions?: ReactNode; rail?: boolean; children: ReactNode }) {
  return (
    <div className="max-w-[1360px] mx-auto px-6 lg:px-8 py-6">
      <div className="flex items-end justify-between gap-4 mb-5">
        <div>
          <h1 className="text-[26px] font-semibold tracking-tight text-ink leading-none">{title}</h1>
          {sub && <p className="text-[13.5px] text-muted mt-2">{sub}</p>}
        </div>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
      {children}
    </div>
  )
}
