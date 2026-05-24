"use client";

import React from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { Badge } from "@/components/ui/Badge";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import { 
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, RadarChart, PolarGrid, PolarAngleAxis, Radar, Legend,
  BarChart, Bar
} from 'recharts';

// --- MOCK DATA ---
const performanceData = [
  { name: 'Mon', portfolio: 4000, vnindex: 2400 },
  { name: 'Tue', portfolio: 3000, vnindex: 1398 },
  { name: 'Wed', portfolio: 2000, vnindex: 9800 },
  { name: 'Thu', portfolio: 2780, vnindex: 3908 },
  { name: 'Fri', portfolio: 1890, vnindex: 4800 },
  { name: 'Sat', portfolio: 2390, vnindex: 3800 },
  { name: 'Sun', portfolio: 3490, vnindex: 4300 },
];

const allocationData = [
  { name: 'FPT', value: 400, color: '#ADCEFF' },
  { name: 'VNM', value: 300, color: '#8884d8' },
  { name: 'HPG', value: 300, color: '#82ca9d' },
  { name: 'TCB', value: 200, color: '#ffc658' },
];

const sectorData = [
  { name: 'Banking', value: 45, color: '#ADCEFF' },
  { name: 'Tech', value: 25, color: '#8884d8' },
  { name: 'Industry', value: 20, color: '#82ca9d' },
  { name: 'Retail', value: 10, color: '#ffc658' },
];

const riskData = [
  { subject: 'Profit', A: 120, fullMark: 150 },
  { subject: 'Risk', A: 98, fullMark: 150 },
  { subject: 'Consistency', A: 86, fullMark: 150 },
  { subject: 'Diversity', A: 99, fullMark: 150 },
  { subject: 'Discipline', A: 85, fullMark: 150 },
];

const holdings = [
  { symbol: 'FPT', qty: 1000, avgPrice: 95.5, currentPrice: 112.4, pnl: 17.5, pnlPercent: 18.2 },
  { symbol: 'HPG', qty: 5000, avgPrice: 28.2, currentPrice: 29.1, pnl: 4.5, pnlPercent: 3.2 },
  { symbol: 'VNM', qty: 2000, avgPrice: 72.1, currentPrice: 68.5, pnl: -7.2, pnlPercent: -4.9 },
];

// --- COMPONENTS ---

export function PerformanceChart() {
  return (
    <div className="h-[300px] w-full mt-lg">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={performanceData}>
          <defs>
            <linearGradient id="colorPortfolio" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#ADCEFF" stopOpacity={0.3}/>
              <stop offset="95%" stopColor="#ADCEFF" stopOpacity={0}/>
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
          <Area 
            type="monotone" 
            dataKey="vnindex" 
            stroke="rgba(255,255,255,0.2)" 
            strokeWidth={2}
            strokeDasharray="5 5"
            fill="transparent" 
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function AllocationPie() {
  return (
    <div className="h-[250px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={allocationData}
            cx="50%"
            cy="50%"
            innerRadius={60}
            outerRadius={80}
            paddingAngle={5}
            dataKey="value"
          >
            {allocationData.map((entry, index) => (
              <Cell key={`cell-${index}`} fill={entry.color} stroke="none" />
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

export function RiskRadar() {
  return (
    <div className="h-[250px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart cx="50%" cy="50%" outerRadius="70%" data={riskData}>
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

export function HoldingsTable() {
  const [isMounted] = React.useState(() => typeof window !== 'undefined');

  if (!isMounted) return <div className="h-48 animate-pulse bg-white/5 rounded-xl" />;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left border-collapse">
        <thead>
          <tr className="border-b border-white/5 font-label-caps text-[10px] text-on-surface-variant tracking-widest uppercase">
            <th className="py-md px-md font-normal">Asset</th>
            <th className="py-md px-md font-normal">Qty</th>
            <th className="py-md px-md font-normal">Avg Price</th>
            <th className="py-md px-md font-normal">Current</th>
            <th className="py-md px-md font-normal text-right">P&L (%)</th>
          </tr>
        </thead>
        <tbody className="text-sm">
          {holdings.map((stock, i) => (
            <tr key={i} className="border-b border-white/[0.02] hover:bg-white/[0.02] transition-colors group">
              <td className="py-lg px-md">
                <span className="font-bold text-primary">{stock.symbol}</span>
              </td>
              <td className="py-lg px-md font-data-mono opacity-80">{stock.qty.toLocaleString()}</td>
              <td className="py-lg px-md font-data-mono opacity-80">{stock.avgPrice}</td>
              <td className="py-lg px-md font-data-mono">{stock.currentPrice}</td>
              <td className={cn(
                "py-lg px-md text-right font-bold font-data-mono",
                stock.pnl >= 0 ? "text-secondary" : "text-error"
              )}>
                {stock.pnl >= 0 ? '+' : ''}{stock.pnlPercent}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
