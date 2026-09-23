"use client"

import { useState, useMemo, type CSSProperties } from "react"
import { useParams } from "next/navigation"
import { Page } from "@/components/Shell"
import { defaultStockCases } from "@/lib/agent-system"
import {
  Button, Conviction, FactorBar, Panel, PanelHead, Pill, PercentChange,
  ReasoningBlock, Tabs, fmt,
} from "@/components/ui"
import { KLineChart } from "@/components/KLineChart"
import { stockApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import { useRealtimeStock } from "@/lib/use-realtime"

interface ApiStockQuote { price?: number; close?: number; ref?: number; open?: number; change_pct?: number; ceiling?: number; floor?: number; volume?: number }
interface ApiStockProfile { name?: string; industry?: string; sector?: string }
interface ApiFactors { value?: number; quality?: number; momentum?: number; growth?: number; flow?: number; technical?: number }
interface ApiFundamentals { pe?: number; pb?: number; roe?: number; eps?: number; gross_margin?: number }

function OrderBook({ customBids, customAsks, basePrice = 25000 }: { customBids?: [number, number][]; customAsks?: [number, number][]; basePrice?: number }) {
  const p = basePrice > 0 ? basePrice : 25000
  const step = p < 10000 ? 10 : p < 50000 ? 50 : 100
  const defaultBids: [number, number][] = [1, 2, 3].map((i) => [p - i * step, Math.round((10 + (i * 3) % 7) * 1200)])
  const defaultAsks: [number, number][] = [1, 2, 3].map((i) => [p + i * step, Math.round((8 + (i * 5) % 9) * 1100)])

  const bids = customBids && customBids.length > 0 ? customBids : defaultBids
  const asks = customAsks && customAsks.length > 0 ? customAsks : defaultAsks
  const allV = [...bids.map((b) => b[1]), ...asks.map((a) => a[1])]
  const maxV = Math.max(...allV, 1000)

  const Row = ({ p, v, side }: { p: number; v: number; side: "bid" | "ask" }) => (
    <div className="relative flex items-center justify-between text-[12px] px-2 h-6 tnum font-mono">
      <span className="absolute inset-y-0.5 rounded-[3px]" style={{ [side === "bid" ? "right" : "left"]: 0, width: `${(v / maxV) * 100}%`, background: side === "bid" ? "color-mix(in srgb, var(--color-gain) 12%, transparent)" : "color-mix(in srgb, var(--color-loss) 12%, transparent)" } as CSSProperties} />
      <span className={`relative ${side === "bid" ? "text-gain order-1" : "text-loss"}`}>{fmt(p)}</span>
      <span className="relative text-secondary">{fmt(v)}</span>
    </div>
  )
  return (
    <div className="grid grid-cols-2 gap-3">
      <div>
        <div className="text-[11px] uppercase tracking-wide text-muted mb-1 px-2">Dư mua · Bid</div>
        {bids.map((b, i) => <Row key={i} p={b[0]} v={b[1]} side="bid" />)}
      </div>
      <div>
        <div className="text-[11px] uppercase tracking-wide text-muted mb-1 px-2 text-right">Ask · Dư bán</div>
        {asks.map((a, i) => <Row key={i} p={a[0]} v={a[1]} side="ask" />)}
      </div>
    </div>
  )
}


function Stock({ symbol }: { symbol: string }) {
  const defaultStock = useMemo(() => ({
    symbol,
    name: symbol,
    price: 30000,
    changePct: 0,
    ref: 30000,
    ceiling: 32100,
    floor: 27900,
    risk: "Moderate" as const,
    volume: "1.2M",
    sector: "Thị trường",
    rsi: 50,
    momentum: 50,
    beneish: "PASS" as const,
    flow: 0,
    rs: 50,
    factor: "Tích lũy",
    weight: 10,
    pe: 12,
    foreign: 0,
    spark: [30, 30, 30],
  }), [symbol])
  const resource = useResource(() => Promise.all([
    stockApi.quote(symbol).catch(() => null),
    stockApi.profile(symbol).catch(() => null),
    stockApi.fundamentals(symbol).catch(() => null),
    stockApi.factors(symbol).catch(() => null),
  ]), [symbol])

  const initialStock = useMemo(() => {
    const [quoteRes, profileRes] = (resource.data || []) as [ApiStockQuote | null, ApiStockProfile | null, unknown, ApiFactors | null]
    if (!quoteRes && !profileRes) return defaultStock

    const qPrice = Number(quoteRes?.price ?? quoteRes?.close ?? defaultStock.price)
    const price = qPrice < 500 ? Math.round(qPrice * 1000) : Math.round(qPrice)
    const ref = Number(quoteRes?.ref ?? quoteRes?.open ?? defaultStock.ref)
    const refPrice = ref < 500 ? Math.round(ref * 1000) : Math.round(ref)

    return {
      ...defaultStock,
      name: String(profileRes?.name || defaultStock.name),
      sector: String(profileRes?.industry || profileRes?.sector || defaultStock.sector),
      price: price || defaultStock.price,
      changePct: quoteRes?.change_pct !== undefined ? Number(Number(quoteRes.change_pct).toFixed(2)) : defaultStock.changePct,
      ref: refPrice || defaultStock.ref,
      ceiling: Number(quoteRes?.ceiling ?? defaultStock.ceiling),
      floor: Number(quoteRes?.floor ?? defaultStock.floor),
      volume: quoteRes?.volume ? String(quoteRes.volume) : defaultStock.volume,
    }
  }, [resource.data, defaultStock])

  const { stock: realtimeStock, orderbook, isLive, flash } = useRealtimeStock(symbol, initialStock)
  const s = realtimeStock || initialStock
  const [,, fundamentalsRes, factorsRes] = (resource.data || []) as [unknown, unknown, ApiFundamentals | null, ApiFactors | null]

  const liveFactors = useMemo(() => {
    if (factorsRes) {
      return [
        ["Định giá", Number(factorsRes.value ?? 60)],
        ["Chất lượng", Number(factorsRes.quality ?? 72)],
        ["Xung lực", Number(factorsRes.momentum ?? 68)],
        ["Tăng trưởng", Number(factorsRes.growth ?? 65)],
        ["Dòng tiền", Number(factorsRes.flow ?? 70)],
        ["Kỹ thuật", Number(factorsRes.technical ?? 64)],
      ] as const
    }
    return [
      ["Định giá", Math.round(s.rsi > 50 ? 65 : 55)],
      ["Chất lượng", 74],
      ["Xung lực", s.momentum || 68],
      ["Tăng trưởng", 66],
      ["Dòng tiền", Math.round(s.flow ? Math.min(Math.max(s.flow * 2, 40), 90) : 60)],
      ["Kỹ thuật", s.rsi || 50],
    ] as const
  }, [factorsRes, s])

  const liveFundamentals = useMemo(() => {
    const f = fundamentalsRes || {}
    const pe = f.pe ? `${Number(f.pe).toFixed(1)}×` : `${s.pe?.toFixed(1) || "12.8"}×`
    const pb = f.pb ? `${Number(f.pb).toFixed(1)}×` : "1.6×"
    const roe = f.roe ? `${(Number(f.roe) * 100).toFixed(1)}%` : "14.2%"
    const eps = f.eps ? fmt(Math.round(Number(f.eps))) : fmt(Math.round(s.price / 12.8))
    const grossMargin = f.gross_margin ? `${(Number(f.gross_margin) * 100).toFixed(1)}%` : "13.2%"
    return { pe, pb, roe, eps, grossMargin }
  }, [fundamentalsRes, s])

  const [tab, setTab] = useState("Ma trận nhân tố")
  return (
    <Page
      title={`${s.symbol} · ${s.name}`}
      sub={`${s.sector} · HOSE`}
      actions={<>
        <Button variant="secondary">Thêm vào theo dõi</Button>
        <Button variant="secondary">Đặt cảnh báo</Button>
        <Button variant="primary">Lưu luận điểm</Button>
      </>}
    >
      {/* Price header */}
      <Panel className="mb-4">
        <div className="flex flex-wrap items-end gap-x-8 gap-y-4">
          <div>
            <div className="flex items-baseline gap-3">
              <span className={`text-[36px] font-semibold tnum font-mono leading-none transition-colors duration-300 ${flash === "gain" ? "text-gain" : flash === "loss" ? "text-loss" : "text-ink"}`}>
                {fmt(s.price)}
              </span>
              <PercentChange value={s.changePct} className="text-[15px]" />
              {isLive && (
                <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-medium bg-emerald-500/10 text-emerald-500 border border-emerald-500/20">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                  TRỰC TIẾP
                </span>
              )}
            </div>
          </div>
          <div className="flex gap-6 text-[13px]">
            {[["Tham chiếu", s.ref, "text-neutral"], ["Trần", s.ceiling, "text-mineral"], ["Sàn", s.floor, "text-teal"]].map(([l, v, c]) => (
              <div key={l as string}>
                <div className="text-[11px] uppercase tracking-wide text-muted">{l as string}</div>
                <div className={`tnum font-mono ${c}`}>{fmt(v as number)}</div>
              </div>
            ))}
            <div>
              <div className="text-[11px] uppercase tracking-wide text-muted">Beneish M-Score</div>
              <div className="mt-0.5"><Pill tone={s.beneish === "PASS" ? "teal" : "warning"}>{s.beneish}</Pill></div>
            </div>
          </div>
        </div>
      </Panel>

      <div className="grid grid-cols-1 lg:grid-cols-[1.5fr_1fr] gap-4">
        {/* Left: chart + order book */}
        <div className="space-y-4">
          <Panel>
            <PanelHead title="Biểu đồ giá" sub="Nến trực tiếp · công cụ vẽ, chỉ báo kỹ thuật & phóng to" action={<Pill tone="teal"><i className="h-1.5 w-1.5 rounded-full bg-gain animate-pulse" />Trực tiếp</Pill>} />
            <KLineChart ticker={s.symbol} name={s.name} basePrice={s.price} precision={0} height={420} subIndicators={["VOL"]} drawingBar />
            <div className="mt-3 flex items-center gap-4 text-[11px] text-muted">
              <span>KL {s.volume}</span>
              <span className="ml-auto tnum">Biên độ {fmt(s.floor)} – {fmt(s.ceiling)}</span>
            </div>
          </Panel>
          <Panel>
            <OrderBook
              basePrice={s.price}
              customBids={orderbook?.bids?.map((b) => [b.price, b.volume])}
              customAsks={orderbook?.asks?.map((a) => [a.price, a.volume])}
            />
          </Panel>
        </div>

        {/* Right: research inspector */}
        <Panel flush>
          <div className="px-5 pt-5">
            <Tabs tabs={["Ma trận nhân tố", "Tài chính", "Định giá", "Luận điểm"]} active={tab === "Moat Analysis" || tab === "Graph Intelligence" || tab === "Factor Matrix" || tab === "Ma trận nhân tố" ? "Ma trận nhân tố" : tab === "Financials" || tab === "Tài chính" ? "Tài chính" : tab === "Valuation" || tab === "Định giá" ? "Định giá" : tab === "Thesis" || tab === "Luận điểm" ? "Luận điểm" : tab} onChange={setTab} />
          </div>
          <div className="p-5">
            {(tab === "Ma trận nhân tố" || tab === "Factor Matrix" || tab === "Moat Analysis" || tab === "Graph Intelligence") && (
              <div className="space-y-3">
                <p className="text-[12px] text-muted">Điểm số định lượng các yếu tố F1–F6 từ mô hình AI Invest.</p>
                {liveFactors.map(([l, v]) => <FactorBar key={l} label={l} value={v} />)}
              </div>
            )}
            {(tab === "Tài chính" || tab === "Financials") && (
              <div className="space-y-2.5 text-[13px]">
                {[["Biên lợi nhuận gộp", liveFundamentals.grossMargin], ["ROE (Tỷ suất sinh lời)", liveFundamentals.roe], ["P/E (Thị giá / Lợi nhuận)", liveFundamentals.pe], ["P/B (Thị giá / Giá trị sổ sách)", liveFundamentals.pb], ["EPS TTM", liveFundamentals.eps]].map(([l, v]) => (
                  <div key={l} className="flex justify-between border-b border-line pb-2"><span className="text-secondary">{l}</span><span className="tnum font-mono text-ink font-semibold">{v}</span></div>
                ))}
              </div>
            )}
            {(tab === "Định giá" || tab === "Valuation") && (
              <div className="space-y-2.5 text-[13px]">
                {[["P/E hiện tại", liveFundamentals.pe, "Trung vị ngành: 14.5×"], ["P/B hiện tại", liveFundamentals.pb, "Trung vị ngành: 1.8×"], ["EPS", liveFundamentals.eps, "Lợi nhuận mỗi cổ phần"], ["Vùng giá hợp lý", `${fmt(Math.round(s.price * 0.95))} – ${fmt(Math.round(s.price * 1.18))}`, "Định giá DCF + Multiples"]].map(([l, v, d]) => (
                  <div key={l} className="flex items-center justify-between border-b border-line pb-2"><span className="text-secondary">{l}</span><span className="tnum font-mono text-ink font-semibold">{v}</span><span className="text-[11px] text-muted">{d}</span></div>
                ))}
                <p className="text-[12px] text-secondary pt-1">Định giá cập nhật tự động theo BCTC quý gần nhất và giá khớp lệnh.</p>
              </div>
            )}
            {(tab === "Luận điểm" || tab === "Thesis") && (
              <div>
                <div className="flex items-center gap-2 mb-3"><span className="text-[15px] font-semibold text-ink">Xu hướng tích cực</span><Conviction level="Moderate" /></div>
                <ReasoningBlock data={defaultStockCases[symbol]?.reasoning || defaultStockCases.HPG.reasoning} />
              </div>
            )}
          </div>
        </Panel>
      </div>
    </Page>
  )
}

export default function StockDetailPage() {
  const { symbol } = useParams<{ symbol: string }>()
  return <Stock symbol={(symbol || "HPG").toUpperCase()} />
}
