"use client"

import { useState, useMemo, type CSSProperties } from "react"
import { useParams } from "next/navigation"
import { Page } from "@/components/Shell"
import {
  Button, FactorBar, Panel, PanelHead, Pill, PercentChange, Tabs, fmt,
} from "@/components/ui"
import { KLineChart } from "@/components/KLineChart"
import { stockApi, workspaceApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import { useRealtimeStock } from "@/lib/use-realtime"
import { Link } from "@/lib/router"

interface ApiStockQuote { price?: number | null; close?: number | null; ref?: number | null; prior_close?: number | null; open?: number | null; change_pct?: number | null; changePct?: number | null; change_percent?: number | null; ceiling?: number | null; floor?: number | null; volume?: number | null; asOf?: string; source?: string; stale?: boolean }
interface ApiStockProfile { name?: string; industry?: string; sector?: string }
interface ApiFactors { factor_scores?: { value_score?: number | null; quality_score?: number | null; momentum_score?: number | null; growth_score?: number | null; low_vol_score?: number | null; dividend_score?: number | null } }
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
    changePct: null,
    ref: null,
    ceiling: 0,
    floor: 0,
    risk: "Unknown" as const,
    volume: "—",
    sector: "",
    rsi: 0,
    momentum: 0,
    beneish: "UNKNOWN" as const,
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
  const [quoteRes, profileRes, fundamentalsRes, factorsRes] = (resource.data || []) as [ApiStockQuote | null, ApiStockProfile | null, ApiFundamentals | null, ApiFactors | null]

  const initialStock = useMemo(() => {
    if (!quoteRes && !profileRes) return defaultStock

    const qPrice = Number(quoteRes?.price ?? quoteRes?.close ?? defaultStock.price)
    const price = qPrice < 500 ? Math.round(qPrice * 1000) : Math.round(qPrice)
    const rawRef = quoteRes?.ref ?? quoteRes?.prior_close
    const ref = rawRef == null ? null : Number(rawRef)
    const refPrice = ref == null || !Number.isFinite(ref) ? null : ref > 0 && ref < 500 ? Math.round(ref * 1000) : Math.round(ref)
    const rawChange = quoteRes?.change_pct ?? quoteRes?.changePct ?? quoteRes?.change_percent
    const parsedChange = rawChange == null ? NaN : Number(rawChange)

    return {
      ...defaultStock,
      name: String(profileRes?.name || defaultStock.name),
      sector: String(profileRes?.industry || profileRes?.sector || defaultStock.sector),
      price: price || defaultStock.price,
      changePct: Number.isFinite(parsedChange) ? Number(parsedChange.toFixed(2)) : null,
      ref: refPrice,
      ceiling: Number(quoteRes?.ceiling ?? 0) > 0 && Number(quoteRes?.ceiling) < 500 ? Number(quoteRes?.ceiling) * 1000 : Number(quoteRes?.ceiling ?? 0),
      floor: Number(quoteRes?.floor ?? 0) > 0 && Number(quoteRes?.floor) < 500 ? Number(quoteRes?.floor) * 1000 : Number(quoteRes?.floor ?? 0),
      volume: quoteRes?.volume ? String(quoteRes.volume) : defaultStock.volume,
    }
  }, [resource.data, defaultStock])

  const { stock: realtimeStock, orderbook, isLive, flash } = useRealtimeStock(symbol, initialStock)
  const s = realtimeStock || initialStock

  const liveFundamentals = useMemo(() => {
    const f = fundamentalsRes || {}
    const pe = f.pe != null ? `${Number(f.pe).toFixed(1)}×` : "—"
    const pb = f.pb != null ? `${Number(f.pb).toFixed(1)}×` : "—"
    const roe = f.roe != null ? `${(Number(f.roe)).toFixed(1)}%` : "—"
    const eps = f.eps != null ? fmt(Math.round(Number(f.eps))) : "—"
    const grossMargin = f.gross_margin != null ? `${(Number(f.gross_margin) * 100).toFixed(1)}%` : "—"
    return { pe, pb, roe, eps, grossMargin }
  }, [fundamentalsRes, s])

  const [tab, setTab] = useState("Ma trận nhân tố")
  const [actionMessage, setActionMessage] = useState("")
  const [watchlistPending, setWatchlistPending] = useState(false)

  const addToWatchlist = async () => {
    setWatchlistPending(true)
    setActionMessage("")
    try {
      const lists = await workspaceApi.watchlists()
      const existingLists = Array.isArray(lists) ? lists as { id: string; name: string; symbols: string[] }[] : []
      if (existingLists.some(list => list.symbols.includes(symbol))) {
        setActionMessage(`${symbol} đã có trong danh sách theo dõi.`)
        return
      }
      const target = existingLists[0]
      if (target) {
        await workspaceApi.updateWatchlist(target.id, { symbols: [...target.symbols, symbol] })
        setActionMessage(`Đã thêm ${symbol} vào “${target.name}”.`)
      } else {
        await workspaceApi.createWatchlist({ name: "Theo dõi chính", symbols: [symbol] })
        setActionMessage(`Đã thêm ${symbol} vào danh sách theo dõi.`)
      }
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "Không thể cập nhật danh sách theo dõi.")
    } finally {
      setWatchlistPending(false)
    }
  }

  const factorScores = factorsRes?.factor_scores
  return (
    <Page
      title={s.name && s.name !== s.symbol ? `${s.symbol} · ${s.name}` : s.symbol}
      sub={s.sector || "Chưa có dữ liệu ngành"}
      actions={<>
        <Button variant="secondary" onClick={() => void addToWatchlist()} disabled={watchlistPending}>{watchlistPending ? "Đang lưu…" : "Thêm vào theo dõi"}</Button>
        <Link to="/trade"><Button variant="primary">Đặt lệnh</Button></Link>
      </>}
    >
      {actionMessage && <p role="status" className="mb-3 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-secondary">{actionMessage}</p>}
      {/* Price header */}
      <Panel className="mb-4">
        <div className="flex flex-wrap items-end gap-x-8 gap-y-4">
          <div>
            <div className="flex items-baseline gap-3">
              <span className={`text-[36px] font-semibold tnum font-mono leading-none transition-colors duration-300 ${flash === "gain" ? "text-gain" : flash === "loss" ? "text-loss" : "text-ink"}`}>
                {s.price > 0 ? fmt(s.price) : "—"}
              </span>
              {s.price > 0 && s.changePct != null && Number.isFinite(s.changePct) ? <PercentChange value={s.changePct} className="text-[15px]" /> : <span className="text-muted">Chưa có biến động</span>}
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
              <div className="mt-0.5 text-muted">Chưa có dữ liệu</div>
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
            <div className="mt-3 flex flex-wrap items-center gap-4 text-[11px] text-secondary">
              <span>KL {s.volume}</span>
              <span>{quoteRes?.stale ? "Dữ liệu cuối ngày" : isLive ? "Đang nhận giá trực tiếp" : "Giá gần nhất"}{quoteRes?.asOf ? ` · ${new Date(quoteRes.asOf).toLocaleDateString("vi-VN")}` : ""}</span>
              <span className="ml-auto tnum">Biên độ {s.floor > 0 ? fmt(s.floor) : "—"} – {s.ceiling > 0 ? fmt(s.ceiling) : "—"}</span>
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
                {factorScores ? <>
                  {[["Định giá", factorScores.value_score], ["Chất lượng", factorScores.quality_score], ["Xung lực", factorScores.momentum_score], ["Tăng trưởng", factorScores.growth_score], ["Biến động thấp", factorScores.low_vol_score], ["Cổ tức", factorScores.dividend_score]].filter(([, value]) => value != null).map(([label, value]) => <FactorBar key={String(label)} label={String(label)} value={Number(value)} />)}
                  {Object.values(factorScores).every(value => value == null) && <p className="text-muted">Mô hình chưa trả điểm cho mã này.</p>}
                </> : <p className="text-muted">Chưa có dữ liệu nhân tố.</p>}
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
                {[["P/E hiện tại", liveFundamentals.pe], ["P/B hiện tại", liveFundamentals.pb], ["EPS", liveFundamentals.eps]].map(([l, v]) => (
                  <div key={l} className="flex items-center justify-between border-b border-line pb-2"><span className="text-secondary">{l}</span><span className="tnum font-mono text-ink font-semibold">{v}</span></div>
                ))}
                <p className="text-[12px] text-secondary pt-1">Chưa có dữ liệu định giá mục tiêu hoặc trung vị ngành từ nguồn hiện tại.</p>
              </div>
            )}
            {(tab === "Luận điểm" || tab === "Thesis") && (
              <p className="text-[13px] text-secondary">Chưa có luận điểm đầu tư được liên kết với hồ sơ {symbol}.</p>
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
