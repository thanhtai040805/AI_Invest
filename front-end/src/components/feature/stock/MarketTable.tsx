"use client";

import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import { useStockStore } from "@/stores/useStockStore";
import { formatVolume, getPriceColor } from "@/lib/market-utils";
import Link from "next/link";
import { useMarketSnapshot } from "@/hooks/useMarketData";

export function MarketTable() {
  const stocks = useStockStore((state) => state.stocks);
  const searchQuery = useStockStore((state) => state.searchQuery);
  const { isLoading, isFetching } = useMarketSnapshot();

  const filteredStocks = stocks.filter(
    (s) =>
      s.symbol.toLowerCase().includes(searchQuery.toLowerCase()) ||
      s.name.toLowerCase().includes(searchQuery.toLowerCase()),
  );

  if (isLoading && stocks.length === 0) {
    return (
      <div className="p-xl text-center text-on-surface-variant/50 text-sm font-outfit">
        <span className="inline-block w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin mr-3 align-middle" />
        Đang tải bảng giá trực tuyến...
      </div>
    );
  }

  return (
    <div className="relative overflow-hidden rounded-2xl border border-white/5 bg-surface-container-lowest/40 backdrop-blur-xl">
      <div className="overflow-x-auto scrollbar-thin">
        <table className="w-full text-left border-collapse min-w-[1000px] select-none">
          <thead>
            <tr className="border-b border-white/5 bg-white/[0.02] text-[10px] text-on-surface-variant font-medium tracking-widest uppercase font-outfit">
              <th className="px-md py-4 sticky left-0 bg-[#08080a]/95 backdrop-blur-md z-20 border-r border-white/5">MÃ</th>
              <th className="px-md py-4 text-right text-yellow-500 font-bold">TC</th>
              <th className="px-md py-4 text-right text-purple-400 font-bold">TRẦN</th>
              <th className="px-md py-4 text-right text-cyan-400 font-bold">SÀN</th>
              <th className="px-md py-4 text-right">MỞ</th>
              <th className="px-md py-4 text-right">CAO</th>
              <th className="px-md py-4 text-right">THẤP</th>
              <th className="px-md py-4 text-right text-on-surface font-bold bg-white/[0.02]">GIÁ KHỚP</th>
              <th className="px-md py-4 text-right">+/-</th>
              <th className="px-md py-4 text-right">KL GIAO DỊCH</th>
              <th className="px-md py-4 text-right">GIÁ TRỊ (TỶ)</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/[0.03]">
            {filteredStocks.length > 0 ? (
              filteredStocks.map((stock, i) => (
                <motion.tr
                  key={stock.symbol}
                  initial={{ opacity: 0, y: 5 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ type: "spring", stiffness: 100, damping: 20, delay: i * 0.015 }}
                  className="group hover:bg-white/[0.02] transition-colors duration-200 cursor-pointer text-xs font-mono"
                >
                  <td className="px-md py-3 sticky left-0 bg-[#08080a]/95 backdrop-blur-md z-10 group-hover:bg-[#111115] transition-colors border-r border-white/5">
                    <Link href={`/stock/${stock.symbol}`} className="flex flex-col relative z-10">
                      <span
                        className={cn(
                          "font-extrabold text-[13px] tracking-tight font-outfit transition-transform group-hover:translate-x-1 duration-300 origin-left inline-block",
                          getPriceColor(stock.price, stock.prevClose, stock.ceiling, stock.floor),
                        )}
                      >
                        {stock.symbol}
                      </span>
                      <span className="text-[9px] text-on-surface-variant/60 font-sans mt-0.5 max-w-[90px] truncate block uppercase font-medium">
                        {stock.name}
                      </span>
                    </Link>
                  </td>
                  <td className="px-md py-3 text-right text-yellow-500/80 font-medium font-data-mono">
                    {stock.prevClose.toFixed(2)}
                  </td>
                  <td className="px-md py-3 text-right text-purple-400/80 font-medium font-data-mono">
                    {stock.ceiling.toFixed(2)}
                  </td>
                  <td className="px-md py-3 text-right text-cyan-400/80 font-medium font-data-mono">
                    {stock.floor.toFixed(2)}
                  </td>
                  <td
                    className={cn(
                      "px-md py-3 text-right font-medium font-data-mono",
                      getPriceColor(stock.open, stock.prevClose, stock.ceiling, stock.floor),
                    )}
                  >
                    {stock.open.toFixed(2)}
                  </td>
                  <td
                    className={cn(
                      "px-md py-3 text-right font-medium font-data-mono",
                      getPriceColor(stock.high, stock.prevClose, stock.ceiling, stock.floor),
                    )}
                  >
                    {stock.high.toFixed(2)}
                  </td>
                  <td
                    className={cn(
                      "px-md py-3 text-right font-medium font-data-mono",
                      getPriceColor(stock.low, stock.prevClose, stock.ceiling, stock.floor),
                    )}
                  >
                    {stock.low.toFixed(2)}
                  </td>
                  <td className="px-md py-3 text-right bg-white/[0.01] group-hover:bg-white/[0.03] transition-colors border-x border-white/5">
                    <span
                      className={cn(
                        "font-extrabold text-[13px] font-data-mono",
                        getPriceColor(stock.price, stock.prevClose, stock.ceiling, stock.floor),
                      )}
                    >
                      {stock.price.toFixed(2)}
                    </span>
                  </td>
                  <td
                    className={cn(
                      "px-md py-3 text-right font-bold font-data-mono",
                      stock.trend === "up" ? "text-secondary" : "text-error",
                    )}
                  >
                    <span className="flex items-center justify-end gap-0.5">
                      <span className="material-symbols-outlined text-[12px]">
                        {stock.trend === "up" ? "arrow_drop_up" : "arrow_drop_down"}
                      </span>
                      {stock.changePercent > 0 ? "+" : ""}
                      {stock.changePercent.toFixed(2)}%
                    </span>
                  </td>
                  <td className="px-md py-3 text-right text-on-surface-variant/80 font-medium font-data-mono">
                    {formatVolume(stock.volume)}
                  </td>
                  <td className="px-md py-3 text-right text-on-surface-variant/80 font-medium font-data-mono">
                    {(stock.tradingValue / 1_000_000_000).toFixed(2)}
                  </td>
                </motion.tr>
              ))
            ) : (
              <tr>
                <td colSpan={11} className="px-lg py-xl text-center text-on-surface-variant/40 italic font-outfit text-sm">
                  {isFetching ? "Đang cập nhật dữ liệu..." : "Không tìm thấy mã cổ phiếu nào phù hợp..."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
