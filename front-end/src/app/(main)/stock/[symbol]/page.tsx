"use client";

import { useParams } from "next/navigation";
import { motion } from "framer-motion";
import { GlassCard } from "@/components/ui/GlassCard";
import { Badge } from "@/components/ui/Badge";
import { useStockStore } from "@/stores/useStockStore";
import { getPriceColor, formatCurrency } from "@/lib/market-utils";
import { cn } from "@/lib/utils";
import { OrderBook } from "@/components/feature/stock/OrderBook";
import PriceChart from "@/components/feature/stock/PriceChart";
import { FundamentalData } from "@/components/feature/stock/FundamentalData";
import { SentimentAnalysis } from "@/components/feature/stock/SentimentAnalysis";
import { TradingData } from "@/components/feature/stock/TradingData";
import { NewsList } from "@/components/feature/stock/NewsList";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";
import { useState, useMemo } from "react";
import { useStockQuote, useStockProfile, useStockFundamentals, useAIConsensus } from "@/hooks/useMarketData";
export default function StockDetailPage() {
   const { symbol: rawSymbol } = useParams();
   const symbol = String(rawSymbol ?? "").toUpperCase();
   const stocks = useStockStore((state) => state.stocks);
   const fromStore = stocks.find((s) => s.symbol === symbol);

   useStockQuote(symbol);
   const { data: profile } = useStockProfile(symbol);
   const { data: fundamentals } = useStockFundamentals(symbol);
   const { data: consensus } = useAIConsensus(symbol);

   const stock = useMemo(() => {
      if (fromStore) return fromStore;
      return {
         symbol,
         name: profile?.name ?? symbol,
         price: 0,
         change: 0,
         changePercent: 0,
         volume: 0,
         tradingValue: 0,
         open: 0,
         high: 0,
         low: 0,
         prevClose: 0,
         ceiling: 0,
         floor: 0,
         signal: "THEO DÕI" as const,
         trend: "steady" as const,
         lastUpdate: new Date().toISOString(),
      };
   }, [fromStore, symbol, profile]);

   const [timeframe, setTimeframe] = useState('1D');
   const [layout, setLayout] = useState<'1x1' | '2x2' | '3x1'>('1x1');

   return (
      <ErrorBoundary>
         <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="flex flex-col h-screen overflow-hidden bg-[#050505]"
         >
            {/* TOP BAR - PRO HEADER */}
            <div className="h-14 border-b border-white/5 flex items-center justify-between px-lg bg-[#0a0a0a] z-50">
               <div className="flex items-center gap-xl">
                  <div className="flex items-center gap-md">
                     <span className="text-xl font-black text-primary tracking-tighter">{stock.symbol}</span>
                     <span className="text-[10px] font-bold opacity-40 uppercase truncate max-w-[120px]">{stock.name}</span>
                  </div>
                  <div className="h-6 w-[1px] bg-white/10" />
                  <div className="flex items-baseline gap-md">
                     <span className={cn("text-lg font-bold font-data-mono", getPriceColor(stock.price, stock.prevClose, stock.ceiling, stock.floor))}>
                        {stock.price.toFixed(1)}
                     </span>
                     <span className={cn("text-[10px] font-bold", stock.trend === 'up' ? 'text-secondary' : 'text-error')}>
                        {stock.change > 0 ? '+' : ''}{stock.change} ({stock.changePercent.toFixed(2)}%)
                     </span>
                  </div>
               </div>

               <div className="flex items-center gap-lg">
                  <div className="flex gap-1 bg-white/5 p-0.5 rounded-lg border border-white/10">
                     {['1x1', '2x2', '3x1'].map(l => (
                        <button
                           key={l}
                           onClick={() => setLayout(l as any)}
                           className={cn(
                              "px-2 py-1 text-[9px] font-black rounded transition-all",
                              layout === l ? "bg-secondary text-white" : "opacity-40 hover:bg-white/10"
                           )}
                        >
                           {l}
                        </button>
                     ))}
                  </div>
                  <div className="h-6 w-[1px] bg-white/10" />
                  <div className="flex gap-1 bg-white/5 p-0.5 rounded-lg border border-white/10">
                     {['1m', '5m', '15m', '1H', '1D', 'W'].map(tf => (
                        <button
                           key={tf}
                           onClick={() => setTimeframe(tf)}
                           className={cn(
                              "px-2 py-1 text-[9px] font-black rounded transition-all",
                              timeframe === tf ? "bg-primary text-white" : "opacity-40 hover:bg-white/10"
                           )}
                        >
                           {tf}
                        </button>
                     ))}
                  </div>
                  <div className="h-6 w-[1px] bg-white/10" />
                  <div className="flex gap-md">
                     <button className="material-symbols-outlined text-[18px] opacity-40 hover:text-primary transition-all">add_chart</button>
                     <button className="material-symbols-outlined text-[18px] opacity-40 hover:text-primary transition-all">brush</button>
                     <button className="material-symbols-outlined text-[18px] opacity-40 hover:text-primary transition-all">settings</button>
                  </div>
               </div>
            </div>

            {/* MAIN WORKSPACE - GRID LAYOUT */}
            <div className="flex-1 grid grid-cols-12 overflow-hidden">

               {/* LEFT COLUMN: DOM & ORDER FLOW */}
               <div className="col-span-2 border-r border-white/5 flex flex-col bg-[#080808]">
                  <div className="p-md border-b border-white/5 bg-white/[0.01]">
                     <h4 className="text-[9px] font-black uppercase tracking-widest opacity-40">Microstructure</h4>
                  </div>
                  <OrderBook symbol={symbol} />
                  <div className="flex-1 p-md flex flex-col">
                     <h4 className="text-[9px] font-black uppercase tracking-widest opacity-40 mb-md">Advanced Order</h4>
                     <div className="space-y-lg flex-1">
                        <div className="grid grid-cols-2 gap-md">
                           <button className="py-2.5 rounded-xl bg-secondary text-white text-[10px] font-black shadow-lg shadow-secondary/10 hover:brightness-110 transition-all">BUY / LONG</button>
                           <button className="py-2.5 rounded-xl bg-error text-white text-[10px] font-black shadow-lg shadow-error/10 hover:brightness-110 transition-all">SELL / SHORT</button>
                        </div>

                        <div className="space-y-lg">
                           <div className="space-y-2">
                              <label className="text-[8px] font-black opacity-30 uppercase">Order Type</label>
                              <select className="w-full bg-white/5 border border-white/10 rounded-xl p-2 text-[10px] font-bold focus:outline-none appearance-none">
                                 <option>LIMIT ORDER</option>
                                 <option>MARKET ORDER</option>
                                 <option>STOP LIMIT</option>
                              </select>
                           </div>

                           <div className="space-y-2">
                              <label className="text-[8px] font-black opacity-30 uppercase">Quantity (LOT)</label>
                              <input type="number" defaultValue="1000" className="w-full bg-white/5 border border-white/10 rounded-xl p-2 text-xs font-data-mono font-bold focus:outline-none focus:border-primary" />
                           </div>

                           <div className="grid grid-cols-2 gap-md">
                              <div className="space-y-1">
                                 <label className="text-[8px] font-black text-secondary/60 uppercase">Take Profit</label>
                                 <input type="number" placeholder="Price" className="w-full bg-secondary/5 border border-secondary/10 rounded-lg p-2 text-[10px] font-bold focus:outline-none" />
                              </div>
                              <div className="space-y-1">
                                 <label className="text-[8px] font-black text-error/60 uppercase">Stop Loss</label>
                                 <input type="number" placeholder="Price" className="w-full bg-error/5 border border-error/10 rounded-lg p-2 text-[10px] font-bold focus:outline-none" />
                              </div>
                           </div>
                        </div>
                     </div>

                     <div className="mt-auto pt-lg border-t border-white/5">
                        <div className="bg-primary/5 p-md rounded-xl space-y-2">
                           <div className="flex justify-between text-[9px] font-bold">
                              <span className="opacity-40 uppercase">Open PnL</span>
                              <span className="text-secondary font-data-mono">+12.5M</span>
                           </div>
                           <div className="flex justify-between text-[9px] font-bold">
                              <span className="opacity-40 uppercase">Position</span>
                              <span className="font-data-mono">5,000 VHM</span>
                           </div>
                        </div>
                     </div>
                  </div>
               </div>

               {/* CENTER COLUMN: MAIN CHART & ANALYTICS */}
               <div className="col-span-7 flex flex-col overflow-y-auto no-scrollbar border-r border-white/5">
                  <div className={cn(
                     "flex-1 min-h-[600px] border-b border-white/5 relative bg-[#050505]",
                     layout === '2x2' && "grid grid-cols-2 grid-rows-2",
                     layout === '3x1' && "grid grid-rows-3"
                  )}>
                     {layout === '1x1' ? (
                        <>
                           {/* CHART OVERLAYS */}
                           <div className="absolute top-md left-md z-10 flex flex-col gap-sm">
                              <Badge variant="secondary" className="bg-[#0a0a0a]/80 backdrop-blur-md">RSI: 62.4</Badge>
                              <Badge variant="outline" className="bg-[#0a0a0a]/80 backdrop-blur-md text-cyan-400">MA20: 112.5</Badge>
                           </div>
                           <PriceChart symbol={symbol} interval={timeframe} />
                        </>
                     ) : layout === '2x2' ? (
                        <>
                           <div className="border-r border-b border-white/5 relative"><PriceChart symbol={symbol} interval={timeframe} /><Badge className="absolute top-2 left-2 text-[8px]">CHART 1</Badge></div>
                           <div className="border-b border-white/5 relative"><PriceChart symbol={symbol} interval={timeframe} /><Badge className="absolute top-2 left-2 text-[8px]">CHART 2</Badge></div>
                           <div className="border-r border-white/5 relative"><PriceChart symbol={symbol} interval={timeframe} /><Badge className="absolute top-2 left-2 text-[8px]">CHART 3</Badge></div>
                           <div className="relative"><PriceChart symbol={symbol} interval={timeframe} /><Badge className="absolute top-2 left-2 text-[8px]">CHART 4</Badge></div>
                        </>
                     ) : (
                        <>
                           <div className="border-b border-white/5 relative"><PriceChart symbol={symbol} interval={timeframe} /><Badge className="absolute top-2 left-2 text-[8px]">TIMEFRAME 1</Badge></div>
                           <div className="border-b border-white/5 relative"><PriceChart symbol={symbol} interval={timeframe} /><Badge className="absolute top-2 left-2 text-[8px]">TIMEFRAME 2</Badge></div>
                           <div className="relative"><PriceChart symbol={symbol} interval={timeframe} /><Badge className="absolute top-2 left-2 text-[8px]">TIMEFRAME 3</Badge></div>
                        </>
                     )}
                  </div>

                  <div className="p-lg space-y-lg bg-white/[0.01]">
                     <TradingData stock={stock} />
                     <div className="grid grid-cols-2 gap-lg">
                        <FundamentalData
                           pe={fundamentals?.pe?.toFixed(1) ?? "—"}
                           pb={fundamentals?.pb?.toFixed(1) ?? "—"}
                           eps={fundamentals?.eps?.toLocaleString() ?? "—"}
                           roe={fundamentals?.roe ? `${fundamentals.roe.toFixed(1)}%` : "—"}
                           dividend="—"
                           marketCap={formatCurrency(stock.marketCap || 0, 'T')}
                        />
                        <SentimentAnalysis />
                     </div>
                  </div>
               </div>

               {/* RIGHT COLUMN: NEWS & INSIGHTS */}
               <div className="col-span-3 flex flex-col bg-[#080808]">
                  <div className="p-md border-b border-white/5 bg-white/[0.01]">
                     <h4 className="text-[9px] font-black uppercase tracking-widest opacity-40">Pro Insights & News</h4>
                  </div>
                  <div className="flex-1 overflow-y-auto p-md space-y-lg no-scrollbar">
                     <GlassCard className="bg-primary/5 border-primary/10 p-md">
                        <div className="flex items-center gap-md mb-md">
                           <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center text-white">
                              <span className="material-symbols-outlined text-sm">auto_awesome</span>
                           </div>
                           <span className="text-[10px] font-black uppercase tracking-widest">Aura Consensus</span>
                        </div>
                        <p className="text-xs leading-relaxed opacity-80 italic">
                           {consensus?.summary ?? "Đang phân tích..."}
                        </p>
                        <div className="mt-md flex justify-between items-end">
                           <span className="text-[9px] font-bold opacity-40">
                              {consensus?.technical?.confidence
                                 ? `CONFIDENCE: ${Math.round(consensus.technical.confidence * 100)}%`
                                 : "AI ANALYSIS"}
                           </span>
                           <Badge variant="secondary">{consensus?.consensus ?? "—"}</Badge>
                        </div>
                     </GlassCard>

                     <div className="space-y-md">
                        <h5 className="text-[10px] font-black opacity-40 uppercase tracking-widest border-l-2 border-primary pl-2">Tin tức liên quan</h5>
                        <NewsList symbol={symbol} />
                     </div>
                  </div>

                  <div className="p-md border-t border-white/5 bg-[#0a0a0a]">
                     <button className="w-full py-3 rounded-xl bg-white/5 border border-white/10 text-[10px] font-black uppercase tracking-widest hover:bg-white/10 transition-all">Xem toàn bộ lịch sử</button>
                  </div>
               </div>

            </div>
         </motion.div>
      </ErrorBoundary>
   );
}
