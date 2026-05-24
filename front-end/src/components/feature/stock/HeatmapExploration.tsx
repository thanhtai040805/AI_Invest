"use client";

import React, { useState, useMemo } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { motion } from "framer-motion";
import { 
  Treemap, ResponsiveContainer, Tooltip,
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Cell,
  LineChart, Line, ScatterChart, Scatter, ZAxis
} from 'recharts';
import { useRouter } from "next/navigation";
import { useMarketSnapshot } from "@/hooks/useMarketData";
import { useStockStore } from "@/stores/useStockStore";

import { cn } from "@/lib/utils";

export function MarketBubbleMap({ sector }: { sector: string }) {
  const router = useRouter();
  const { data: stocks } = useMarketSnapshot();
  const stockStore = useStockStore((s) => s.stocks);

  const displayStocks = stocks && stocks.length > 0 ? stocks : stockStore;

  const bubbleData = useMemo(() => {
    const data = displayStocks.flatMap(s => ({
      name: s.symbol,
      size: Math.max(s.volume / 100000, 50),
      change: s.changePercent || 0,
      industry: s.industry || 'Other',
      x: ((s.symbol.length * 37 + 13) % 100),
      y: s.changePercent || 0,
    }));
    return sector === 'All' ? data : data.filter(d => d.industry === sector);
  }, [displayStocks, sector]);

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
            data={bubbleData}
            onClick={(data) => {
              const d = data as { name?: string };
              if (d.name) router.push(`/stock/${d.name}`);
            }}
          >
            {bubbleData.map((entry, index) => (
              <Cell
                key={`cell-${index}`}
                fill={entry.change > 0 ? '#10b981' : '#ef4444'}
                fillOpacity={0.6}
                stroke={entry.change > 0 ? '#10b981' : '#ef4444'}
              />
            ))}
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}

// --- COMPONENTS ---

interface CustomContentProps {
  root?: unknown;
  depth?: number;
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  index?: number;
  payload?: Record<string, unknown>;
  colors?: unknown[];
  rank?: unknown;
  name?: string;
  change?: number;
  router?: ReturnType<typeof useRouter>;
}

const CustomizedContent = (props: CustomContentProps) => {
  const { depth, x, y, width, height, name, change, router } = props;

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
  const { data: stocks } = useMarketSnapshot();
  const stockStore = useStockStore((s) => s.stocks);

  const displayStocks = stocks && stocks.length > 0 ? stocks : stockStore;

  const treemapData = useMemo(() => {
    const sectorMap = displayStocks.reduce((acc, stock) => {
      const sec = stock.industry || 'Other';
      if (!acc[sec]) {
        acc[sec] = [];
      }
      acc[sec].push({
        name: stock.symbol,
        size: Math.max(stock.volume / 100000, 50),
        change: stock.changePercent || 0,
      });
      return acc;
    }, {} as Record<string, Array<{ name: string; size: number; change: number }>>);

    return Object.entries(sectorMap).map(([name, children]) => ({
      name,
      children,
    }));
  }, [displayStocks]);

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
  const { data: stocks } = useMarketSnapshot();
  const stockStore = useStockStore((s) => s.stocks);

  const displayStocks = stocks && stocks.length > 0 ? stocks : stockStore;

  const sectorFlowData = useMemo(() => {
    const sectorMap = displayStocks.reduce((acc, stock) => {
      const sec = stock.industry || 'Other';
      if (!acc[sec]) {
        acc[sec] = { change: 0, volume: 0 };
      }
      acc[sec].change += stock.changePercent || 0;
      acc[sec].volume += stock.volume || 0;
      return acc;
    }, {} as Record<string, { change: number; volume: number }>);

    return Object.entries(sectorMap).map(([name, data]) => ({
      name,
      flow: data.change * data.volume / 1000000,
      color: data.change > 0 ? '#10b981' : '#ef4444',
    }));
  }, [displayStocks]);

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
  const { data: stocks } = useMarketSnapshot();
  const stockStore = useStockStore((s) => s.stocks);

  const displayStocks = stocks && stocks.length > 0 ? stocks : stockStore;

  const liquidityData = useMemo(() => {
    const totalVolume = displayStocks.reduce((sum, s) => sum + (s.volume || 0), 0);
    return [
      { time: '9:00', current: totalVolume * 0.1, prev: totalVolume * 0.08 },
      { time: '10:00', current: totalVolume * 0.3, prev: totalVolume * 0.25 },
      { time: '11:00', current: totalVolume * 0.5, prev: totalVolume * 0.45 },
      { time: '13:00', current: totalVolume * 0.7, prev: totalVolume * 0.65 },
      { time: '14:00', current: totalVolume * 0.9, prev: totalVolume * 0.8 },
      { time: '15:00', current: totalVolume, prev: totalVolume * 0.9 },
    ];
  }, [displayStocks]);

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
