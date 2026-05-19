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
import Link from "next/link";

// Curated premium financial palette matching warm zinc / amber theme
const CHROME_PALETTE = [
  "#e8a940", // Amber / Gold
  "#2dbd7e", // Emerald / Gain
  "#7bbcee", // Slate-blue / Info
  "#f87171", // Rose / Loss
  "#a78bfa", // Lavendar / Alt
];

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
      <div className="flex flex-col min-h-[80dvh] items-center justify-center p-xl text-center">
        <GlassCard className="p-xl border-[#e8a940]/10 shadow-[0_24px_48px_rgba(0,0,0,0.5)]">
          <div className="w-12 h-12 rounded-full bg-[#e8a940]/10 flex items-center justify-center text-[#e8a940] mx-auto mb-lg">
            <span className="material-symbols-outlined text-[24px]">lock</span>
          </div>
          <p className="text-lg font-bold text-[#e8a940] mb-md uppercase tracking-wider">Đăng nhập để xem danh mục</p>
          <p className="text-xs text-on-surface-variant leading-relaxed opacity-70 mb-lg">
            Hệ thống Portfolio mô phỏng yêu cầu xác thực bảo mật.
          </p>
          <Link href="/auth" className="px-xl py-2 bg-primary text-on-primary rounded-xl font-bold hover:bg-primary/80 transition-all">Đăng nhập</Link>
        </GlassCard>
      </div>
    );
  }

  return (
    <div className="pb-xl space-y-lg px-xl pt-lg">
      {/* Header Dashboard section */}
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

      {/* Equity & Allocation asymmetric grids */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-lg">
        <div className="lg:col-span-8">
          <GlassCard className="p-xl h-[420px] flex flex-col border-white/5">
            <div className="flex justify-between items-center mb-xl">
              <div className="flex items-center gap-xs">
                <span className="material-symbols-outlined text-[#e8a940] text-sm">show_chart</span>
                <h3 className="text-[10px] font-black opacity-45 uppercase tracking-widest">Đường cong tài sản (Equity Curve)</h3>
              </div>
              <Badge variant="outline">1D INTERVAL</Badge>
            </div>
            <div className="flex-1 w-full min-h-0 text-[10px] font-data-mono">
              {isClient && (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={performanceData}>
                    <defs>
                      <linearGradient id="colorValue" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#e8a940" stopOpacity={0.2} />
                        <stop offset="95%" stopColor="#e8a940" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="rgba(255,255,255,0.02)" />
                    <XAxis dataKey="date" stroke="rgba(255,255,255,0.3)" />
                    <YAxis
                      stroke="rgba(255,255,255,0.3)"
                      tickFormatter={(v) => (v / 1_000_000).toLocaleString() + "M"}
                    />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: '#111112',
                        border: '1px solid rgba(255,255,255,0.08)',
                        borderRadius: '12px',
                        fontFamily: 'JetBrains Mono',
                        fontSize: '11px'
                      }}
                      labelClassName="text-on-surface-variant"
                      itemStyle={{ color: '#e8a940' }}
                      formatter={(value: number) => [formatCurrency(value, 'VND'), 'Tài sản']}
                    />
                    <Area type="monotone" dataKey="value" stroke="#e8a940" strokeWidth={2} fill="url(#colorValue)" />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </div>
          </GlassCard>
        </div>

        <div className="lg:col-span-4">
          <GlassCard className="p-xl h-[420px] flex flex-col border-white/5">
            <div className="flex items-center gap-xs mb-xl">
              <span className="material-symbols-outlined text-[#e8a940] text-sm">pie_chart</span>
              <h3 className="text-[10px] font-black opacity-45 uppercase tracking-widest">Phân bổ tài sản (Allocation)</h3>
            </div>
            <div className="flex-1 relative min-h-0">
              {isClient && allocationData.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={allocationData} innerRadius={70} outerRadius={105} paddingAngle={4} dataKey="value">
                      {allocationData.map((_, index) => (
                        <Cell key={index} fill={CHROME_PALETTE[index % CHROME_PALETTE.length]} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{
                        backgroundColor: '#111112',
                        border: '1px solid rgba(255,255,255,0.08)',
                        borderRadius: '12px',
                        fontFamily: 'JetBrains Mono',
                        fontSize: '11px'
                      }}
                    />
                  </PieChart>
                </ResponsiveContainer>
              ) : (
                <div className="h-full flex items-center justify-center text-center opacity-40 text-xs italic">
                  Chưa có phân bổ vị thế
                </div>
              )}
              <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                <span className="text-[9px] font-bold opacity-45 uppercase tracking-wider">Cổ phiếu</span>
                <span className="text-2xl font-black font-data-mono text-on-surface">{assets.length}</span>
              </div>
            </div>
          </GlassCard>
        </div>
      </div>

      {/* Risk Metrics */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-lg">
        {[
          { label: 'Sharpe Ratio', value: risk?.sharpe, suffix: '', desc: 'Lợi nhuận điều chỉnh rủi ro' },
          { label: 'Max Drawdown', value: risk?.maxDrawdown, suffix: '%', desc: 'Mức sụt giảm tối đa từ đỉnh' },
          { label: 'Alpha (Niên độ)', value: risk?.alpha, suffix: '%', desc: 'Hiệu suất vượt trội thị trường' },
          { label: 'Beta Hệ số', value: risk?.beta, suffix: '', desc: 'Độ nhạy biến động thị trường' },
        ].map((m) => (
          <GlassCard key={m.label} className="p-xl border-white/5 transition-all hover:-translate-y-1 duration-300">
            <p className="text-[9px] font-black opacity-45 uppercase tracking-widest mb-2">{m.label}</p>
            <p className="text-3xl font-black font-data-mono text-[#e8a940]">
              {m.value != null ? `${m.value}${m.suffix}` : '—'}
            </p>
            <p className="text-[10px] opacity-50 mt-2 font-medium">{m.desc}</p>
          </GlassCard>
        ))}
      </div>

      {/* Positions Table */}
      <GlassCard className="p-0 border-white/5 overflow-hidden shadow-2xl">
        <div className="p-xl border-b border-white/5 flex items-center justify-between">
          <div className="flex items-center gap-xs">
            <span className="material-symbols-outlined text-[#e8a940] text-sm">toc</span>
            <h3 className="text-[10px] font-black opacity-45 uppercase tracking-widest">Danh mục Vị thế hiện tại</h3>
          </div>
          <Badge variant="outline" dot>{assets.length} VỊ THẾ</Badge>
        </div>
        {assets.length === 0 ? (
          <div className="p-xl text-center">
            <p className="text-sm opacity-45 italic">Chưa có vị thế trong tài khoản. Hãy đặt lệnh mô phỏng từ trang Chi tiết cổ phiếu.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse min-w-[1000px] font-data-mono">
              <thead>
                <tr className="text-[10px] font-black opacity-45 uppercase border-b border-white/5 bg-white/[0.01]">
                  <th className="py-4 px-xl">Mã</th>
                  <th className="py-4 px-xl text-right">Giá vốn</th>
                  <th className="py-4 px-xl text-right">Giá hiện tại</th>
                  <th className="py-4 px-xl text-right">Khối lượng</th>
                  <th className="py-4 px-xl text-right">Giá trị vị thế</th>
                  <th className="py-4 px-xl text-right">Lợi nhuận (P&L)</th>
                  <th className="py-4 px-xl text-right">Tỷ trọng</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.02]">
                {assets.map((a) => (
                  <tr key={a.symbol} className="border-b border-white/[0.01] hover:bg-white/[0.02] transition-colors">
                    <td className="py-4 px-xl">
                      <span className="font-black text-[#e8a940] text-sm">{a.symbol}</span>
                    </td>
                    <td className="py-4 px-xl text-right text-xs opacity-80">{a.avgPrice.toLocaleString()}</td>
                    <td className="py-4 px-xl text-right text-xs">{a.currentPrice.toLocaleString()}</td>
                    <td className="py-4 px-xl text-right text-xs">{a.quantity.toLocaleString()}</td>
                    <td className="py-4 px-xl text-right text-xs font-semibold">{formatCurrency(a.currentValue, 'VND')}</td>
                    <td className={cn("py-4 px-xl text-right text-xs font-black", a.profit >= 0 ? "text-secondary" : "text-error")}>
                      {a.profitPercent >= 0 ? '+' : ''}{a.profitPercent.toFixed(2)}%
                    </td>
                    <td className="py-4 px-xl text-right text-xs opacity-50">
                      {((a.currentValue / totalEquity) * 100).toFixed(1)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </GlassCard>
    </div>
  );
}
