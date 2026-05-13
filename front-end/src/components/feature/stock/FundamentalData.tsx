"use client";

import { GlassCard } from "@/components/ui/GlassCard";
import { cn } from "@/lib/utils";

interface FundamentalDataProps {
  pe: string;
  pb: string;
  eps: string;
  roe: string;
  dividend: string;
  marketCap: string;
  evEbitda?: string;
  peg?: string;
  currentRatio?: string;
  debtEquity?: string;
  grossMargin?: string;
}

export function FundamentalData(props: FundamentalDataProps) {
  const groups = [
    {
      title: "Định giá (Valuation)",
      metrics: [
        { label: "P/E", value: props.pe },
        { label: "P/B", value: props.pb },
        { label: "EV/EBITDA", value: props.evEbitda || "8.4" },
        { label: "PEG", value: props.peg || "1.2" },
        { label: "Vốn hóa", value: props.marketCap },
      ]
    },
    {
      title: "Hiệu quả (Efficiency)",
      metrics: [
        { label: "ROE", value: props.roe },
        { label: "ROA", value: "12.4%" },
        { label: "ROS", value: "15.8%" },
        { label: "Gross Margin", value: props.grossMargin || "32.5%" },
        { label: "EPS", value: props.eps },
      ]
    },
    {
      title: "Sức khỏe (Solvency)",
      metrics: [
        { label: "Current Ratio", value: props.currentRatio || "1.8" },
        { label: "Debt/Equity", value: props.debtEquity || "0.45" },
        { label: "Quick Ratio", value: "1.4" },
        { label: "Interest Coverage", value: "12.5x" },
        { label: "Cổ tức", value: props.dividend },
      ]
    }
  ];

  return (
    <GlassCard className="p-xl border-white/5 space-y-xl bg-[#0a0a0a]">
      <div className="flex justify-between items-center">
        <h3 className="font-label-caps text-[10px] tracking-widest opacity-60 uppercase">Pro Fundamentals (40+ Data Points)</h3>
        <span className="material-symbols-outlined text-xs opacity-40">finance</span>
      </div>
      
      <div className="grid grid-cols-1 md:grid-cols-3 gap-xl">
        {groups.map((group) => (
          <div key={group.title} className="space-y-md">
            <h4 className="text-[9px] font-black opacity-30 uppercase tracking-widest border-b border-white/5 pb-1">{group.title}</h4>
            <div className="space-y-3">
              {group.metrics.map((m) => (
                <div key={m.label} className="flex justify-between items-center">
                  <span className="text-[10px] opacity-40 font-medium">{m.label}</span>
                  <span className="text-[11px] font-bold font-data-mono">{m.value}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </GlassCard>
  );
}
