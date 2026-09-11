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

export default function Markets() {
  const resource = useResource(() => Promise.all([
    marketApi.indices().catch(() => null),
    marketApi.snapshot().catch(() => null),
  ]), [])

  const { indicesData: initialIndices, stockList } = useMemo(() => {
    const [indicesRes, snapshotRes] = (resource.data || []) as [any, any]
    const indicesList = Array.isArray(indicesRes?.indices) ? indicesRes.indices : []
    const rawStocks = Array.isArray(snapshotRes?.stocks) ? snapshotRes.stocks : []

    const vnIndexItem = indicesList.find((x: any) => String(x.symbol).includes("VNINDEX") || String(x.symbol).includes("VN-INDEX"))
    const vn30Item = indicesList.find((x: any) => String(x.symbol).includes("VN30"))

    const indicesData = {
      vnIndexVal: vnIndexItem ? Number(vnIndexItem.value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "1,284.32",
      vnIndexPct: vnIndexItem ? Number(vnIndexItem.change_pct) : 0.72,
      vn30Val: vn30Item ? Number(vn30Item.value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "1,351.27",
      vn30Pct: vn30Item ? Number(vn30Item.change_pct) : 0.48,
    }

    if (!rawStocks.length) {
      return { indicesData, stockList: [] }
    }

    const liveStocks: Stock[] = rawStocks.slice(0, 30).map((r: any) => {
      const volNum = Number(r.volume ?? 0)
      const volStr = volNum >= 1e6 ? `${(volNum / 1e6).toFixed(1)}M` : `${(volNum / 1e3).toFixed(0)}k`

      return {
        symbol: String(r.symbol),
        name: String(r.name || r.symbol),
        sector: "Market",
        price: Number(r.price ?? 0),
        changePct: Number(Number(r.change_pct ?? 0).toFixed(2)),
        ref: Number(r.ref ?? r.price ?? 0),
        ceiling: Number(r.ceiling ?? r.price ?? 0),
        floor: Number(r.floor ?? r.price ?? 0),
        volume: volStr,
        foreign: Math.round(Number(r.foreign_flow ?? 0)),
        weight: 2.0,
        momentum: Math.round(Number(r.momentum ?? 0)),
        rs: Math.round(Number(r.rs ?? 50)),
        flow: Math.round(Number(r.foreign_flow ?? 0)),
        factor: "Technical",
        risk: "Low",
        beneish: "PASS",
        spark: [],
      }
    })

    return { indicesData, stockList: liveStocks }
  }, [resource.data])

  const { indices: liveIndices, isLive } = useRealtimeMarket(initialIndices)
  const currentIndices = liveIndices || initialIndices

  return (
    <Page
      title="Price board"
      sub="HOSE · live quotes, ceiling/floor bands, liquidity, and foreign flow."
      actions={
        isLive ? (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-[12px] font-medium bg-emerald-500/10 text-emerald-500 border border-emerald-500/20">
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
            LIVE STREAM
          </span>
        ) : undefined
      }
    >
      <MetricStrip
        items={[
          {
            label: "VN-Index",
            value: currentIndices.vnIndexVal,
            sub: <PercentChange value={currentIndices.vnIndexPct} arrow={false} />,
          },
          {
            label: "VN30",
            value: currentIndices.vn30Val,
            sub: <PercentChange value={currentIndices.vn30Pct} arrow={false} />,
          },
          { label: "Liquidity", value: "18.7T" },
          {
            label: "Foreign flow",
            value: <span className="text-gain">+412B</span>,
          },
        ]}
      />
      <Panel flush className="mt-4">
        <div className="p-5 pb-2">
          <PanelHead
            title="HOSE watchboard"
            sub="Prices in VND · volume shares · net foreign flow in VND bn"
          />
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-[13px] min-w-[1000px]">
            <thead>
              <tr className="text-[10px] uppercase tracking-wide text-muted border-y border-line">
                {[
                  "Symbol",
                  "Company",
                  "Ceiling",
                  "Floor",
                  "Reference",
                  "Last",
                  "Change",
                  "Volume",
                  "Foreign",
                  "Momentum",
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
                  </td>
                  <td className="px-3 text-secondary">{s.name}</td>
                  <td className="px-3 text-right font-mono text-[12px] text-gold">
                    {fmt(s.ceiling)}
                  </td>
                  <td className="px-3 text-right font-mono text-[12px] text-mineral">
                    {fmt(s.floor)}
                  </td>
                  <td className="px-3 text-right font-mono text-[12px] text-secondary">
                    {fmt(s.ref)}
                  </td>
                  <td className="px-3 text-right tnum font-mono font-semibold text-ink">
                    {fmt(s.price)}
                  </td>
                  <td className="px-3 text-right">
                    <PercentChange value={s.changePct} arrow={false} />
                  </td>
                  <td className="px-3 text-right tnum font-mono text-secondary">
                    {s.volume}
                  </td>
                  <td
                    className={`px-3 text-right tnum font-mono ${s.foreign >= 0 ? "text-gain" : "text-loss"}`}
                  >
                    {s.foreign >= 0 ? "+" : ""}
                    {s.foreign}B
                  </td>
                  <td className="px-3 text-right tnum font-mono text-ink">
                    {s.momentum}
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
