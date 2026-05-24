'use client';

import { useState, useEffect, useCallback } from 'react';

export type MarketSessionState =
  | 'pre_open'
  | 'opening_auction'
  | 'continuous_morning'
  | 'lunch_break'
  | 'continuous_afternoon'
  | 'closing_auction'
  | 'closed';

interface MarketSessionInfo {
  state: MarketSessionState;
  isOpen: boolean;
  isPreMarket: boolean;
  isLunchBreak: boolean;
  nextEvent: string;
  nextEventTime: string;
  timeUntilNextEvent: string;
}

const SCHEDULE = {
  pre_open: { hour: 8, minute: 30, label: 'Pre-Open' },
  opening_auction: { hour: 9, minute: 0, label: 'Opening Auction' },
  continuous_morning: { hour: 9, minute: 15, label: 'Continuous Morning' },
  lunch_break: { hour: 11, minute: 30, label: 'Lunch Break' },
  continuous_afternoon: { hour: 13, minute: 0, label: 'Continuous Afternoon' },
  closing_auction: { hour: 14, minute: 30, label: 'Closing Auction' },
  closed: { hour: 14, minute: 45, label: 'Market Closed' },
};

const TRANSITIONS: Array<{ state: MarketSessionState; hour: number; minute: number }> = [
  { state: 'pre_open', hour: 8, minute: 30 },
  { state: 'opening_auction', hour: 9, minute: 0 },
  { state: 'continuous_morning', hour: 9, minute: 15 },
  { state: 'lunch_break', hour: 11, minute: 30 },
  { state: 'continuous_afternoon', hour: 13, minute: 0 },
  { state: 'closing_auction', hour: 14, minute: 30 },
  { state: 'closed', hour: 14, minute: 45 },
];

function getCurrentState(): MarketSessionState {
  const now = new Date();
  const day = now.getDay();
  if (day === 0 || day === 6) return 'closed';

  const minutes = now.getHours() * 60 + now.getMinutes();

  for (let i = TRANSITIONS.length - 1; i >= 0; i--) {
    const t = TRANSITIONS[i];
    const tMinutes = t.hour * 60 + t.minute;
    if (minutes >= tMinutes) return t.state;
  }
  return 'closed';
}

function getNextEvent(): { state: MarketSessionState; label: string; eta: string; time: string } {
  const now = new Date();
  const currentMinutes = now.getHours() * 60 + now.getMinutes();

  for (const t of TRANSITIONS) {
    const tMinutes = t.hour * 60 + t.minute;
    if (tMinutes > currentMinutes) {
      const diff = tMinutes - currentMinutes;
      const hours = Math.floor(diff / 60);
      const mins = diff % 60;
      const eta = hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;
      return {
        state: t.state,
        label: SCHEDULE[t.state].label,
        eta,
        time: `${String(t.hour).padStart(2, '0')}:${String(t.minute).padStart(2, '0')}`,
      };
    }
  }

  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  while (tomorrow.getDay() === 0 || tomorrow.getDay() === 6) {
    tomorrow.setDate(tomorrow.getDate() + 1);
  }
  const first = TRANSITIONS[0];
  return {
    state: first.state,
    label: `${SCHEDULE[first.state].label} (${tomorrow.toLocaleDateString('vi-VN')})`,
    eta: 'next session',
    time: `${String(first.hour).padStart(2, '0')}:${String(first.minute).padStart(2, '0')}`,
  };
}

export function useMarketSession(): MarketSessionInfo {
  const [state, setState] = useState<MarketSessionState>(getCurrentState);

  const update = useCallback(() => {
    setState(getCurrentState());
  }, []);

  useEffect(() => {
    const interval = setInterval(update, 30_000);
    return () => clearInterval(interval);
  }, [update]);

  const next = getNextEvent();
  const isOpen = ['opening_auction', 'continuous_morning', 'continuous_afternoon', 'closing_auction'].includes(state);
  const isPreMarket = state === 'pre_open';
  const isLunchBreak = state === 'lunch_break';

  return {
    state,
    isOpen,
    isPreMarket,
    isLunchBreak,
    nextEvent: next.label,
    nextEventTime: next.time,
    timeUntilNextEvent: next.eta,
  };
}
