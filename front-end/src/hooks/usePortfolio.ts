'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { portfolioAPI } from '@/services/api';
import { usePortfolioStore } from '@/stores/usePortfolioStore';
import { useEffect } from 'react';

export function usePortfolioSummary(enabled = true) {
  const setSummary = usePortfolioStore((s) => s.setSummary);

  const query = useQuery({
    queryKey: ['portfolio', 'summary'],
    queryFn: () => portfolioAPI.getSummary(),
    enabled,
    refetchInterval: 15_000,
    retry: false,
  });

  useEffect(() => {
    if (query.data) {
      setSummary({
        totalEquity: query.data.nav ?? query.data.totalEquity ?? 0,
        totalProfit: query.data.pnl ?? query.data.totalProfit ?? 0,
        totalProfitPercent: query.data.pnlPercent ?? query.data.totalProfitPercent ?? 0,
        dailyPnL: query.data.dailyPnL ?? query.data.pnl ?? 0,
        dailyPnLPercent: query.data.dailyPnLPercent ?? query.data.pnlPercent ?? 0,
        buyingPower: query.data.buyingPower ?? query.data.cash ?? 0,
        assetsCount: query.data.positionCount ?? query.data.assetsCount ?? 0,
        holdings: query.data.holdings ?? [],
      });
    }
  }, [query.data, setSummary]);

  return query;
}

export function usePortfolioPositions(enabled = true) {
  const setAssets = usePortfolioStore((s) => s.setAssets);

  const query = useQuery({
    queryKey: ['portfolio', 'positions'],
    queryFn: () => portfolioAPI.getPositions(),
    enabled,
    refetchInterval: 15_000,
    retry: false,
  });

  useEffect(() => {
    if (query.data) {
      setAssets(
        query.data.map((p: Record<string, number | string>) => ({
          symbol: String(p.symbol),
          quantity: Number(p.quantity),
          avgPrice: Number(p.avgPrice),
          currentPrice: Number(p.currentPrice),
          currentValue: Number(p.marketValue),
          profit: Number(p.pnl),
          profitPercent: Number(p.pnlPercent),
        })),
      );
    }
  }, [query.data, setAssets]);

  return query;
}

export function usePortfolioPerformance(enabled = true) {
  return useQuery({
    queryKey: ['portfolio', 'performance'],
    queryFn: () => portfolioAPI.getPerformance(),
    enabled,
    retry: false,
  });
}

export function usePortfolioRiskMetrics(enabled = true) {
  return useQuery({
    queryKey: ['portfolio', 'risk-metrics'],
    queryFn: () => portfolioAPI.getRiskMetrics(),
    enabled,
    retry: false,
  });
}

export function usePlaceOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: portfolioAPI.placeOrder,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['portfolio'] });
    },
  });
}
