'use client';

import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import { marketAPI, stockAPI, screenerAPI, portfolioAPI, aiAPI } from '@/services/api';
import { socketClient } from '@/services/socket';
import { useMarketStore } from '@/stores/useMarketStore';
import { useStockStore } from '@/stores/useStockStore';
import {
  mapIndicesResponse,
  mapBreadthResponse,
  mapLiquidityResponse,
  mapHeatmapSectors,
  mapSnapshotToQuotes,
  mapQuoteResponse,
} from '@/lib/api-mappers';

export function useMarketIndices() {
  const setIndices = useMarketStore((s) => s.setIndices);

  const query = useQuery({
    queryKey: ['market', 'indices'],
    queryFn: async () => {
      const data = await marketAPI.getIndices();
      setIndices(mapIndicesResponse(data));
      return data;
    },
    refetchInterval: 30_000,
    staleTime: 2000,
  });

  useEffect(() => {
    return socketClient.subscribeMarket((data) => {
      if (data?.indices) setIndices(mapIndicesResponse(data));
    });
  }, [setIndices]);

  return query;
}

export function useMarketBreadth() {
  const setBreadth = useMarketStore((s) => s.setBreadth);

  const query = useQuery({
    queryKey: ['market', 'breadth'],
    queryFn: async () => {
      const data = await marketAPI.getBreadth();
      setBreadth(mapBreadthResponse(data));
      return data;
    },
    refetchInterval: 15_000,
    staleTime: 5000,
  });

  useEffect(() => {
    return socketClient.subscribeBreadth((data) => setBreadth(mapBreadthResponse(data)));
  }, [setBreadth]);

  return query;
}

export function useMarketLiquidity() {
  const setLiquidity = useMarketStore((s) => s.setLiquidity);

  return useQuery({
    queryKey: ['market', 'liquidity'],
    queryFn: async () => {
      const data = await marketAPI.getLiquidity();
      setLiquidity(mapLiquidityResponse(data));
      return data;
    },
    refetchInterval: 30_000,
    staleTime: 10_000,
  });
}

export function useMarketSnapshot(exchange?: string) {
  const setStocks = useStockStore((s) => s.setStocks);

  const query = useQuery({
    queryKey: ['market', 'snapshot', exchange],
    queryFn: async () => {
      const data = await marketAPI.getSnapshot(exchange);
      setStocks(mapSnapshotToQuotes(data));
      return data;
    },
    refetchInterval: 15_000,
    staleTime: 3000,
  });

  useEffect(() => {
    return socketClient.subscribeSnapshot((data) => {
      if (data?.stocks) setStocks(mapSnapshotToQuotes(data));
    });
  }, [setStocks]);

  return query;
}

export function useMarketHeatmap() {
  const setSectors = useMarketStore((s) => s.setSectors);

  return useQuery({
    queryKey: ['market', 'heatmap'],
    queryFn: async () => {
      const data = await marketAPI.getHeatmap();
      setSectors(mapHeatmapSectors(data));
      return data;
    },
    refetchInterval: 30_000,
    staleTime: 15_000,
  });
}

/** Load all dashboard market data in one call */
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
  });
}

export function useStockQuote(symbol: string) {
  const updateStock = useStockStore((s) => s.updateStock);

  const query = useQuery({
    queryKey: ['stock', symbol, 'quote'],
    queryFn: async () => {
      const data = await stockAPI.getQuote(symbol);
      updateStock(symbol.toUpperCase(), mapQuoteResponse(data));
      return data;
    },
    enabled: !!symbol,
    refetchInterval: 10_000,
    staleTime: 1000,
  });

  useEffect(() => {
    if (!symbol) return;
    return socketClient.subscribeStock(symbol, {
      onPrice: (data) => updateStock(symbol.toUpperCase(), mapQuoteResponse(data)),
    });
  }, [symbol, updateStock]);

  return query;
}

export function useStockOrderBook(symbol: string) {
  return useQuery({
    queryKey: ['stock', symbol, 'orderbook'],
    queryFn: () => stockAPI.getOrderBook(symbol),
    enabled: !!symbol,
    refetchInterval: 5000,
    staleTime: 1000,
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
  }, [symbol]); // eslint-disable-line react-hooks/exhaustive-deps
}

export function useStockFundamentals(symbol: string) {
  return useQuery({
    queryKey: ['stock', symbol, 'fundamentals'],
    queryFn: () => stockAPI.getFundamentals(symbol),
    enabled: !!symbol,
    staleTime: 6 * 60 * 60 * 1000,
  });
}

export function useScreener(filters: Record<string, unknown>, enabled = true) {
  return useQuery({
    queryKey: ['screener', filters],
    queryFn: () => screenerAPI.filter(filters),
    enabled,
    staleTime: 30_000,
  });
}

export function usePortfolioSummary() {
  return useQuery({
    queryKey: ['portfolio', 'summary'],
    queryFn: () => portfolioAPI.getSummary(),
    refetchInterval: 15_000,
  });
}

export function usePortfolioPositions() {
  return useQuery({
    queryKey: ['portfolio', 'positions'],
    queryFn: () => portfolioAPI.getPositions(),
    refetchInterval: 15_000,
  });
}

export function useAIConsensus(symbol: string) {
  return useQuery({
    queryKey: ['ai', 'consensus', symbol],
    queryFn: () => aiAPI.getConsensus(symbol),
    enabled: !!symbol,
    staleTime: 5 * 60 * 1000,
  });
}

export function useSymbolSearch(query: string) {
  return useQuery({
    queryKey: ['search', query],
    queryFn: () => marketAPI.search(query),
    enabled: query.length >= 1,
    staleTime: 10_000,
  });
}

export function useStockNews(symbol: string) {
  return useQuery({
    queryKey: ['stock', symbol, 'news'],
    queryFn: () => stockAPI.getNews(symbol),
    enabled: !!symbol,
    staleTime: 10 * 60 * 1000,
  });
}
