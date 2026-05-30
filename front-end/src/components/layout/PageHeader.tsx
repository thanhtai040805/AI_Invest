"use client";

import { cn } from "@/lib/utils";
import { SymbolSearch } from "@/components/feature/stock/SymbolSearch";
import { motion } from "framer-motion";

interface PageHeaderProps {
  title: string;
  subtitle?: string;
  extra?: React.ReactNode;
}

export function PageHeader({ title, subtitle, extra }: PageHeaderProps) {
  return (
    <motion.header 
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: "spring", stiffness: 100, damping: 20 }}
      className="flex flex-col md:flex-row md:items-center md:justify-between gap-md mb-xl pb-md border-b border-white/5"
    >
      <div className="flex-1 min-w-0">
        <h2 className="font-outfit text-2xl md:text-3xl font-black text-on-surface tracking-tight leading-none">
          {title}
        </h2>
        {subtitle && (
          <p className="font-outfit text-xs text-on-surface-variant font-medium mt-1 uppercase tracking-wider opacity-60">
            {subtitle}
          </p>
        )}
      </div>
      <div className="flex items-center gap-sm shrink-0">
        <div className="w-full md:w-[320px]">
          <SymbolSearch className="w-full" />
        </div>
        {extra && <div className="flex items-center gap-xs">{extra}</div>}
      </div>
    </motion.header>
  );
}
