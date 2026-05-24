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
        Không có tin tức mới cho {symbol}
      </div>
    );
  }

  return (
    <div className="space-y-md">
      {news.map((item: { id: string; url?: string; title: string; sentimentLabel?: string; friendlyKeyword?: string; publishDate?: string }, i: number) => {
        const formattedDate = item.publishDate
          ? new Date(item.publishDate).toLocaleString("vi-VN", {
              hour: "2-digit",
              minute: "2-digit",
              day: "2-digit",
              month: "2-digit",
            })
          : "Vừa xong";

        return (
          <motion.a
            key={item.id || i}
            href={item.url || "#"}
            target="_blank"
            rel="noopener noreferrer"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.05 }}
            className="block group space-y-2 border-b border-white/5 pb-md last:border-0"
          >
            <div className="flex items-start gap-2 justify-between">
              <p className="text-[11px] font-bold group-hover:text-primary transition-all leading-snug flex-1">
                {item.title}
              </p>
              {item.sentimentLabel && (
                <span
                  className={cn(
                    "text-[8px] font-black tracking-wider px-1.5 py-0.5 rounded border uppercase shrink-0",
                    item.sentimentLabel === "POSITIVE" &&
                      "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
                    item.sentimentLabel === "NEGATIVE" &&
                      "bg-rose-500/10 text-rose-400 border-rose-500/20",
                    item.sentimentLabel === "NEUTRAL" &&
                      "bg-white/5 text-white/40 border-white/10"
                  )}
                >
                  {item.sentimentLabel === "POSITIVE" && "Tích cực"}
                  {item.sentimentLabel === "NEGATIVE" && "Tiêu cực"}
                  {item.sentimentLabel === "NEUTRAL" && "Trung lập"}
                </span>
              )}
            </div>
            <div className="flex justify-between items-center text-[9px] opacity-40">
              <span className="font-mono">{item.friendlyKeyword || "Tin nhanh"}</span>
              <span>{formattedDate}</span>
            </div>
          </motion.a>
        );
      })}
    </div>
  );
}
