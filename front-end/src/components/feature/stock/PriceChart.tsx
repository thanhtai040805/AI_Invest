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

export default function PriceChart({ symbol, interval = "1D" }: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const cachedDataRef = useRef<Map<string, any[]>>(new Map());
  const lastCandleRef = useRef<any>(null);
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
      console.log(`[PriceChart] REST ${symbol} ${interval}: source=${response?.source} count=${rows.length} range=${startDate}→${endDate}`);

      if (rows.length === 0) {
        hasReturnedEmptyRef.current = true;
        console.log(`[PriceChart] No data, returning [] to stop loop`);
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

      console.log(`[PriceChart] Mapped ${unique.length} unique candles. First: ${new Date(unique[0].timestamp).toISOString().split('T')[0]}, Last: ${new Date(unique[unique.length-1].timestamp).toISOString().split('T')[0]}`);

      // If chart requests historical range but we only got today's candle → return [] to stop loop
      if (to < todayStart && unique.length === 1 && unique[0].timestamp >= todayStart) {
        hasReturnedEmptyRef.current = true;
        console.log(`[PriceChart] Historical request but only got today's candle → returning [] to stop loop`);
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
    if (!containerRef.current || !symbol) return;

    hasReturnedEmptyRef.current = false;
    containerRef.current.innerHTML = "";

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
        multiplier: periodConfig.multiplier,
        timespan: periodConfig.timespan,
        text: interval,
      },
      mainIndicators: ["MA"],
      subIndicators: ["VOL"],
      datafeed: {
        searchSymbols: async () => [{ ticker: symbol, name: symbol, exchange: "HOSE", market: "stocks" }],
        getHistoryKLineData: async (_symbol: any, _period: any, from: number, to: number) => {
          const data = await fetchData(from, to);
          console.log(`[PriceChart] getHistoryKLineData: from=${new Date(from).toISOString().split('T')[0]} to=${new Date(to).toISOString().split('T')[0]} count=${data.length}`);
          return data;
        },
        subscribe: (_symbol: any, _period: any, callback: any) => {
          console.log(`[PriceChart] Subscribing to OHLC WebSocket for ${symbol} ${interval}`);
          const unsub = socketClient.subscribeStock(symbol, {
            onOhlc: (data: any) => {
              let ts = new Date(data.lastUpdate || data.timestamp).getTime();
              if (interval === "1D" || interval === "W") {
                ts = startOfDay(ts);
              }
              const candle = {
                timestamp: ts,
                open: Number(data.open),
                high: Number(data.high),
                low: Number(data.low),
                close: Number(data.close),
                volume: Number(data.volume ?? 0),
              };
              console.log(`[PriceChart] WS onOhlc → ts=${ts} date=${new Date(ts).toISOString().split('T')[0]} O=${candle.open} C=${candle.close}`);
              lastCandleRef.current = candle;
              callback(candle);
            },
            onOhlcClosed: (data: any) => {
              let ts = new Date(data.lastUpdate || data.timestamp).getTime();
              if (interval === "1D" || interval === "W") {
                ts = startOfDay(ts);
              }
              const candle = {
                timestamp: ts,
                open: Number(data.open),
                high: Number(data.high),
                low: Number(data.low),
                close: Number(data.close),
                volume: Number(data.volume ?? 0),
              };
              console.log(`[PriceChart] WS onOhlcClosed → ts=${ts} date=${new Date(ts).toISOString().split('T')[0]} O=${candle.open} C=${candle.close}`);
              lastCandleRef.current = candle;
              callback(candle);
            },
          });
          return unsub;
        },
        unsubscribe: () => {},
      },
    };

    chartRef.current = new KLineChartPro(options as any);

    return () => {
      try {
        chartRef.current?.dispose?.();
      } catch {
        /* ignore */
      }
      chartRef.current = null;
      if (containerRef.current) containerRef.current.innerHTML = "";
    };
  }, [symbol, interval, fetchData]);

  return <div ref={containerRef} className="w-full h-full min-h-[400px]" />;
}
