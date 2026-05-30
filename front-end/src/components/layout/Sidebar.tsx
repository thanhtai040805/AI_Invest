"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { cn } from "@/lib/utils";

/* ─────────────────────────────────────────────
   Sidebar — redesigned icon rail
   DESIGN_VARIANCE=8: compact icon rail on sm, 
   labelled on lg. No hover-tooltip gymnastics.
───────────────────────────────────────────── */

const navGroups = [
  {
    label: "Thị trường",
    items: [
      { name: "Tổng quan",    href: "/dashboard",     icon: "space_dashboard" },
      { name: "Tin tức AI",   href: "/market-news",   icon: "newspaper" },
      { name: "Lọc cổ phiếu", href: "/screener",      icon: "filter_list" },
      { name: "Bản đồ nhiệt", href: "/market-heatmap", icon: "grid_view" },
    ],
  },
  {
    label: "Giao dịch",
    items: [
      { name: "Danh mục",    href: "/portfolio",   icon: "account_balance_wallet" },
      { name: "Auto-Pilot",  href: "/auto-pilot",  icon: "rocket_launch" },
      { name: "So sánh",     href: "/compare",     icon: "compare_arrows" },
      { name: "Tương quan",  href: "/correlation", icon: "scatter_plot" },
      { name: "Alpha Zoo",   href: "/alpha-zoo",   icon: "psychology" },
    ],
  },
  {
    label: "AI & Cộng đồng",
    items: [
      { name: "Trợ lý AI",  href: "/agent",     icon: "auto_awesome" },
      { name: "Cộng đồng",  href: "/community", icon: "groups" },
    ],
  },
];

const bottomItems = [
  { name: "Cài đặt", href: "/settings", icon: "settings" },
];

export default function Sidebar() {
  const pathname = usePathname();

  const isActive = (href: string) =>
    pathname === href || pathname?.startsWith(href + "/");

  return (
    <aside className="w-[60px] lg:w-[216px] border-r border-white/[0.04] bg-[#070709] flex flex-col shrink-0 sticky top-0 h-[calc(100dvh-2rem)] z-30 overflow-hidden">

      {/* ── Brand ── */}
      <div className="h-14 flex items-center px-4 border-b border-white/[0.04] shrink-0">
        <Link href="/" className="group flex items-center gap-3 min-w-0">
          <div className="w-7 h-7 rounded-lg aura-glow flex items-center justify-center shrink-0 transition-all duration-200 group-hover:scale-105">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" className="text-[#e8a940]">
              <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" fill="currentColor" />
            </svg>
          </div>
          <div className="hidden lg:flex flex-col min-w-0 overflow-hidden">
            <p className="font-bold text-[13px] tracking-tight text-white/90 leading-none truncate">AIInvest</p>
            <p className="font-label-caps text-[8px] text-white/20 tracking-[0.18em] mt-0.5">TRADING SYSTEM</p>
          </div>
        </Link>
      </div>

      {/* ── Nav groups ── */}
      <nav className="flex-1 overflow-y-auto no-scrollbar py-3 px-2 space-y-5">
        {navGroups.map((group) => (
          <div key={group.label}>
            {/* Group label — hidden on collapsed */}
            <p className="hidden lg:block font-label-caps text-[8px] tracking-[0.18em] text-white/18 px-2 mb-1.5">
              {group.label}
            </p>
            <div className="space-y-px">
              {group.items.map((item) => {
                const active = isActive(item.href);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    title={item.name}
                    className="relative group flex items-center rounded-lg"
                  >
                    {/* Active background pill */}
                    {active && (
                      <motion.div
                        layoutId="activeNavBg"
                        className="absolute inset-0 bg-[#e8a940]/[0.08] rounded-lg"
                        transition={{ type: "spring", stiffness: 420, damping: 38 }}
                      />
                    )}

                    {/* Active left accent bar */}
                    <AnimatePresence>
                      {active && (
                        <motion.span
                          initial={{ scaleY: 0, opacity: 0 }}
                          animate={{ scaleY: 1, opacity: 1 }}
                          exit={{ scaleY: 0, opacity: 0 }}
                          transition={{ type: "spring", stiffness: 500, damping: 40 }}
                          className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 bg-[#e8a940] rounded-r-full"
                          style={{ boxShadow: "0 0 10px rgba(232,169,64,0.6)" }}
                        />
                      )}
                    </AnimatePresence>

                    <div
                      className={cn(
                        "relative flex items-center gap-3 w-full px-3 py-2 rounded-lg transition-all duration-150",
                        active
                          ? "text-[#e8a940]"
                          : "text-white/30 hover:text-white/65 hover:bg-white/[0.03]"
                      )}
                    >
                      <span
                        className={cn(
                          "material-symbols-outlined text-[19px] shrink-0 transition-all duration-150",
                          active ? "" : "group-hover:scale-[1.08]"
                        )}
                        style={{
                          fontVariationSettings: active
                            ? "'FILL' 1, 'wght' 500, 'GRAD' 0, 'opsz' 20"
                            : "'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 20",
                        }}
                      >
                        {item.icon}
                      </span>
                      <span className="text-[12.5px] font-medium hidden lg:block leading-none truncate">
                        {item.name}
                      </span>
                    </div>
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* ── Bottom: settings + user chip ── */}
      <div className="shrink-0 border-t border-white/[0.04] py-2 px-2 space-y-px">
        {bottomItems.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            title={item.name}
            className={cn(
              "flex items-center gap-3 px-3 py-2 rounded-lg transition-all duration-150",
              isActive(item.href)
                ? "text-[#e8a940] bg-[#e8a940]/[0.07]"
                : "text-white/25 hover:text-white/60 hover:bg-white/[0.03]"
            )}
          >
            <span
              className="material-symbols-outlined text-[19px] shrink-0"
              style={{ fontVariationSettings: "'FILL' 0, 'wght' 400, 'opsz' 20" }}
            >
              {item.icon}
            </span>
            <span className="text-[12.5px] font-medium hidden lg:block">{item.name}</span>
          </Link>
        ))}

        <Link
          href="/auth"
          title="Đăng xuất"
          className="flex items-center gap-3 px-3 py-2 rounded-lg text-white/25 hover:text-[#f87171] hover:bg-[#f87171]/[0.05] transition-all duration-150"
        >
          <span
            className="material-symbols-outlined text-[19px] shrink-0"
            style={{ fontVariationSettings: "'FILL' 0, 'wght' 400, 'opsz' 20" }}
          >
            logout
          </span>
          <span className="text-[12.5px] font-medium hidden lg:block">Đăng xuất</span>
        </Link>

        {/* User chip */}
        <div className="mt-1 flex items-center gap-2.5 px-3 py-2 rounded-lg bg-white/[0.02] border border-white/[0.04]">
          <div className="w-6 h-6 rounded-md bg-[#e8a940]/12 border border-[#e8a940]/20 flex items-center justify-center shrink-0">
            <span className="text-[#e8a940] text-[10px] font-bold leading-none">T</span>
          </div>
          <div className="hidden lg:block min-w-0 flex-1">
            <p className="text-[11px] font-medium text-white/45 truncate leading-none">Trader</p>
            <p className="font-label-caps text-[8px] text-white/18 tracking-[0.12em] mt-0.5">PRO PLAN</p>
          </div>
          <span
            className="material-symbols-outlined text-[14px] text-white/15 hidden lg:block shrink-0"
            style={{ fontVariationSettings: "'FILL' 0, 'wght' 300, 'opsz' 16" }}
          >
            more_horiz
          </span>
        </div>
      </div>
    </aside>
  );
}
