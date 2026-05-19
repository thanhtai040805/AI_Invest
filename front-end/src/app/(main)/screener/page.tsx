"use client";

import { cn } from "@/lib/utils";
import { formatVolume, formatCurrency, getPriceColor } from "@/lib/market-utils";
import Link from "next/link";
import { Badge } from "@/components/ui/Badge";
import { ScreenerFilters } from "@/services/api";
import { useScreenerFilter, useBuiltinPresets, exportScreenerCsv } from "@/hooks/useScreener";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, useMemo } from "react";

const DEFAULT_FILTERS: ScreenerFilters = { limit: 100, sort: "changePercent", sortDir: "desc" };

function ScreenerContent() {
  const searchParams = useSearchParams();
  const sectorParam = searchParams.get("sector");

  const [filters, setFilters] = useState<ScreenerFilters>(DEFAULT_FILTERS);
  const [activePreset, setActivePreset] = useState("custom");

  useEffect(() => {
    if (sectorParam) {
      setFilters(prev => ({ ...prev, exchange: sectorParam })); // Overloading exchange for sector if needed, or add sector field
      setActivePreset("custom");
    }
  }, [sectorParam]);
  const [peMax, setPeMax] = useState(20);
  const [roeMin, setRoeMin] = useState(10);
  const [rsiRange, setRsiRange] = useState<[number, number]>([30, 70]);

  const { data: builtinPresets } = useBuiltinPresets();
  const appliedFilters = useMemo(
    () => ({
      ...filters,
      peMax: activePreset === "custom" ? peMax : filters.peMax,
      roeMin: activePreset === "custom" ? roeMin : filters.roeMin,
      rsiMin: activePreset === "custom" ? rsiRange[0] : filters.rsiMin,
      rsiMax: activePreset === "custom" ? rsiRange[1] : filters.rsiMax,
    }),
    [filters, activePreset, peMax, roeMin, rsiRange],
  );

  const { data, isLoading, isFetching, refetch } = useScreenerFilter(appliedFilters);
  const stocks = data?.stocks ?? [];
  const total = data?.total ?? stocks.length;

  const applyBuiltin = (preset: { id: string; name: string; filters: ScreenerFilters }) => {
    setActivePreset(preset.id);
    setFilters({ ...DEFAULT_FILTERS, ...preset.filters });
  };

  const presets = builtinPresets ?? [
    { id: "valuation", name: "Valuation", filters: { peMax: 15, roeMin: 12 } },
    { id: "growth", name: "Growth", filters: { roeMin: 15 } },
    { id: "technical", name: "Technical", filters: { rsiMin: 30, rsiMax: 70 } },
  ];

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-[#050505]">
      <div className="h-16 border-b border-white/5 flex items-center justify-between px-xl bg-[#0a0a0a]">
        <div className="flex items-center gap-xl">
          <h1 className="text-xl font-black text-primary tracking-tighter uppercase">Market Screener</h1>
          <div className="flex gap-md bg-white/5 p-1 rounded-lg border border-white/10 flex-wrap">
            {presets.map((p: { id: string; name: string; filters: ScreenerFilters }) => (
              <button
                key={p.id}
                onClick={() => applyBuiltin(p)}
                className={cn(
                  "px-4 py-1 text-[10px] font-black rounded uppercase tracking-widest transition-all",
                  activePreset === p.id ? "bg-primary text-white" : "opacity-40 hover:opacity-100",
                )}
              >
                {p.name}
              </button>
            ))}
            <button
              onClick={() => setActivePreset("custom")}
              className={cn(
                "px-4 py-1 text-[10px] font-black rounded uppercase",
                activePreset === "custom" ? "bg-secondary/20 text-secondary" : "opacity-40",
              )}
            >
              Custom
            </button>
          </div>
          {filters.exchange && (
            <Badge variant="secondary" className="bg-primary/20 text-primary border-primary/10 rounded-full py-1">
              Sector: {filters.exchange}
              <button
                onClick={() => setFilters(prev => ({ ...prev, exchange: undefined }))}
                className="ml-2 hover:text-white transition-colors"
              >
                ×
              </button>
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-lg">
          <span className="text-[10px] font-bold opacity-40 uppercase tracking-widest">
            Matches: {total} {isFetching ? "· updating…" : ""}
          </span>
          <button
            onClick={() => refetch()}
            className="text-[10px] font-black uppercase opacity-60 hover:text-primary"
          >
            Refresh
          </button>
          <button
            onClick={() => exportScreenerCsv(stocks)}
            disabled={!stocks.length}
            className="flex items-center gap-2 bg-secondary/10 text-secondary border border-secondary/20 px-4 py-2 rounded-xl text-[10px] font-black uppercase disabled:opacity-30"
          >
            <span className="material-symbols-outlined text-sm">download</span>
            Export CSV
          </button>
        </div>
      </div>

      <div className="flex-1 flex overflow-hidden">
        <div className="w-72 border-r border-white/5 flex flex-col bg-[#080808] overflow-y-auto no-scrollbar p-xl space-y-xl">
          <div className="space-y-3">
            <label className="text-[9px] font-bold opacity-40 uppercase">P/E max</label>
            <input
              type="range"
              min={0}
              max={50}
              value={peMax}
              onChange={(e) => {
                setActivePreset("custom");
                setPeMax(Number(e.target.value));
              }}
              className="w-full"
            />
            <span className="text-[10px] text-primary font-bold">{peMax}</span>
          </div>
          <div className="space-y-3">
            <label className="text-[9px] font-bold opacity-40 uppercase">ROE min (%)</label>
            <input
              type="range"
              min={0}
              max={40}
              value={roeMin}
              onChange={(e) => {
                setActivePreset("custom");
                setRoeMin(Number(e.target.value));
              }}
              className="w-full"
            />
            <span className="text-[10px] text-primary font-bold">{roeMin}%</span>
          </div>
          <div className="space-y-3">
            <label className="text-[9px] font-bold opacity-40 uppercase">RSI range</label>
            <span className="text-[10px] text-primary font-bold">
              {rsiRange[0]} – {rsiRange[1]}
            </span>
          </div>
        </div>

        <div className="flex-1 overflow-auto p-xl">
          {isLoading ? (
            <p className="text-sm opacity-40 text-center py-xl">Đang quét thị trường...</p>
          ) : (
            <table className="w-full text-left border-collapse min-w-[1200px] font-data-mono">
              <thead>
                <tr className="border-b border-white/5 text-[10px] font-black opacity-30 uppercase">
                  <th className="py-4 px-md">Ticker</th>
                  <th className="py-4 px-md text-right">Price</th>
                  <th className="py-4 px-md text-right">Change %</th>
                  <th className="py-4 px-md text-right">P/E</th>
                  <th className="py-4 px-md text-right">P/B</th>
                  <th className="py-4 px-md text-right">ROE</th>
                  <th className="py-4 px-md text-right">D/E</th>
                  <th className="py-4 px-md text-right">RSI</th>
                  <th className="py-4 px-md text-right">Volume</th>
                  <th className="py-4 px-md text-center">Signal</th>
                </tr>
              </thead>
              <tbody>
                {stocks.map((s: Record<string, number | string>) => (
                  <tr key={String(s.symbol)} className="border-b border-white/[0.02] hover:bg-white/[0.02]">
                    <td className="py-4 px-md">
                      <Link href={`/stock/${s.symbol}`} className="font-black text-primary">
                        {String(s.symbol)}
                      </Link>
                      <p className="text-[9px] opacity-30">{String(s.name ?? "")}</p>
                    </td>
                    <td className={cn("py-4 px-md text-right text-xs font-bold", Number(s.changePercent) >= 0 ? "text-secondary" : "text-error")}>
                      {Number(s.price).toFixed(1)}
                    </td>
                    <td className={cn("py-4 px-md text-right text-xs font-bold", Number(s.changePercent) >= 0 ? "text-secondary" : "text-error")}>
                      {Number(s.changePercent) > 0 ? "+" : ""}
                      {Number(s.changePercent).toFixed(2)}%
                    </td>
                    <td className="py-4 px-md text-right text-cyan-400">{Number(s.pe).toFixed(1)}</td>
                    <td className="py-4 px-md text-right">{Number(s.pb).toFixed(1)}</td>
                    <td className="py-4 px-md text-right text-secondary">{Number(s.roe).toFixed(1)}%</td>
                    <td className="py-4 px-md text-right">{Number(s.de).toFixed(2)}</td>
                    <td className="py-4 px-md text-right">{Number(s.rsi).toFixed(1)}</td>
                    <td className="py-4 px-md text-right opacity-40">{formatVolume(Number(s.volume))}</td>
                    <td className="py-4 px-md text-center">
                      <Badge variant="outline" className="text-[9px]">
                        {String(s.signal ?? "—")}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ScreenerPage() {
  return (
    <Suspense fallback={<div className="h-screen w-screen flex items-center justify-center bg-[#050505] text-primary">Loading...</div>}>
      <ScreenerContent />
    </Suspense>
  );
}
