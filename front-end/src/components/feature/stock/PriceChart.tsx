"use client";

import { useEffect, useRef, useMemo, useState } from "react";
import { init, dispose, Chart, KLineData, OverlayCreate, CandleType } from "klinecharts";

interface PriceChartProps {
  height?: number;
}

export default function PriceChart({ height = 500 }: PriceChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<Chart | null>(null);
  const [activeTool, setActiveTool] = useState<string>("cursor");

  // Generate Dummy Data
  const fullData = useMemo(() => {
    let base = 110;
    const now = Date.now();
    return Array.from({ length: 300 }).map((_, i) => {
      const open = base + (Math.random() * 2 - 1);
      const close = open + (Math.random() * 2 - 1);
      const high = Math.max(open, close) + Math.random();
      const low = Math.min(open, close) - Math.random();
      base = close;
      return {
        timestamp: now - (300 - i) * 60 * 1000,
        open,
        high,
        low,
        close,
        volume: Math.floor(Math.random() * 1000000),
      };
    });
  }, []);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = init(chartContainerRef.current, {
        styles: {
            grid: {
                show: true,
                horizontal: { color: 'rgba(255, 255, 255, 0.05)' },
                vertical: { color: 'rgba(255, 255, 255, 0.05)' }
            },
            candle: {
                type: CandleType.CandleSolid,
                bar: {
                    upColor: '#10b981',
                    downColor: '#ef4444',
                    noChangeColor: '#888888'
                }
            },
            indicator: {
                ohlc: {
                    upColor: '#10b981',
                    downColor: '#ef4444'
                }
            }
        }
    });

    if (!chart) return;
    chartRef.current = chart;

    // Stable v9 API
    try {
      chart.applyNewData(fullData as KLineData[]);
      
      // Add Indicators
      chart.createIndicator('MA', true, { id: 'pane_1' });
      chart.createIndicator('VOL', false, { height: 80 });
    } catch (err) {
      console.error("KLineChart Initialization Error:", err);
    }

    const handleResize = () => {
      chart.resize();
    };

    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      if (chartContainerRef.current) {
        dispose(chartContainerRef.current);
      }
    };
  }, [fullData]);

  // Drawing Tool Handler
  const selectTool = (name: string) => {
    setActiveTool(name);
    if (!chartRef.current) return;

    if (name === "cursor") {
      // Logic for default cursor is handled by library
    } else if (name === "eraser") {
      chartRef.current.removeOverlay();
    } else {
      chartRef.current.createOverlay({
        name: name,
        extendData: 'extended data',
        onDrawEnd: () => {
            // Can switch back to cursor after drawing if desired
            // setActiveTool("cursor");
            return true;
        }
      });
    }
  };

  const tools = [
    { id: 'cursor', icon: 'near_me', label: 'Cursor' },
    { id: 'trendLine', icon: 'show_chart', label: 'Trendline' },
    { id: 'horizontalLine', icon: 'horizontal_rule', label: 'Horz Line' },
    { id: 'verticalLine', icon: 'vertical_align_center', label: 'Vert Line' },
    { id: 'rayLine', icon: 'trending_flat', label: 'Ray' },
    { id: 'fibonacciRetracement', icon: 'format_overline', label: 'Fibonacci' },
    { id: 'rect', icon: 'rectangle', label: 'Rectangle' },
    { id: 'eraser', icon: 'delete', label: 'Clear All' },
  ];

  return (
    <div className="w-full h-full flex bg-[#050505] rounded-xl border border-white/5 overflow-hidden">
      {/* LEFT TOOLBAR */}
      <div className="w-12 border-r border-white/5 flex flex-col items-center py-4 gap-4 bg-[#0a0a0a]">
        {tools.map((tool) => (
          <button
            key={tool.id}
            title={tool.label}
            onClick={() => selectTool(tool.id)}
            className={`w-8 h-8 rounded-lg flex items-center justify-center transition-all ${
              activeTool === tool.id 
                ? 'bg-primary text-on-primary shadow-lg shadow-primary/20' 
                : 'text-on-surface-variant/40 hover:text-white hover:bg-white/5'
            }`}
          >
            <span className="material-symbols-outlined text-[18px]">{tool.icon}</span>
          </button>
        ))}
      </div>

      {/* CHART AREA */}
      <div className="flex-1 relative">
        <div className="absolute top-4 left-4 z-20 pointer-events-none">
            <div className="flex items-center gap-2">
                <span className="text-xl font-black text-white italic tracking-tighter opacity-60">AIINVEST PRO</span>
                <span className="px-2 py-0.5 rounded bg-primary/20 text-primary text-[8px] font-bold">K-LINE CORE</span>
            </div>
        </div>
        
        <div 
          ref={chartContainerRef} 
          style={{ height: height }}
          className="w-full"
        />

        {/* TOOL TIP */}
        {activeTool !== 'cursor' && activeTool !== 'eraser' && (
           <div className="absolute top-4 right-4 z-20 bg-primary/20 backdrop-blur border border-primary/30 px-3 py-1 rounded-full text-[10px] text-primary font-bold animate-pulse uppercase tracking-widest">
              Drawing Mode: {activeTool}
           </div>
        )}
      </div>
    </div>
  );
}
