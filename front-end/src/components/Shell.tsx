"use client"

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react"
import { createPortal } from "react-dom"
import {
  BookOpenText,
  BriefcaseBusiness,
  ChartCandlestick,
  Compass,
  Cpu,
  Layers,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  type LucideIcon,
} from "lucide-react"
import { Link, STOCK_MODAL_EVENT, useRouter, type StockModalEventDetail } from "../lib/router"
import { useAuth } from "../lib/auth"
import { marketApi } from "../lib/api"
import { useResource } from "../lib/api/use-resource"
import { useRealtimeMarket } from "../lib/use-realtime"
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
    group: "Phân tích & Tín hiệu",
    items: [
      { label: "AI War Room", route: "/agent", icon: Cpu },
      { label: "Hồ sơ Luận điểm", route: "/research", icon: BookOpenText },
      { label: "Bộ lọc HOSE", route: "/discovery", icon: Compass },
      { label: "Dòng tiền Ngành", route: "/sectors", icon: Layers },
    ],
  },
  {
    group: "Giao dịch & Tài sản",
    items: [
      { label: "Khớp lệnh", route: "/trade", icon: ChartCandlestick },
      { label: "Danh mục", route: "/portfolio", icon: BriefcaseBusiness },
    ],
  },
]

const SIDEBAR_STORAGE_KEY = "aiinvest:sidebar-collapsed"
const SIDEBAR_EVENT = "aiinvest:sidebar-change"

function subscribeSidebar(callback: () => void) {
  window.addEventListener("storage", callback)
  window.addEventListener(SIDEBAR_EVENT, callback)
  return () => {
    window.removeEventListener("storage", callback)
    window.removeEventListener(SIDEBAR_EVENT, callback)
  }
}

function getSidebarCollapsedSnapshot(): boolean {
  try {
    const raw = window.localStorage.getItem(SIDEBAR_STORAGE_KEY)
    return raw === null ? true : raw === "1"
  } catch {
    return true
  }
}

function writeSidebarCollapsed(collapsed: boolean) {
  try {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, collapsed ? "1" : "0")
    window.dispatchEvent(new Event(SIDEBAR_EVENT))
  } catch {
    // ignore storage errors
  }
}

const noopSubscribe = () => () => {}

function useIsApplePlatform(): boolean {
  return useSyncExternalStore(
    noopSubscribe,
    () =>
      typeof navigator !== "undefined" &&
      /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent),
    () => false,
  )
}

const numberValue = (value: unknown) =>
  value == null || value === "" || !Number.isFinite(Number(value)) ? null : Number(value)

function getHoseSessionPhase(now: Date): string {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Ho_Chi_Minh",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(now)
  const weekday = parts.find((p) => p.type === "weekday")?.value ?? "Mon"
  if (weekday === "Sat" || weekday === "Sun") return "Nghỉ cuối tuần"
  const hh = Number(parts.find((p) => p.type === "hour")?.value ?? 0)
  const mm = Number(parts.find((p) => p.type === "minute")?.value ?? 0)
  const mins = hh * 60 + mm
  if (mins >= 9 * 60 && mins < 9 * 60 + 15) return "ATO"
  if (mins >= 9 * 60 + 15 && mins < 11 * 60 + 30) return "Phiên sáng"
  if (mins >= 11 * 60 + 30 && mins < 13 * 60) return "Nghỉ trưa"
  if (mins >= 13 * 60 && mins < 14 * 60 + 30) return "Phiên chiều"
  if (mins >= 14 * 60 + 30 && mins < 14 * 60 + 45) return "ATC"
  return "Đóng cửa"
}

function BrandMark() {
  return (
    <span className="grid h-8 w-8 shrink-0 place-items-center rounded-[8px] bg-ink text-paper shadow-[inset_0_1px_0_rgba(255,255,255,0.14)]">
      <svg viewBox="0 0 24 24" className="h-[17px] w-[17px]" fill="none" aria-hidden="true">
        <path
          d="M12 4.5L5.75 19.5H8.65L10.05 15.85H13.95L15.35 19.5H18.25L12 4.5ZM10.9 13.55L12 10.45L13.1 13.55H10.9Z"
          fill="currentColor"
        />
      </svg>
    </span>
  )
}

function UserMenu() {
  const { name, logout } = useAuth()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const initials =
    name
      .trim()
      .split(/\s+/)
      .slice(-2)
      .map((w) => w[0]?.toUpperCase())
      .join("") || "AI"

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", onDoc)
    return () => document.removeEventListener("mousedown", onDoc)
  }, [])

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label="Tài khoản"
        aria-expanded={open}
        className="grid h-8 w-8 place-items-center rounded-full border border-line-strong bg-soft text-[11.5px] font-semibold text-ink transition-[background-color,border-color,transform] duration-[120ms] hover:border-ink/40 hover:bg-raised active:scale-[0.97]"
      >
        {initials}
      </button>
      {open && (
        <div className="popover-enter origin-top-right absolute right-0 mt-2 w-52 rounded-[10px] border border-line-strong bg-surface py-1.5 shadow-xl shadow-ink/10 z-40">
          <div className="px-3 py-2 border-b border-line">
            <div className="text-[13px] font-medium text-ink truncate">{name}</div>
            <div className="text-[11px] text-secondary">Không gian làm việc HOSE</div>
          </div>
          <div className="pt-1">
            <button
              type="button"
              onClick={logout}
              className="w-full text-left px-3 h-8 text-[13px] text-loss hover:bg-loss/8 transition-colors duration-[120ms]"
            >
              Đăng xuất
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function TopBar({ onSearch }: { onSearch: () => void }) {
  const { path, navigate } = useRouter()
  const isApple = useIsApplePlatform()
  const market = useResource(() => marketApi.indices(), [])
  const [nowMs, setNowMs] = useState(() => Date.now())

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 15_000)
    return () => window.clearInterval(timer)
  }, [])

  const initialIndices = useMemo(() => {
    const raw = market.data as
      | Array<Record<string, unknown>>
      | { data?: Array<Record<string, unknown>>; indices?: Array<Record<string, unknown>> }
      | null
    const list = Array.isArray(raw) ? raw : (raw?.data ?? raw?.indices ?? [])
    const findIndex = (sym: string) =>
      list.find(
        (item) =>
          String(item.symbol ?? item.code ?? item.name ?? "")
            .toUpperCase()
            .replaceAll("-", "") === sym,
      ) ?? null
    const vni = findIndex("VNINDEX")
    const vn30 = findIndex("VN30") ?? findIndex("VN30_ESTIMATED")
    const vniNum = numberValue(vni?.value ?? vni?.close ?? vni?.price)
    const vn30Num = numberValue(vn30?.value ?? vn30?.close ?? vn30?.price)
    return {
      vnIndexVal:
        vniNum != null && vniNum > 0
          ? vniNum.toLocaleString("vi-VN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
          : "—",
      vnIndexPct: numberValue(vni?.changePct ?? vni?.changePercent ?? vni?.change_pct),
      vn30Val:
        vn30Num != null && vn30Num > 0
          ? vn30Num.toLocaleString("vi-VN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
          : undefined,
      vn30Pct: numberValue(vn30?.changePct ?? vn30?.changePercent ?? vn30?.change_pct),
    }
  }, [market.data])

  const { indices, isLive, breadth, liquidity, indexFlash } = useRealtimeMarket(initialIndices, {
    snapshot: false,
    heatmap: false,
  })
  const sessionPhase = getHoseSessionPhase(new Date(nowMs))

  const allNavItems = useMemo(() => nav.flatMap((g) => g.items), [])
  const activeRoute =
    allNavItems.find((i) => path === i.route || path.startsWith(`${i.route}/`))?.route ?? ""

  const stat = (
    label: string,
    value: nodeOrStr,
    sub?: ReactNode,
    className = "",
    flashKey?: string,
    flashClass = "",
  ) => (
    <div
      className={`flex flex-col justify-center px-3.5 border-l border-line first:border-l-0 shrink-0 ${className}`}
    >
      <span className="text-[9.5px] font-semibold tracking-[0.06em] text-secondary uppercase leading-none">
        {label}
      </span>
      <span
        key={flashKey}
        className={`tnum font-mono text-[12.5px] text-ink mt-1 leading-none flex items-baseline gap-1.5 rounded px-0.5 -mx-0.5 ${flashClass}`}
      >
        {value}
        {sub}
      </span>
    </div>
  )

  const vniFlashClass =
    indexFlash.dir === "gain" ? "tick-up" : indexFlash.dir === "loss" ? "tick-down" : ""

  const totalBillion = numberValue(liquidity?.totalValueBillion)

  return (
    <header className="sticky top-0 z-40 h-12 bg-surface border-b border-line flex items-center gap-2 px-3.5 md:px-5 shrink-0">
      <select
        aria-label="Điều hướng"
        value={activeRoute}
        onChange={(event) => {
          if (event.target.value) navigate(event.target.value)
        }}
        className="h-8 min-w-0 max-w-[170px] rounded-[7px] border border-line bg-surface px-2 text-[12.5px] font-medium text-ink md:hidden"
      >
        {!activeRoute && (
          <option value="" disabled>
            Điều hướng
          </option>
        )}
        {nav.map((section) => (
          <optgroup key={section.group} label={section.group}>
            {section.items.map((item) => (
              <option key={item.route} value={item.route}>
                {item.label}
              </option>
            ))}
          </optgroup>
        ))}
      </select>

      {/* Live HOSE Market Tape */}
      <div className="hidden md:flex items-center min-w-0 overflow-x-auto no-scrollbar">
        <div
          className="inline-flex items-center gap-1.5 h-6 px-2.5 mr-3 rounded-full border border-line bg-paper text-[11px] font-medium text-ink shrink-0"
          title={isLive ? "Đang nhận dữ liệu trực tiếp" : "Trạng thái phiên HOSE"}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              market.error ? "bg-loss" : isLive ? "bg-gain pulse-subtle" : "bg-muted"
            }`}
          />
          <span suppressHydrationWarning>
            {market.error ? "Mất kết nối" : isLive ? `Trực tiếp · ${sessionPhase}` : sessionPhase}
          </span>
        </div>

        {stat(
          "VN-Index",
          indices?.vnIndexVal ?? "—",
          indices?.vnIndexPct == null ? (
            <span className="text-muted">—</span>
          ) : (
            <PercentChange value={indices.vnIndexPct} className="text-[11px]" arrow={false} />
          ),
          "",
          `vni-${indexFlash.seq}`,
          vniFlashClass,
        )}

        {stat(
          "VN30",
          indices?.vn30Val ?? "—",
          indices?.vn30Pct == null ? (
            <span className="text-muted">—</span>
          ) : (
            <PercentChange value={indices.vn30Pct} className="text-[11px]" arrow={false} />
          ),
          "hidden lg:flex",
        )}

        {stat(
          "Độ rộng",
          breadth ? (
            <span className="flex items-center gap-1">
              <span className="text-gain">{breadth.advancers}↑</span>
              <span className="text-muted">·</span>
              <span className="text-loss">{breadth.decliners}↓</span>
            </span>
          ) : (
            "—"
          ),
          undefined,
          "hidden lg:flex",
        )}

        {stat(
          "GTGD HOSE",
          totalBillion != null
            ? `${totalBillion.toLocaleString("vi-VN", { maximumFractionDigits: 1 })} tỷ`
            : "—",
          undefined,
          "hidden xl:flex",
        )}
      </div>

      {/* Unified Right Action Cluster */}
      <div className="ml-auto flex items-center gap-2.5 shrink-0">
        <button
          type="button"
          onClick={onSearch}
          aria-label="Tìm mã cổ phiếu hoặc điều hướng nhanh"
          className="flex items-center gap-2 h-8 px-2.5 rounded-[7px] border border-line bg-paper/80 text-secondary hover:border-line-strong hover:text-ink transition-[background-color,border-color,color,transform] duration-[120ms] active:scale-[0.99] text-[12.5px] md:w-56 lg:w-60"
        >
          <Search className="h-3.5 w-3.5 shrink-0 text-muted" strokeWidth={1.85} aria-hidden="true" />
          <span className="md:hidden">Tìm mã</span>
          <span className="hidden md:inline truncate">Tìm mã CP, điều hướng…</span>
          <kbd className="ml-auto hidden md:inline text-[10px] font-mono bg-surface border border-line rounded px-1.5 py-0.5 text-secondary shrink-0">
            {isApple ? "⌘K" : "Ctrl K"}
          </kbd>
        </button>
        <div className="h-4 w-px bg-line shrink-0" aria-hidden="true" />
        <UserMenu />
      </div>
    </header>
  )
}

type nodeOrStr = ReactNode

function Sidebar({ suppressTips = false }: { suppressTips?: boolean }) {
  const { path } = useRouter()
  const isApple = useIsApplePlatform()
  const collapsed = useSyncExternalStore(subscribeSidebar, getSidebarCollapsedSnapshot, () => true)
  const [hoveredTip, setHoveredTip] = useState<{ label: string; top: number; left: number } | null>(
    null,
  )
  const tipTimerRef = useRef<number | null>(null)

  const clearTipTimer = () => {
    if (tipTimerRef.current !== null) {
      window.clearTimeout(tipTimerRef.current)
      tipTimerRef.current = null
    }
  }

  const showTip = (label: string, el: HTMLElement) => {
    if (!collapsed || suppressTips) return
    clearTipTimer()
    const rect = el.getBoundingClientRect()
    tipTimerRef.current = window.setTimeout(() => {
      setHoveredTip({
        label,
        top: rect.top + rect.height / 2,
        left: rect.right + 10,
      })
    }, 80)
  }

  const hideTip = () => {
    clearTipTimer()
    setHoveredTip(null)
  }

  useEffect(() => {
    if (!hoveredTip) return
    const dismiss = () => setHoveredTip(null)
    window.addEventListener("scroll", dismiss, { passive: true })
    window.addEventListener("blur", dismiss)
    return () => {
      window.removeEventListener("scroll", dismiss)
      window.removeEventListener("blur", dismiss)
    }
  }, [hoveredTip])

  useEffect(() => {
    return () => clearTipTimer()
  }, [])

  const activeTip = collapsed && !suppressTips ? hoveredTip : null

  return (
    <aside
      aria-label="Thanh điều hướng không gian làm việc"
      className={`hidden md:flex md:sticky md:top-0 md:z-40 md:h-dvh shrink-0 bg-surface border-r border-line flex-col transition-[width] duration-[160ms] ease-[var(--ease-out-expo)] ${
        collapsed ? "w-14" : "w-[220px]"
      }`}
    >
      {/* Top Brand Cell aligned cleanly with 48px TopBar */}
      <div
        className="h-12 border-b border-line shrink-0 flex items-center px-3"
        onMouseEnter={(e) => showTip("AIInvest · AI War Room", e.currentTarget)}
        onMouseLeave={hideTip}
        onFocusCapture={(e) => showTip("AIInvest · AI War Room", e.currentTarget)}
        onBlurCapture={hideTip}
      >
        <Link
          to="/agent"
          onClick={hideTip}
          title="AIInvest — Về AI War Room"
          className="flex items-center gap-2.5 w-full min-w-0 overflow-hidden"
        >
          <BrandMark />
          <span
            className={`font-semibold text-[15px] tracking-tight text-ink whitespace-nowrap transition-[opacity,transform] duration-[140ms] ease-[var(--ease-out-expo)] ${
              collapsed ? "opacity-0 -translate-x-1 pointer-events-none" : "opacity-100 translate-x-0"
            }`}
          >
            AIInvest
          </span>
        </Link>
      </div>

      {/* Navigation Items with Stationary Icon Coordinates */}
      <nav
        onScroll={hideTip}
        className="flex-1 overflow-y-auto overflow-x-hidden bg-surface overscroll-contain py-3 space-y-1"
      >
        {nav.map((section, groupIdx) => (
          <div key={section.group}>
            {groupIdx > 0 && collapsed && (
              <div className="w-5 h-px mx-auto my-2 bg-line" aria-hidden="true" />
            )}
            <div
              className={`overflow-hidden transition-[height,opacity] duration-[140ms] ease-out ${
                collapsed ? "h-0 opacity-0" : `h-6 opacity-100 ${groupIdx > 0 ? "mt-2" : ""}`
              }`}
            >
              <div className="px-3.5 text-[10px] font-semibold tracking-[0.14em] uppercase text-muted whitespace-nowrap leading-6">
                {section.group}
              </div>
            </div>

            <div className="space-y-0.5">
              {section.items.map((item) => {
                const active = path === item.route || path.startsWith(`${item.route}/`)
                const Icon = item.icon
                return (
                  <div
                    key={item.route}
                    className="relative px-2.5"
                    onMouseEnter={(e) => showTip(item.label, e.currentTarget)}
                    onMouseLeave={hideTip}
                    onFocusCapture={(e) => showTip(item.label, e.currentTarget)}
                    onBlurCapture={hideTip}
                  >
                    {active && (
                      <span
                        aria-hidden="true"
                        className="absolute left-0 top-2 bottom-2 w-[2.5px] rounded-r-full bg-mineral"
                      />
                    )}
                    <Link
                      to={item.route}
                      onClick={hideTip}
                      aria-label={item.label}
                      className={`flex items-center h-9 w-full rounded-[7px] overflow-hidden transition-[background-color,color] duration-[120ms] ${
                        active
                          ? "bg-raised text-ink font-medium"
                          : "text-secondary hover:text-ink hover:bg-soft/75"
                      }`}
                    >
                      <span className="w-9 h-9 shrink-0 grid place-items-center">
                        <Icon className="h-[18px] w-[18px] shrink-0" strokeWidth={1.75} aria-hidden="true" />
                      </span>
                      <span
                        className={`text-[13px] whitespace-nowrap pr-2.5 transition-[opacity,transform] duration-[140ms] ease-[var(--ease-out-expo)] ${
                          collapsed
                            ? "opacity-0 -translate-x-1 pointer-events-none"
                            : "opacity-100 translate-x-0"
                        }`}
                      >
                        {item.label}
                      </span>
                    </Link>
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* Bottom Collapse / Expand Control */}
      <div className="border-t border-line px-2.5 py-2 shrink-0">
        <button
          type="button"
          onClick={() => {
            hideTip()
            writeSidebarCollapsed(!collapsed)
          }}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Mở rộng thanh điều hướng ([)" : "Thu gọn thanh điều hướng ([)"}
          onMouseEnter={(e) =>
            collapsed && showTip(`Mở rộng thanh bên (${isApple ? "⌘B" : "Ctrl B"} / [)`, e.currentTarget)
          }
          onMouseLeave={hideTip}
          onFocus={(e) =>
            collapsed && showTip(`Mở rộng thanh bên (${isApple ? "⌘B" : "Ctrl B"} / [)`, e.currentTarget)
          }
          onBlur={hideTip}
          className="flex h-9 w-full items-center rounded-[7px] overflow-hidden text-secondary hover:bg-soft hover:text-ink transition-colors duration-[120ms]"
        >
          <span className="w-9 h-9 shrink-0 grid place-items-center">
            {collapsed ? (
              <PanelLeftOpen className="h-[18px] w-[18px]" strokeWidth={1.75} aria-hidden="true" />
            ) : (
              <PanelLeftClose className="h-[18px] w-[18px]" strokeWidth={1.75} aria-hidden="true" />
            )}
          </span>
          <span
            className={`text-[12.5px] font-medium whitespace-nowrap pr-2 transition-[opacity,transform] duration-[140ms] ease-[var(--ease-out-expo)] ${
              collapsed ? "opacity-0 -translate-x-1 pointer-events-none" : "opacity-100 translate-x-0"
            }`}
          >
            Thu gọn menu
          </span>
        </button>
      </div>

      {typeof document !== "undefined" &&
        activeTip &&
        createPortal(
          <div
            role="tooltip"
            style={{ top: activeTip.top, left: activeTip.left }}
            className="popover-enter origin-left fixed z-[90] -translate-y-1/2 pointer-events-none rounded-[7px] bg-ink px-2.5 py-1.5 text-[12px] font-medium text-paper shadow-lg shadow-ink/15 whitespace-nowrap"
          >
            {activeTip.label}
          </div>,
          document.body,
        )}
    </aside>
  )
}

interface PaletteItem {
  type: string
  label: string
  route: string
  hint: string
}

function CommandPalette({ onClose }: { onClose: () => void }) {
  const { navigate } = useRouter()
  const isApple = useIsApplePlatform()
  const [q, setQ] = useState("")
  const [activeIndex, setActiveIndex] = useState(0)
  const [searchState, setSearchState] = useState<{
    query: string
    items: Array<{ symbol: string; name?: string; exchange?: string }>
  }>({ query: "", items: [] })

  const trimmed = q.trim()
  const tickerCandidate = useMemo(() => {
    const upper = trimmed.toUpperCase()
    return /^[A-Z0-9]{3,5}$/.test(upper) ? upper : null
  }, [trimmed])

  useEffect(() => {
    if (!trimmed) return
    let cancelled = false
    const timer = window.setTimeout(() => {
      marketApi
        .search(trimmed)
        .then((res) => {
          if (cancelled) return
          const rows = Array.isArray(res) ? res : []
          setSearchState({
            query: trimmed,
            items: rows.slice(0, 8) as Array<{ symbol: string; name?: string; exchange?: string }>,
          })
        })
        .catch(() => {
          if (!cancelled) setSearchState({ query: trimmed, items: [] })
        })
    }, 150)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [trimmed])

  const allStaticItems = useMemo<PaletteItem[]>(() => {
    const routes = nav.flatMap((s) =>
      s.items.map((i) => ({ type: "Điều hướng", label: i.label, route: i.route, hint: s.group })),
    )
    const commands = [
      { type: "Tác vụ nhanh", label: "Kiểm tra rủi ro danh mục", route: "/portfolio", hint: "Danh mục" },
      { type: "Tác vụ nhanh", label: "Kiểm tra sổ lệnh đang chờ", route: "/trade", hint: "Khớp lệnh" },
    ]
    return [...routes, ...commands]
  }, [])

  const results = useMemo<PaletteItem[]>(() => {
    const lower = trimmed.toLowerCase()
    const filteredStatic = lower
      ? allStaticItems.filter((i) => i.label.toLowerCase().includes(lower))
      : allStaticItems

    const matchedSymbols: PaletteItem[] =
      trimmed && searchState.query === trimmed
        ? searchState.items
            .filter((item) => item.symbol && item.symbol.toUpperCase() !== tickerCandidate)
            .map((item) => {
              const sym = item.symbol.toUpperCase()
              return {
                type: "Mã cổ phiếu",
                label: `${sym}${item.name && item.name !== sym ? ` — ${item.name}` : ""}`,
                route: `/stock/${sym}`,
                hint: item.exchange || "HOSE",
              }
            })
        : []

    if (tickerCandidate) {
      return [
        {
          type: "Cổ phiếu",
          label: `Mở bảng phân tích ${tickerCandidate} ngay tại trang`,
          route: `/stock/${tickerCandidate}`,
          hint: "Popup chi tiết",
        },
        {
          type: "Cổ phiếu",
          label: `Đặt lệnh giao dịch ${tickerCandidate}`,
          route: `/trade?symbol=${tickerCandidate}`,
          hint: "Khớp lệnh LIVE",
        },
        ...matchedSymbols,
        ...filteredStatic,
      ]
    }
    return [...matchedSymbols, ...filteredStatic]
  }, [allStaticItems, trimmed, searchState, tickerCandidate])

  const clampedIndex = Math.max(0, Math.min(results.length - 1, activeIndex))

  const grouped = useMemo(() => {
    const out: Array<{ type: string; items: Array<{ item: PaletteItem; flatIndex: number }> }> = []
    const byType = new Map<string, Array<{ item: PaletteItem; flatIndex: number }>>()
    results.forEach((item, flatIndex) => {
      let bucket = byType.get(item.type)
      if (!bucket) {
        bucket = []
        byType.set(item.type, bucket)
        out.push({ type: item.type, items: bucket })
      }
      bucket.push({ item, flatIndex })
    })
    return out
  }, [results])

  const choose = (route: string) => {
    navigate(route)
    onClose()
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[12vh] px-4"
      role="dialog"
      aria-modal="true"
      aria-label="Tìm kiếm nhanh và điều hướng"
    >
      <div className="backdrop-enter absolute inset-0 bg-ink/25 backdrop-blur-[2px]" onClick={onClose} />
      <div className="cmd-enter relative w-full max-w-xl bg-surface border border-line-strong rounded-[12px] shadow-2xl shadow-ink/10 overflow-hidden">
        <div className="flex items-center gap-3 px-4 min-h-[50px] border-b border-line py-3">
          <Search className="h-4 w-4 text-muted shrink-0" strokeWidth={1.85} aria-hidden="true" />
          <input
            autoFocus
            value={q}
            onChange={(e) => {
              setQ(e.target.value)
              setActiveIndex(0)
            }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown" && results.length > 0) {
                e.preventDefault()
                setActiveIndex((prev) => (prev + 1) % results.length)
                return
              }
              if (e.key === "ArrowUp" && results.length > 0) {
                e.preventDefault()
                setActiveIndex((prev) => (prev - 1 + results.length) % results.length)
                return
              }
              if (e.key === "Enter" && results[clampedIndex]) {
                e.preventDefault()
                choose(results[clampedIndex].route)
              }
            }}
            placeholder={`Nhập mã CP, tên công ty (HPG, FPT, VCB) hoặc điều hướng… (${isApple ? "⌘K" : "Ctrl K"})`}
            className="flex-1 bg-transparent outline-none text-[13.5px] text-ink placeholder:text-muted"
          />
          <kbd className="text-[10px] font-mono bg-soft border border-line rounded px-1.5 py-0.5 text-muted">
            ESC
          </kbd>
        </div>
        <div className="max-h-[52vh] overflow-y-auto bg-surface overscroll-contain py-2">
          {grouped.map((grp) => (
            <div key={grp.type} className="px-2 mb-1">
              <div className="text-[10px] font-semibold tracking-[0.12em] uppercase text-muted px-2 py-1.5">
                {grp.type}
              </div>
              {grp.items.map(({ item, flatIndex }) => {
                const selected = flatIndex === clampedIndex
                return (
                  <button
                    key={`${item.type}:${item.label}:${item.route}`}
                    type="button"
                    onMouseEnter={() => setActiveIndex(flatIndex)}
                    onClick={() => choose(item.route)}
                    className={`w-full flex items-center gap-3 px-2.5 h-9 rounded-[6px] text-left transition-colors duration-[120ms] ${
                      selected ? "bg-raised text-ink" : "text-secondary hover:bg-soft hover:text-ink"
                    }`}
                  >
                    <span className="text-[13px] truncate">{item.label}</span>
                    <span className="ml-auto text-[11px] text-muted shrink-0">{item.hint}</span>
                  </button>
                )
              })}
            </div>
          ))}
          {results.length === 0 && (
            <div className="px-4 py-8 text-center text-[13px] text-muted">
              Không tìm thấy kết quả phù hợp. Hãy thử gõ mã CP như HPG, FPT, VCB.
            </div>
          )}
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
      const key = e.key.toLowerCase()
      if ((e.metaKey || e.ctrlKey) && key === "k") {
        e.preventDefault()
        setCmd((v) => !v)
        return
      }
      if ((e.metaKey || e.ctrlKey) && key === "b") {
        e.preventDefault()
        writeSidebarCollapsed(!getSidebarCollapsedSnapshot())
        return
      }
      if (e.key === "[" && !e.metaKey && !e.ctrlKey && !e.altKey) {
        const el = document.activeElement as HTMLElement | null
        const tag = el?.tagName
        if (
          tag !== "INPUT" &&
          tag !== "TEXTAREA" &&
          tag !== "SELECT" &&
          !el?.isContentEditable
        ) {
          e.preventDefault()
          writeSidebarCollapsed(!getSidebarCollapsedSnapshot())
          return
        }
      }
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
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setModalState((prev) => (prev.symbol ? { symbol: null, symbols: [] } : prev))
  }, [path])

  const overlayOpen = cmd || Boolean(modalState.symbol)
  useEffect(() => {
    if (!overlayOpen) return
    const prev = document.body.style.overflow
    document.body.style.overflow = "hidden"
    return () => {
      document.body.style.overflow = prev
    }
  }, [overlayOpen])

  const activeIdx = modalState.symbol ? modalState.symbols.indexOf(modalState.symbol) : -1

  return (
    <div className="flex min-h-dvh bg-paper">
      <Sidebar suppressTips={overlayOpen} />
      <div className="flex-1 flex flex-col min-w-0 bg-paper">
        <TopBar onSearch={() => setCmd(true)} />
        <main className="flex-1 min-w-0 bg-paper">{children}</main>
      </div>
      {cmd && <CommandPalette onClose={() => setCmd(false)} />}
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

export function Page({
  title,
  meta,
  sub,
  actions,
  variant = "default",
  fetching = false,
  children,
}: {
  title: string
  meta?: ReactNode
  sub?: ReactNode
  actions?: ReactNode
  variant?: "default" | "compact"
  rail?: boolean
  fetching?: boolean
  children: ReactNode
}) {
  const inlineMeta = meta ?? sub
  return (
    <div
      className={`relative max-w-[1360px] mx-auto px-5 lg:px-7 ${
        variant === "compact" ? "pt-3.5 pb-5 space-y-3.5" : "pt-4 pb-6"
      }`}
    >
      {fetching && (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute top-0 inset-x-0 h-[1.5px] overflow-hidden bg-mineral/15"
        >
          <span className="skeleton-shimmer" />
        </div>
      )}
      <div
        className={`flex flex-wrap items-center justify-between gap-x-4 gap-y-2 ${
          variant === "compact" ? "" : "mb-4"
        }`}
      >
        <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5 min-w-0">
          <h1 className="text-[20px] font-semibold tracking-tight text-ink leading-tight">
            {title}
          </h1>
          {inlineMeta && (
            <span className="text-[12.5px] text-secondary leading-snug">
              · {inlineMeta}
            </span>
          )}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2 shrink-0">{actions}</div>}
      </div>
      {children}
    </div>
  )
}
