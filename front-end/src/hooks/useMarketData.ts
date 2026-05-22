'use client';

import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef } from 'react';
import { marketAPI, stockAPI, screenerAPI, portfolioAPI, aiAPI } from '@/services/api';
import { socketClient } from '@/services/socket';
import { useMarketStore } from '@/stores/useMarketStore';
import { useStockStore } from '@/stores/useStockStore';
import { useOrderBookStore } from '@/stores/useOrderBookStore';
import { useTradesStore } from '@/stores/useTradesStore';
import { useLiquidityStore } from '@/stores/useLiquidityStore';
import { useHeatmapStore } from '@/stores/useHeatmapStore';
import {
  mapIndicesResponse,
  mapBreadthResponse,
  mapLiquidityResponse,
  mapHeatmapSectors,
  mapSnapshotToQuotes,
  mapQuoteResponse,
} from '@/lib/api-mappers';
import {
  safeParse,
  OrderBookSchema,
  TradeSchema,
  TradeExtraSchema,
} from '@/lib/validation';

/**
 * SINGLE WEBSOCKET GATEWAY PATTERN
 *
 * Market-wide events (indices, breadth, snapshot, liquidity, heatmap)
 * are subscribed ONLY by RealtimeProvider. These hooks read from stores.
 *
 * Per-stock events (orderbook, trades, quote) are subscribed by hooks
 * because they're symbol-specific and only needed when the component mounts.
 */

export function useMarketIndices() {
  const indices = useMarketStore((s) => s.indices);
  const setIndices = useMarketStore((s) => s.setIndices);

  return useQuery({
    queryKey: ['market', 'indices'],
    queryFn: async () => {
      const data = await marketAPI.getIndices();
      setIndices(mapIndicesResponse(data));
      return data;
    },
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    select: () => indices,
  });
}

export function useMarketBreadth() {
  const breadth = useMarketStore((s) => s.breadth);
  const setBreadth = useMarketStore((s) => s.setBreadth);

  return useQuery({
    queryKey: ['market', 'breadth'],
    queryFn: async () => {
      const data = await marketAPI.getBreadth();
      setBreadth(mapBreadthResponse(data));
      return data;
    },
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    select: () => breadth,
  });
}

export function useMarketLiquidity() {
  const liquidity = useLiquidityStore((s) => s.data);
  const setLiquidity = useLiquidityStore((s) => s.setLiquidity);

  return useQuery({
    queryKey: ['market', 'liquidity'],
    queryFn: async () => {
      const data = await marketAPI.getLiquidity();
      if (data?.points) {
        setLiquidity({
          totalValueBillion: 0,
          stockCount: 0,
          topByVolume: [],
          lastUpdate: new Date().toISOString(),
        });
      }
      return data;
    },
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    select: () => liquidity,
  });
}

export function useMarketSnapshot(exchange?: string) {
  const stocks = useStockStore((s) => s.stocks);
  const setStocks = useStockStore((s) => s.setStocks);

  return useQuery({
    queryKey: ['market', 'snapshot', exchange],
    queryFn: async () => {
      const data = await marketAPI.getSnapshot(exchange);
      setStocks(mapSnapshotToQuotes(data));
      return data;
    },
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    select: () => stocks,
  });
}

export function useMarketHeatmap() {
  const sectors = useHeatmapStore((s) => s.sectors);
  const setHeatmap = useHeatmapStore((s) => s.setHeatmap);

  return useQuery({
    queryKey: ['market', 'heatmap'],
    queryFn: async () => {
      const data = await marketAPI.getHeatmap();
      if (data?.sectors) {
        setHeatmap(mapHeatmapSectors(data));
      }
      return data;
    },
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    select: () => (sectors.length > 0 ? sectors : mapHeatmapSectors({})),
  });
}

export function useDashboardMarketData() {
  useMarketIndices();
  useMarketBreadth();
  useMarketLiquidity();
  useMarketHeatmap();
  return useMarketSnapshot();
}

export function useStockProfile(symbol: string) {
  return useQuery({
    queryKey: ['stock', symbol, 'profile'],
    queryFn: () => stockAPI.getProfile(symbol),
    enabled: !!symbol,
    staleTime: 60 * 60 * 1000,
    gcTime: 5 * 60_000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
  });
}

export function useStockOHLCV(
  symbol: string,
  interval: string = '1D',
  start?: string,
  end?: string,
) {
  return useQuery({
    queryKey: ['stock', symbol, 'ohlcv', interval, start, end],
    queryFn: () => stockAPI.getOHLCV(symbol, { interval, start, end }),
    enabled: !!symbol,
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
  });
}

export function useStockQuote(symbol: string) {
  const stocks = useStockStore((s) => s.stocks);
  const updateStock = useStockStore((s) => s.updateStock);
  const subscribedRef = useRef<string | null>(null);

  const query = useQuery({
    queryKey: ['stock', symbol, 'quote'],
    queryFn: async () => {
      const data = await stockAPI.getQuote(symbol);
      const mapped = mapQuoteResponse(data);
      updateStock(symbol.toUpperCase(), mapped);
      return mapped;
    },
    enabled: !!symbol,
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    if (!symbol) return;
    const sym = symbol.toUpperCase();
    if (subscribedRef.current === sym) return;

    subscribedRef.current = sym;
    return socketClient.subscribeStock(sym, {
      onPrice: (data) => {
        const mapped = mapQuoteResponse(data);
        updateStock(sym, mapped);
      },
    });
  }, [symbol, updateStock]);

  const stock = stocks.find((s) => s.symbol === symbol?.toUpperCase());
  return { ...query, data: stock || query.data };
}

export function useStockOrderBook(symbol: string) {
  const setOrderBook = useOrderBookStore((s) => s.setOrderBook);
  const orderBookEntry = useOrderBookStore((s) => s.getOrderBook(symbol));
  const subscribedRef = useRef<string | null>(null);

  useEffect(() => {
    if (!symbol) return;
    const sym = symbol.toUpperCase();
    if (subscribedRef.current === sym) return;

    subscribedRef.current = sym;
    return socketClient.subscribeStock(sym, {
      onOrderBook: (data) => {
        const validated = safeParse(OrderBookSchema, data);
        if (validated) {
          setOrderBook(symbol, {
            bids: validated.bids,
            asks: validated.asks,
            lastUpdate: validated.lastUpdate?.toString(),
          });
        }
      },
    });
  }, [symbol, setOrderBook]);

  return useQuery({
    queryKey: ['stock', symbol, 'orderbook'],
    queryFn: async () => {
      if (orderBookEntry) {
        return orderBookEntry;
      }
      return stockAPI.getOrderBook(symbol);
    },
    enabled: !!symbol,
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
  });
}

export function useStockTrades(symbol: string) {
  const addTrade = useTradesStore((s) => s.addTrade);
  const setTrades = useTradesStore((s) => s.setTrades);
  const allTrades = useTradesStore((s) => s.trades);
  const trades = useMemo(() => allTrades[symbol.toUpperCase()] ?? [], [allTrades, symbol]);
  const subscribedRef = useRef<string | null>(null);

  useEffect(() => {
    if (!symbol) return;
    const sym = symbol.toUpperCase();
    if (subscribedRef.current === sym) return;

    subscribedRef.current = sym;
    return socketClient.subscribeStock(sym, {
      onTrade: (data) => {
        const validated = safeParse(TradeSchema, data);
        if (validated) {
          addTrade(symbol, {
            time: validated.lastUpdate
              ? new Date(validated.lastUpdate).toLocaleTimeString()
              : new Date().toLocaleTimeString(),
            price: validated.price,
            volume: validated.volume,
            side: validated.changePercent >= 0 ? 'buy' : 'sell',
          });
        }
      },
      onTradeExtra: (data) => {
        const validated = safeParse(TradeExtraSchema, data);
        if (validated) {
          addTrade(symbol, {
            time: validated.receivedAt
              ? new Date(validated.receivedAt * 1000).toLocaleTimeString()
              : new Date().toLocaleTimeString(),
            price: validated.price,
            volume: validated.volume,
            side: validated.matchType === 'S' ? 'sell' : 'buy',
          });
        }
      },
    });
  }, [symbol, addTrade]);

  return useQuery({
    queryKey: ['stock', symbol, 'trades'],
    queryFn: async () => {
      if (trades.length > 0) {
        return { trades, source: 'websocket' };
      }
      const apiData = await stockAPI.getTrades(symbol);
      if (apiData?.trades) {
        const formatted = apiData.trades.map((t: any) => ({
          time: t.receivedAt
            ? new Date(t.receivedAt * 1000).toLocaleTimeString()
            : t.time || new Date().toLocaleTimeString(),
          price: t.price,
          volume: t.volume,
          side: t.matchType === 'S' || t.side === 'sell' ? 'sell' : 'buy',
        }));
        setTrades(symbol, formatted);
        return { trades: formatted, source: 'api' };
      }
      return { trades: [], source: 'api' };
    },
    enabled: !!symbol,
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
  });
}

export function useStockRealtime(
  symbol: string,
  callbacks: {
    onPrice?: (data: unknown) => void;
    onOrderBook?: (data: unknown) => void;
    onTrade?: (data: unknown) => void;
  },
) {
  useEffect(() => {
    if (!symbol) return;
    return socketClient.subscribeStock(symbol, callbacks);
  }, [symbol]);
}

export function useStockFundamentals(symbol: string) {
  return useQuery({
    queryKey: ['stock', symbol, 'fundamentals'],
    queryFn: () => stockAPI.getFundamentals(symbol),
    enabled: !!symbol,
    staleTime: 6 * 60 * 60 * 1000,
    gcTime: 5 * 60_000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
  });
}

export function useScreener(filters: Record<string, unknown>, enabled = true) {
  return useQuery({
    queryKey: ['screener', filters],
    queryFn: () => screenerAPI.filter(filters),
    enabled,
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
  });
}

export function usePortfolioSummary() {
  return useQuery({
    queryKey: ['portfolio', 'summary'],
    queryFn: () => portfolioAPI.getSummary(),
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
  });
}

export function usePortfolioPositions() {
  return useQuery({
    queryKey: ['portfolio', 'positions'],
    queryFn: () => portfolioAPI.getPositions(),
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
  });
}

export function useAIConsensus(symbol: string) {
  return useQuery({
    queryKey: ['ai', 'consensus', symbol],
    queryFn: () => aiAPI.getConsensus(symbol),
    enabled: !!symbol,
    staleTime: 5 * 60 * 1000,
    gcTime: 5 * 60_000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
  });
}

export function useStockNews(symbol: string) {
  return useQuery({
    queryKey: ['stock', symbol, 'news'],
    queryFn: () => stockAPI.getNews(symbol),
    enabled: !!symbol,
    staleTime: 10 * 60 * 1000,
    gcTime: 5 * 60_000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
  });
}

export function useSymbolSearch(query: string) {
  return useQuery({
    queryKey: ['search', query],
    queryFn: () => marketAPI.search(query),
    enabled: query.length >= 1,
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
  });
}
