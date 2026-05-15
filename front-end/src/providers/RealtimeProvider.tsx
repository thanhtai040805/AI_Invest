'use client';

import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { socketClient } from '@/services/socket';
import { useMarketStore } from '@/stores/useMarketStore';
import { useStockStore } from '@/stores/useStockStore';
import {
  mapIndicesResponse,
  mapBreadthResponse,
  mapSnapshotToQuotes,
} from '@/lib/api-mappers';

export function RealtimeProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const setIndices = useMarketStore((s) => s.setIndices);
  const setBreadth = useMarketStore((s) => s.setBreadth);
  const setStocks = useStockStore((s) => s.setStocks);

  useEffect(() => {
    socketClient.connect();

    const unsubMarket = socketClient.subscribeMarket((data) => {
      if (data?.indices) {
        setIndices(mapIndicesResponse(data));
        queryClient.setQueryData(['market', 'indices'], data);
      }
    });

    const unsubBreadth = socketClient.subscribeBreadth((data) => {
      setBreadth(mapBreadthResponse(data));
      queryClient.setQueryData(['market', 'breadth'], data);
    });

    const unsubSnapshot = socketClient.subscribeSnapshot((data) => {
      if (data?.stocks) {
        setStocks(mapSnapshotToQuotes(data));
        queryClient.setQueryData(['market', 'snapshot'], data);
      }
    });

    return () => {
      unsubMarket();
      unsubBreadth();
      unsubSnapshot();
    };
  }, [queryClient, setIndices, setBreadth, setStocks]);

  return <>{children}</>;
}
