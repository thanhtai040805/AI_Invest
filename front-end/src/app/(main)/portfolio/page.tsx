"use client";

import { motion } from "framer-motion";
import { GlassCard } from "@/components/ui/GlassCard";
import { Badge } from "@/components/ui/Badge";
import { usePortfolioStore } from "@/stores/usePortfolioStore";
import { formatCurrency } from "@/lib/market-utils";
import { useState, useEffect } from "react";
import {
   AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
   PieChart, Pie, Cell
} from "recharts";
import { cn } from "@/lib/utils";


const COLORS = ['#adff2f', '#00e5ff', '#ff4d4d', '#ffab00', '#7c4dff'];

export default function PortfolioAnalysisPage() {
   const { summary, assets } = usePortfolioStore();
   const [isClient, setIsClient] = useState(false);

   useEffect(() => {
      setIsClient(true);
   }, []);

   // Mock performance data (Equity Curve)
   const performanceData = [
      { date: '2026-04-01', value: 1000000000 },
      { date: '2026-04-05', value: 1020000000 },
      { date: '2026-04-10', value: 1015000000 },
      { date: '2026-04-15', value: 1050000000 },
      { date: '2026-04-20', value: 1080000000 },
      { date: '2026-04-25', value: 1065000000 },
      { date: '2026-05-01', value: 1120000000 },
      { date: '2026-05-10', value: 1245000000 },
   ];

   const allocationData = assets.map(a => ({
      name: a.symbol,
      value: a.currentValue
   }));

   return (
      <div className="flex flex-col h-screen overflow-hidden bg-[#050505]">
         {/* HEADER */}
         <div className="h-16 border-b border-white/5 flex items-center justify-between px-xl bg-[#0a0a0a]">
            <div className="flex items-center gap-xl">
               <h1 className="text-xl font-black text-primary tracking-tighter uppercase">Portfolio Intelligence</h1>
               <div className="flex gap-md">
                  <Badge variant="secondary" className="bg-secondary/10 text-secondary">PRO ACCOUNT</Badge>
               </div>
            </div>
            <div className="flex items-center gap-lg">
               <div className="text-right">
                  <p className="text-[10px] font-bold opacity-40 uppercase">Total Equity</p>
                  <p className="text-lg font-black text-on-surface font-data-mono">{formatCurrency(summary.totalEquity, 'VND')}</p>
               </div>
               <div className="h-8 w-[1px] bg-white/10 mx-md" />
               <div className="text-right">
                  <p className="text-[10px] font-bold opacity-40 uppercase">Total P&L</p>
                  <p className="text-lg font-black text-secondary font-data-mono">+{formatCurrency(summary.totalProfit, 'VND')} ({summary.totalProfitPercent.toFixed(2)}%)</p>
               </div>
            </div>
         </div>

         <div className="flex-1 overflow-y-auto no-scrollbar p-xl space-y-xl">
            {/* TOP ROW: EQUITY CURVE & ALLOCATION */}
            <div className="grid grid-cols-12 gap-xl">
               <div className="col-span-8">
                  <GlassCard className="p-xl h-[400px] flex flex-col bg-[#0a0a0a]">
                     <div className="flex justify-between items-center mb-xl">
                        <h3 className="text-[10px] font-black opacity-30 uppercase tracking-widest">Equity Curve (Account Growth)</h3>
                        <div className="flex gap-md">
                           <button className="text-[9px] font-black opacity-40 hover:opacity-100">1M</button>
                           <button className="text-[9px] font-black text-primary">3M</button>
                           <button className="text-[9px] font-black opacity-40 hover:opacity-100">YTD</button>
                        </div>
                     </div>
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
                                 <YAxis hide domain={['dataMin - 10000000', 'dataMax + 10000000']} />
                                 <Tooltip
                                    contentStyle={{ backgroundColor: '#0a0a0a', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '12px' }}
                                    itemStyle={{ color: '#adff2f', fontWeight: 'bold' }}
                                    formatter={(value: any) => [formatCurrency(value as number, 'VND'), 'Equity']}
                                 />
                                 <Area type="monotone" dataKey="value" stroke="var(--primary)" strokeWidth={3} fillOpacity={1} fill="url(#colorValue)" />
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
                        {isClient && (
                           <ResponsiveContainer width="100%" height="100%">
                              <PieChart>
                                 <Pie
                                    data={allocationData}
                                    innerRadius={60}
                                    outerRadius={100}
                                    paddingAngle={5}
                                    dataKey="value"
                                 >
                                    {allocationData.map((entry, index) => (
                                       <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
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
                     <div className="space-y-2 mt-md">
                        {assets.map((a, i) => (
                           <div key={a.symbol} className="flex justify-between items-center">
                              <div className="flex items-center gap-2">
                                 <div className="w-2 h-2 rounded-full" style={{ backgroundColor: COLORS[i % COLORS.length] }} />
                                 <span className="text-[10px] font-bold">{a.symbol}</span>
                              </div>
                              <span className="text-[10px] font-data-mono opacity-60">{(a.currentValue / summary.totalEquity * 100).toFixed(1)}%</span>
                           </div>
                        ))}
                     </div>
                  </GlassCard>
               </div>
            </div>

            {/* MIDDLE ROW: PRO RISK METRICS */}
            <div className="grid grid-cols-4 gap-xl">
               {[
                  { label: 'Sharpe Ratio', value: '1.85', desc: 'Risk-adjusted return', color: 'text-secondary' },
                  { label: 'Max Drawdown', value: '-12.4%', desc: 'Peak to trough decline', color: 'text-error' },
                  { label: 'Alpha (vs VN-I)', value: '+4.2%', desc: 'Excess market return', color: 'text-secondary' },
                  { label: 'Beta', value: '1.15', desc: 'Systemic risk factor', color: 'text-cyan-400' }
               ].map(m => (
                  <GlassCard key={m.label} className="p-xl bg-[#0a0a0a]">
                     <p className="text-[9px] font-black opacity-30 uppercase tracking-widest mb-2">{m.label}</p>
                     <p className={cn("text-2xl font-black font-data-mono", m.color)}>{m.value}</p>
                     <p className="text-[10px] opacity-40 mt-1">{m.desc}</p>
                  </GlassCard>
               ))}
            </div>

            {/* BOTTOM ROW: HOLDINGS DETAIL */}
            <GlassCard className="p-0 border-white/5 overflow-hidden bg-[#0a0a0a]">
               <div className="p-xl border-b border-white/5 flex justify-between items-center">
                  <h3 className="text-[10px] font-black opacity-30 uppercase tracking-widest">Holdings Attribution</h3>
                  <button className="text-[10px] font-black text-primary uppercase tracking-widest">Manage Alerts</button>
               </div>
               <table className="w-full text-left">
                  <thead>
                     <tr className="text-[9px] font-black opacity-30 uppercase tracking-widest border-b border-white/5">
                        <th className="py-4 px-xl">Symbol</th>
                        <th className="py-4 px-xl text-right">Avg Price</th>
                        <th className="py-4 px-xl text-right">Market Price</th>
                        <th className="py-4 px-xl text-right">Qty</th>
                        <th className="py-4 px-xl text-right">Value</th>
                        <th className="py-4 px-xl text-right">P&L (%)</th>
                        <th className="py-4 px-xl text-right">Weight</th>
                     </tr>
                  </thead>
                  <tbody className="font-data-mono">
                     {assets.map(a => (
                        <tr key={a.symbol} className="border-b border-white/[0.02] hover:bg-white/[0.02] transition-colors group cursor-pointer">
                           <td className="py-4 px-xl font-black text-primary">{a.symbol}</td>
                           <td className="py-4 px-xl text-right text-xs opacity-60">{a.avgPrice.toLocaleString()}</td>
                           <td className="py-4 px-xl text-right text-xs font-bold">{a.currentPrice.toLocaleString()}</td>
                           <td className="py-4 px-xl text-right text-xs">{a.quantity.toLocaleString()}</td>
                           <td className="py-4 px-xl text-right text-xs font-bold">{formatCurrency(a.currentValue, 'VND')}</td>
                           <td className={cn("py-4 px-xl text-right text-xs font-black", a.profit > 0 ? "text-secondary" : "text-error")}>
                              {a.profitPercent.toFixed(2)}%
                           </td>
                           <td className="py-4 px-xl text-right text-xs opacity-40">{(a.currentValue / summary.totalEquity * 100).toFixed(1)}%</td>
                        </tr>
                     ))}
                  </tbody>
               </table>
            </GlassCard>
         </div>
      </div>
   );
}
