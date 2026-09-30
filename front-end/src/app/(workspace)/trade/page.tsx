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
import type { ApiProblem } from "@/lib/api/client"

type ShadowOrder = { id: string; symbol: string; side: "BUY" | "SELL"; quantity: number; price: string | number | null; status: string; createdAt: string }

export default function Trade() {
  const [symbol, setSymbol] = useState("HPG")
  const [side, setSide] = useState<"Buy" | "Sell">("Buy")
  const [ord, setOrd] = useState("Limit")
  const [quantity, setQuantity] = useState("100")
  const [priceInput, setPriceInput] = useState("")
  const [orderPending, setOrderPending] = useState(false)
  const [orderMessage, setOrderMessage] = useState("")

  // Fetch real summary & orders from portfolio
  const portfolioRes = useResource(() => Promise.allSettled([
    portfolioApi.summary(),
    portfolioApi.orders(),
  ]), [])

  // Fetch real quote & history for the selected symbol
  const stockRes = useResource(() => Promise.all([
    stockApi.quote(symbol).catch(() => null),
    stockApi.ohlcv(symbol).catch(() => []),
    stockApi.orderbook(symbol).catch(() => null),
  ]), [symbol])

  const [summaryResult, ordersResult] = portfolioRes.data || []
  const summaryData = summaryResult?.status === "fulfilled" ? summaryResult.value : null
  const ordersData = ordersResult?.status === "fulfilled" && Array.isArray(ordersResult.value) ? ordersResult.value : null
  const [quoteData, historyData, loadedBookData] = stockRes.data || [null, [], null]
  const bookData = stockRes.loading ? null : loadedBookData

  const liveQuote = useMemo(() => {
    if (!quoteData) {
      return {
        symbol,
        name: symbol,
        price: 0,
        ref: null,
        ceiling: 0,
        floor: 0,
        changePct: null,
        volume: "—",
        flow: 0,
        hasFlow: false,
      }
    }
    const qPrice = Number(quoteData.price ?? quoteData.close ?? 0)
    const price = qPrice < 500 && qPrice > 0 ? Math.round(qPrice * 1000) : Math.round(qPrice)
    const rawRef = quoteData.ref ?? quoteData.prior_close
    const ref = rawRef == null ? null : Number(rawRef)
    const refPrice = ref == null || !Number.isFinite(ref) ? null : ref < 500 && ref > 0 ? Math.round(ref * 1000) : Math.round(ref)
    const normalizePrice = (value: unknown) => {
      const parsed = Number(value)
      return parsed > 0 && parsed < 500 ? Math.round(parsed * 1000) : Math.round(parsed)
    }
    const ceil = normalizePrice(quoteData.ceiling ?? 0)
    const flr = normalizePrice(quoteData.floor ?? 0)
    const rawChange = quoteData.changePct ?? quoteData.change_pct ?? quoteData.change_percent
    const changePct = rawChange == null ? null : Number(rawChange)
    const rawVolume = quoteData.volume ?? quoteData.volume_total
    const vol = rawVolume == null ? null : Number(rawVolume)
    const rawFlow = quoteData.foreign_flow ?? quoteData.foreign_net_vol
    const flow = rawFlow == null ? NaN : Number(rawFlow)

    return {
      symbol,
      name: quoteData.name || symbol,
      price,
      ref: refPrice,
      ceiling: ceil,
      floor: flr,
      changePct: changePct === null || !Number.isFinite(changePct) ? null : Number(changePct.toFixed(2)),
      volume: vol === null || !Number.isFinite(vol) ? "—" : vol >= 1e6 ? `${(vol / 1e6).toFixed(1)}M` : vol >= 1e3 ? `${(vol / 1e3).toFixed(0)}k` : fmt(vol),
      flow: Number.isFinite(flow) ? Math.round(flow) : 0,
      hasFlow: Number.isFinite(flow),
    }
  }, [quoteData, symbol])

  const initialStock = useMemo(() => ({
    symbol: liveQuote.symbol,
    name: liveQuote.name,
    price: liveQuote.price,
    changePct: liveQuote.changePct,
    ref: liveQuote.ref,
    ceiling: liveQuote.ceiling,
    floor: liveQuote.floor,
    risk: "Unknown" as const,
    volume: liveQuote.volume,
    sector: "HOSE",
    rsi: 0,
    momentum: 0,
    beneish: "UNKNOWN" as const,
    flow: liveQuote.flow,
    rs: 0,
    factor: "",
    weight: 0,
    pe: 0,
    foreign: liveQuote.flow,
    spark: [],
  }), [liveQuote])

  const { stock: rawStock, flash, isLive } = useRealtimeStock(symbol, initialStock)
  const rtStock = rawStock || initialStock
  const rawChange = quoteData?.changePct ?? quoteData?.change_pct ?? quoteData?.change_percent
  const quoteChange = rawChange == null ? NaN : Number(rawChange)

  // Real chart points
  const chartPoints = useMemo(() => {
    if (Array.isArray(historyData) && historyData.length > 0) {
      const valid = historyData.slice(-30).map((h: {close?: number; close_adj?: number}) => {
        const c = Number(h.close ?? h.close_adj ?? 0)
        return c < 500 && c > 0 ? c * 1000 : c
      }).filter((v: number) => Number.isFinite(v) && v > 0)
      if (valid.length > 0) return valid
    }
    return []
  }, [historyData])

  const chartLabels = useMemo<[string, string, string] | undefined>(() => {
    const rows = Array.isArray(historyData) ? (historyData.slice(-30) as { date?: string; time?: string; close?: number; close_adj?: number }[]).filter(row => {
      const close = Number(row.close ?? row.close_adj ?? 0)
      return Number.isFinite(close) && close > 0
    }) : []
    if (rows.length < 2) return undefined
    const label = (row: { date?: string; time?: string }) => {
      const date = new Date(String(row.date ?? row.time ?? ""))
      return Number.isFinite(date.getTime()) ? date.toLocaleDateString("vi-VN", { day: "2-digit", month: "short" }) : "—"
    }
    return [label(rows[0]), label(rows[Math.floor((rows.length - 1) / 2)]), label(rows[rows.length - 1])]
  }, [historyData])

  const currentPrice = rtStock.price || liveQuote.price
  const quoteAvailable = currentPrice > 0
  const liveChange = isLive && rtStock.price > 0 && rtStock.ref != null && rtStock.ref > 0 ? ((rtStock.price - rtStock.ref) / rtStock.ref) * 100 : null
  const shownChange = Number.isFinite(quoteChange) ? quoteChange : liveChange
  const hasChangePct = shownChange !== null
  const parsedQty = Number(quantity.replace(/,/g, ""))
  const orderPrice = priceInput ? Number(priceInput.replace(/,/g, "")) : currentPrice
  const estimateAvailable = quoteAvailable && Number.isInteger(parsedQty) && parsedQty > 0 && Number.isFinite(orderPrice) && orderPrice > 0
  const estValue = estimateAvailable ? parsedQty * orderPrice : null
  const estFee = estValue === null ? null : Math.max(Math.round(estValue * 0.001), 10000)

  const rawCash = summaryData?.cashBalance ?? summaryData?.cash
  const parsedCash = rawCash == null ? NaN : Number(rawCash)
  const cash = Number.isFinite(parsedCash) ? parsedCash : null
  const orders = (ordersData ?? []) as ShadowOrder[]
  const openOrdersCount = ordersData === null ? null : orders.filter((o) => o.status === "PENDING" || o.status === "OPEN").length
  const filledOrdersCount = ordersData === null ? null : orders.filter((o) => o.status === "FILLED" && new Date(o.createdAt).toDateString() === new Date().toDateString()).length

  async function submitOrder() {
    setOrderMessage("")
    if (!Number.isInteger(parsedQty) || parsedQty < 100 || parsedQty % 100 !== 0 || parsedQty > 500000) {
      setOrderMessage("Khối lượng phải là lô 100, tối đa 500.000 cổ phiếu.")
      return
    }
    if (ord === "Limit" && (!Number.isFinite(orderPrice) || orderPrice <= 0)) {
      setOrderMessage("Cần nhập giá LO hợp lệ.")
      return
    }
    setOrderPending(true)
    try {
      const result = await portfolioApi.order({
        symbol, side: side.toUpperCase(), orderType: ord === "Limit" ? "LO" : ord === "Market" ? "MP" : ord,
        quantity: parsedQty, ...(ord === "Limit" ? { price: orderPrice } : {}),
      })
      setOrderMessage(`Lệnh ảo ${result.status}: ${fmt(Number(result.price))} VND/cp.`)
      await portfolioRes.reload()
    } catch (error) {
      setOrderMessage((error as ApiProblem).message || "Không thể gửi lệnh ảo.")
    } finally {
      setOrderPending(false)
    }
  }

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
          <Pill tone="teal">Shadow · không gửi lệnh thật</Pill>
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
                  {quoteAvailable ? fmt(currentPrice) : "—"}
                </span>
                {!hasChangePct ? <span className="text-[12px] text-muted">Chưa có biến động</span> : <PercentChange value={shownChange ?? 0} />}
              </div>
            </div>
            <div className="text-right text-[11px] font-mono">
              <div className="text-muted">Khối lượng: <span className="text-ink font-semibold">{rtStock.volume}</span></div>
              <div className="flex gap-2 mt-1">
                <span className="text-loss">Sàn: {rtStock.floor > 0 ? fmt(rtStock.floor) : "—"}</span>
                <span className="text-muted">TC: {rtStock.ref != null && rtStock.ref > 0 ? fmt(rtStock.ref) : "—"}</span>
                <span className="text-gain">Trần: {rtStock.ceiling > 0 ? fmt(rtStock.ceiling) : "—"}</span>
              </div>
            </div>
          </div>

          <MarketLineChart
            height={190}
            series={[
              {
                label: symbol,
                data: chartPoints,
                color: !hasChangePct ? "var(--color-muted)" : Number(shownChange) >= 0 ? "var(--color-gain)" : "var(--color-loss)",
              },
            ]}
            xLabels={chartLabels}
          />

          <div className="grid grid-cols-4 gap-3 mt-4 text-[12px]">
            {[
              ["Lệnh mở", openOrdersCount === null ? "—" : String(openOrdersCount)],
              ["Khớp hôm nay", filledOrdersCount === null ? "—" : String(filledOrdersCount)],
              ["Sức mua khả dụng", cash === null ? "—" : fmt(cash)],
              ["Khối ngoại ròng", liveQuote.hasFlow ? `${liveQuote.flow >= 0 ? "+" : ""}${liveQuote.flow.toLocaleString("vi-VN")}` : "—"],
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
              value={priceInput || (currentPrice > 0 ? String(currentPrice) : "")}
                onChange={(e) => setPriceInput(e.target.value)}
                className="mt-1 w-full h-9 border border-line rounded-[6px] px-3 tnum font-mono text-ink outline-none focus:border-mineral bg-surface"
              />
            </div>
          </div>

          <div className="mt-4 pt-4 border-t border-line space-y-2 text-[12px]">
            {[
              ["Giá trị lệnh dự tính", estValue === null ? "—" : fmt(estValue)],
              ["Phí giao dịch ước tính (0,10%, tối thiểu 10.000đ)", estFee === null ? "—" : fmt(estFee)],
              ["Thuế bán ước tính (0,10%)", side === "Sell" && estValue !== null ? fmt(Math.round(estValue * 0.001)) : "—"],
            ].map(([l, v]) => (
              <div key={l} className="flex justify-between">
                <span className="text-muted">{l}</span>
                <span className="tnum font-mono text-ink font-semibold">{v}</span>
              </div>
            ))}
          </div>

          <Button
            variant="primary"
            onClick={() => void submitOrder()}
            disabled={orderPending || !quoteAvailable || ord === "ATO" || ord === "ATC"}
            className={`w-full mt-4 ${side === "Buy" ? "bg-gain hover:bg-gain" : "bg-loss hover:bg-loss"} text-white`}
          >
            Xác nhận {side === "Buy" ? "Mua" : "Bán"} {symbol}
          </Button>
          {!quoteAvailable && <p className="mt-2 text-[12px] text-warning">Chưa có giá hợp lệ cho {symbol}; tải lại dữ liệu trước khi đặt lệnh.</p>}
          {orderMessage && <p role="status" className="mt-2 text-[12px] text-secondary">{orderMessage}</p>}
          {(ord === "ATO" || ord === "ATC") && <p className="mt-2 text-[12px] text-muted">Chưa hỗ trợ mô phỏng khớp lệnh định kỳ.</p>}
        </Panel>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
        <Panel>
          <PanelHead title="Sổ lệnh chờ và khớp gần nhất" sub="Nhật ký lệnh tài khoản từ PostgreSQL" />
          <div className="overflow-x-auto">
            {ordersData === null ? (
              <div className="py-6 text-center text-muted text-[12px]">Không thể tải lịch sử lệnh.</div>
            ) : orders.length > 0 ? (
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
                  {orders.slice(0, 8).map((o) => (
                    <tr key={o.id}>
                      <td className="py-2 font-mono font-bold text-ink">{o.symbol}</td>
                      <td className={o.side === "BUY" ? "text-gain font-semibold" : "text-loss font-semibold"}>{o.side || "BUY"}</td>
                      <td className="text-right font-mono tnum">{Number(o.quantity).toLocaleString("vi-VN")}</td>
                      <td className="text-right font-mono tnum">{fmt(Number(o.price))}</td>
                      <td className="text-right"><Pill tone={o.status === "FILLED" ? "gain" : "teal"}>{o.status}</Pill></td>
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
          <PanelHead title="Sổ lệnh chào mua / chào bán" sub={`${bookData?.lastUpdate ? `Feed ${new Date(bookData.lastUpdate).toLocaleString("vi-VN")}` : "Chưa có sổ lệnh từ nguồn dữ liệu"}; giá khớp cần dữ liệu dưới 10 giây`} action={<Button variant="quiet" onClick={() => void stockRes.reload()}>Làm mới</Button>} />
          <div className="grid grid-cols-2 gap-3 text-[12px] font-mono">
            <div>
              <div className="text-[10.5px] uppercase text-muted mb-1 px-1 font-semibold">Dư mua (Bids)</div>
              {(bookData?.bids ?? []).slice(0, 3).map((level: {price: number; volume: number}, i: number) => (
                <div key={i} className="flex justify-between py-1 px-2 border-b border-line">
                  <span className="text-gain">{fmt(level.price < 500 ? level.price * 1000 : level.price)}</span>
                  <span className="text-secondary">{level.volume.toLocaleString("vi-VN")}</span>
                </div>
              ))}
              {!bookData?.bids?.length && <p className="px-2 py-2 text-[11px] text-muted">Không có mức dư mua.</p>}
            </div>
            <div>
              <div className="text-[10.5px] uppercase text-muted mb-1 px-1 text-right font-semibold">Dư bán (Asks)</div>
              {(bookData?.asks ?? []).slice(0, 3).map((level: {price: number; volume: number}, i: number) => (
                <div key={i} className="flex justify-between py-1 px-2 border-b border-line">
                  <span className="text-loss">{fmt(level.price < 500 ? level.price * 1000 : level.price)}</span>
                  <span className="text-secondary">{level.volume.toLocaleString("vi-VN")}</span>
                </div>
              ))}
              {!bookData?.asks?.length && <p className="px-2 py-2 text-right text-[11px] text-muted">Không có mức dư bán.</p>}
            </div>
          </div>
        </Panel>
      </div>
    </Page>
  )
}
