"use client";

import { GlassCard } from "@/components/ui/GlassCard";

interface InsightItemProps {
  type: 'success' | 'warning' | 'info';
  text: string;
}

function InsightItem({ type, text }: InsightItemProps) {
  const icons = {
    success: 'check_circle',
    warning: 'warning',
    info: 'info'
  };
  const colors = {
    success: 'text-secondary',
    warning: 'text-tertiary',
    info: 'text-primary'
  };

  return (
    <li className="flex gap-sm">
      <span className={cn("material-symbols-outlined text-[18px]", colors[type])}>{icons[type]}</span>
      <span className="font-body-sm text-on-surface-variant">{text}</span>
    </li>
  );
}

export function SmartInsights() {
  return (
    <aside className="w-80 border-l border-white/5 bg-surface-container-lowest/50 backdrop-blur-2xl p-lg flex flex-col gap-xl">
      <div>
        <div className="flex items-center gap-sm mb-lg">
          <span className="material-symbols-outlined text-tertiary" style={{ fontVariationSettings: "'FILL' 1" }}>auto_awesome</span>
          <h2 className="font-headline-lg text-[20px] text-on-surface">Smart Insights</h2>
        </div>
        <div className="space-y-lg">
          <div className="p-md rounded-xl bg-surface-container/40 border-l-4 border-secondary">
            <p className="font-body-sm text-on-surface mb-xs">AI nhận định: Xu hướng tăng tiếp diễn</p>
            <p className="text-[12px] text-on-surface-variant leading-relaxed">
              HPG phá vỡ vùng kháng cự 28.000 với khối lượng lớn. RSI chưa vào vùng quá mua.
            </p>
          </div>
          <ul className="space-y-md">
            <InsightItem type="success" text="Dòng tiền lớn (Shark) đang gia tăng vị thế." />
            <InsightItem type="success" text="MACD cắt lên đường tín hiệu trên khung 4H." />
            <InsightItem type="warning" text="Vùng kháng cự mạnh tại 30.500." />
          </ul>
        </div>
      </div>
    </aside>
  );
}

import { cn } from "@/lib/utils";
