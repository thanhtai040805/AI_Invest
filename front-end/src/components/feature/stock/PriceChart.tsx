"use client";

import { useEffect, useRef, useMemo } from "react";
import { KLineChartPro } from "@klinecharts/pro";
import "@klinecharts/pro/dist/klinecharts-pro.css";
import { useStockOHLCV } from "@/hooks/useMarketData";

interface CustomKLineData {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

interface PriceChartProps {
  symbol: string;
  interval?: string;
}

export default function PriceChart({ symbol, interval = "1D" }: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<{ dispose?: () => void } | null>(null);
  const { data: ohlcvData } = useStockOHLCV(symbol, interval);

  const klineData = useMemo((): CustomKLineData[] => {
    const rows = ohlcvData?.data ?? [];
    return rows.map((c: { time: string; open: number; high: number; low: number; close: number; volume?: number }) => ({
      timestamp: new Date(c.time).getTime(),
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
      volume: c.volume,
    }));
  }, [ohlcvData]);

  useEffect(() => {
    if (!containerRef.current || !symbol) return;

    containerRef.current.innerHTML = "";

    const options = {
      container: containerRef.current,
      locale: "vi-VN",
      timezone: "Asia/Ho_Chi_Minh",
      symbol: {
        ticker: symbol,
        name: symbol,
        exchange: "HOSE",
        market: "stocks",
        priceCurrency: "vnd",
        type: "stock",
      },
      period: {
        multiplier: interval === "1D" ? 1 : 15,
        timespan: interval === "1D" ? "day" : "minute",
        text: interval,
      },
      mainIndicators: ["MA"],
      subIndicators: ["VOL"],
      datafeed: {
        searchSymbols: async () => [{ ticker: symbol, name: symbol, exchange: "HOSE", market: "stocks" }],
        getHistoryKLineData: async () => (klineData.length ? klineData : []),
        subscribe: (_sym: unknown, _period: unknown, callback: (d: CustomKLineData) => void) => {
          if (klineData.length) callback(klineData[klineData.length - 1]);
          return "0";
        },
        unsubscribe: () => { },
      },
    };

    chartRef.current = new KLineChartPro(options as Parameters<typeof KLineChartPro>[0]);

    // Load saved drawings if available
    const savedDrawings = localStorage.getItem(`drawings_${symbol}_${interval}`);
    if (savedDrawings && chartRef.current) {
      try {
        const drawings = JSON.parse(savedDrawings);
        // Note: Specific implementation depends on KLineChart API to restore overlays.
        // Assuming we could restore if KLineChartPro exposes the underlying chart instance.
        const chart = (chartRef.current as any).getChart?.() ?? chartRef.current;
        if (chart && typeof chart.createOverlay === 'function') {
          drawings.forEach((d: any) => chart.createOverlay(d));
        }
      } catch (e) {
        console.warn("Failed to load saved drawings", e);
      }
    }

    // Auto-save drawings periodically
    const saveInterval = setInterval(() => {
      const chart = (chartRef.current as any)?.getChart?.() ?? chartRef.current;
      if (chart && typeof chart.getOverlayIds === 'function' && typeof chart.getOverlayById === 'function') {
        const overlayIds = chart.getOverlayIds() || [];
        const drawings = overlayIds.map((id: string) => chart.getOverlayById(id)).filter(Boolean);
        if (drawings.length > 0) {
          localStorage.setItem(`drawings_${symbol}_${interval}`, JSON.stringify(drawings));
        }
      }
    }, 5000);

    return () => {
      clearInterval(saveInterval);
      try {
        chartRef.current?.dispose?.();
      } catch {
        /* ignore */
      }
      chartRef.current = null;
      if (containerRef.current) containerRef.current.innerHTML = "";
    };
  }, [symbol, interval, klineData]);

  return <div ref={containerRef} className="w-full h-[600px]" />;
}
