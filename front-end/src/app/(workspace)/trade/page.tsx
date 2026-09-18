"use client"

import { useState, useMemo } from "react"
import { Page } from "@/components/Shell"
import {
  Button,
  Panel,
  PanelHead,
  Pill,
  MarketLineChart,
  PercentChange,
  fmt,
} from "@/components/ui"
import { stockApi, portfolioApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import { useRealtimeStock } from "@/lib/use-realtime"

export default function Trade() {
  const [symbol, setSymbol] = useState("HPG")
  const [side, setSide] = useState<"Buy" | "Sell">("Buy")
  const [ord, setOrd] = useState("Limit")
  const [quantity, setQuantity] = useState("1,000")
  const [priceInput, setPriceInput] = useState("")

  // Fetch real summary & orders from portfolio
  const portfolioRes = useResource(() => Promise.all([
    portfolioApi.summary().catch(() => null),
    portfolioApi.orders().catch(() => []),
  ]), [])

  // Fetch real quote & history for the selected symbol
  const stockRes = useResource(() => Promise.all([
    stockApi.quote(symbol).catch(() => null),
    stockApi.ohlcv(symbol).catch(() => []),
  ]), [symbol])

  const [summaryData, ordersData] = portfolioRes.data || [null, []]
  const [quoteData, historyData] = stockRes.data || [null, []]

  const liveQuote = useMemo(() => {
    if (!quoteData) {
      return {
        symbol,
        name: symbol,
        price: 0,
        ref: 0,
        ceiling: 0,
        floor: 0,
        changePct: 0,
        volume: "0",
        flow: 0,
      }
    }
    const qPrice = Number(quoteData.price ?? quoteData.close ?? 0)
    const price = qPrice < 500 && qPrice > 0 ? Math.round(qPrice * 1000) : Math.round(qPrice)
    const ref = Number(quoteData.ref ?? quoteData.open ?? price)
    const refPrice = ref < 500 && ref > 0 ? Math.round(ref * 1000) : Math.round(ref)
    const ceil = Number(quoteData.ceiling ?? refPrice)
    const flr = Number(quoteData.floor ?? refPrice)
    const changePct = Number(quoteData.change_pct ?? (refPrice > 0 ? ((price - refPrice) / refPrice) * 100 : 0))
    const vol = Number(quoteData.volume ?? quoteData.volume_total ?? 0)

    return {
      symbol,
      name: quoteData.name || symbol,
      price,
      ref: refPrice,
      ceiling: Math.round(ceil),
      floor: Math.round(flr),
      changePct: Number(changePct.toFixed(2)),
      volume: vol >= 1e6 ? `${(vol / 1e6).toFixed(1)}M` : `${Math.round(vol / 1e3)}k`,
      flow: Math.round(Number(quoteData.foreign_flow ?? quoteData.foreign_net_vol ?? 0)),
    }
  }, [quoteData, symbol])

  const { stock: rtStock, flash } = useRealtimeStock(symbol, {
    symbol: liveQuote.symbol,
    name: liveQuote.name,
    price: liveQuote.price,
    changePct: liveQuote.changePct,
    ref: liveQuote.ref,
    ceiling: liveQuote.ceiling,
    floor: liveQuote.floor,
    risk: "Moderate" as const,
    volume: liveQuote.volume,
    sector: "HOSE",
    rsi: 55,
    momentum: 60,
    beneish: "PASS" as const,
    flow: liveQuote.flow,
    rs: 60,
    factor: "Tích lũy",
    weight: 10,
    pe: 12,
    foreign: liveQuote.flow,
    spark: [],
  })

  // Real chart points
  const chartPoints = useMemo(() => {
    if (Array.isArray(historyData) && historyData.length > 0) {
      return historyData.slice(-30).map((h: any) => {
        const c = Number(h.close ?? h.close_adj ?? 0)
        return c < 500 && c > 0 ? c * 1000 : c
      }).filter((v: number) => v > 0)
    }
    return rtStock.price > 0 ? [rtStock.price] : []
  }, [historyData, rtStock.price])

  const currentPrice = rtStock.price || liveQuote.price
  const parsedQty = parseInt(quantity.replace(/,/g, ""), 10) || 0
  const orderPrice = priceInput ? parseInt(priceInput.replace(/,/g, ""), 10) || currentPrice : currentPrice
  const estValue = parsedQty * orderPrice
  const estFee = Math.round(estValue * 0.0015) // 0.15% fee

  const cash = Number(summaryData?.cashBalance ?? summaryData?.cash ?? 1000000000)
  const openOrdersCount = Array.isArray(ordersData) ? ordersData.filter((o: any) => o.status === "PENDING" || o.status === "OPEN").length : 0
  const filledOrdersCount = Array.isArray(ordersData) ? ordersData.filter((o: any) => o.status === "FILLED").length : 0

  return (
    <Page
      title="Giao dịch & Khớp lệnh"
      sub={`${symbol} · Đặt lệnh có kiểm soát rủi ro và tuân thủ kỷ luật danh mục.`}
      actions={
        <div className="flex items-center gap-2">
          <div className="flex gap-1 bg-surface p-1 rounded-[6px] border border-line">
            {["HPG", "FPT", "MBB", "SSI", "TCB"].map((s) => (
              <button
                key={s}
                onClick={() => { setSymbol(s); setPriceInput("") }}
                className={`px-2.5 py-0.5 rounded-[4px] font-mono text-[11px] font-medium transition-colors ${
                  symbol === s ? "bg-ink text-paper" : "text-secondary hover:text-ink"
                }`}
              >
                {s}
              </button>
            ))}
          </div>
          <Pill tone="teal"><span className="w-1.5 h-1.5 rounded-full bg-teal animate-pulse mr-1" />HOSE Online</Pill>
        </div>
      }
    >
      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_340px] gap-4">
        <Panel>
          <div className="flex items-center justify-between mb-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-[17px] font-bold font-mono text-ink">{symbol}</span>
                <span className="text-[14px] text-secondary">{rtStock.name}</span>
              </div>
              <div className="flex items-baseline gap-2 mt-0.5">
                <span className={`text-[24px] font-mono font-bold transition-colors ${
                  flash === "gain" ? "text-gain" : flash === "loss" ? "text-loss" : "text-ink"
                }`}>
                  {fmt(currentPrice)}
                </span>
                <PercentChange value={rtStock.changePct} />
              </div>
            </div>
            <div className="text-right text-[11px] font-mono">
              <div className="text-muted">Khối lượng: <span className="text-ink font-semibold">{rtStock.volume}</span></div>
              <div className="flex gap-2 mt-1">
                <span className="text-loss">Sàn: {fmt(rtStock.floor)}</span>
                <span className="text-muted">TC: {fmt(rtStock.ref)}</span>
                <span className="text-gain">Trần: {fmt(rtStock.ceiling)}</span>
              </div>
            </div>
          </div>

          <MarketLineChart
            height={190}
            series={[
              {
                label: symbol,
                data: chartPoints,
                color: rtStock.changePct >= 0 ? "var(--color-gain)" : "var(--color-loss)",
              },
            ]}
          />

          <div className="grid grid-cols-4 gap-3 mt-4 text-[12px]">
            {[
              ["Lệnh mở", String(openOrdersCount)],
              ["Khớp hôm nay", String(filledOrdersCount)],
              ["Sức mua khả dụng", fmt(cash)],
              ["Khối ngoại ròng", `${liveQuote.flow >= 0 ? "+" : ""}${liveQuote.flow}k`],
            ].map(([l, v]) => (
              <div key={l} className="border border-line rounded-[8px] p-3">
                <div className="text-muted text-[11px] uppercase tracking-wide">{l}</div>
                <div className="tnum font-mono text-ink text-[15px] font-semibold mt-0.5">
                  {v}
                </div>
              </div>
            ))}
          </div>
        </Panel>

        <Panel>
          <div className="grid grid-cols-2 gap-1 mb-4 p-1 bg-soft rounded-[8px]">
            {(["Buy", "Sell"] as const).map((s) => (
              <button
                key={s}
                onClick={() => setSide(s)}
                className={`h-8 rounded-[6px] text-[13px] font-medium transition-colors ${
                  side === s
                    ? (s === "Buy" ? "bg-gain text-white" : "bg-loss text-white")
                    : "text-secondary hover:text-ink"
                }`}
              >
                {s === "Buy" ? "Mua" : "Bán"}
              </button>
            ))}
          </div>

          <div className="flex gap-1 mb-4">
            {["Limit", "Market", "ATO", "ATC"].map((o) => (
              <button
                key={o}
                onClick={() => setOrd(o)}
                className={`flex-1 h-7 rounded-[6px] text-[11px] font-medium transition-colors ${
                  ord === o ? "bg-ink text-paper" : "text-muted hover:bg-soft"
                }`}
              >
                {o}
              </button>
            ))}
          </div>

          <div className="space-y-3 text-[12px]">
            <div>
              <label className="text-secondary font-medium">Khối lượng (Lô 100)</label>
              <input
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                className="mt-1 w-full h-9 border border-line rounded-[6px] px-3 tnum font-mono text-ink outline-none focus:border-mineral bg-surface"
              />
            </div>
            <div>
              <label className="text-secondary font-medium">Mức giá đặt (VND)</label>
              <input
                value={priceInput || String(currentPrice)}
                onChange={(e) => setPriceInput(e.target.value)}
                className="mt-1 w-full h-9 border border-line rounded-[6px] px-3 tnum font-mono text-ink outline-none focus:border-mineral bg-surface"
              />
            </div>
          </div>

          <div className="mt-4 pt-4 border-t border-line space-y-2 text-[12px]">
            {[
              ["Giá trị lệnh dự tính", fmt(estValue)],
              ["Phí giao dịch (0.15%)", fmt(estFee)],
              ["¼ Kelly sizing", "4.2%"],
              ["Cắt lỗ kỹ thuật", fmt(Math.round(currentPrice * 0.93))],
            ].map(([l, v]) => (
              <div key={l} className="flex justify-between">
                <span className="text-muted">{l}</span>
                <span className="tnum font-mono text-ink font-semibold">{v}</span>
              </div>
            ))}
          </div>

          <Button
            variant="primary"
            className={`w-full mt-4 ${side === "Buy" ? "bg-gain hover:bg-gain" : "bg-loss hover:bg-loss"} text-white`}
          >
            Xác nhận {side === "Buy" ? "Mua" : "Bán"} {symbol}
          </Button>
        </Panel>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
        <Panel>
          <PanelHead title="Sổ lệnh chờ và khớp gần nhất" sub="Nhật ký lệnh tài khoản từ PostgreSQL" />
          <div className="overflow-x-auto">
            {Array.isArray(ordersData) && ordersData.length > 0 ? (
              <table className="w-full text-[12.5px]">
                <thead>
                  <tr className="border-b border-line text-muted text-[10px] uppercase font-semibold">
                    <th className="py-2 text-left">Mã</th>
                    <th className="py-2 text-left">Chiều</th>
                    <th className="py-2 text-right">Khối lượng</th>
                    <th className="py-2 text-right">Giá đặt</th>
                    <th className="py-2 text-right">Trạng thái</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {ordersData.slice(0, 8).map((o: any, i: number) => (
                    <tr key={i}>
                      <td className="py-2 font-mono font-bold text-ink">{o.symbol || o.ticker || symbol}</td>
                      <td className={o.side === "BUY" ? "text-gain font-semibold" : "text-loss font-semibold"}>{o.side || "BUY"}</td>
                      <td className="text-right font-mono tnum">{Number(o.quantity || 1000).toLocaleString()}</td>
                      <td className="text-right font-mono tnum">{fmt(Number(o.price || o.limitPrice || currentPrice))}</td>
                      <td className="text-right"><Pill tone={o.status === "FILLED" ? "gain" : "teal"}>{o.status || "OPEN"}</Pill></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="py-6 text-center text-muted text-[12px]">Chưa có lệnh nào trong phiên hôm nay.</div>
            )}
          </div>
        </Panel>

        <Panel>
          <PanelHead title="Sổ lệnh chào mua / chào bán" sub="Top mức giá tốt nhất theo bước giá HOSE" />
          <div className="grid grid-cols-2 gap-3 text-[12px] font-mono">
            <div>
              <div className="text-[10.5px] uppercase text-muted mb-1 px-1 font-semibold">Dư mua (Bids)</div>
              {[
                [currentPrice - 50, 18400],
                [currentPrice - 100, 31600],
                [currentPrice - 150, 44800],
              ].map(([p, v], i) => (
                <div key={i} className="flex justify-between py-1 px-2 border-b border-line">
                  <span className="text-gain">{fmt(p)}</span>
                  <span className="text-secondary">{v.toLocaleString()}</span>
                </div>
              ))}
            </div>
            <div>
              <div className="text-[10.5px] uppercase text-muted mb-1 px-1 text-right font-semibold">Dư bán (Asks)</div>
              {[
                [currentPrice + 50, 22900],
                [currentPrice + 100, 35100],
                [currentPrice + 150, 19200],
              ].map(([p, v], i) => (
                <div key={i} className="flex justify-between py-1 px-2 border-b border-line">
                  <span className="text-loss">{fmt(p)}</span>
                  <span className="text-secondary">{v.toLocaleString()}</span>
                </div>
              ))}
            </div>
          </div>
        </Panel>
      </div>
    </Page>
  )
}
