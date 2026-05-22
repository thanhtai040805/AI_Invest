'use client';

import { useEffect, useState, useCallback, createContext, useContext, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { socketClient, ConnectionStatus } from '@/services/socket';
import { useMarketStore } from '@/stores/useMarketStore';
import { useStockStore } from '@/stores/useStockStore';
import { useLiquidityStore } from '@/stores/useLiquidityStore';
import { useHeatmapStore } from '@/stores/useHeatmapStore';
import {
  mapIndicesResponse,
  mapBreadthResponse,
  mapSnapshotToQuotes,
  mapQuoteResponse,
  mapHeatmapSectors,
} from '@/lib/api-mappers';

/**
 * REALTIME PROVIDER — Single WebSocket Gateway
 *
 * Subscribes to ALL market-wide events (indices, breadth, snapshot, liquidity, heatmap).
 * Individual hooks do NOT subscribe to these events — they read from Zustand stores.
 *
 * Per-stock subscriptions (orderbook, trades, quote) are handled by individual hooks
 * because they're symbol-specific and only needed when the component mounts.
 */

interface RealtimeContextValue {
  status: ConnectionStatus;
  isReconnecting: boolean;
  lastReconnectAt: string | null;
}

const RealtimeContext = createContext<RealtimeContextValue>({
  status: 'disconnected',
  isReconnecting: false,
  lastReconnectAt: null,
});

export const useRealtimeContext = () => useContext(RealtimeContext);

export function RealtimeProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<ConnectionStatus>('disconnected');
  const [isReconnecting, setIsReconnecting] = useState(false);
  const [lastReconnectAt, setLastReconnectAt] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const reconnectTimestampRef = useRef<number>(0);

  const setIndices = useMarketStore((s) => s.setIndices);
  const setBreadth = useMarketStore((s) => s.setBreadth);
  const updateStock = useStockStore((s) => s.updateStock);
  const setStocks = useStockStore((s) => s.setStocks);
  const setLiquidity = useLiquidityStore((s) => s.setLiquidity);
  const setHeatmap = useHeatmapStore((s) => s.setHeatmap);

  const invalidateStaleQueries = useCallback(() => {
    const ts = reconnectTimestampRef.current;
    queryClient.invalidateQueries({
      predicate: (query) => query.state.dataUpdatedAt < ts,
    });
  }, [queryClient]);

  useEffect(() => {
    socketClient.connect();

    const unsubStatus = socketClient.onStatusChange((newStatus) => {
      setStatus(newStatus);
      setIsReconnecting(newStatus === 'connecting');

      if (newStatus === 'connected') {
        reconnectTimestampRef.current = Date.now();
        setLastReconnectAt(new Date().toISOString());
        invalidateStaleQueries();
      }
    });

    const unsubIndices = socketClient.subscribeIndices((data) => {
      if (data && typeof data === 'object' && 'indices' in data) {
        const mapped = mapIndicesResponse(data);
        if (mapped.length > 0) {
          setIndices(mapped);
          queryClient.setQueryData(['market', 'indices'], data);
        }
      }
    });

    const unsubBreadth = socketClient.subscribeBreadth((data) => {
      if (data && typeof data === 'object') {
        const mapped = mapBreadthResponse(data);
        setBreadth(mapped);
        queryClient.setQueryData(['market', 'breadth'], data);
      }
    });

    const unsubSnapshot = socketClient.subscribeSnapshot((data) => {
      if (data && typeof data === 'object' && 'stocks' in data) {
        const mapped = mapSnapshotToQuotes(data);
        if (mapped.length > 0) {
          setStocks(mapped);
          queryClient.setQueryData(['market', 'snapshot'], data);
        }
      }
    });

    const unsubLiquidity = socketClient.subscribeLiquidity((data) => {
      if (data && typeof data === 'object') {
        setLiquidity(data as any);
        queryClient.setQueryData(['market', 'liquidity'], data);
      }
    });

    const unsubHeatmap = socketClient.subscribeHeatmap((data) => {
      if (data && typeof data === 'object' && 'sectors' in data) {
        setHeatmap(mapHeatmapSectors(data as any));
        queryClient.setQueryData(['market', 'heatmap'], data);
      }
    });

    return () => {
      unsubStatus();
      unsubIndices();
      unsubBreadth();
      unsubSnapshot();
      unsubLiquidity();
      unsubHeatmap();
    };
  }, [queryClient, setIndices, setBreadth, setStocks, updateStock, setLiquidity, setHeatmap, invalidateStaleQueries]);

  const handleStockPrice = useCallback(
    (symbol: string, data: unknown) => {
      if (data && typeof data === 'object') {
        const mapped = mapQuoteResponse(data);
        updateStock(symbol.toUpperCase(), mapped);
        queryClient.setQueryData(['stock', symbol.toUpperCase(), 'quote'], data);
      }
    },
    [updateStock, queryClient]
  );

  return (
    <RealtimeContext.Provider value={{ status, isReconnecting, lastReconnectAt }}>
      {children}
    </RealtimeContext.Provider>
  );
}
