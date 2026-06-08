"use client";
import { GlassCard } from "@/components/ui/GlassCard";
import { cn } from "@/lib/utils";
import { getPriceColor, formatVolume } from "@/lib/market-utils";
import { StockQuote } from "@/types/stock";
import { useStockTechnicalIndicators, useStockFundamentals } from "@/hooks/useMarketData";

interface TradingDataProps {
  stock: StockQuote;
}

export function TradingData({ stock }: TradingDataProps) {
  const { data: techData } = useStockTechnicalIndicators(stock.symbol);
  const { data: fundData } = useStockFundamentals(stock.symbol);
  
  const tech = techData?.indicators || {};
  const ratios = fundData?.ratios || {};
  const relVol = stock.avgVolume ? (stock.volume / stock.avgVolume).toFixed(2) : "1.00";
  
  const indicatorGroups = [
    {
      title: "Trend & Liquidity",
      metrics: [
        { label: "Mở cửa", value: stock.open.toFixed(2), color: getPriceColor(stock.open, stock.prevClose, stock.ceiling, stock.floor) },
        { label: "Cao / Thấp", value: `${stock.high.toFixed(2)} / ${stock.low.toFixed(2)}`, color: "text-on-surface" },
        { label: "Giá TB (VWAP)", value: tech.vwap ? tech.vwap.toFixed(2) : ((stock.high + stock.low + stock.price) / 3).toFixed(2), color: "text-primary" },
        { label: "Khối lượng", value: formatVolume(stock.volume), color: "text-on-surface" },
        { label: "KL TB 10N", value: formatVolume(stock.avgVolume || 0), color: "text-on-surface-variant" },
        { label: "Relative Vol", value: relVol + "x", color: Number(relVol) > 1.5 ? "text-secondary" : "text-on-surface" },
      ]
    },
    {
      title: "Momentum (14D)",
      metrics: [
        { label: "RSI", value: tech.rsi_14 ? tech.rsi_14.toFixed(2) : "—", color: tech.rsi_14 > 70 ? "text-error" : tech.rsi_14 < 30 ? "text-secondary" : "text-on-surface" },
        { label: "Stochastic", value: tech.stoch_k ? `${tech.stoch_k.toFixed(1)} / ${tech.stoch_d?.toFixed(1)}` : "—", color: "text-on-surface-variant" },
        { label: "MACD", value: tech.macd ? tech.macd.toFixed(2) : "—", color: tech.macd > 0 ? "text-secondary" : "text-error" },
        { label: "Signal Line", value: tech.macd_signal ? tech.macd_signal.toFixed(2) : "—", color: "text-on-surface-variant" },
        { label: "Momentum", value: tech.momentum_1m ? `${tech.momentum_1m.toFixed(2)}%` : "—", color: tech.momentum_1m > 0 ? "text-secondary" : "text-error" },
        { label: "ROC", value: tech.momentum_5d ? `${tech.momentum_5d.toFixed(2)}%` : "—", color: tech.momentum_5d > 0 ? "text-secondary" : "text-error" },
      ]
    },
    {
      title: "Volatility",
      metrics: [
        { label: "ATR (14)", value: tech.atr_14 ? tech.atr_14.toFixed(2) : "—", color: "text-on-surface" },
        { label: "Bollinger %B", value: tech.bb_pct ? (tech.bb_pct * 100).toFixed(1) + "%" : "—", color: "text-on-surface-variant" },
        { label: "Standard Dev", value: tech.volatility_20d ? tech.volatility_20d.toFixed(2) + "%" : "—", color: "text-on-surface" },
        { label: "Beta (vs VN-I)", value: ratios.beta ? ratios.beta.toFixed(2) : "—", color: "text-on-surface-variant" },
        { label: "Alpha", value: ratios.alpha_1y ? ratios.alpha_1y.toFixed(2) : "—", color: ratios.alpha_1y > 0 ? "text-secondary" : "text-error" },
        { label: "Sharpe Ratio", value: ratios.sharpe_ratio_1y ? ratios.sharpe_ratio_1y.toFixed(2) : "—", color: "text-on-surface-variant" },
      ]
    }
  ];

  return (
    <GlassCard className="p-xl border-white/5 space-y-xl bg-[#0a0a0a]">
      <div className="flex justify-between items-center">
        <h3 className="font-label-caps text-[10px] tracking-widest opacity-60 uppercase">Technical & Risk Metrics</h3>
        <div className="flex gap-md">
           <span className="text-[9px] font-bold text-secondary uppercase bg-secondary/10 px-2 py-0.5 rounded">Bullish Momentum</span>
        </div>
      </div>
      
      <div className="grid grid-cols-1 md:grid-cols-3 gap-xl">
        {indicatorGroups.map((group) => (
          <div key={group.title} className="space-y-md">
            <h4 className="text-[9px] font-black opacity-30 uppercase tracking-widest border-b border-white/5 pb-1">{group.title}</h4>
            <div className="grid grid-cols-2 gap-y-4 gap-x-md">
              {group.metrics.map((m) => (
                <div key={m.label} className="space-y-1">
                  <p className="text-[9px] font-medium text-on-surface-variant opacity-40 uppercase tracking-widest truncate">{m.label}</p>
                  <p className={cn("text-sm font-bold font-data-mono", m.color)}>{m.value}</p>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </GlassCard>
  );
}
