"use client";

import { GlassCard } from "@/components/ui/GlassCard";
import { Badge } from "@/components/ui/Badge";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import { useState, useEffect } from "react";
import { aiAPI } from "@/services/api";

export function AutoPilotStats() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-md">
      {[
        { label: 'DAILY ROI', value: '+1.25%', trend: 'up', color: 'text-secondary' },
        { label: 'MAX DRAWDOWN', value: '-4.8%', trend: 'down', color: 'text-error' },
        { label: 'WIN RATE', value: '78.5%', trend: 'up', color: 'text-on-surface' },
        { label: 'VOLATILITY', value: 'LOW', trend: 'neutral', color: 'text-primary' },
      ].map((stat, i) => (
        <GlassCard key={i} className="flex flex-col gap-xs p-xl">
          <span className="font-label-caps text-[10px] text-on-surface-variant tracking-[0.2em]">{stat.label}</span>
          <span className={cn("font-headline-sm text-headline-sm", stat.color)}>{stat.value}</span>
        </GlassCard>
      ))}
    </div>
  );
}

export function RiskScore({ score }: { score: number }) {
  const strokeDasharray = 364;
  const strokeDashoffset = strokeDasharray - (strokeDasharray * (score / 10));

  return (
    <GlassCard className="flex flex-col items-center justify-center text-center p-xl h-full">
      <div className="relative w-32 h-32 mb-lg flex items-center justify-center">
        <svg className="w-full h-full transform -rotate-90 overflow-visible">
          <circle className="text-white/5" cx="64" cy="64" fill="transparent" r="58" stroke="currentColor" strokeWidth="8"></circle>
          <motion.circle 
            initial={{ strokeDashoffset: strokeDasharray }}
            animate={{ strokeDashoffset }}
            transition={{ duration: 1.5, ease: "easeOut" }}
            className="text-primary" 
            cx="64" cy="64" fill="transparent" r="58" 
            stroke="currentColor" 
            strokeDasharray={strokeDasharray}
            strokeWidth="8"
            strokeLinecap="round"
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <p className="text-headline-lg font-headline-lg text-on-surface">{score}</p>
          <p className="font-label-caps text-[9px] text-on-surface-variant uppercase tracking-widest">Risk Score</p>
        </div>
      </div>
      <h3 className="font-title-md text-on-surface mb-xs">Balanced Strategy</h3>
      <p className="text-[11px] text-on-surface-variant leading-relaxed opacity-70">
        AI đang ưu tiên tích lũy cổ phiếu cơ bản và phòng vệ trước biến động thị trường.
      </p>
    </GlassCard>
  );
}

export function BacktestWidget() {
  const [symbol, setSymbol] = useState("VNM");
  const [strategy, setStrategy] = useState("MACD_CROSSOVER");
  const [status, setStatus] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [result, setResult] = useState<any>(null);

  useEffect(() => {
    let interval: NodeJS.Timeout;
    if (jobId && status === "running") {
      interval = setInterval(async () => {
        try {
          const res = await aiAPI.getBacktestStatus(jobId);
          if (res.status === "completed") {
            setStatus("completed");
            setResult(res.result);
            clearInterval(interval);
          } else if (res.status === "failed") {
            setStatus("failed");
            clearInterval(interval);
          }
        } catch (e) {
          console.error("Error polling backtest", e);
        }
      }, 2000);
    }
    return () => clearInterval(interval);
  }, [jobId, status]);

  const handleRun = async () => {
    try {
      setStatus("running");
      setResult(null);
      const res = await aiAPI.submitBacktest({
        symbol,
        strategy,
        startDate: "2023-01-01",
        endDate: "2023-12-31"
      });
      setJobId(res.jobId);
    } catch (e) {
      console.error(e);
      setStatus("failed");
    }
  };

  return (
    <GlassCard className="flex flex-col gap-lg">
      <h3 className="font-title-md">Backtest Strategy</h3>
      <div className="grid grid-cols-2 gap-md">
        <input 
          className="bg-white/5 border border-white/10 rounded-xl p-2 text-xs font-data-mono uppercase"
          value={symbol}
          onChange={e => setSymbol(e.target.value)}
          placeholder="Symbol (e.g. VNM)"
        />
        <select 
          className="bg-white/5 border border-white/10 rounded-xl p-2 text-xs font-data-mono"
          value={strategy}
          onChange={e => setStrategy(e.target.value)}
        >
          <option value="MACD_CROSSOVER">MACD Crossover</option>
          <option value="RSI_REVERSION">RSI Reversion</option>
        </select>
      </div>
      <button 
        onClick={handleRun}
        disabled={status === "running"}
        className="w-full py-2 bg-primary text-on-primary rounded-xl font-bold text-xs disabled:opacity-50"
      >
        {status === "running" ? "Running..." : "Run Backtest"}
      </button>

      {result && (
        <div className="mt-md p-md bg-white/5 rounded-xl text-xs space-y-2">
          <div className="flex justify-between"><span className="opacity-60">Total Return</span><span className="font-bold text-secondary">{result.totalReturn}%</span></div>
          <div className="flex justify-between"><span className="opacity-60">Max Drawdown</span><span className="font-bold text-error">{result.maxDrawdown}%</span></div>
          <div className="flex justify-between"><span className="opacity-60">Win Rate</span><span className="font-bold">{result.winRate}%</span></div>
        </div>
      )}
    </GlassCard>
  );
}

export function BacktestHistory() {
  const [history, setHistory] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    aiAPI.getBacktestHistory()
      .then(setHistory)
      .catch(console.error)
      .finally(() => setIsLoading(false));
  }, []);

  if (isLoading) return <div className="opacity-40 text-[10px] uppercase font-bold text-center py-xl">Loading History...</div>;

  return (
    <GlassCard className="flex flex-col gap-md">
      <div className="flex justify-between items-center">
        <h3 className="font-title-md uppercase tracking-wider">Backtest History</h3>
        <Badge variant="outline" className="opacity-40">{history.length}</Badge>
      </div>
      <div className="space-y-md max-h-[400px] overflow-y-auto no-scrollbar">
        {history.length > 0 ? history.map((item, i) => (
          <div key={i} className="p-md bg-white/[0.02] border border-white/5 rounded-xl space-y-2 hover:bg-white/[0.04] transition-all">
            <div className="flex justify-between items-center">
              <span className="font-bold text-primary">{item.symbol}</span>
              <span className="text-[10px] opacity-40 font-data-mono">{new Date(item.createdAt).toLocaleDateString()}</span>
            </div>
            <div className="text-[10px] font-bold opacity-60 uppercase">{item.strategy.replace('_', ' ')}</div>
            <div className="flex gap-lg mt-2">
              <div className="flex flex-col">
                <span className="text-[9px] opacity-40 uppercase">Return</span>
                <span className={cn("text-xs font-bold", item.result?.totalReturn >= 0 ? "text-secondary" : "text-error")}>
                  {item.result?.totalReturn}%
                </span>
              </div>
              <div className="flex flex-col">
                <span className="text-[9px] opacity-40 uppercase">Drawdown</span>
                <span className="text-xs font-bold text-error">{item.result?.maxDrawdown}%</span>
              </div>
            </div>
          </div>
        )) : (
          <div className="text-center py-xl opacity-20 text-[10px] font-bold uppercase italic">No history found</div>
        )}
      </div>
    </GlassCard>
  );
}
