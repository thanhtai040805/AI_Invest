"use client";

import { useState } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { Badge } from "@/components/ui/Badge";
import { usePortfolioStore } from "@/stores/usePortfolioStore";
import { formatCurrency } from "@/lib/market-utils";
import { cn } from "@/lib/utils";
import {
  usePortfolioSummary,
  usePortfolioPositions,
  usePortfolioPerformance,
  usePortfolioRiskMetrics,
} from "@/hooks/usePortfolio";
import {
  PerformanceChart,
  AllocationPie,
  RiskRadar,
  HoldingsTable,
} from "@/components/feature/stock/SimulatorAnalysis";
import Link from "next/link";

function hasAuthToken() {
  return typeof window !== 'undefined' && !!localStorage.getItem('aiinvest_access_token');
}

export default function PortfolioAnalysisPage() {
  const { summary, assets } = usePortfolioStore();
  const [isClient] = useState(() => typeof window !== 'undefined');
  const authed = hasAuthToken();

  usePortfolioSummary(authed);
  usePortfolioPositions(authed);
  const { data: perf } = usePortfolioPerformance(authed);
  const { data: risk } = usePortfolioRiskMetrics(authed);

  // Transform equity curve into PerformanceChart format
  const performanceData = perf?.equityCurve?.length
    ? perf.equityCurve.map((p: { date: string; value: number }, i: number) => ({
        name: new Date(p.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
        portfolio: p.value,
        vnindex: perf.equityCurve[i]?.benchmark ?? undefined,
      }))
    : [];

  // Transform positions into AllocationPie format
  const allocationData = assets
    .filter((a) => a.currentValue > 0)
    .map((a, i) => ({
      name: a.symbol,
      value: a.currentValue,
      color: ['#ADCEFF', '#8884d8', '#82ca9d', '#ffc658', '#f87171', '#a78bfa', '#e8a940'][i % 7],
    }));

  // Compute risk radar values from real risk metrics
  const riskRadarData = [
    { subject: 'Profit', A: risk?.alpha ? Math.min(Math.abs(risk.alpha) * 10, 150) : 0, fullMark: 150 },
    { subject: 'Risk', A: risk?.beta ? Math.max(150 - Math.abs(risk.beta - 1) * 50, 20) : 0, fullMark: 150 },
    { subject: 'Consistency', A: risk?.sharpe ? Math.min(Math.max(risk.sharpe * 20 + 50, 0), 150) : 0, fullMark: 150 },
    { subject: 'Diversity', A: Math.min(assets.length * 25, 150), fullMark: 150 },
    { subject: 'Discipline', A: risk?.maxDrawdown ? Math.max(150 - Math.abs(risk.maxDrawdown) * 3, 20) : 0, fullMark: 150 },
  ];

  // Transform assets into HoldingsTable format
  const holdingsData = assets.map((a) => ({
    symbol: a.symbol,
    quantity: a.quantity,
    avgPrice: a.avgPrice,
    currentPrice: a.currentPrice,
    profitPercent: a.profitPercent,
  }));

  const totalEquity = summary.totalEquity || 1;

  if (!authed) {
    return (
      <div className="flex flex-col min-h-[80dvh] items-center justify-center p-xl text-center">
        <GlassCard className="p-xl border-[#e8a940]/10 shadow-[0_24px_48px_rgba(0,0,0,0.5)]">
          <div className="w-12 h-12 rounded-full bg-[#e8a940]/10 flex items-center justify-center text-[#e8a940] mx-auto mb-lg">
            <span className="material-symbols-outlined text-[24px]">lock</span>
          </div>
          <p className="text-lg font-bold text-[#e8a940] mb-md uppercase tracking-wider">Đăng nhập để xem danh mục</p>
          <p className="text-xs text-on-surface-variant leading-relaxed opacity-70 mb-lg">
            Hệ thống Portfolio yêu cầu xác thực bảo mật.
          </p>
          <Link href="/auth" className="px-xl py-2 bg-primary text-on-primary rounded-xl font-bold hover:bg-primary/80 transition-all">Đăng nhập</Link>
        </GlassCard>
      </div>
    );
  }

  return (
    <div className="pb-xl space-y-lg px-xl pt-lg">
      {/* Header */}
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-md border-b border-white/5 pb-lg">
        <div className="flex items-center gap-md">
          <div className="w-10 h-10 rounded-xl bg-[#e8a940]/10 flex items-center justify-center text-[#e8a940] border border-[#e8a940]/20">
            <span className="material-symbols-outlined text-[20px]">donut_large</span>
          </div>
          <div>
            <h1 className="text-2xl font-black text-[#e8a940] tracking-tighter uppercase leading-none">Portfolio Intelligence</h1>
            <p className="text-xs text-on-surface-variant mt-1">Phân tích danh mục, quản trị rủi ro & hiệu suất tài sản trực quan.</p>
          </div>
        </div>
        <div className="flex items-center gap-lg bg-white/4 border border-white/5 p-3 rounded-xl">
          <div className="text-right">
            <p className="text-[9px] font-bold opacity-40 uppercase tracking-wider">Tài sản ròng (NAV)</p>
            <p className="text-lg font-black font-data-mono tracking-tight">{formatCurrency(summary.totalEquity, 'VND')}</p>
          </div>
          <div className="h-8 w-[1px] bg-white/10" />
          <div className="text-right">
            <p className="text-[9px] font-bold opacity-40 uppercase tracking-wider">Lợi nhuận ròng</p>
            <p className={cn("text-lg font-black font-data-mono tracking-tight", summary.totalProfit >= 0 ? "text-secondary" : "text-error")}>
              {summary.totalProfit >= 0 ? '+' : ''}{formatCurrency(summary.totalProfit, 'VND')} ({summary.totalProfitPercent.toFixed(2)}%)
            </p>
          </div>
        </div>
      </div>

      {/* Stat Cards (simulator style) */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-lg">
        {[
          { label: 'NET WORTH', value: formatCurrency(summary.totalEquity, 'VND'), sub: 'VND', trend: summary.totalProfitPercent >= 0 ? `+${summary.totalProfitPercent.toFixed(1)}%` : `${summary.totalProfitPercent.toFixed(1)}%`, color: 'text-primary' },
          { label: 'TOTAL PROFIT', value: `${summary.totalProfit >= 0 ? '+' : ''}${formatCurrency(summary.totalProfit, 'VND')}`, sub: 'VND', trend: summary.totalProfitPercent >= 0 ? `+${summary.totalProfitPercent.toFixed(1)}%` : `${summary.totalProfitPercent.toFixed(1)}%`, color: summary.totalProfit >= 0 ? 'text-secondary' : 'text-error' },
          { label: 'DAILY CHANGE', value: `${summary.dailyPnL >= 0 ? '+' : ''}${formatCurrency(summary.dailyPnL, 'VND')}`, sub: 'VND', trend: summary.dailyPnLPercent >= 0 ? `+${summary.dailyPnLPercent.toFixed(1)}%` : `${summary.dailyPnLPercent.toFixed(1)}%`, color: summary.dailyPnL >= 0 ? 'text-secondary' : 'text-error' },
          { label: 'BUYING POWER', value: formatCurrency(summary.buyingPower, 'VND'), sub: 'VND', trend: 'Available', color: 'text-on-surface' },
        ].map((stat, i) => (
          <GlassCard key={i} className="group relative overflow-hidden">
            <div className="absolute top-0 right-0 p-lg opacity-10 group-hover:opacity-20 transition-opacity">
              <span className="material-symbols-outlined text-[48px]">monitoring</span>
            </div>
            <p className="text-[10px] font-black opacity-40 uppercase tracking-[0.2em] mb-sm">{stat.label}</p>
            <div className="flex items-baseline gap-xs">
              <span className={cn("text-2xl font-black tracking-tight", stat.color)}>{stat.value}</span>
              <span className="text-[10px] opacity-40 font-bold">{stat.sub}</span>
            </div>
            <div className="mt-md flex items-center gap-sm">
              <span className={cn("text-[10px] font-bold px-2 py-0.5 rounded-full", stat.trend.startsWith('+') ? "bg-secondary/10 text-secondary" : "bg-white/10 opacity-60")}>
                {stat.trend}
              </span>
              <span className="text-[9px] opacity-40 uppercase">vs Prev Close</span>
            </div>
          </GlassCard>
        ))}
      </div>

      {/* Performance Chart & Risk Radar */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-lg">
        <GlassCard className="lg:col-span-8 p-xl">
          <div className="flex items-center justify-between mb-lg">
            <div>
              <h3 className="font-title-lg">Growth Performance</h3>
              <p className="text-[11px] text-on-surface-variant">Đường cong tài sản theo thời gian</p>
            </div>
            <div className="flex gap-md">
              <div className="flex items-center gap-xs text-[9px] text-primary font-bold">
                <div className="w-2 h-2 rounded-full bg-primary" /> PORTFOLIO
              </div>
              <div className="flex items-center gap-xs text-[9px] opacity-40 font-bold">
                <div className="w-2 h-2 rounded-full bg-white/40" /> BENCHMARK
              </div>
            </div>
          </div>
          {isClient && <PerformanceChart data={performanceData} />}
        </GlassCard>

        <div className="lg:col-span-4 flex flex-col gap-lg">
          <GlassCard className="flex-1 p-xl">
            <h3 className="font-title-md mb-md">Risk Radar</h3>
            <p className="text-[11px] text-on-surface-variant mb-lg">Đánh giá hồ sơ nhà đầu tư</p>
            {isClient && <RiskRadar data={riskRadarData} />}
          </GlassCard>
        </div>
      </div>

      {/* Allocation & Holdings */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-lg">
        <GlassCard className="lg:col-span-4 p-xl">
          <h3 className="font-title-md mb-lg">Asset Allocation</h3>
          {isClient && allocationData.length > 0 ? (
            <>
              <AllocationPie data={allocationData} />
              <div className="mt-lg space-y-sm">
                {allocationData.map((a) => (
                  <div key={a.name} className="flex justify-between items-center text-[11px]">
                    <span className="opacity-60">{a.name}</span>
                    <span className="font-bold">{((a.value / (summary.totalEquity || 1)) * 100).toFixed(1)}%</span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="h-[250px] flex items-center justify-center text-center opacity-40 text-xs italic">
              Chưa có phân bổ vị thế
            </div>
          )}
        </GlassCard>

        <GlassCard className="lg:col-span-8 p-xl overflow-hidden">
          <div className="flex justify-between items-center mb-xl">
            <div>
              <h3 className="font-title-md">Holdings Details</h3>
              <p className="text-[11px] text-on-surface-variant">Chi tiết lãi lỗ từng vị thế đang nắm giữ</p>
            </div>
            <Badge variant="outline">{assets.length} VỊ THẾ</Badge>
          </div>
          {isClient && assets.length > 0 ? (
            <HoldingsTable data={holdingsData} />
          ) : (
            <div className="py-xl text-center">
              <p className="text-sm opacity-45 italic">Chưa có vị thế trong tài khoản.</p>
            </div>
          )}
        </GlassCard>
      </div>
    </div>
  );
}
