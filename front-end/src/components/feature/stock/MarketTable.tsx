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
      <div className="p-xl text-center text-on-surface-variant opacity-50 text-sm">
        Đang tải bảng giá...
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left border-collapse min-w-[1000px]">
        <thead>
          <tr className="border-b border-white/5 bg-white/[0.02] text-[10px] text-on-surface-variant font-label-caps tracking-widest">
            <th className="px-md py-xl sticky left-0 bg-[#0a0a0a] z-20">MÃ</th>
            <th className="px-md py-xl text-right text-yellow-400">TC</th>
            <th className="px-md py-xl text-right text-purple-500">TRẦN</th>
            <th className="px-md py-xl text-right text-cyan-400">SÀN</th>
            <th className="px-md py-xl text-right">MỞ</th>
            <th className="px-md py-xl text-right">CAO</th>
            <th className="px-md py-xl text-right">THẤP</th>
            <th className="px-md py-xl text-right text-on-surface">GIÁ KHỚP</th>
            <th className="px-md py-xl text-right">+/-</th>
            <th className="px-md py-xl text-right">KL</th>
            <th className="px-md py-xl text-right">GT (TỶ)</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.03]">
          {filteredStocks.length > 0 ? (
            filteredStocks.map((stock, i) => (
              <motion.tr
                key={stock.symbol}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: i * 0.02 }}
                className="group hover:bg-white/[0.03] transition-all duration-200 cursor-pointer text-[11px] font-data-mono"
              >
                <td className="px-md py-2 sticky left-0 bg-[#0a0a0a] z-10 group-hover:bg-[#151515] transition-colors border-r border-white/5">
                  <Link href={`/stock/${stock.symbol}`} className="flex flex-col relative z-10">
                    <span
                      className={cn(
                        "font-bold",
                        getPriceColor(stock.price, stock.prevClose, stock.ceiling, stock.floor),
                      )}
                    >
                      {stock.symbol}
                    </span>
                    <span className="text-[8px] opacity-40 uppercase truncate max-w-[80px] font-sans">
                      {stock.name}
                    </span>
                  </Link>
                </td>
                <td className="px-md py-2 text-right text-yellow-400 opacity-80">
                  {stock.prevClose.toFixed(2)}
                </td>
                <td className="px-md py-2 text-right text-purple-500 opacity-80">
                  {stock.ceiling.toFixed(2)}
                </td>
                <td className="px-md py-2 text-right text-cyan-400 opacity-80">
                  {stock.floor.toFixed(2)}
                </td>
                <td
                  className={cn(
                    "px-md py-2 text-right",
                    getPriceColor(stock.open, stock.prevClose, stock.ceiling, stock.floor),
                  )}
                >
                  {stock.open.toFixed(2)}
                </td>
                <td
                  className={cn(
                    "px-md py-2 text-right",
                    getPriceColor(stock.high, stock.prevClose, stock.ceiling, stock.floor),
                  )}
                >
                  {stock.high.toFixed(2)}
                </td>
                <td
                  className={cn(
                    "px-md py-2 text-right",
                    getPriceColor(stock.low, stock.prevClose, stock.ceiling, stock.floor),
                  )}
                >
                  {stock.low.toFixed(2)}
                </td>
                <td className="px-md py-2 text-right bg-white/[0.02]">
                  <span
                    className={cn(
                      "font-bold",
                      getPriceColor(stock.price, stock.prevClose, stock.ceiling, stock.floor),
                    )}
                  >
                    {stock.price.toFixed(2)}
                  </span>
                </td>
                <td
                  className={cn(
                    "px-md py-2 text-right font-bold",
                    stock.trend === "up" ? "text-secondary" : "text-error",
                  )}
                >
                  {stock.changePercent > 0 ? "+" : ""}
                  {stock.changePercent.toFixed(2)}%
                </td>
                <td className="px-md py-2 text-right text-on-surface-variant">
                  {formatVolume(stock.volume)}
                </td>
                <td className="px-md py-2 text-right text-on-surface-variant">
                  {(stock.tradingValue / 1_000_000_000).toFixed(2)}
                </td>
              </motion.tr>
            ))
          ) : (
            <tr>
              <td colSpan={11} className="px-lg py-xl text-center text-on-surface-variant opacity-40 italic">
                {isFetching ? "Đang cập nhật..." : "Không tìm thấy mã cổ phiếu nào..."}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
