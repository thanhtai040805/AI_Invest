"use client";

import { GlassCard } from "@/components/ui/GlassCard";
import { cn } from "@/lib/utils";
import { getPriceColor, formatVolume } from "@/lib/market-utils";
import { StockQuote } from "@/types/stock";

interface TradingDataProps {
  stock: StockQuote;
}

export function TradingData({ stock }: TradingDataProps) {
  const relVol = stock.avgVolume ? (stock.volume / stock.avgVolume).toFixed(2) : "1.00";
  
  const indicatorGroups = [
    {
      title: "Trend & Liquidity",
      metrics: [
        { label: "Mở cửa", value: stock.open.toFixed(1), color: getPriceColor(stock.open, stock.prevClose, stock.ceiling, stock.floor) },
        { label: "Cao / Thấp", value: `${stock.high.toFixed(1)} / ${stock.low.toFixed(1)}`, color: "text-on-surface" },
        { label: "Giá TB (VWAP)", value: ((stock.high + stock.low + stock.price) / 3).toFixed(1), color: "text-primary" },
        { label: "Khối lượng", value: formatVolume(stock.volume), color: "text-on-surface" },
        { label: "KL TB 10N", value: formatVolume(stock.avgVolume || 0), color: "text-on-surface-variant" },
        { label: "Relative Vol", value: relVol + "x", color: Number(relVol) > 1.5 ? "text-secondary" : "text-on-surface" },
      ]
    },
    {
      title: "Momentum (14D)",
      metrics: [
        { label: "RSI", value: "62.4", color: "text-secondary" },
        { label: "Stochastic", value: "75.2", color: "text-secondary" },
        { label: "MACD", value: "+1.25", color: "text-secondary" },
        { label: "Signal Line", value: "+0.85", color: "text-on-surface" },
        { label: "Momentum", value: "+4.2", color: "text-secondary" },
        { label: "ROC", value: "+2.1%", color: "text-secondary" },
      ]
    },
    {
      title: "Volatility",
      metrics: [
        { label: "ATR (14)", value: "2.45", color: "text-on-surface" },
        { label: "Bollinger %B", value: "0.82", color: "text-on-surface" },
        { label: "Standard Dev", value: "4.5%", color: "text-on-surface" },
        { label: "Beta (vs VN-I)", value: "1.15", color: "text-cyan-400" },
        { label: "Alpha", value: "+0.45", color: "text-secondary" },
        { label: "Sharpe Ratio", value: "1.85", color: "text-secondary" },
      ]
    }
  ];

  return (
    <GlassCard className="p-xl border-white/5 space-y-xl bg-[#0a0a0a]">
      <div className="flex justify-between items-center">
        <h3 className="font-label-caps text-[10px] tracking-widest opacity-60 uppercase">Technical & Risk Metrics</h3>
        <div className="flex gap-md">
           <span className="text-[9px] font-bold text-secondary uppercase bg-secondary/10 px-2 py-0.5 rounded">Bullish Momentum</span>
        </div>
      </div>
      
      <div className="grid grid-cols-1 md:grid-cols-3 gap-xl">
        {indicatorGroups.map((group) => (
          <div key={group.title} className="space-y-md">
            <h4 className="text-[9px] font-black opacity-30 uppercase tracking-widest border-b border-white/5 pb-1">{group.title}</h4>
            <div className="grid grid-cols-2 gap-y-4 gap-x-md">
              {group.metrics.map((m) => (
                <div key={m.label} className="space-y-1">
                  <p className="text-[9px] font-medium text-on-surface-variant opacity-40 uppercase tracking-widest truncate">{m.label}</p>
                  <p className={cn("text-sm font-bold font-data-mono", m.color)}>{m.value}</p>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </GlassCard>
  );
}
