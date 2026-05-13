"use client";

import { cn } from "@/lib/utils";

interface PageHeaderProps {
  title: string;
  subtitle?: string;
  extra?: React.ReactNode;
}

export function PageHeader({ title, subtitle, extra }: PageHeaderProps) {
  return (
    <header className="flex justify-between items-center mb-xl">
      <div>
        <h2 className="font-headline-lg text-headline-lg text-on-surface">{title}</h2>
        {subtitle && <p className="font-body-sm text-body-sm text-on-surface-variant">{subtitle}</p>}
      </div>
      {extra && <div className="flex items-center gap-md">{extra}</div>}
    </header>
  );
}
