"use client"

import { useMemo } from "react"
import { Page } from "@/components/Shell"
import { Link } from "@/lib/router"
import type { Stock } from "@/types"
import {
  Button,
  Panel,
  PanelHead,
  PercentChange,
  RiskLabel,
  Sparkline,
} from "@/components/ui"
import { marketApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"

export default function Discovery() {
  const resource = useResource(() => marketApi.snapshot().catch(() => null), [])

  const stockList = useMemo(() => {
    const raw = resource.data as any
    const apiStocks = Array.isArray(raw?.stocks) ? raw.stocks : []
    if (!apiStocks.length) return []

    return apiStocks.map((r: any) => {
      const sym = String(r.symbol)
      const mom = Math.round(Number(r.momentum ?? 0))
      const rsVal = Math.round(Number(r.rs ?? 50))
      const flowBn = Math.round(Number(r.foreign_flow ?? 0))
      const change = Number(Number(r.change_pct ?? 0).toFixed(2))

      return {
        symbol: sym,
        name: String(r.name || sym),
        sector: "HOSE",
        price: Number(r.price ?? 0),
        changePct: change,
        ref: Number(r.ref ?? r.price ?? 0),
        ceiling: Number(r.ceiling ?? r.price ?? 0),
        floor: Number(r.floor ?? r.price ?? 0),
        volume: String(r.volume || "0"),
        foreign: flowBn,
        weight: 2.0,
        momentum: mom,
        rs: rsVal,
        flow: flowBn,
        factor: mom > 60 ? "Momentum" : rsVal > 60 ? "Quality" : "Value",
        risk: change <= -3 ? "Elevated" : change <= -1 ? "Moderate" : "Low",
        beneish: "PASS",
        spark: [100, 100 + change],
      } as Stock
    })
  }, [resource.data])

  const buckets = useMemo(() => {
    if (!stockList.length) return []

    const pick = (list: Stock[]) => list.slice(0, 3).map((s) => s.symbol)
    return [
      {
        name: "Momentum expansion",
        syms: pick([...stockList].sort((a, b) => b.momentum - a.momentum)),
      },
      {
        name: "Accumulation",
        syms: pick([...stockList].filter((s) => s.flow > 0).sort((a, b) => b.flow - a.flow)),
      },
      {
        name: "Relative strength",
        syms: pick([...stockList].sort((a, b) => b.rs - a.rs)),
      },
      {
        name: "Value compression",
        syms: pick([...stockList].filter((s) => s.changePct < 0).sort((a, b) => a.changePct - b.changePct)),
      },
      {
        name: "Institutional flow",
        syms: pick([...stockList].sort((a, b) => Math.abs(b.foreign) - Math.abs(a.foreign))),
      },
      {
        name: "Risk deterioration",
        syms: pick([...stockList].filter((s) => s.risk !== "Low").sort((a, b) => a.changePct - b.changePct)),
      },
    ].filter((b) => b.syms.length > 0)
  }, [stockList])
  return (
    <Page
      title="Alpha Discovery"
      sub="What deserves my attention?"
      actions={<Button variant="primary">Save screen</Button>}
    >
      <div className="grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-4">
        <Panel className="h-fit">
          <PanelHead title="Filter builder" />
          <div className="space-y-3.5 text-[12px]">
            {[
              "Market",
              "Sector",
              "Market cap",
              "Liquidity",
              "Valuation",
              "Quality",
              "Momentum",
              "Growth",
              "Sentiment",
              "Flow",
              "Risk",
              "AI signal",
            ].map((f) => (
              <div key={f}>
                <label className="text-secondary">{f}</label>
                <div className="mt-1 h-8 border border-line rounded-[6px] flex items-center px-2.5 text-muted hover:border-ink/25 cursor-pointer">
                  Any
                </div>
              </div>
            ))}
            <Button variant="primary" className="w-full">
              Apply filters
            </Button>
          </div>
        </Panel>
        <div className="space-y-4">
          {buckets.map((b) => (
            <Panel key={b.name} flush>
              <div className="px-5 pt-4 pb-2">
                <PanelHead
                  title={b.name}
                  sub={`${b.syms.length} candidates`}
                  action={<Button variant="ghost">Compare</Button>}
                />
              </div>
              <div className="divide-y divide-line">
                {b.syms.map((sym) => {
                  const s = stockList.find((x) => x.symbol === sym) || {
                    symbol: sym,
                    name: sym,
                    price: 50000,
                    changePct: 0,
                    rsi: 50,
                    pe: 12,
                    foreignFlow: 0,
                    risk: "Moderate" as const,
                  }
                  return (
                    <div
                      key={sym}
                      className="flex items-center gap-4 px-5 py-2.5 hover:bg-soft/50 transition-colors"
                    >
                      <Link
                        to={`/stock/${sym}`}
                        className="font-mono font-medium text-ink w-12 hover:underline"
                      >
                        {sym}
                      </Link>
                      <span className="text-[12.5px] text-secondary flex-1 min-w-0 truncate">
                        {s.name}
                      </span>
                      <Sparkline data={s.spark} width={60} height={18} />
                      <PercentChange
                        value={s.changePct}
                        className="text-[12.5px] w-16 text-right"
                      />
                      <RiskLabel risk={s.risk} />
                      <Link to={`/stock/${sym}`}>
                        <Button variant="quiet">Research</Button>
                      </Link>
                    </div>
                  )
                })}
              </div>
            </Panel>
          ))}
        </div>
      </div>
    </Page>
  )
}
