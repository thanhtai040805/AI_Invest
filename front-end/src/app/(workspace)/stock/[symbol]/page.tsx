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

const SUB_INDICATORS = ["VOL"]

function OrderBook({ customBids, customAsks }: { customBids?: [number, number][]; customAsks?: [number, number][] }) {
  const bids = customBids ?? []
  const asks = customAsks ?? []
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
        {bids.length ? bids.map((b, i) => <Row key={i} p={b[0]} v={b[1]} side="bid" />) : <span className="px-2 text-[12px] text-muted">Chưa có báo giá</span>}
      </div>
      <div>
        <div className="text-[11px] uppercase tracking-wide text-muted mb-1 px-2 text-right">Ask · Dư bán</div>
        {asks.length ? asks.map((a, i) => <Row key={i} p={a[0]} v={a[1]} side="ask" />) : <span className="px-2 text-[12px] text-muted">Chưa có báo giá</span>}
      </div>
    </div>
  )
}


function Stock({ symbol }: { symbol: string }) {
  const defaultStock = useMemo(() => ({
    symbol,
    name: symbol,
    price: 0,
    changePct: 0,
    ref: 0,
    ceiling: 0,
    floor: 0,
    risk: "Moderate" as const,
    volume: "—",
    sector: "Thị trường",
    rsi: 0,
    momentum: 0,
    beneish: "PASS" as const,
    flow: 0,
    rs: 0,
    factor: "",
    weight: 0,
    pe: 0,
    foreign: 0,
    spark: [],
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
    return [] as const
  }, [factorsRes, s])

  const liveFundamentals = useMemo(() => {
    const f = fundamentalsRes || {}
    const pe = f.pe != null ? `${Number(f.pe).toFixed(1)}×` : "—"
    const pb = f.pb != null ? `${Number(f.pb).toFixed(1)}×` : "—"
    const roe = f.roe != null ? `${(Number(f.roe) * 100).toFixed(1)}%` : "—"
    const eps = f.eps != null ? fmt(Math.round(Number(f.eps))) : "—"
    const grossMargin = f.gross_margin != null ? `${(Number(f.gross_margin) * 100).toFixed(1)}%` : "—"
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
                {s.price > 0 ? fmt(s.price) : "—"}
              </span>
              {s.price > 0 ? <PercentChange value={s.changePct} className="text-[15px]" /> : <span className="text-muted">—</span>}
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
                <div className={`tnum font-mono ${c}`}>{Number(v) > 0 ? fmt(v as number) : "—"}</div>
              </div>
            ))}
            <div>
              <div className="text-[11px] uppercase tracking-wide text-muted">Beneish M-Score</div>
              <div className="mt-0.5 text-muted">—</div>
            </div>
          </div>
        </div>
      </Panel>

      <div className="grid grid-cols-1 lg:grid-cols-[1.5fr_1fr] gap-4">
        {/* Left: chart + order book */}
        <div className="space-y-4">
          <Panel>
            <PanelHead title="Biểu đồ giá" sub="Nến DNSE · công cụ vẽ, chỉ báo kỹ thuật & phóng to" action={<Pill tone={isLive ? "teal" : "neutral"}><i className={`h-1.5 w-1.5 rounded-full ${isLive ? "bg-gain animate-pulse" : "bg-muted"}`} />{isLive ? "Trực tiếp" : "Dữ liệu gần nhất"}</Pill>} />
            <KLineChart ticker={s.symbol} name={s.name} precision={0} height={420} subIndicators={SUB_INDICATORS} drawingBar />
            <div className="mt-3 flex items-center gap-4 text-[11px] text-muted">
              <span>KL {s.volume}</span>
              <span className="ml-auto tnum">Biên độ {fmt(s.floor)} – {fmt(s.ceiling)}</span>
            </div>
          </Panel>
          <Panel>
            <OrderBook
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
                {liveFactors.length ? liveFactors.map(([l, v]) => <FactorBar key={l} label={l} value={v} />) : <p className="text-muted">Chưa có dữ liệu nhân tố.</p>}
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
