"use client";

import { motion } from "framer-motion";
import { GlassCard } from "@/components/ui/GlassCard";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/Badge";
import { useState, useEffect, useMemo } from "react";
import { useStockOrderBook, useStockRealtime } from "@/hooks/useMarketData";

interface OrderBookLevel {
  price: number;
  volume: number;
}

interface OrderBookProps {
  symbol: string;
}

export function OrderBook({ symbol }: OrderBookProps) {
  const [activeTab, setActiveTab] = useState<"DOM" | "Tape">("DOM");
  const [liveBook, setLiveBook] = useState<{ bids: OrderBookLevel[]; asks: OrderBookLevel[] } | null>(null);
  const [trades, setTrades] = useState<Array<{ time: string; price: number; volume: number; side: "buy" | "sell" }>>([]);

  const { data: orderbook } = useStockOrderBook(symbol);

  useStockRealtime(symbol, {
    onOrderBook: (data: { bids?: OrderBookLevel[]; asks?: OrderBookLevel[] }) => {
      if (data?.bids && data?.asks) setLiveBook({ bids: data.bids, asks: data.asks });
    },
    onTrade: (data: { time?: string; price?: number; volume?: number; side?: string }) => {
      if (!data?.price) return;
      setTrades((prev) => [
        {
          time: data.time ?? new Date().toLocaleTimeString(),
          price: data.price!,
          volume: data.volume ?? 0,
          side: (data.side === "sell" ? "sell" : "buy") as "buy" | "sell",
        },
        ...prev,
      ].slice(0, 30));
    },
  });

  const book = liveBook ?? orderbook;
  const asks = useMemo(() => [...(book?.asks ?? [])].reverse().slice(0, 10), [book]);
  const bids = useMemo(() => (book?.bids ?? []).slice(0, 10), [book]);

  let cumAsk = 0;
  asks.forEach((a) => {
    cumAsk += a.volume;
    (a as OrderBookLevel & { cumulativeVolume?: number }).cumulativeVolume = cumAsk;
  });
  let cumBid = 0;
  bids.forEach((b) => {
    cumBid += b.volume;
    (b as OrderBookLevel & { cumulativeVolume?: number }).cumulativeVolume = cumBid;
  });

  const totalVol = cumAsk + cumBid || 1;
  const midPrice = asks.length && bids.length ? (asks[asks.length - 1].price + bids[0].price) / 2 : 0;
  const spread = asks.length && bids.length ? asks[asks.length - 1].price - bids[0].price : 0;

  return (
    <GlassCard className="p-0 border-white/5 overflow-hidden flex flex-col h-full shadow-2xl bg-[#0a0a0a]">
      <div className="flex justify-between items-center p-md border-b border-white/5 bg-white/[0.02]">
        <div className="flex gap-md">
          {(["DOM", "Tape"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={cn(
                "text-[10px] font-bold uppercase tracking-widest transition-all",
                activeTab === tab ? "text-primary" : "opacity-40 hover:opacity-100",
              )}
            >
              {tab === "Tape" ? "Time & Sales" : "DOM"}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-sm text-[10px] font-data-mono">
          <span className="opacity-40">SPREAD:</span>
          <span className="text-on-surface font-bold text-secondary">
            {spread.toFixed(1)} ({midPrice > 0 ? ((spread / midPrice) * 100).toFixed(2) : 0}%)
          </span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto no-scrollbar font-data-mono">
        {activeTab === "DOM" ? (
          <table className="w-full text-[10px] border-collapse">
            <thead className="sticky top-0 bg-[#0a0a0a] z-20 shadow-md">
              <tr className="text-on-surface-variant opacity-40 border-b border-white/5">
                <th className="py-2 px-md text-left font-normal uppercase">Bán / Cum.</th>
                <th className="py-2 px-md text-center font-normal uppercase">Giá</th>
                <th className="py-2 px-md text-right font-normal uppercase">Mua / Cum.</th>
              </tr>
            </thead>
            <tbody>
              {asks.map((level, i) => (
                <tr key={`ask-${i}`} className="group hover:bg-white/[0.02] relative">
                  <td className="py-1 px-md text-left relative">
                    <span className="text-error font-bold">{(level.volume / 1000).toFixed(1)}k</span>
                  </td>
                  <td className="py-1 px-md text-center text-error font-black bg-error/[0.02] border-x border-white/5">
                    {level.price.toFixed(1)}
                  </td>
                  <td className="py-1 px-md text-right opacity-10">---</td>
                </tr>
              ))}
              <tr className="bg-white/5 border-y border-white/10">
                <td colSpan={3} className="py-1 px-md text-center text-xs font-black">
                  {midPrice.toFixed(1)}
                </td>
              </tr>
              {bids.map((level, i) => (
                <tr key={`bid-${i}`} className="group hover:bg-white/[0.02] relative">
                  <td className="py-1 px-md text-left opacity-10">---</td>
                  <td className="py-1 px-md text-center text-secondary font-black bg-secondary/[0.02] border-x border-white/5">
                    {level.price.toFixed(1)}
                  </td>
                  <td className="py-1 px-md text-right">
                    <span className="text-secondary font-bold">{(level.volume / 1000).toFixed(1)}k</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="p-md space-y-1">
            {trades.length === 0 ? (
              <p className="text-[10px] opacity-40 text-center py-md">Chờ khớp lệnh...</p>
            ) : (
              trades.map((trade, i) => (
                <motion.div
                  key={i}
                  initial={{ x: -10, opacity: 0 }}
                  animate={{ x: 0, opacity: 1 }}
                  className="flex justify-between items-center text-[10px] py-1 border-b border-white/[0.03]"
                >
                  <span className="opacity-30">{trade.time}</span>
                  <span className={cn("font-bold", trade.side === "buy" ? "text-secondary" : "text-error")}>
                    {trade.price.toFixed(1)}
                  </span>
                  <span className="font-bold">{(trade.volume / 1000).toFixed(1)}k</span>
                  <Badge variant="outline" className={cn("text-[8px] px-1 py-0 h-4 border-none bg-white/5", trade.side === "buy" ? "text-secondary" : "text-error")}>
                    {trade.side.toUpperCase()}
                  </Badge>
                </motion.div>
              ))
            )}
          </div>
        )}
      </div>

      <div className="p-md bg-white/[0.02] border-t border-white/5">
        <div className="h-2 w-full bg-white/5 rounded-full overflow-hidden flex">
          <div className="h-full bg-secondary" style={{ width: `${(cumBid / totalVol) * 100}%` }} />
          <div className="h-full bg-error" style={{ width: `${(cumAsk / totalVol) * 100}%` }} />
        </div>
      </div>
    </GlassCard>
  );
}
