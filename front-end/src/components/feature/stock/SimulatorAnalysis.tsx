"use client";

import React from "react";
import { cn } from "@/lib/utils";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, RadarChart, PolarGrid, PolarAngleAxis, Radar, Legend,
} from 'recharts';

const CHROME_PALETTE = [
  "#ADCEFF", "#8884d8", "#82ca9d", "#ffc658", "#f87171",
];

// --- COMPONENTS ---

interface PerfDataPoint {
  name: string;
  portfolio: number;
  vnindex?: number;
}

export function PerformanceChart({ data }: { data?: PerfDataPoint[] }) {
  const chartData = data?.length ? data : [];
  if (!chartData.length) return null;
  return (
    <div className="h-[300px] w-full mt-lg">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={chartData}>
          <defs>
            <linearGradient id="colorPortfolio" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#ADCEFF" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#ADCEFF" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
          <XAxis
            dataKey="name"
            stroke="rgba(255,255,255,0.3)"
            fontSize={10}
            tickLine={false}
            axisLine={false}
          />
          <YAxis hide />
          <Tooltip
            contentStyle={{ backgroundColor: 'rgba(23, 23, 23, 0.9)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '12px' }}
            itemStyle={{ fontSize: '12px' }}
          />
          <Area
            type="monotone"
            dataKey="portfolio"
            stroke="#ADCEFF"
            strokeWidth={3}
            fillOpacity={1}
            fill="url(#colorPortfolio)"
          />
          {chartData[0]?.vnindex != null && (
            <Area
              type="monotone"
              dataKey="vnindex"
              stroke="rgba(255,255,255,0.2)"
              strokeWidth={2}
              strokeDasharray="5 5"
              fill="transparent"
            />
          )}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

interface AllocDataPoint {
  name: string;
  value: number;
  color?: string;
}

export function AllocationPie({ data }: { data?: AllocDataPoint[] }) {
  const chartData = data?.length ? data : [];
  if (!chartData.length) return null;
  return (
    <div className="h-[250px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={chartData}
            cx="50%"
            cy="50%"
            innerRadius={60}
            outerRadius={80}
            paddingAngle={5}
            dataKey="value"
          >
            {chartData.map((entry, index) => (
              <Cell key={`cell-${index}`} fill={entry.color || CHROME_PALETTE[index % CHROME_PALETTE.length]} stroke="none" />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{ backgroundColor: 'rgba(23, 23, 23, 0.9)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '12px' }}
          />
          <Legend iconType="circle" wrapperStyle={{ fontSize: '10px', paddingTop: '20px' }} />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}

interface RiskDataPoint {
  subject: string;
  A: number;
  fullMark: number;
}

export function RiskRadar({ data }: { data?: RiskDataPoint[] }) {
  const chartData = data?.length ? data : [];
  if (!chartData.length) return null;
  return (
    <div className="h-[250px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart cx="50%" cy="50%" outerRadius="70%" data={chartData}>
          <PolarGrid stroke="rgba(255,255,255,0.05)" />
          <PolarAngleAxis dataKey="subject" tick={{ fill: 'rgba(255,255,255,0.5)', fontSize: 10 }} />
          <Radar
            name="Investor Profile"
            dataKey="A"
            stroke="#ADCEFF"
            fill="#ADCEFF"
            fillOpacity={0.5}
          />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
}

interface HoldingRow {
  symbol: string;
  qty?: number;
  quantity?: number;
  avgPrice: number;
  currentPrice: number;
  pnl?: number;
  profit?: number;
  pnlPercent?: number;
  profitPercent?: number;
}

export function HoldingsTable({ data }: { data?: HoldingRow[] }) {
  const [isMounted] = React.useState(() => typeof window !== 'undefined');
  const rows = data?.length ? data : [];

  if (!isMounted) return <div className="h-48 animate-pulse bg-white/5 rounded-xl" />;
  if (!rows.length) return null;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left border-collapse">
        <thead>
          <tr className="border-b border-white/5 text-[10px] text-on-surface-variant tracking-widest uppercase">
            <th className="py-md px-md font-normal">Asset</th>
            <th className="py-md px-md font-normal">Qty</th>
            <th className="py-md px-md font-normal">Avg Price</th>
            <th className="py-md px-md font-normal">Current</th>
            <th className="py-md px-md font-normal text-right">P&L (%)</th>
          </tr>
        </thead>
        <tbody className="text-sm">
          {rows.map((stock, i) => {
            const qty = stock.qty ?? stock.quantity ?? 0;
            const pnl = stock.pnlPercent ?? stock.profitPercent ?? 0;
            return (
              <tr key={i} className="border-b border-white/[0.02] hover:bg-white/[0.02] transition-colors group">
                <td className="py-lg px-md">
                  <span className="font-bold text-primary">{stock.symbol}</span>
                </td>
                <td className="py-lg px-md opacity-80">{qty.toLocaleString()}</td>
                <td className="py-lg px-md opacity-80">{stock.avgPrice}</td>
                <td className="py-lg px-md">{stock.currentPrice}</td>
                <td className={cn(
                  "py-lg px-md text-right font-bold",
                  pnl >= 0 ? "text-secondary" : "text-error"
                )}>
                  {pnl >= 0 ? '+' : ''}{pnl}%
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
