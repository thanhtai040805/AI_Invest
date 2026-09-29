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
import { useRealtimeMarket } from "@/lib/use-realtime"
import type { ApiMarketIndex, ApiMarketSnapshot, ApiMarketStock } from "@/types"

interface ApiIndices { indices?: ApiMarketIndex[] }
type MarketRow = Stock & { foreignKnown: boolean; changeKnown: boolean; momentumKnown: boolean; source?: string }

export default function Markets() {
  const resource = useResource(() => Promise.all([
    marketApi.indices().catch(() => null),
    marketApi.snapshot().catch(() => null),
  ]), [])

  const { snapshot: liveSnapshot, indices: liveIndices, isLive, liquidity: liveLiquidity } = useRealtimeMarket()
  const { indicesData: initialIndices, stockList } = useMemo(() => {
    const [indicesRes, snapshotRes] = (resource.data || []) as [ApiIndices | null, ApiMarketSnapshot | null]
    const indicesList = Array.isArray(indicesRes?.indices) ? indicesRes.indices : []
    const rawStocks = Array.isArray(liveSnapshot?.stocks) && liveSnapshot.stocks.length
      ? liveSnapshot.stocks : Array.isArray(snapshotRes?.stocks) ? snapshotRes.stocks : []

    const indexName = (item: ApiMarketIndex) => String(item.symbol ?? item.name ?? "").toUpperCase().replaceAll("-", "")
    const vnIndexItem = indicesList.find((x) => indexName(x) === "VNINDEX")
    const vn100Item = indicesList.find((x) => indexName(x) === "VN100")

    const indicesData = {
      vnIndexVal: vnIndexItem ? Number(vnIndexItem.value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—",
      vnIndexPct: vnIndexItem ? Number(vnIndexItem.changePercent ?? vnIndexItem.change_pct ?? 0) : 0,
      vn100Val: vn100Item ? Number(vn100Item.value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—",
      vn100Pct: vn100Item ? Number(vn100Item.changePercent ?? vn100Item.change_pct ?? 0) : 0,
    }

    if (!rawStocks.length) {
      return { indicesData, stockList: [] }
    }

    const liveStocks: MarketRow[] = (rawStocks as ApiMarketStock[]).map((r) => {
      const volNum = Number(r.volume ?? 0)
      const volStr = volNum >= 1e6 ? `${(volNum / 1e6).toFixed(1)}M` : `${(volNum / 1e3).toFixed(0)}k`

      return {
        symbol: String(r.symbol),
        name: String(r.name || r.symbol),
        sector: String(r.industry || "HOSE"),
        price: Number(r.price ?? 0),
        changePct: Number(Number(r.change_pct ?? 0).toFixed(2)),
        ref: Number(r.ref ?? r.price ?? 0),
        ceiling: Number(r.ceiling ?? 0),
        floor: Number(r.floor ?? 0),
        volume: volStr,
        foreign: Number(r.foreign_flow ?? 0),
        foreignKnown: r.foreign_flow != null,
        changeKnown: r.change_pct != null,
        momentumKnown: r.momentum != null,
        source: r.source,
        weight: 0,
        momentum: Math.round(Number(r.momentum ?? 0)),
        rs: Math.round(Number(r.rs ?? 0)),
        flow: Math.round(Number(r.foreign_flow ?? 0)),
        factor: "",
        risk: "Low",
        beneish: "PASS",
        spark: [],
      }
    })

    return { indicesData, stockList: liveStocks }
  }, [resource.data, liveSnapshot])

  const currentIndices = liveIndices || initialIndices
  const foreignBillion = stockList.reduce((sum, stock) => sum + stock.foreign, 0)
  const hasForeign = stockList.some((stock) => stock.foreignKnown)
  const liquidityBillion = Number(liveLiquidity?.totalValueBillion ?? 0)

  return (
    <Page
      title="Bảng giá trực tuyến"
      sub="HOSE · Giá khớp thời gian thực, biên độ trần/sàn, thanh khoản và khối ngoại."
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
            sub: currentIndices.vnIndexVal !== "—" ? <PercentChange value={currentIndices.vnIndexPct} arrow={false} /> : "—",
          },
          {
            label: "VN100",
            value: currentIndices.vn100Val ?? "—",
            sub: currentIndices.vn100Val && currentIndices.vn100Val !== "—" ? <PercentChange value={currentIndices.vn100Pct ?? 0} arrow={false} /> : "—",
          },
          { label: "Thanh khoản", value: liquidityBillion > 0 ? `${(liquidityBillion / 1000).toFixed(1)}T` : "—" },
          {
            label: "Khối ngoại",
            value: hasForeign ? <span className={foreignBillion >= 0 ? "text-gain" : "text-loss"}>{foreignBillion >= 0 ? "+" : ""}{foreignBillion.toFixed(0)}B</span> : "—",
          },
        ]}
      />
      <Panel flush className="mt-4">
        <div className="p-5 pb-2">
          <PanelHead
            title="Bảng theo dõi sàn HOSE"
            sub={`Giá tính bằng VNĐ · Khối ngoại tính bằng tỷ VNĐ · ${liveSnapshot?.liveSymbols ?? 0}/${liveSnapshot?.total ?? stockList.length} mã đã nhận tick phiên này`}
          />
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-[13px] min-w-[1000px]">
            <thead>
              <tr className="text-[10px] uppercase tracking-wide text-muted border-y border-line">
                {[
                  "Mã CP",
                  "Doanh nghiệp",
                  "Trần",
                  "Sàn",
                  "Tham chiếu",
                  "Khớp lệnh",
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
              {stockList.map((s) => (
                <tr
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
                    {s.source !== "dnse-ws" && <span className="ml-1 text-[10px] font-normal text-muted" title="Chưa nhận tick DNSE trong phiên">· chờ tick</span>}
                  </td>
                  <td className="px-3 text-secondary">{s.name}</td>
                  <td className="px-3 text-right font-mono text-[12px] text-gold">
                    {s.ceiling > 0 ? fmt(s.ceiling) : "—"}
                  </td>
                  <td className="px-3 text-right font-mono text-[12px] text-mineral">
                    {s.floor > 0 ? fmt(s.floor) : "—"}
                  </td>
                  <td className="px-3 text-right font-mono text-[12px] text-secondary">
                    {s.ref > 0 ? fmt(s.ref) : "—"}
                  </td>
                  <td className="px-3 text-right tnum font-mono font-semibold text-ink">
                    {s.price > 0 ? fmt(s.price) : "—"}
                  </td>
                  <td className="px-3 text-right">
                    {s.changeKnown ? <PercentChange value={s.changePct} arrow={false} /> : "—"}
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
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </Page>
  )
}
