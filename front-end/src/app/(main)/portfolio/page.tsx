"use client";

import { useState, useEffect } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { Badge } from "@/components/ui/Badge";
import { usePortfolioStore } from "@/stores/usePortfolioStore";
import { formatCurrency } from "@/lib/market-utils";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell,
} from "recharts";
import { cn } from "@/lib/utils";
import {
  usePortfolioSummary,
  usePortfolioPositions,
  usePortfolioPerformance,
  usePortfolioRiskMetrics,
} from "@/hooks/usePortfolio";

const COLORS = ['#adff2f', '#00e5ff', '#ff4d4d', '#ffab00', '#7c4dff'];

function hasAuthToken() {
  return typeof window !== 'undefined' && !!localStorage.getItem('aiinvest_access_token');
}

export default function PortfolioAnalysisPage() {
  const { summary, assets } = usePortfolioStore();
  const [isClient, setIsClient] = useState(false);
  const authed = hasAuthToken();

  usePortfolioSummary(authed);
  usePortfolioPositions(authed);
  const { data: perf } = usePortfolioPerformance(authed);
  const { data: risk } = usePortfolioRiskMetrics(authed);

  useEffect(() => setIsClient(true), []);

  const performanceData = perf?.equityCurve?.length
    ? perf.equityCurve.map((p: { date: string; value: number }) => ({ date: p.date, value: p.value }))
    : [{ date: new Date().toISOString().slice(0, 10), value: summary.totalEquity || 0 }];

  const allocationData = assets
    .filter((a) => a.currentValue > 0)
    .map((a) => ({ name: a.symbol, value: a.currentValue }));

  const totalEquity = summary.totalEquity || 1;

  if (!authed) {
    return (
      <div className="flex flex-col h-screen items-center justify-center bg-[#050505] p-xl text-center">
        <p className="text-lg font-bold text-primary mb-md">Đăng nhập để xem danh mục</p>
        <p className="text-sm opacity-50 max-w-md">
          Portfolio mô phỏng yêu cầu JWT. Đăng ký/đăng nhập qua API{' '}
          <code className="text-secondary">/api/v1/auth</code> và lưu Access token vào{' '}
          <code className="text-secondary">localStorage.aiinvest_access_token</code>.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-[#050505]">
      <div className="h-16 border-b border-white/5 flex items-center justify-between px-xl bg-[#0a0a0a]">
        <div className="flex items-center gap-xl">
          <h1 className="text-xl font-black text-primary tracking-tighter uppercase">Portfolio Intelligence</h1>
          <Badge variant="secondary" className="bg-secondary/10 text-secondary">SIMULATED</Badge>
        </div>
        <div className="flex items-center gap-lg">
          <div className="text-right">
            <p className="text-[10px] font-bold opacity-40 uppercase">Total Equity</p>
            <p className="text-lg font-black font-data-mono">{formatCurrency(summary.totalEquity, 'VND')}</p>
          </div>
          <div className="h-8 w-[1px] bg-white/10" />
          <div className="text-right">
            <p className="text-[10px] font-bold opacity-40 uppercase">Total P&L</p>
            <p className={cn("text-lg font-black font-data-mono", summary.totalProfit >= 0 ? "text-secondary" : "text-error")}>
              {summary.totalProfit >= 0 ? '+' : ''}{formatCurrency(summary.totalProfit, 'VND')} ({summary.totalProfitPercent.toFixed(2)}%)
            </p>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto no-scrollbar p-xl space-y-xl">
        <div className="grid grid-cols-12 gap-xl">
          <div className="col-span-8">
            <GlassCard className="p-xl h-[400px] flex flex-col bg-[#0a0a0a]">
              <h3 className="text-[10px] font-black opacity-30 uppercase tracking-widest mb-xl">Equity Curve</h3>
              <div className="flex-1">
                {isClient && (
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={performanceData}>
                      <defs>
                        <linearGradient id="colorValue" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="var(--primary)" stopOpacity={0.3} />
                          <stop offset="95%" stopColor="var(--primary)" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="rgba(255,255,255,0.03)" />
                      <XAxis dataKey="date" hide />
                      <YAxis hide />
                      <Tooltip
                        contentStyle={{ backgroundColor: '#0a0a0a', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '12px' }}
                        formatter={(value: number) => [formatCurrency(value, 'VND'), 'Equity']}
                      />
                      <Area type="monotone" dataKey="value" stroke="var(--primary)" strokeWidth={3} fill="url(#colorValue)" />
                    </AreaChart>
                  </ResponsiveContainer>
                )}
              </div>
            </GlassCard>
          </div>
          <div className="col-span-4">
            <GlassCard className="p-xl h-[400px] flex flex-col bg-[#0a0a0a]">
              <h3 className="text-[10px] font-black opacity-30 uppercase tracking-widest mb-xl">Asset Allocation</h3>
              <div className="flex-1 relative">
                {isClient && allocationData.length > 0 && (
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={allocationData} innerRadius={60} outerRadius={100} paddingAngle={5} dataKey="value">
                        {allocationData.map((_, index) => (
                          <Cell key={index} fill={COLORS[index % COLORS.length]} />
                        ))}
                      </Pie>
                      <Tooltip />
                    </PieChart>
                  </ResponsiveContainer>
                )}
                <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                  <span className="text-[10px] font-bold opacity-40 uppercase">Assets</span>
                  <span className="text-lg font-black">{assets.length}</span>
                </div>
              </div>
            </GlassCard>
          </div>
        </div>

        <div className="grid grid-cols-4 gap-xl">
          {[
            { label: 'Sharpe Ratio', value: risk?.sharpe, suffix: '', desc: 'Risk-adjusted return' },
            { label: 'Max Drawdown', value: risk?.maxDrawdown, suffix: '%', desc: 'Peak to trough' },
            { label: 'Alpha (ann.)', value: risk?.alpha, suffix: '%', desc: 'Excess return' },
            { label: 'Beta', value: risk?.beta, suffix: '', desc: 'Market sensitivity' },
          ].map((m) => (
            <GlassCard key={m.label} className="p-xl bg-[#0a0a0a]">
              <p className="text-[9px] font-black opacity-30 uppercase tracking-widest mb-2">{m.label}</p>
              <p className="text-2xl font-black font-data-mono text-primary">
                {m.value != null ? `${m.value}${m.suffix}` : '—'}
              </p>
              <p className="text-[10px] opacity-40 mt-1">{m.desc}</p>
            </GlassCard>
          ))}
        </div>

        <GlassCard className="p-0 border-white/5 overflow-hidden bg-[#0a0a0a]">
          <div className="p-xl border-b border-white/5">
            <h3 className="text-[10px] font-black opacity-30 uppercase tracking-widest">Holdings</h3>
          </div>
          {assets.length === 0 ? (
            <p className="p-xl text-sm opacity-40 text-center">Chưa có vị thế. Đặt lệnh mô phỏng từ trang cổ phiếu.</p>
          ) : (
            <table className="w-full text-left font-data-mono">
              <thead>
                <tr className="text-[9px] font-black opacity-30 uppercase border-b border-white/5">
                  <th className="py-4 px-xl">Symbol</th>
                  <th className="py-4 px-xl text-right">Avg</th>
                  <th className="py-4 px-xl text-right">Market</th>
                  <th className="py-4 px-xl text-right">Qty</th>
                  <th className="py-4 px-xl text-right">Value</th>
                  <th className="py-4 px-xl text-right">P&L %</th>
                  <th className="py-4 px-xl text-right">Weight</th>
                </tr>
              </thead>
              <tbody>
                {assets.map((a) => (
                  <tr key={a.symbol} className="border-b border-white/[0.02] hover:bg-white/[0.02]">
                    <td className="py-4 px-xl font-black text-primary">{a.symbol}</td>
                    <td className="py-4 px-xl text-right text-xs opacity-60">{a.avgPrice.toLocaleString()}</td>
                    <td className="py-4 px-xl text-right text-xs">{a.currentPrice.toLocaleString()}</td>
                    <td className="py-4 px-xl text-right text-xs">{a.quantity.toLocaleString()}</td>
                    <td className="py-4 px-xl text-right text-xs">{formatCurrency(a.currentValue, 'VND')}</td>
                    <td className={cn("py-4 px-xl text-right text-xs font-black", a.profit >= 0 ? "text-secondary" : "text-error")}>
                      {a.profitPercent.toFixed(2)}%
                    </td>
                    <td className="py-4 px-xl text-right text-xs opacity-40">
                      {((a.currentValue / totalEquity) * 100).toFixed(1)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </GlassCard>
      </div>
    </div>
  );
}
