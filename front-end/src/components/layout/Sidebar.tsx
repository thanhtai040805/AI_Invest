"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

const navItems = [
  { name: "Tổng quan",    href: "/dashboard",       icon: "dashboard" },
  { name: "Tin tức AI",   href: "/market-news",     icon: "newspaper" },
  { name: "Lọc cổ phiếu", href: "/screener",         icon: "filter_list" },
  { name: "Biểu đồ",      href: "/advanced-chart",   icon: "candlestick_chart" },
  { name: "Cảnh báo",     href: "/alerts",            icon: "notifications" },
  { name: "Cộng đồng",    href: "/community",         icon: "groups" },
  { name: "Trợ lý AI",    href: "/ai-assistant",      icon: "auto_awesome" },
  { name: "Danh mục",     href: "/portfolio",         icon: "account_balance_wallet" },
  { name: "Auto-Pilot",   href: "/auto-pilot",        icon: "rocket_launch" },
];

const bottomItems = [
  { name: "Mô phỏng", href: "/simulator", icon: "science" },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-[68px] lg:w-[220px] border-r border-white/[0.04] bg-[#070708] flex flex-col py-5 shrink-0 h-screen sticky top-0 z-50">
      <div className="flex flex-col h-full gap-1">

        {/* ── Brand ── */}
        <div className="px-4 mb-5">
          <Link href="/" className="group flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg aura-glow flex items-center justify-center shrink-0">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" className="text-[#e8a940]">
                <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" fill="currentColor" />
              </svg>
            </div>
            <div className="hidden lg:block overflow-hidden">
              <p className="font-bold text-sm tracking-tight text-white/90 leading-none">AIInvest</p>
              <p className="text-[9px] text-white/25 tracking-[0.18em] mt-0.5 uppercase">Trading System</p>
            </div>
          </Link>
        </div>

        {/* ── Primary nav ── */}
        <nav className="flex-1 px-2.5 space-y-px overflow-y-auto no-scrollbar">
          {navItems.map((item) => {
            const isActive =
              pathname === item.href || pathname?.startsWith(item.href + "/");
            return (
              <Link
                key={item.name}
                href={item.href}
                className="relative group flex items-center rounded-lg"
              >
                {isActive && (
                  <motion.div
                    layoutId="activeNav"
                    className="absolute inset-0 bg-[#e8a940]/8 rounded-lg"
                    transition={{ type: "spring", stiffness: 400, damping: 35 }}
                  />
                )}
                {/* Active left bar */}
                {isActive && (
                  <span className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-4 bg-[#e8a940] rounded-r-full shadow-[0_0_8px_rgba(232,169,64,0.7)]" />
                )}
                <div
                  className={cn(
                    "relative flex items-center gap-3 w-full px-3 py-2.5 rounded-lg transition-all duration-150",
                    isActive
                      ? "text-[#e8a940]"
                      : "text-white/35 hover:text-white/70 hover:bg-white/[0.03]"
                  )}
                >
                  <span
                    className={cn(
                      "material-symbols-outlined text-[20px] shrink-0 transition-all duration-150",
                      isActive ? "" : "group-hover:scale-105"
                    )}
                    style={{
                      fontVariationSettings: isActive ? "'FILL' 1, 'wght' 500" : "'FILL' 0",
                    }}
                  >
                    {item.icon}
                  </span>
                  <span className="text-[13px] font-medium hidden lg:inline leading-none truncate">
                    {item.name}
                  </span>
                </div>
              </Link>
            );
          })}
        </nav>

        {/* ── Bottom section ── */}
        <div className="px-2.5 border-t border-white/[0.04] pt-3 space-y-px">
          {bottomItems.map((item) => (
            <Link
              key={item.name}
              href={item.href}
              className="flex items-center gap-3 px-3 py-2.5 rounded-lg text-white/25 hover:text-white/55 hover:bg-white/[0.03] transition-all duration-150"
            >
              <span className="material-symbols-outlined text-[20px] shrink-0">{item.icon}</span>
              <span className="text-[13px] font-medium hidden lg:inline">Mô phỏng</span>
            </Link>
          ))}
          <Link
            href="#"
            className="flex items-center gap-3 px-3 py-2.5 rounded-lg text-white/25 hover:text-white/55 hover:bg-white/[0.03] transition-all duration-150"
          >
            <span className="material-symbols-outlined text-[20px] shrink-0">settings</span>
            <span className="text-[13px] font-medium hidden lg:inline">Cài đặt</span>
          </Link>
          <Link
            href="#"
            className="flex items-center gap-3 px-3 py-2.5 rounded-lg text-white/25 hover:text-rose-400 hover:bg-rose-500/5 transition-all duration-150"
          >
            <span className="material-symbols-outlined text-[20px] shrink-0">logout</span>
            <span className="text-[13px] font-medium hidden lg:inline">Đăng xuất</span>
          </Link>

          {/* User chip */}
          <div className="flex items-center gap-2.5 px-3 py-2 mt-1 rounded-lg bg-white/[0.025] border border-white/[0.04]">
            <div className="w-6 h-6 rounded-md bg-[#e8a940]/15 border border-[#e8a940]/25 flex items-center justify-center shrink-0">
              <span className="text-[#e8a940] text-[11px] font-bold">T</span>
            </div>
            <div className="hidden lg:block min-w-0">
              <p className="text-[11px] font-medium text-white/45 truncate">Trader</p>
            </div>
          </div>
        </div>
      </div>
    </aside>
  );
}
