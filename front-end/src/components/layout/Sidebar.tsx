"use client";

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { motion } from 'framer-motion';
import { cn } from "@/lib/utils";

const navItems = [
  { name: 'Dashboard', href: '/dashboard', icon: 'dashboard' },
  { name: 'Screener', href: '/screener', icon: 'filter_list' },
  { name: 'Advanced Chart', href: '/advanced-chart', icon: 'show_chart' },
  { name: 'Alerts', href: '/alerts', icon: 'notifications' },
  { name: 'Community', href: '/community', icon: 'groups' },
  { name: 'AI Assistant', href: '/ai-assistant', icon: 'auto_awesome' },
  { name: 'Portfolio', href: '/portfolio', icon: 'account_balance_wallet' },
  { name: 'Auto-Pilot', href: '/auto-pilot', icon: 'rocket_launch' },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-20 lg:w-64 border-r border-white/5 bg-surface-container-lowest/40 backdrop-blur-3xl flex flex-col justify-between py-xl shrink-0 h-screen sticky top-0 z-50">
      <div className="flex flex-col h-full">
        {/* Brand Section */}
        <div className="px-lg mb-xl">
          <Link href="/" className="group flex flex-col">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg aura-glow flex items-center justify-center shadow-lg shadow-primary/20">
                <span className="material-symbols-outlined text-on-primary text-[20px]">rocket_launch</span>
              </div>
              <h1 className="font-headline-md text-headline-md text-on-surface group-hover:text-primary transition-colors hidden lg:block tracking-tight">AIInvest</h1>
            </div>
            <p className="font-label-caps text-[10px] text-on-surface-variant opacity-60 mt-2 hidden lg:block tracking-[0.2em]">NextGen Aerospace Trading</p>
          </Link>
        </div>

        {/* Navigation */}
        <nav className="flex-1 space-y-1 px-sm">
          {navItems.map((item) => {
            const isActive = pathname === item.href || pathname?.startsWith(item.href + '/');
            return (
              <Link
                key={item.name}
                href={item.href}
                className="relative group flex items-center"
              >
                <div className={cn(
                  "flex items-center gap-md w-full px-lg py-3.5 transition-all duration-500 rounded-2xl relative z-10",
                  isActive
                    ? 'text-primary bg-primary/5'
                    : 'text-on-surface-variant hover:text-on-surface hover:bg-white/5'
                )}>
                  <span className={cn(
                    "material-symbols-outlined text-[24px] transition-all duration-500",
                    isActive ? "scale-110" : "group-hover:scale-110 opacity-70 group-hover:opacity-100"
                  )} style={{ fontVariationSettings: isActive ? "'FILL' 1" : "" }}>
                    {item.icon}
                  </span>
                  <span className="font-title-md text-[13px] hidden lg:inline tracking-wide">
                    {item.name}
                  </span>
                </div>

                {/* Active Indicator Bar */}
                {isActive && (
                  <motion.div
                    layoutId="activeNav"
                    className="absolute left-0 w-1 h-8 bg-primary rounded-r-full shadow-[0_0_15px_rgba(173,198,255,0.6)] z-20"
                    transition={{ type: "spring", stiffness: 300, damping: 30 }}
                  />
                )}

                {/* Hover Background Accent */}
                {!isActive && (
                  <div className="absolute inset-0 bg-white/5 opacity-0 group-hover:opacity-100 rounded-2xl transition-opacity duration-300 mx-sm" />
                )}
              </Link>
            );
          })}
        </nav>

        {/* Bottom Actions */}
        <div className="px-lg mt-auto space-y-xl pt-xl border-t border-white/5">
          <div className="flex flex-col gap-lg">
            <Link href="#" className="flex items-center gap-md text-on-surface-variant hover:text-primary transition-all group lg:px-0 px-2">
              <span className="material-symbols-outlined text-[24px] opacity-60 group-hover:opacity-100 transition-opacity">settings</span>
              <span className="font-label-caps text-[11px] hidden lg:inline tracking-widest">Settings</span>
            </Link>
            <Link href="#" className="flex items-center gap-md text-on-surface-variant hover:text-error transition-all group lg:px-0 px-2">
              <span className="material-symbols-outlined text-[24px] opacity-60 group-hover:opacity-100 transition-opacity">logout</span>
              <span className="font-label-caps text-[11px] hidden lg:inline tracking-widest">Logout</span>
            </Link>
          </div>
        </div>
      </div>
    </aside>
  );
}
