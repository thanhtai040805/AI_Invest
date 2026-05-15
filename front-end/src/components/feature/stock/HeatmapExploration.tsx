"use client";

import React, { useState } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { motion } from "framer-motion";
import { 
  Treemap, ResponsiveContainer, Tooltip,
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Cell,
  LineChart, Line, ScatterChart, Scatter, ZAxis
} from 'recharts';
import { useRouter } from "next/navigation";

// ... (existing data stays the same) ...

export function MarketBubbleMap({ sector }: { sector: string }) {
  // Flatten data for bubble chart
  const flatData = treemapData.flatMap(s => 
    s.children.map(c => ({
      ...c,
      sector: s.name,
      x: Math.random() * 100, // Random X for spread
      y: c.change
    }))
    ).filter(d => sector === 'All' || d.sector === sector);
  const router = useRouter();

  return (
    <div className="h-[550px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
          <XAxis type="number" dataKey="x" name="Spread" hide />
          <YAxis 
            type="number" 
            dataKey="y" 
            name="Change" 
            unit="%" 
            stroke="rgba(255,255,255,0.4)" 
            fontSize={10}
            domain={['auto', 'auto']}
          />
          <ZAxis type="number" dataKey="size" range={[100, 2000]} name="Volume" />
          <Tooltip 
            cursor={{ strokeDasharray: '3 3' }}
            contentStyle={{ backgroundColor: 'rgba(23, 23, 23, 0.9)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '12px' }}
          />
          <Scatter 
            name="Stocks" 
            data={flatData}
            onClick={(data) => router.push(`/stock/${data.name}`)}
          >
            {flatData.map((entry, index) => (
              <Cell 
                key={`cell-${index}`} 
                fill={entry.change > 0 ? '#10b981' : '#ef4444'} 
                fillOpacity={0.6}
                stroke={entry.change > 0 ? '#10b981' : '#ef4444'}
              />
            ))}
          </Scatter>
          {/* Labels for bubbles */}
          {flatData.filter(d => d.size > 200).map((d, i) => (
            <text 
              key={i} 
              x={0} 
              y={0} 
              fontSize={10} 
              fill="#fff" 
              textAnchor="middle"
            >
              {d.name}
            </text>
          ))}
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}
import { cn } from "@/lib/utils";

// --- MOCK DATA: Đầy đủ các nhóm ngành ---
const treemapData = [
  {
    name: 'Banking',
    children: [
      { name: 'VCB', size: 450, change: 1.2 },
      { name: 'BID', size: 380, change: -0.5 },
      { name: 'TCB', size: 320, change: 2.4 },
      { name: 'MBB', size: 280, change: 0.8 },
      { name: 'CTG', size: 260, change: -1.1 },
      { name: 'STB', size: 220, change: 3.5 },
    ],
  },
  {
    name: 'Real Estate',
    children: [
      { name: 'VHM', size: 400, change: -2.4 },
      { name: 'VIC', size: 380, change: -1.8 },
      { name: 'DXG', size: 180, change: 4.5 },
      { name: 'NLG', size: 160, change: 2.1 },
      { name: 'NVL', size: 140, change: -6.8 },
      { name: 'KBC', size: 130, change: 1.5 },
    ],
  },
  {
    name: 'Steel & Resources',
    children: [
      { name: 'HPG', size: 420, change: 2.1 },
      { name: 'HSG', size: 120, change: 1.8 },
      { name: 'NKG', size: 100, change: 2.5 },
    ],
  },
  {
    name: 'Consumer & Retail',
    children: [
      { name: 'MSN', size: 250, change: 0.5 },
      { name: 'MWG', size: 240, change: 2.8 },
      { name: 'PNJ', size: 150, change: 1.2 },
      { name: 'VRE', size: 140, change: -1.5 },
    ],
  },
  {
    name: 'Energy & Chemicals',
    children: [
      { name: 'GAS', size: 300, change: -0.2 },
      { name: 'PLX', size: 180, change: 0.5 },
      { name: 'DPM', size: 120, change: 3.2 },
      { name: 'DCM', size: 110, change: 2.8 },
    ],
  },
  {
    name: 'Technology',
    children: [
      { name: 'FPT', size: 480, change: 3.2 },
      { name: 'CMG', size: 90, change: 1.5 },
      { name: 'ELC', size: 70, change: 0.2 },
    ],
  },
];

const sectorFlowData = [
  { name: 'Banking', flow: 850, color: '#10b981' },
  { name: 'Real Estate', flow: -430, color: '#ef4444' },
  { name: 'Technology', flow: 620, color: '#10b981' },
  { name: 'Steel', flow: 380, color: '#10b981' },
  { name: 'Retail', flow: 150, color: '#10b981' },
  { name: 'Energy', flow: -120, color: '#ef4444' },
];

const liquidityData = [
  { time: '9:00', current: 1200, prev: 1000 },
  { time: '10:00', current: 4500, prev: 4200 },
  { time: '11:00', current: 8900, prev: 7500 },
  { time: '13:00', current: 12000, prev: 11000 },
  { time: '14:00', current: 18500, prev: 16000 },
  { time: '15:00', current: 21450, prev: 19500 },
];

// --- COMPONENTS ---

const CustomizedContent = (props: any) => {
  const { root, depth, x, y, width, height, index, payload, colors, rank, name, change, router } = props;

  const getColor = (val: number) => {
    if (val > 2) return '#10b981'; // Strong Green
    if (val > 0) return '#059669'; // Green
    if (val === 0) return '#6b7280'; // Gray
    if (val > -2) return '#dc2626'; // Red
    return '#991b1b'; // Strong Red
  };

  return (
    <g>
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        style={{
          fill: getColor(change),
          stroke: 'rgba(0,0,0,0.2)',
          strokeWidth: 2 / (depth + 1),
          strokeOpacity: 1 / (depth + 1),
        }}
        onClick={() => name && router?.push(`/stock/${name}`)}
        className="cursor-pointer hover:brightness-110 transition-all"
      />
      {width > 25 && height > 25 && (
        <text
          x={x + width / 2}
          y={y + height / 2 - 2}
          textAnchor="middle"
          dominantBaseline="middle"
          fill="#fff"
          fontSize={Math.min(width / 5, 10)}
          fontWeight="bold"
        >
          {name}
        </text>
      )}
      {width > 35 && height > 35 && (
        <text
          x={x + width / 2}
          y={y + height / 2 + 8}
          textAnchor="middle"
          dominantBaseline="middle"
          fill="#fff"
          fontSize={9}
          opacity={0.8}
        >
          {change > 0 ? '+' : ''}{change}%
        </text>
      )}
    </g>
  );
};

export function MarketTreemap({ sector }: { sector: string }) {
  const router = useRouter();
  const filteredData = treemapData.filter(item => 
    sector === 'All' || item.name === sector
  );

  return (
    <div className="h-[550px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <Treemap
          data={filteredData}
          dataKey="size"
          aspectRatio={4 / 3}
          stroke="#fff"
          content={<CustomizedContent router={router} />}
        >
           <Tooltip 
             contentStyle={{ backgroundColor: 'rgba(23, 23, 23, 0.9)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '12px' }}
           />
        </Treemap>
      </ResponsiveContainer>
    </div>
  );
}

export function SectorFlowMatrix() {
  return (
    <div className="h-[400px] w-full mt-lg">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={sectorFlowData} layout="vertical">
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" horizontal={false} />
          <XAxis type="number" hide />
          <YAxis 
            dataKey="name" 
            type="category" 
            stroke="rgba(255,255,255,0.4)" 
            fontSize={10} 
            width={100}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip 
             cursor={{ fill: 'rgba(255,255,255,0.05)' }}
             contentStyle={{ backgroundColor: 'rgba(23, 23, 23, 0.9)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '12px' }}
          />
          <Bar dataKey="flow" radius={[0, 4, 4, 0]}>
            {sectorFlowData.map((entry, index) => (
              <Cell key={`cell-${index}`} fill={entry.flow > 0 ? '#10b981' : '#ef4444'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function LiquidityChart() {
  return (
    <div className="h-[150px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={liquidityData}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
          <XAxis dataKey="time" hide />
          <YAxis hide />
          <Tooltip 
             contentStyle={{ backgroundColor: 'rgba(23, 23, 23, 0.9)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '12px' }}
          />
          <Line type="monotone" dataKey="current" stroke="#ADCEFF" strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="prev" stroke="rgba(255,255,255,0.2)" strokeWidth={2} dot={false} strokeDasharray="5 5" />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
