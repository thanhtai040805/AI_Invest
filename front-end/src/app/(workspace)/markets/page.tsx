"use client"

import { useMemo } from "react"
import { Page } from "@/components/Shell"
import { Link } from "@/lib/router"
import type { Stock } from "@/types"
import {
  MetricStrip,
  Panel,
  PanelHead,
  PercentChange,
  fmt,
} from "@/components/ui"
import { marketApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import { useRealtimeMarket, useRealtimeMarketOrderBooks } from "@/lib/use-realtime"
import type { ApiMarketIndex, ApiMarketSnapshot, ApiMarketStock } from "@/types"

interface ApiIndices { indices?: ApiMarketIndex[] }
interface ApiSnapshot extends ApiMarketSnapshot { source?: string; stale?: boolean; asOf?: string; foreignFlowAsOf?: string | null }
interface ApiOrderBookLevel { price: number; volume: number }
interface ApiOrderBook { symbol?: string; bids: ApiOrderBookLevel[]; asks: ApiOrderBookLevel[]; receivedAt?: number; stale?: boolean; lastUpdate?: string; source?: string }
interface ApiOrderBooks { orderbooks?: Record<string, ApiOrderBook> }
interface ApiLiquidity { totalValueBillion?: number | null; approximate?: boolean; stale?: boolean; source?: string; lastUpdate?: string; asOf?: string }
type MarketRow = Stock & { foreignKnown: boolean; momentumKnown: boolean; source?: string; stale?: boolean }

export default function Markets() {
  const resource = useResource(() => Promise.all([
    marketApi.indices().catch(() => null),
    marketApi.snapshot().catch(() => null),
    marketApi.liquidity().catch(() => null),
    marketApi.orderbooks().catch(() => null),
  ]), [])

  const { snapshot: liveSnapshot, indices: liveIndices, isLive, liquidity: liveLiquidity } = useRealtimeMarket()
  const { indicesData: initialIndices, stockList } = useMemo(() => {
    const [indicesRes, snapshotRes] = (resource.data || []) as [ApiIndices | null, ApiSnapshot | null, ApiLiquidity | null, ApiOrderBooks | null]
    const indicesList = Array.isArray(indicesRes?.indices) ? indicesRes.indices : []
    const rawStocks = Array.isArray(liveSnapshot?.stocks) && liveSnapshot.stocks.length
      ? liveSnapshot.stocks : Array.isArray(snapshotRes?.stocks) ? snapshotRes.stocks : []
    const databaseStocks = new Map((snapshotRes?.stocks ?? []).map((stock) => [String(stock.symbol).toUpperCase(), stock]))

    const indexName = (item: ApiMarketIndex) => String(item.symbol ?? item.name ?? "").toUpperCase().replaceAll("-", "")
    const vnIndexItem = indicesList.find((x) => indexName(x) === "VNINDEX")

    const indexValue = (item?: ApiMarketIndex) => {
      if (item?.value == null) return "—"
      const value = Number(item?.value)
      return Number.isFinite(value) && value > 0 ? value.toLocaleString("vi-VN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—"
    }
    const indexChange = (item?: ApiMarketIndex) => {
      const raw = item?.changePercent ?? item?.change_pct
      const value = raw == null ? NaN : Number(raw)
      return Number.isFinite(value) ? value : null
    }
    const indicesData = {
      vnIndexVal: indexValue(vnIndexItem),
      vnIndexPct: indexChange(vnIndexItem),
    }

    if (!rawStocks.length) {
      return { indicesData, stockList: [] }
    }

    const liveStocks: MarketRow[] = (rawStocks as ApiMarketStock[]).map((r) => {
      const databaseStock = databaseStocks.get(String(r.symbol).toUpperCase())
      const volNum = r.volume == null ? null : Number(r.volume)
      const volStr = volNum === null || !Number.isFinite(volNum) ? "—" : volNum >= 1e6 ? `${(volNum / 1e6).toFixed(1)}M` : volNum >= 1e3 ? `${(volNum / 1e3).toFixed(0)}k` : fmt(volNum)
      const rawChange = r.changePercent ?? r.change_pct
      const changeValue = rawChange == null ? NaN : Number(rawChange)
      const rawForeign = r.foreign_flow ?? r.foreignFlow ?? databaseStock?.foreign_flow ?? databaseStock?.foreignFlow
      const foreignValue = rawForeign == null ? NaN : Number(rawForeign)
      const momentumValue = r.momentum == null ? NaN : Number(r.momentum)
      const hasChange = Number.isFinite(changeValue)
      const hasForeignFlow = Number.isFinite(foreignValue)
      const hasMomentum = Number.isFinite(momentumValue)
      const isSocketSnapshot = Array.isArray(liveSnapshot?.stocks) && liveSnapshot.stocks.length > 0

      return {
        symbol: String(r.symbol),
        name: String(r.name || r.symbol),
        sector: String(r.industry || "—"),
        price: Number(r.price ?? 0),
        changePct: hasChange ? Number(changeValue.toFixed(2)) : null,
        ref: r.ref == null || !Number.isFinite(Number(r.ref)) ? null : Number(r.ref),
        ceiling: Number(r.ceiling ?? 0),
        floor: Number(r.floor ?? 0),
        volume: volStr,
        foreign: hasForeignFlow ? foreignValue : 0,
        foreignKnown: hasForeignFlow,
        momentumKnown: hasMomentum,
        source: r.source ?? (isSocketSnapshot ? undefined : snapshotRes?.source),
        stale: r.stale ?? (isSocketSnapshot ? undefined : snapshotRes?.stale),
        weight: 0,
        momentum: hasMomentum ? Math.round(momentumValue) : 0,
        rs: Math.round(Number(r.rs ?? 0)),
        flow: hasForeignFlow ? Math.round(foreignValue) : 0,
        factor: "",
        risk: "Unknown",
        beneish: "UNKNOWN",
        spark: [],
      }
    })

    return { indicesData, stockList: liveStocks }
  }, [resource.data, liveSnapshot])

  const currentIndices = liveIndices || initialIndices
  const foreignBillion = stockList.reduce((sum, stock) => sum + (stock.foreignKnown ? stock.foreign : 0), 0)
  const foreignKnownCount = stockList.filter((stock) => stock.foreignKnown).length
  const hasForeign = foreignKnownCount > 0
  const [, snapshotRes, liquidityRes, orderbooksRes] = (resource.data || []) as [ApiIndices | null, ApiSnapshot | null, ApiLiquidity | null, ApiOrderBooks | null]
  const liveLiquidityAt = Date.parse(String(liveLiquidity?.lastUpdate ?? ""))
  const liquidity = (liveLiquidityAt > 0 && Date.now() - liveLiquidityAt < 15_000 ? liveLiquidity : liquidityRes) as ApiLiquidity | null
  const liquidityBillion = Number(liquidity?.totalValueBillion ?? 0)
  const initialOrderbooks = useMemo(() => orderbooksRes?.orderbooks ?? {}, [orderbooksRes])
  const orderbooks = useRealtimeMarketOrderBooks(initialOrderbooks)
  const foreignAsOf = snapshotRes?.foreignFlowAsOf
    ? new Date(snapshotRes.foreignFlowAsOf).toLocaleDateString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" })
    : null

  return (
    <Page
      title="Bảng giá trực tuyến"
      sub="HOSE · Giá, biên độ, thanh khoản, khối ngoại và giá Bid/Ask trong thời gian dữ liệu còn trực tiếp."
      actions={
        isLive ? (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-[12px] font-medium bg-emerald-500/10 text-emerald-500 border border-emerald-500/20">
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
            TRỰC TIẾP
          </span>
        ) : undefined
      }
    >
      <MetricStrip
        items={[
          {
            label: "VN-Index",
            value: currentIndices.vnIndexVal,
            sub: currentIndices.vnIndexVal !== "—" && currentIndices.vnIndexPct != null ? <PercentChange value={currentIndices.vnIndexPct} arrow={false} /> : "—",
          },
          { label: "Thanh khoản", value: liquidityBillion > 0 ? `${(liquidityBillion / 1000).toFixed(1)}T` : "—", sub: liquidity?.approximate ? `Ước tính theo giá đóng cửa${liquidity.asOf ? ` · ${new Date(liquidity.asOf).toLocaleDateString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" })}` : ""}` : liquidityBillion > 0 ? "DNSE trực tiếp" : "Chưa có dữ liệu" },
          {
            label: "Khối ngoại",
            value: hasForeign ? <span className={foreignBillion >= 0 ? "text-gain" : "text-loss"}>{foreignBillion >= 0 ? "+" : ""}{foreignBillion.toFixed(0)}B</span> : "—",
            sub: `${foreignKnownCount}/${stockList.length} mã có dữ liệu${foreignAsOf ? ` · ${foreignAsOf}` : ""}`,
          },
        ]}
      />
      <Panel flush className="mt-4">
        <div className="p-5 pb-2">
          <PanelHead
            title="Bảng theo dõi sàn HOSE"
            sub={`Giá tính bằng VNĐ · Khối ngoại tính bằng tỷ VNĐ · Bid/Ask hiển thị snapshot gần nhất, dữ liệu cũ được đánh dấu · ${liveSnapshot ? `${liveSnapshot.liveSymbols ?? stockList.filter(stock => stock.source === "dnse-ws").length}/${liveSnapshot.total ?? stockList.length} mã có tick trong snapshot` : stockList.length ? `${stockList.length} mã từ snapshot gần nhất` : "Chưa có snapshot dữ liệu"}${snapshotRes?.stale && !liveSnapshot ? ` · dữ liệu cuối ngày${snapshotRes.asOf ? ` ${new Date(snapshotRes.asOf).toLocaleDateString("vi-VN")}` : ""}` : ""}`}
          />
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-[13px] min-w-[1220px]">
            <thead>
              <tr className="text-[10px] uppercase tracking-wide text-muted border-y border-line">
                {[
                  "Mã CP",
                  "Doanh nghiệp",
                  "Trần",
                  "Sàn",
                  "Tham chiếu",
                  "Khớp lệnh",
                  "Bid · giá / KL",
                  "Ask · giá / KL",
                  "Biến động",
                  "Khối lượng",
                  "Khối ngoại",
                  "Xung lực",
                ].map((h, i) => (
                  <th
                    key={h}
                    className={`font-medium py-2.5 ${i >= 2 ? "text-right px-3" : "text-left px-3"} ${i === 0 ? "pl-5" : ""}`}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {!stockList.length && <tr><td colSpan={12} className="px-5 py-10 text-center text-sm text-secondary">{resource.loading ? "Đang tải dữ liệu thị trường…" : "Chưa có dữ liệu bảng giá cho snapshot này."}</td></tr>}
              {stockList.map((s) => {
                const book = orderbooks[s.symbol]
                const bid = book?.bids?.[0]
                const ask = book?.asks?.[0]
                return <tr
                  key={s.symbol}
                  className="hover:bg-soft/50 transition-colors"
                >
                  <td className="py-2.5 pl-5">
                    <Link
                      to={`/stock/${s.symbol}`}
                      className="font-mono font-semibold text-ink hover:underline"
                    >
                      {s.symbol}
                    </Link>
                    {s.source === "dnse-ws" ? <span className="ml-1 text-[10px] font-normal text-gain">· tick</span> : s.stale || s.source === "postgres" ? <span className="ml-1 text-[10px] font-normal text-muted" title="Giá đóng cửa trong cơ sở dữ liệu">· cuối ngày</span> : <span className="ml-1 text-[10px] font-normal text-muted" title="Nguồn của hàng giá chưa được xác nhận">· nguồn chưa rõ</span>}
                  </td>
                  <td className="px-3 text-secondary">{s.name}</td>
                  <td className="px-3 text-right font-mono text-[12px] text-gold">
                    {s.ceiling > 0 ? fmt(s.ceiling) : "—"}
                  </td>
                  <td className="px-3 text-right font-mono text-[12px] text-mineral">
                    {s.floor > 0 ? fmt(s.floor) : "—"}
                  </td>
                  <td className="px-3 text-right font-mono text-[12px] text-secondary">
                    {s.ref != null && s.ref > 0 ? fmt(s.ref) : "—"}
                  </td>
                  <td className="px-3 text-right tnum font-mono font-semibold text-ink">
                    {s.price > 0 ? fmt(s.price) : "—"}
                  </td>
                  <td className="px-3 text-right font-mono text-[11px]">
                    {bid ? <><span className={book?.stale ? "text-muted" : "text-gain"}>{fmt(bid.price)}</span><span className="ml-1 text-muted">{fmt(bid.volume)}</span>{book?.stale && <span className="ml-1 text-amber-700" title={book.receivedAt > 0 ? `Orderbook cũ · nhận lúc ${new Date(book.receivedAt).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" })}` : "Orderbook cũ · không rõ thời điểm"}>· cũ</span>}</> : "—"}
                  </td>
                  <td className="px-3 text-right font-mono text-[11px]">
                    {ask ? <><span className={book?.stale ? "text-muted" : "text-loss"}>{fmt(ask.price)}</span><span className="ml-1 text-muted">{fmt(ask.volume)}</span>{book?.stale && <span className="ml-1 text-amber-700" title={book.receivedAt > 0 ? `Orderbook cũ · nhận lúc ${new Date(book.receivedAt).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" })}` : "Orderbook cũ · không rõ thời điểm"}>· cũ</span>}</> : "—"}
                  </td>
                  <td className="px-3 text-right">
                    {s.changePct != null && Number.isFinite(s.changePct) ? <><PercentChange value={s.changePct} arrow={false} />{Math.abs(s.changePct) >= 20 && <span className="ml-1 block text-[10px] text-warning" title="Đối chiếu giá tham chiếu và sự kiện doanh nghiệp trước khi diễn giải">Biến động lớn · cần đối chiếu</span>}</> : "—"}
                  </td>
                  <td className="px-3 text-right tnum font-mono text-secondary">
                    {s.volume}
                  </td>
                  <td
                    className={`px-3 text-right tnum font-mono ${s.foreign >= 0 ? "text-gain" : "text-loss"}`}
                  >
                    {s.foreignKnown ? `${s.foreign >= 0 ? "+" : ""}${s.foreign.toFixed(1)}B` : "—"}
                  </td>
                  <td className="px-3 text-right tnum font-mono text-ink">
                    {s.momentumKnown ? s.momentum : "—"}
                  </td>
                </tr>
              })}
            </tbody>
          </table>
        </div>
      </Panel>
    </Page>
  )
}
