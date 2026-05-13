"use client";

import { motion } from "framer-motion";
import { GlassCard } from "@/components/ui/GlassCard";
import { Badge } from "@/components/ui/Badge";
import { useStockStore } from "@/stores/useStockStore";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { getPriceColor, formatVolume, formatCurrency } from "@/lib/market-utils";
import Link from "next/link";

export default function ScreenerPage() {
   const stocks = useStockStore((state) => state.stocks);
   const [activeFilter, setActiveFilter] = useState('Valuation');

   const filters = [
      { name: 'Valuation', icon: 'payments' },
      { name: 'Growth', icon: 'trending_up' },
      { name: 'Technical', icon: 'analytics' },
      { name: 'Dividend', icon: 'savings' },
      { name: 'Ownership', icon: 'groups' }
   ];

   return (
      <div className="flex flex-col h-screen overflow-hidden bg-[#050505]">
         {/* HEADER */}
         <div className="h-16 border-b border-white/5 flex items-center justify-between px-xl bg-[#0a0a0a]">
            <div className="flex items-center gap-xl">
               <h1 className="text-xl font-black text-primary tracking-tighter uppercase">Market Screener</h1>
               <div className="flex gap-md bg-white/5 p-1 rounded-lg border border-white/10">
                  <button className="px-4 py-1 text-[10px] font-black bg-primary text-white rounded shadow-lg shadow-primary/20">PRESET: ALPHA_BULL</button>
                  <button className="px-4 py-1 text-[10px] font-black opacity-40 hover:opacity-100 transition-all uppercase tracking-widest">Custom Scan</button>
               </div>
            </div>
            <div className="flex items-center gap-lg">
               <span className="text-[10px] font-bold opacity-40 uppercase tracking-widest">Matches: 24 / 1,420</span>
               <button className="flex items-center gap-2 bg-secondary/10 text-secondary border border-secondary/20 px-4 py-2 rounded-xl text-[10px] font-black uppercase tracking-widest hover:bg-secondary/20 transition-all">
                  <span className="material-symbols-outlined text-sm">download</span>
                  Export CSV
               </button>
            </div>
         </div>

         <div className="flex-1 flex overflow-hidden">
            {/* SIDEBAR FILTERS */}
            <div className="w-72 border-r border-white/5 flex flex-col bg-[#080808] overflow-y-auto no-scrollbar">
               <div className="p-xl space-y-xl">
                  <div className="space-y-md">
                     <h4 className="text-[10px] font-black opacity-30 uppercase tracking-widest">Quick Filters</h4>
                     <div className="flex flex-col gap-1">
                        {filters.map((f, i) => (
                           <button
                              key={f.name}
                              onClick={() => setActiveFilter(f.name)}
                              className={cn(
                                 "flex items-center justify-between px-md py-3 rounded-xl transition-all",
                                 activeFilter === f.name ? "bg-primary/10 text-primary border border-primary/20" : "hover:bg-white/5 opacity-60"
                              )}
                           >
                              <div className="flex items-center gap-md">
                                 <span className="material-symbols-outlined text-sm">{f.icon}</span>
                                 <span className="text-[11px] font-bold">{f.name}</span>
                              </div>
                              <span className="text-[9px] font-black opacity-40">{i * 12 + 5}</span>
                           </button>
                        ))}
                     </div>
                  </div>

                  <div className="space-y-xl">
                     <h4 className="text-[10px] font-black opacity-30 uppercase tracking-widest">Metrics Sliders</h4>
                     {[
                        { label: 'P/E Ratio', min: '0', max: '100', val: '15' },
                        { label: 'Dividend Yield', min: '0%', max: '15%', val: '4%' },
                        { label: 'RSI (14)', min: '0', max: '100', val: '30-70' }
                     ].map(s => (
                        <div key={s.label} className="space-y-3">
                           <div className="flex justify-between text-[9px] font-bold opacity-60">
                              <span>{s.label}</span>
                              <span className="text-primary">{s.val}</span>
                           </div>
                           <div className="h-1 w-full bg-white/5 rounded-full relative overflow-hidden">
                              <div className="absolute inset-y-0 left-1/4 right-1/4 bg-primary/40 rounded-full" />
                           </div>
                        </div>
                     ))}
                  </div>
               </div>
            </div>

            {/* MAIN TABLE AREA */}
            <div className="flex-1 flex flex-col bg-[#050505] overflow-hidden">
               <div className="p-xl overflow-x-auto no-scrollbar">
                  <table className="w-full text-left border-collapse min-w-[1200px]">
                     <thead>
                        <tr className="border-b border-white/5 text-[10px] font-black text-on-surface-variant opacity-30 uppercase tracking-widest">
                           <th className="py-4 px-md">Ticker</th>
                           <th className="py-4 px-md text-right">Price</th>
                           <th className="py-4 px-md text-right">Change %</th>
                           <th className="py-4 px-md text-right">Market Cap</th>
                           <th className="py-4 px-md text-right">P/E</th>
                           <th className="py-4 px-md text-right">P/B</th>
                           <th className="py-4 px-md text-right">ROE</th>
                           <th className="py-4 px-md text-right">D/E</th>
                           <th className="py-4 px-md text-right">RSI</th>
                           <th className="py-4 px-md text-right">Volume</th>
                           <th className="py-4 px-md text-center">Signal</th>
                        </tr>
                     </thead>
                     <tbody className="font-data-mono">
                        {stocks.map((s, i) => (
                           <tr key={s.symbol} className="border-b border-white/[0.02] hover:bg-white/[0.02] transition-colors group cursor-pointer">
                              <td className="py-4 px-md">
                                 <Link href={`/stock/${s.symbol}`} className="flex flex-col">
                                    <span className="text-sm font-black text-primary group-hover:underline underline-offset-4">{s.symbol}</span>
                                    <span className="text-[9px] font-bold opacity-30 uppercase truncate max-w-[100px]">{s.name}</span>
                                 </Link>
                              </td>
                              <td className={cn("py-4 px-md text-right text-xs font-bold", getPriceColor(s.price, s.prevClose, s.ceiling, s.floor))}>
                                 {s.price.toFixed(1)}
                              </td>
                              <td className={cn("py-4 px-md text-right text-xs font-bold", s.change > 0 ? "text-secondary" : "text-error")}>
                                 {s.change > 0 ? '+' : ''}{s.changePercent.toFixed(2)}%
                              </td>
                              <td className="py-4 px-md text-right text-xs font-bold opacity-60">
                                 {formatCurrency(s.marketCap || 12450000000000, 'T')}
                              </td>
                              <td className="py-4 px-md text-right text-xs font-bold text-cyan-400">14.5</td>
                              <td className="py-4 px-md text-right text-xs font-bold">1.8</td>
                              <td className="py-4 px-md text-right text-xs font-bold text-secondary">22.4%</td>
                              <td className="py-4 px-md text-right text-xs font-bold">0.45</td>
                              <td className="py-4 px-md text-right text-xs font-bold">62.8</td>
                              <td className="py-4 px-md text-right text-xs font-bold opacity-40">
                                 {formatVolume(s.volume)}
                              </td>
                              <td className="py-4 px-md text-center">
                                 <Badge variant={s.signal === 'MUA' ? 'secondary' : s.signal === 'BÁN' ? 'error' : 'outline'} className="text-[9px] font-black">
                                    {s.signal}
                                 </Badge>
                              </td>
                           </tr>
                        ))}
                     </tbody>
                  </table>
               </div>
            </div>
         </div>
      </div>
   );
}
