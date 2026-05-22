'use client';

import { useEffect } from 'react';
import { socketClient } from '@/services/socket';
import { useWatchlistStore } from '@/stores/useWatchlistStore';

interface SymbolSubscriberProps {
  symbol: string;
}

/**
 * Auto-subscribes to a symbol's WebSocket channels when the component mounts.
 * Also tracks the symbol as last-viewed in the watchlist store.
 *
 * Usage: <SymbolSubscriber symbol="FPT" />
 */
export function SymbolSubscriber({ symbol }: SymbolSubscriberProps) {
  const setLastViewed = useWatchlistStore((s) => s.setLastViewed);

  useEffect(() => {
    if (!symbol) return;
    const sym = symbol.toUpperCase();
    setLastViewed(sym);

    socketClient.connect();
    socketClient.getSocket()?.emit('subscribe:symbol', sym);

    return () => {
      socketClient.getSocket()?.emit('unsubscribe:symbol', sym);
    };
  }, [symbol, setLastViewed]);

  return null;
}
