"use client";

import { useStockNews } from "@/hooks/useMarketData";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

export function NewsList({ symbol }: { symbol: string }) {
  const { data: news, isLoading } = useStockNews(symbol);

  if (isLoading) {
    return (
      <div className="space-y-md">
        {[1, 2, 3].map(i => (
          <div key={i} className="animate-pulse space-y-2">
            <div className="h-4 bg-white/5 rounded w-full" />
            <div className="h-3 bg-white/5 rounded w-1/4" />
          </div>
        ))}
      </div>
    );
  }

  if (!news || news.length === 0) {
    return (
      <div className="text-center py-xl opacity-30 text-[10px] font-bold uppercase tracking-widest">
        No recent news for {symbol}
      </div>
    );
  }

  return (
    <div className="space-y-md">
      {news.map((item: any, i: number) => (
        <motion.a
          key={i}
          href={item.link || "#"}
          target="_blank"
          rel="noopener noreferrer"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: i * 0.05 }}
          className="block group space-y-2 border-b border-white/5 pb-md last:border-0"
        >
          <p className="text-[11px] font-bold group-hover:text-primary transition-all leading-snug">
            {item.title}
          </p>
          <div className="flex justify-between items-center text-[9px] opacity-40">
            <span>{item.source || "Market News"}</span>
            <span>{item.time || "Recently"}</span>
          </div>
        </motion.a>
      ))}
    </div>
  );
}
