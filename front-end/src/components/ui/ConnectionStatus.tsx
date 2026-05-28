'use client';

import { useState } from 'react';
import { useRealtimeContext } from '@/providers/RealtimeProvider';
import { useMarketSession } from '@/hooks/useMarketSession';
import { cn } from '@/lib/utils';

export function ConnectionStatus() {
  const [dismissed, setDismissed] = useState(false);
  const { status, isReconnecting, lastReconnectAt } = useRealtimeContext();
  const { isOpen, state, nextEvent, timeUntilNextEvent } = useMarketSession();

  if ((status === 'connected' && isOpen) || dismissed) {
    return null;
  }

  const getStatusInfo = () => {
    if (!isOpen) {
      return {
        color: 'bg-blue-500/80',
        text: `Market ${state.replace('_', ' ')} — ${nextEvent} in ${timeUntilNextEvent}`,
        show: true,
      };
    }

    switch (status) {
      case 'connecting':
        return {
          color: 'bg-yellow-500/80',
          text: 'Connecting to real-time data...',
          show: true,
        };
      case 'disconnected':
        return {
          color: 'bg-red-500/80',
          text: lastReconnectAt
            ? `Disconnected since ${new Date(lastReconnectAt).toLocaleTimeString()}. Using cached data.`
            : 'Real-time disconnected. Using cached data.',
          show: true,
        };
      case 'error':
        return {
          color: 'bg-red-600/80',
          text: 'Connection error. Some features may be unavailable.',
          show: true,
        };
      default:
        return { color: '', text: '', show: false };
    }
  };

  const info = getStatusInfo();
  if (!info.show) return null;

  return (
    <div
      className={cn(
        'fixed bottom-4 right-4 z-50 flex items-center gap-2 rounded-lg px-4 py-2 text-xs text-white shadow-lg backdrop-blur-sm transition-all duration-300',
        info.color
      )}
    >
      <span className="relative flex h-2 w-2">
        {isReconnecting && (
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-white opacity-75" />
        )}
        <span className={cn(
          'relative inline-flex h-2 w-2 rounded-full bg-white',
          status === 'error' ? 'opacity-50' : ''
        )} />
      </span>
      <span>{info.text}</span>
      <button
        onClick={() => setDismissed(true)}
        className="ml-2 inline-flex items-center justify-center rounded-md px-1.5 py-0.5 text-[11px] font-medium text-white/70 hover:bg-white/20 hover:text-white transition-colors"
      >
        ✕
      </button>
    </div>
  );
}
