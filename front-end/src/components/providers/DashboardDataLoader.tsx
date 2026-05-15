'use client';

import { useDashboardMarketData } from '@/hooks/useMarketData';

/** Mounts TanStack Query fetches + Socket.IO subscriptions for the dashboard */
export function DashboardDataLoader({ children }: { children: React.ReactNode }) {
  const { isError } = useDashboardMarketData();

  if (isError) {
    return (
      <div className="p-lg text-error text-sm">
        Không thể tải dữ liệu thị trường. Kiểm tra backend (port 3001) và AI engine (port 8000).
      </div>
    );
  }

  return <>{children}</>;
}
