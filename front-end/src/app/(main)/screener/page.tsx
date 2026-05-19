"use client";

import { cn } from "@/lib/utils";
import { formatVolume, formatCurrency, getPriceColor } from "@/lib/market-utils";
import Link from "next/link";
import { Badge } from "@/components/ui/Badge";
import { GlassCard } from "@/components/ui/GlassCard";
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
      setFilters(prev => ({ ...prev, exchange: sectorParam }));
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
    <div className="pb-xl space-y-lg px-xl pt-lg">
      {/* Header Panel */}
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-md border-b border-white/5 pb-lg">
        <div className="flex flex-col md:flex-row items-start md:items-center gap-lg">
          <div className="flex items-center gap-md">
            <div className="w-10 h-10 rounded-xl bg-[#e8a940]/10 flex items-center justify-center text-[#e8a940] border border-[#e8a940]/20">
              <span className="material-symbols-outlined text-[20px]">filter_alt</span>
            </div>
            <div>
              <h1 className="text-2xl font-black text-[#e8a940] tracking-tighter uppercase leading-none">Bộ lọc cổ phiếu (Screener)</h1>
              <p className="text-xs text-on-surface-variant mt-1">Sàng lọc các cơ hội đầu tư chất lượng dựa trên các tiêu chí tài chính & kỹ thuật.</p>
            </div>
          </div>
          
          <div className="flex gap-xs bg-white/4 p-1 rounded-xl border border-white/5 flex-wrap">
            {presets.map((p: { id: string; name: string; filters: ScreenerFilters }) => (
              <button
                key={p.id}
                onClick={() => applyBuiltin(p)}
                className={cn(
                  "px-3 py-1.5 text-[10px] font-bold rounded-lg uppercase tracking-wider transition-all",
                  activePreset === p.id 
                    ? "bg-[#e8a940] text-black font-extrabold shadow-sm" 
                    : "opacity-60 hover:opacity-100 text-on-surface"
                )}
              >
                {p.name}
              </button>
            ))}
            <button
              onClick={() => setActivePreset("custom")}
              className={cn(
                "px-3 py-1.5 text-[10px] font-bold rounded-lg uppercase tracking-wider transition-all",
                activePreset === "custom" 
                  ? "bg-[#2dbd7e]/20 text-[#2dbd7e] font-extrabold border border-[#2dbd7e]/30" 
                  : "opacity-60 text-on-surface"
              )}
            >
              Tùy chỉnh (Custom)
            </button>
          </div>
          
          {filters.exchange && (
            <Badge variant="primary" dot className="rounded-xl py-1">
              Ngành: {filters.exchange}
              <button
                onClick={() => setFilters(prev => ({ ...prev, exchange: undefined }))}
                className="ml-2 font-bold hover:text-white transition-colors"
              >
                ×
              </button>
            </Badge>
          )}
        </div>
        
        <div className="flex items-center gap-lg">
          <span className="text-[10px] font-bold opacity-45 uppercase tracking-wider font-data-mono">
            Kết quả: {total} {isFetching ? "· đang cập nhật…" : ""}
          </span>
          <button
            onClick={() => refetch()}
            className="text-[10px] font-black uppercase text-on-surface-variant hover:text-[#e8a940] transition-colors"
          >
            Làm mới
          </button>
          <button
            onClick={() => exportScreenerCsv(stocks)}
            disabled={!stocks.length}
            className="flex items-center gap-2 bg-[#2dbd7e]/10 text-[#2dbd7e] border border-[#2dbd7e]/20 px-4 py-2 rounded-xl text-[10px] font-black uppercase disabled:opacity-30 transition-all hover:bg-[#2dbd7e]/20"
          >
            <span className="material-symbols-outlined text-sm">download</span>
            Xuất CSV
          </button>
        </div>
      </div>

      {/* Main Grid: Left Filters, Right Results Table */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-lg items-start">
        {/* Left side Filter Panel */}
        <div className="lg:col-span-3">
          <GlassCard className="p-xl space-y-xl border-white/5">
            <h3 className="text-[10px] font-black opacity-45 uppercase tracking-widest border-b border-white/5 pb-2">Tiêu chí sàng lọc</h3>
            
            <div className="space-y-3">
              <div className="flex justify-between">
                <label className="text-[10px] font-bold opacity-60 uppercase">P/E tối đa</label>
                <span className="text-xs text-[#e8a940] font-bold font-data-mono">{peMax}</span>
              </div>
              <input
                type="range"
                min={0}
                max={50}
                value={peMax}
                onChange={(e) => {
                  setActivePreset("custom");
                  setPeMax(Number(e.target.value));
                }}
                className="w-full accent-[#e8a940] bg-white/10 h-1.5 rounded-lg appearance-none cursor-pointer"
              />
              <p className="text-[9px] opacity-40 leading-relaxed">Chọn hệ số P/E kỳ vọng tốt nhất. Lý tưởng &lt; 15.</p>
            </div>

            <div className="space-y-3">
              <div className="flex justify-between">
                <label className="text-[10px] font-bold opacity-60 uppercase">ROE tối thiểu (%)</label>
                <span className="text-xs text-[#e8a940] font-bold font-data-mono">{roeMin}%</span>
              </div>
              <input
                type="range"
                min={0}
                max={40}
                value={roeMin}
                onChange={(e) => {
                  setActivePreset("custom");
                  setRoeMin(Number(e.target.value));
                }}
                className="w-full accent-[#e8a940] bg-white/10 h-1.5 rounded-lg appearance-none cursor-pointer"
              />
              <p className="text-[9px] opacity-40 leading-relaxed">Hiệu suất sinh lời trên vốn chủ sở hữu. Lý tưởng &gt; 12%.</p>
            </div>

            <div className="space-y-3">
              <div className="flex justify-between">
                <label className="text-[10px] font-bold opacity-60 uppercase">Phạm vi RSI (14D)</label>
                <span className="text-xs text-[#e8a940] font-bold font-data-mono">
                  {rsiRange[0]} – {rsiRange[1]}
                </span>
              </div>
              <div className="flex gap-md">
                <div className="flex-1 bg-white/5 border border-white/10 p-2 rounded-xl text-center">
                  <p className="text-[8px] opacity-45 uppercase">Overbought</p>
                  <p className="text-xs font-bold font-data-mono">{rsiRange[1]}</p>
                </div>
                <div className="flex-1 bg-white/5 border border-white/10 p-2 rounded-xl text-center">
                  <p className="text-[8px] opacity-45 uppercase">Oversold</p>
                  <p className="text-xs font-bold font-data-mono">{rsiRange[0]}</p>
                </div>
              </div>
              <p className="text-[9px] opacity-40 leading-relaxed">Chỉ số sức mạnh tương đối. Quá mua &gt; 70, Quá bán &lt; 30.</p>
            </div>
          </GlassCard>
        </div>

        {/* Right side Table Results */}
        <div className="lg:col-span-9">
          <GlassCard className="p-0 border-white/5 overflow-hidden shadow-2xl">
            <div className="p-xl border-b border-white/5">
              <h3 className="text-[10px] font-black opacity-45 uppercase tracking-widest">Danh sách cổ phiếu phù hợp</h3>
            </div>
            {isLoading ? (
              <div className="p-xl text-center text-xs opacity-50 italic">
                Đang quét thị trường tìm các cơ hội đầu tư...
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse min-w-[1000px] font-data-mono">
                  <thead>
                    <tr className="border-b border-white/5 bg-white/[0.01] text-[10px] font-black opacity-45 uppercase">
                      <th className="py-4 px-md">Mã cổ phiếu</th>
                      <th className="py-4 px-md text-right">Giá</th>
                      <th className="py-4 px-md text-right">Biến động</th>
                      <th className="py-4 px-md text-right">P/E</th>
                      <th className="py-4 px-md text-right">P/B</th>
                      <th className="py-4 px-md text-right">ROE</th>
                      <th className="py-4 px-md text-right">Nợ / VCSH</th>
                      <th className="py-4 px-md text-right">RSI (14)</th>
                      <th className="py-4 px-md text-right">Khối lượng</th>
                      <th className="py-4 px-md text-center">Tín hiệu AI</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/[0.02]">
                    {stocks.map((s: Record<string, any>) => (
                      <tr key={String(s.symbol)} className="border-b border-white/[0.01] hover:bg-white/[0.02] transition-colors">
                        <td className="py-4 px-md">
                          <Link href={`/stock/${s.symbol}`} className="font-black text-[#e8a940] text-sm">
                            {String(s.symbol)}
                          </Link>
                          <p className="text-[9px] opacity-40 uppercase font-sans font-medium truncate max-w-[120px]">
                            {String(s.name ?? "")}
                          </p>
                        </td>
                        <td className={cn("py-4 px-md text-right text-xs font-bold", Number(s.changePercent) >= 0 ? "text-secondary" : "text-error")}>
                          {Number(s.price).toFixed(1)}
                        </td>
                        <td className={cn("py-4 px-md text-right text-xs font-black", Number(s.changePercent) >= 0 ? "text-secondary" : "text-error")}>
                          {Number(s.changePercent) > 0 ? "+" : ""}
                          {Number(s.changePercent).toFixed(2)}%
                        </td>
                        <td className="py-4 px-md text-right text-cyan-400 font-semibold">{Number(s.pe).toFixed(1)}</td>
                        <td className="py-4 px-md text-right">{Number(s.pb).toFixed(1)}</td>
                        <td className="py-4 px-md text-right text-secondary font-semibold">{Number(s.roe).toFixed(1)}%</td>
                        <td className="py-4 px-md text-right">{Number(s.de).toFixed(2)}</td>
                        <td className="py-4 px-md text-right font-semibold">{Number(s.rsi).toFixed(1)}</td>
                        <td className="py-4 px-md text-right opacity-50">{formatVolume(Number(s.volume))}</td>
                        <td className="py-4 px-md text-center">
                          <Badge variant={String(s.signal).includes("BUY") ? "secondary" : String(s.signal).includes("SELL") ? "error" : "outline"} className="text-[9px]">
                            {String(s.signal ?? "HOLD")}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </GlassCard>
        </div>
      </div>
    </div>
  );
}

export default function ScreenerPage() {
  return (
    <Suspense fallback={
      <div className="min-h-[80dvh] w-full flex items-center justify-center text-[#e8a940] font-bold text-xs uppercase tracking-widest font-data-mono">
        Đang khởi tạo máy quét thị trường...
      </div>
    }>
      <ScreenerContent />
    </Suspense>
  );
}
