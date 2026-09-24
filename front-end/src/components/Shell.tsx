import { useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { Link, useRouter } from "../lib/router"
import { useAuth } from "../lib/auth"
import { marketApi } from "../lib/api"
import { useResource } from "../lib/api/use-resource"
import { PercentChange } from "./ui"

const nav = [
  { group: "Không gian làm việc", items: [["Tổng quan", "/dashboard"], ["AI War Room", "/agent"], ["Danh mục", "/portfolio"], ["ML Tự hành", "/ml-fund"]] },
  { group: "Thị trường", items: [["Khám phá", "/discovery"], ["Giao dịch", "/trade"], ["Backtest", "/backtest"], ["Thị trường", "/markets"], ["Ngành", "/sectors"], ["Theo dõi", "/watchlist"]] },
  { group: "Thông tin", items: [["Nghiên cứu", "/research"], ["Cộng đồng", "/community"], ["Cài đặt", "/settings"], ["Trợ giúp", "/help"]] },
].map(section => ({ ...section, items: section.items.map(([label, route]) => ({ label, route })) }))

const numberValue = (value: unknown) => Number(value ?? 0)

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
          {[["Hồ sơ", "/profile/me"], ["Cài đặt", "/settings"], ["Trợ giúp", "/help"]].map(([l, r]) => (
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
  const notes = [["Thị trường", "VN-Index đóng cửa +0.72%", "2m", "bg-gain"], ["Danh mục", "Vị thế HPG tăng trưởng +16.1%", "18m", "bg-teal"], ["AI", "Tín hiệu tích lũy gia tăng tại MBB", "34m", "bg-mineral"], ["Khớp lệnh", "Lệnh HPG đang chờ khớp", "1h", "bg-warning"]]
  return <div ref={ref} className="relative"><button aria-label="Thông báo" onClick={() => setOpen((value) => !value)} className="relative grid h-9 w-9 place-items-center rounded-[7px] border border-line bg-surface text-secondary transition-colors hover:border-ink/30 hover:text-ink"><span className="text-[17px] leading-none">♢</span><span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-gain ring-2 ring-surface" /></button>{open && <div className="absolute right-0 z-40 mt-2 w-[340px] rounded-[10px] border border-line-strong bg-surface py-2 shadow-xl shadow-ink/10"><div className="flex items-center justify-between px-3 pb-2"><span className="text-[13px] font-semibold text-ink">Thông báo</span><button className="text-[11px] text-mineral hover:underline">Đánh dấu đã đọc</button></div><div className="border-t border-line">{notes.map(([kind, text, time, tone]) => <button key={text} className="flex w-full gap-3 px-3 py-3 text-left hover:bg-soft/70"><span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${tone}`} /><span className="min-w-0 flex-1"><span className="block text-[11px] font-medium text-muted">{kind}</span><span className="block text-[12.5px] leading-snug text-secondary">{text}</span></span><span className="font-mono text-[10px] text-muted">{time}</span></button>)}</div></div>}</div>
}

function TopBar({ onSearch }: { onSearch: () => void }) {
  const market = useResource(() => Promise.all([marketApi.indices(), marketApi.snapshot()]), [])
  const indices = Array.isArray(market.data?.[0]) ? market.data[0] : (market.data?.[0]?.data ?? market.data?.[0]?.indices ?? [])
  const snapshot = market.data?.[1]?.data ?? market.data?.[1] ?? {}
  const findIndex = (symbol: string) => indices.find((item: Record<string, unknown>) => String(item.symbol ?? item.code ?? item.name).toUpperCase().includes(symbol)) ?? {}
  const vn = findIndex("VNINDEX")
  const vn30 = findIndex("VN30")
  const stat = (label: string, value: string, sub?: ReactNode) => (
    <div className="flex flex-col justify-center px-4 border-l border-line first:border-l-0">
      <span className="text-[10px] font-medium tracking-wide text-muted uppercase leading-none">{label}</span>
      <span className="tnum font-mono text-[13px] text-ink mt-1 leading-none flex items-baseline gap-1.5">{value}{sub}</span>
    </div>
  )
  return (
    <header className="h-14 bg-surface border-b border-line flex items-center pl-5 pr-4 shrink-0">
      <div className="flex items-center min-w-0">
        {stat("VN-Index", numberValue(vn.value ?? vn.close ?? vn.price).toLocaleString("vi-VN", { minimumFractionDigits: 2 }), <PercentChange value={numberValue(vn.changePct ?? vn.change_percent)} className="text-[11px]" arrow={false} />)}
        {stat("VN30", numberValue(vn30.value ?? vn30.close ?? vn30.price).toLocaleString("vi-VN", { minimumFractionDigits: 2 }), <PercentChange value={numberValue(vn30.changePct ?? vn30.change_percent)} className="text-[11px]" arrow={false} />)}
        <div className="hidden lg:contents">
          {stat("Thanh khoản", String(snapshot.liquidity ?? snapshot.totalValue ?? "—"))}
          {stat("Khối ngoại", String(snapshot.foreignFlow ?? snapshot.foreign_net ?? "—"))}
        </div>
        <div className="hidden xl:flex flex-col justify-center px-4 border-l border-line">
          <span className="text-[10px] font-medium tracking-wide text-muted uppercase leading-none">Chế độ thị trường</span>
          <span className="text-[12px] text-ink mt-1 leading-none">{String(snapshot.regime ?? "Chưa có dữ liệu")}</span>
        </div>
      </div>
      <div className="ml-auto flex items-center gap-2">
        <button
          onClick={onSearch}
          className="flex items-center gap-2 h-9 pl-3 pr-2 rounded-[7px] border border-line-strong text-muted hover:border-ink/30 hover:text-secondary transition-colors text-[13px] w-56"
        >
          <span>Tìm mã CP, luận điểm…</span>
          <kbd className="ml-auto text-[10px] font-mono bg-soft border border-line rounded px-1.5 py-0.5 text-secondary">⌘K</kbd>
        </button>
        <NotificationBell />
        <div className="flex items-center gap-1.5 h-9 px-2.5 rounded-[7px] bg-soft text-[11px] text-secondary">
          <span className={`w-1.5 h-1.5 rounded-full ${market.error ? "bg-loss" : "bg-gain animate-pulse"}`} /> {market.error ? "Mất kết nối" : "Trực tiếp"}
        </div>
        <UserMenu />
      </div>
    </header>
  )
}

function Sidebar() {
  const { path } = useRouter()
  return (
    <aside className="w-[228px] shrink-0 bg-surface border-r border-line flex flex-col h-full">
      <Link to="/dashboard" className="h-14 flex items-center gap-2.5 px-5 border-b border-line shrink-0">
        <span className="w-7 h-7 rounded-[7px] bg-ink text-paper grid place-items-center font-serif text-[15px] leading-none">A</span>
        <span className="font-semibold text-[15px] tracking-tight text-ink">AIInvest</span>
        <span className="ml-auto text-[10px] font-mono text-muted">v5.1</span>
      </Link>
      <nav className="flex-1 overflow-y-auto py-3 px-3">
        {nav.map((section) => (
          <div key={section.group} className="mb-4">
            <div className="text-[10px] font-semibold tracking-[0.14em] uppercase text-muted px-2 mb-1.5">{section.group}</div>
            {section.items.map((item) => {
              const active = path === item.route || (item.route !== "/dashboard" && path.startsWith(item.route) && item.route.length > 8)
              return (
                <Link
                  key={item.route}
                  to={item.route}
                  className={`flex items-center h-8 px-2 rounded-[6px] text-[13px] transition-colors ${active ? "bg-soft text-ink font-medium" : "text-secondary hover:text-ink hover:bg-soft/60"}`}
                >
                  <span className={`w-1 h-4 rounded-full mr-2.5 transition-colors ${active ? "bg-mineral" : "bg-transparent"}`} />
                  {item.label}
                </Link>
              )
            })}
          </div>
        ))}
      </nav>
    </aside>
  )
}

function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { navigate } = useRouter()
  const [q, setQ] = useState("")
  const allItems = useMemo(() => {
    const routes = nav.flatMap((s) => s.items.map((i) => ({ type: "Điều hướng", label: i.label, route: i.route, hint: s.group })))
    const commands = [
      { type: "Lệnh thao tác", label: "So sánh HPG và HSG", route: "/discovery", hint: "So sánh" },
      { type: "Lệnh thao tác", label: "Kiểm tra rủi ro danh mục", route: "/portfolio", hint: "Danh mục" },
      { type: "Lệnh thao tác", label: "Kiểm tra sổ lệnh đang chờ", route: "/portfolio", hint: "Danh mục" },
    ]
    return [...commands, ...routes]
  }, [])

  const results = q ? allItems.filter((i) => i.label.toLowerCase().includes(q.toLowerCase())) : allItems.slice(0, 10)
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
            autoFocus value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="Tìm kiếm hoặc gõ lệnh… (⌘K)"
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
                  onClick={() => { navigate(r.route); onClose() }}
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
  const [cmd, setCmd] = useState(false)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setCmd((v) => !v) }
      if (e.key === "Escape") setCmd(false)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])
  return (
    <div className="flex h-full bg-paper">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <TopBar onSearch={() => setCmd(true)} />
        <main className="flex-1 overflow-y-auto">{children}</main>
      </div>
      <CommandPalette open={cmd} onClose={() => setCmd(false)} />
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
