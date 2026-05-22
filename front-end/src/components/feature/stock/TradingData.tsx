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
        { label: "Mở cửa", value: stock.open.toFixed(2), color: getPriceColor(stock.open, stock.prevClose, stock.ceiling, stock.floor) },
        { label: "Cao / Thấp", value: `${stock.high.toFixed(2)} / ${stock.low.toFixed(2)}`, color: "text-on-surface" },
        { label: "Giá TB (VWAP)", value: ((stock.high + stock.low + stock.price) / 3).toFixed(2), color: "text-primary" },
        { label: "Khối lượng", value: formatVolume(stock.volume), color: "text-on-surface" },
        { label: "KL TB 10N", value: formatVolume(stock.avgVolume || 0), color: "text-on-surface-variant" },
        { label: "Relative Vol", value: relVol + "x", color: Number(relVol) > 1.5 ? "text-secondary" : "text-on-surface" },
      ]
    },
    {
      title: "Momentum (14D)",
      metrics: [
        { label: "RSI", value: "—", color: "text-on-surface-variant" },
        { label: "Stochastic", value: "—", color: "text-on-surface-variant" },
        { label: "MACD", value: stock.changePercent > 0 ? `+${(stock.changePercent / 10).toFixed(2)}` : `${(stock.changePercent / 10).toFixed(2)}`, color: stock.changePercent > 0 ? "text-secondary" : "text-error" },
        { label: "Signal Line", value: "—", color: "text-on-surface-variant" },
        { label: "Momentum", value: stock.changePercent > 0 ? `+${stock.changePercent.toFixed(2)}%` : `${stock.changePercent.toFixed(2)}%`, color: stock.changePercent > 0 ? "text-secondary" : "text-error" },
        { label: "ROC", value: stock.changePercent > 0 ? `+${stock.changePercent.toFixed(2)}%` : `${stock.changePercent.toFixed(2)}%`, color: stock.changePercent > 0 ? "text-secondary" : "text-error" },
      ]
    },
    {
      title: "Volatility",
      metrics: [
        { label: "ATR (14)", value: ((stock.high - stock.low) / stock.price * 100).toFixed(2), color: "text-on-surface" },
        { label: "Bollinger %B", value: "—", color: "text-on-surface-variant" },
        { label: "Standard Dev", value: ((stock.high - stock.low) / 2 / stock.price * 100).toFixed(2) + "%", color: "text-on-surface" },
        { label: "Beta (vs VN-I)", value: "—", color: "text-on-surface-variant" },
        { label: "Alpha", value: stock.changePercent > 0 ? `+${(stock.changePercent * 0.1).toFixed(2)}` : `${(stock.changePercent * 0.1).toFixed(2)}`, color: stock.changePercent > 0 ? "text-secondary" : "text-error" },
        { label: "Sharpe Ratio", value: "—", color: "text-on-surface-variant" },
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
