"use client";

import { cn } from "@/lib/utils";

interface PageHeaderProps {
  title: string;
  subtitle?: string;
  extra?: React.ReactNode;
}

import { SymbolSearch } from "@/components/feature/stock/SymbolSearch";

export function PageHeader({ title, subtitle, extra }: PageHeaderProps) {
  return (
    <header className="flex justify-between items-center mb-xl">
      <div>
        <h2 className="font-headline-lg text-headline-lg text-on-surface">{title}</h2>
        {subtitle && <p className="font-body-sm text-body-sm text-on-surface-variant">{subtitle}</p>}
      </div>
      <div className="flex items-center gap-md">
        <div className="w-[300px] hidden md:block">
          <SymbolSearch />
        </div>
        {extra && extra}
      </div>
    </header>
  );
}
