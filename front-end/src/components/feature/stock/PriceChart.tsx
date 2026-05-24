"use client";

import { useEffect, useRef, useCallback } from "react";
import { KLineChartPro } from "@klinecharts/pro";
import "@klinecharts/pro/dist/klinecharts-pro.css";
import { stockAPI } from "@/services/api";
import { socketClient } from "@/services/socket";

interface PriceChartProps {
  symbol: string;
  interval?: string;
}

function startOfDay(ts: number): number {
  const d = new Date(ts);
  return Date.UTC(d.getFullYear(), d.getMonth(), d.getDate(), 0, 0, 0, 0);
}

function normalizeTimestamp(timeStr: string, interval: string): number {
  const ts = new Date(timeStr).getTime();
  if (interval === "1D" || interval === "W") {
    return startOfDay(ts);
  }
  return ts;
}

interface CandleData {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface DatafeedSymbol {
  ticker: string;
  name: string;
  exchange: string;
  market: string;
}

interface DatafeedPeriod {
  multiplier: number;
  timespan: string;
  text: string;
}

export default function PriceChart({ symbol, interval = "1D" }: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<KLineChartPro | null>(null);
  const cachedDataRef = useRef<Map<string, CandleData[]>>(new Map());
  const lastCandleRef = useRef<CandleData | null>(null);
  const hasReturnedEmptyRef = useRef(false);

  const fetchData = useCallback(async (from: number, to: number) => {
    if (hasReturnedEmptyRef.current) {
      return [];
    }

    const cacheKey = `${symbol}-${interval}-${from}-${to}`;
    if (cachedDataRef.current.has(cacheKey)) {
      return cachedDataRef.current.get(cacheKey)!;
    }

    try {
      const startDate = new Date(from).toISOString().split("T")[0];
      const endDate = new Date(to).toISOString().split("T")[0];
      const response = await stockAPI.getOHLCV(symbol, {
        interval,
        start: startDate,
        end: endDate,
      });

      const rows = response?.data ?? [];

      if (rows.length === 0) {
        hasReturnedEmptyRef.current = true;
        return [];
      }

      const todayStart = startOfDay(Date.now());
      const mapped = rows.map((c: { time: string; open: number; high: number; low: number; close: number; volume?: number }) => {
        const ts = normalizeTimestamp(c.time, interval);
        return {
          timestamp: ts,
          open: Number(c.open),
          high: Number(c.high),
          low: Number(c.low),
          close: Number(c.close),
          volume: Number(c.volume ?? 0),
        };
      });

      // Deduplicate by timestamp (keep last occurrence for same day on 1D)
      const deduped = new Map<number, typeof mapped[0]>();
      for (const candle of mapped) {
        deduped.set(candle.timestamp, candle);
      }
      const unique = Array.from(deduped.values()).sort((a, b) => a.timestamp - b.timestamp);

      // If chart requests historical range but we only got today's candle → return [] to stop loop
      if (to < todayStart && unique.length === 1 && unique[0].timestamp >= todayStart) {
        hasReturnedEmptyRef.current = true;
        return [];
      }

      cachedDataRef.current.set(cacheKey, unique);
      if (unique.length > 0) {
        lastCandleRef.current = unique[unique.length - 1];
      }
      return unique;
    } catch (error) {
      console.error("Failed to fetch OHLCV data:", error);
      return [];
    }
  }, [symbol, interval]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !symbol) return;

    hasReturnedEmptyRef.current = false;
    container.innerHTML = "";

    const getPeriodConfig = (int: string) => {
      switch (int) {
        case "1m": return { multiplier: 1, timespan: "minute" };
        case "5m": return { multiplier: 5, timespan: "minute" };
        case "15m": return { multiplier: 15, timespan: "minute" };
        case "1H": return { multiplier: 1, timespan: "hour" };
        case "1D": return { multiplier: 1, timespan: "day" };
        case "W": return { multiplier: 1, timespan: "week" };
        default: return { multiplier: 1, timespan: "day" };
      }
    };

    const periodConfig = getPeriodConfig(interval);

    const options = {
      container: container,
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
        multiplier: periodConfig.multiplier,
        timespan: periodConfig.timespan,
        text: interval,
      },
      mainIndicators: ["MA"],
      subIndicators: ["VOL"],
      datafeed: {
        searchSymbols: async () => [{ ticker: symbol, name: symbol, exchange: "HOSE", market: "stocks" }],
        getHistoryKLineData: async (_symbol: DatafeedSymbol, _period: DatafeedPeriod, from: number, to: number) => {
          const data = await fetchData(from, to);
          return data;
        },
        subscribe: (_symbol: DatafeedSymbol, _period: DatafeedPeriod, callback: (candle: CandleData) => void) => {
          const unsub = socketClient.subscribeStock(symbol, {
            onOhlc: (data: Record<string, unknown>) => {
              let ts = new Date((data.lastUpdate || data.timestamp) as string).getTime();
              if (interval === "1D" || interval === "W") {
                ts = startOfDay(ts);
              }
              const candle: CandleData = {
                timestamp: ts,
                open: Number(data.open),
                high: Number(data.high),
                low: Number(data.low),
                close: Number(data.close),
                volume: Number(data.volume ?? 0),
              };
              lastCandleRef.current = candle;
              callback(candle);
            },
            onOhlcClosed: (data: Record<string, unknown>) => {
              let ts = new Date((data.lastUpdate || data.timestamp) as string).getTime();
              if (interval === "1D" || interval === "W") {
                ts = startOfDay(ts);
              }
              const candle: CandleData = {
                timestamp: ts,
                open: Number(data.open),
                high: Number(data.high),
                low: Number(data.low),
                close: Number(data.close),
                volume: Number(data.volume ?? 0),
              };
              lastCandleRef.current = candle;
              callback(candle);
            },
          });
          return unsub;
        },
        unsubscribe: () => {},
      },
    };

    chartRef.current = new KLineChartPro(options);

    return () => {
      try {
        (chartRef.current as unknown as { dispose?: () => void })?.dispose?.();
      } catch {
        /* ignore */
      }
      chartRef.current = null;
      if (container) container.innerHTML = "";
    };
  }, [symbol, interval, fetchData]);

  return <div ref={containerRef} className="w-full h-full min-h-[400px]" />;
}
