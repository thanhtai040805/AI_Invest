"use client";

import { motion } from "framer-motion";
import { GlassCard } from "@/components/ui/GlassCard";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/Badge";
import { useState, useEffect } from "react";

// Mock data for 20 levels of DOM
const generateLevels = (basePrice: number, count: number, isBid: boolean) => {
  return Array.from({ length: count }).map((_, i) => {
    const price = isBid ? basePrice - (i * 0.1) : basePrice + (i * 0.1);
    const volume = Math.floor(Math.random() * 50000) + 5000;
    return {
      price,
      volume,
      percent: Math.random() * 100,
      cumulativeVolume: 0,
      imbalance: Math.random() * 20 - 10,
    };
  });
};

interface Trade {
  time: string;
  price: number;
  volume: number;
  side: 'buy' | 'sell';
}

export function OrderBook() {
  const [activeTab, setActiveTab] = useState<'DOM' | 'Tape'>('DOM');
  const [trades, setTrades] = useState<Trade[]>([]);
  const basePrice = 114.2;
  const bids = generateLevels(basePrice - 0.1, 20, true);
  const asks = generateLevels(basePrice + 0.1, 20, false).reverse();

  // Mock trade generator
  useEffect(() => {
    const interval = setInterval(() => {
      const newTrade: Trade = {
        time: new Date().toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }),
        price: basePrice + (Math.random() * 0.4 - 0.2),
        volume: Math.floor(Math.random() * 5000) + 100,
        side: Math.random() > 0.5 ? 'buy' : 'sell'
      };
      setTrades(prev => [newTrade, ...prev].slice(0, 30));
    }, 800);
    return () => clearInterval(interval);
  }, []);

  // Calculate cumulative volumes
  let cumAsk = 0;
  asks.forEach(a => { cumAsk += a.volume; a.cumulativeVolume = cumAsk; });
  let cumBid = 0;
  bids.forEach(b => { cumBid += b.volume; b.cumulativeVolume = cumBid; });

  const totalVol = cumAsk + cumBid;

  return (
    <GlassCard className="p-0 border-white/5 overflow-hidden flex flex-col h-full shadow-2xl bg-[#0a0a0a]">
      <div className="flex justify-between items-center p-md border-b border-white/5 bg-white/[0.02]">
        <div className="flex gap-md">
          {['DOM', 'Tape'].map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab as any)}
              className={cn(
                "text-[10px] font-bold uppercase tracking-widest transition-all",
                activeTab === tab ? "text-primary" : "opacity-40 hover:opacity-100"
              )}
            >
              {tab === 'Tape' ? 'Time & Sales' : 'DOM'}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-sm text-[10px] font-data-mono">
          <span className="opacity-40">SPREAD:</span>
          <span className="text-on-surface font-bold text-secondary">0.2 (0.17%)</span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto no-scrollbar font-data-mono">
        {activeTab === 'DOM' ? (
          <table className="w-full text-[10px] border-collapse">
            <thead className="sticky top-0 bg-[#0a0a0a] z-20 shadow-md">
              <tr className="text-on-surface-variant opacity-40 border-b border-white/5">
                <th className="py-2 px-md text-left font-normal uppercase tracking-tighter">Bán / Cum.</th>
                <th className="py-2 px-md text-center font-normal uppercase tracking-tighter">Giá</th>
                <th className="py-2 px-md text-right font-normal uppercase tracking-tighter">Mua / Cum.</th>
              </tr>
            </thead>
            <tbody>
              {asks.map((level, i) => (
                <tr key={`ask-${i}`} className="group hover:bg-white/[0.02] transition-colors relative">
                  <td className="py-1 px-md text-left relative">
                    <div className="absolute inset-0 bg-error/5 group-hover:bg-error/10 transition-colors origin-left" style={{ width: `${(level.volume / 55000) * 100}%` }} />
                    <span className="relative z-10 text-error font-bold">{(level.volume / 1000).toFixed(1)}k</span>
                    <span className="relative z-10 opacity-30 ml-2">{(level.cumulativeVolume! / 1000).toFixed(1)}k</span>
                  </td>
                  <td className="py-1 px-md text-center text-error font-black bg-error/[0.02] border-x border-white/5">
                    {level.price.toFixed(1)}
                  </td>
                  <td className="py-1 px-md text-right opacity-10">---</td>
                </tr>
              ))}
              <tr className="bg-white/5 border-y border-white/10">
                <td colSpan={3} className="py-1 px-md text-center">
                  <div className="flex justify-between items-center px-lg">
                    <span className="text-[9px] font-bold text-error uppercase">Bán mạnh</span>
                    <span className="text-xs font-black text-on-surface">114.2</span>
                    <span className="text-[9px] font-bold text-secondary uppercase">Mua mạnh</span>
                  </div>
                </td>
              </tr>
              {bids.map((level, i) => (
                <tr key={`bid-${i}`} className="group hover:bg-white/[0.02] transition-colors relative">
                  <td className="py-1 px-md text-left opacity-10">---</td>
                  <td className="py-1 px-md text-center text-secondary font-black bg-secondary/[0.02] border-x border-white/5">
                    {level.price.toFixed(1)}
                  </td>
                  <td className="py-1 px-md text-right relative">
                    <div className="absolute inset-0 bg-secondary/5 group-hover:bg-secondary/10 transition-colors origin-right" style={{ width: `${(level.volume / 55000) * 100}%`, right: 0, left: 'auto' }} />
                    <span className="relative z-10 opacity-30 mr-2">{(level.cumulativeVolume! / 1000).toFixed(1)}k</span>
                    <span className="relative z-10 text-secondary font-bold">{(level.volume / 1000).toFixed(1)}k</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="p-md space-y-1">
            {trades.map((trade, i) => (
              <motion.div
                initial={{ x: -10, opacity: 0 }}
                animate={{ x: 0, opacity: 1 }}
                key={i}
                className="flex justify-between items-center text-[10px] py-1 border-b border-white/[0.03]"
              >
                <span className="opacity-30">{trade.time}</span>
                <span className={cn("font-bold", trade.side === 'buy' ? 'text-secondary' : 'text-error')}>
                  {trade.price.toFixed(1)}
                </span>
                <span className="font-bold">{(trade.volume / 1000).toFixed(1)}k</span>
                <Badge variant="outline" className={cn("text-[8px] px-1 py-0 h-4 border-none bg-white/5", trade.side === 'buy' ? 'text-secondary' : 'text-error')}>
                  {trade.side.toUpperCase()}
                </Badge>
              </motion.div>
            ))}
          </div>
        )}
      </div>

      <div className="p-md bg-white/[0.02] border-t border-white/5 space-y-md">
        <div className="flex justify-between items-center text-[10px]">
          <span className="opacity-40 uppercase tracking-widest font-bold">Lực mua / bán</span>
          <span className="font-data-mono font-bold text-secondary">{(cumBid / totalVol * 100).toFixed(1)}%</span>
        </div>
        <div className="h-2 w-full bg-white/5 rounded-full overflow-hidden flex shadow-inner">
          <div className="h-full bg-secondary shadow-[0_0_10px_rgba(var(--secondary-rgb),0.3)]" style={{ width: `${(cumBid / totalVol * 100)}%` }} />
          <div className="h-full bg-error shadow-[0_0_10px_rgba(var(--error-rgb),0.3)]" style={{ width: `${(cumAsk / totalVol * 100)}%` }} />
        </div>
      </div>
    </GlassCard>
  );
}
