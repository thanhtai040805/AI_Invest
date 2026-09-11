"use client"

import { useState, useMemo } from "react"
import { Page } from "@/components/Shell"
import { Link } from "@/lib/router"
import {
  Button,
  Conviction,
  Panel,
  Pill,
  Tabs,
} from "@/components/ui"
import { workspaceApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"

export default function Signals() {
  const [tab, setTab] = useState("Emerging")
  const resource = useResource(() => workspaceApi.signals().catch(() => null), [])

  const rows = useMemo(() => {
    const raw = resource.data as any
    const signalList = Array.isArray(raw?.current) ? raw.current : []
    if (!signalList.length) return []

    return signalList.slice(0, 10).map((sig: any) => {
      const rank = Number(sig.composite_rank ?? 0.8)
      const mom = Math.round(rank * 100)
      const flowBn = Math.round(Number(sig.foreign_flow ?? 0))
      return {
        symbol: String(sig.symbol),
        sector: String(sig.sector_group || "General"),
        momentum: mom,
        flow: flowBn,
        observation: `Tín hiệu ${sig.signal} (Rank ${rank.toFixed(2)})`,
        evidence: `Khối ngoại ròng: ${flowBn >= 0 ? "+" : ""}${flowBn}B · Hard flags: ${sig.hard_flags ?? 0}`,
        risk: Number(sig.hard_flags) > 0 ? "Có rủi ro vi phạm (Hard flag)" : `Xác nhận ${mom > 75 ? "Cao" : "Trung bình"}`,
      }
    })
  }, [resource.data])

  return (
    <Page
      title="Signals"
      sub="Machine-detected market changes with evidence and conviction."
    >
      <Tabs
        tabs={["Emerging", "Confirmed", "Weakening", "Invalidated"]}
        active={tab}
        onChange={setTab}
      />
      <div className="space-y-3 mt-4">
        {rows.map((s) => (
          <Panel key={s.symbol}>
            <div className="flex items-start gap-4">
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1.5">
                  <Link
                    to={`/stock/${s.symbol}`}
                    className="font-mono font-semibold text-ink hover:underline"
                  >
                    {s.symbol}
                  </Link>
                  <Pill tone="mineral">{tab}</Pill>
                  <Conviction level={s.momentum > 75 ? "Strong" : "Moderate"} />
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-1.5 text-[12.5px] text-secondary mt-2">
                  <div>
                    <span className="text-[10px] uppercase tracking-wide text-muted mr-2">
                      Observation
                    </span>
                    Turnover expanded above 2× average
                  </div>
                  <div>
                    <span className="text-[10px] uppercase tracking-wide text-muted mr-2">
                      Evidence
                    </span>
                    Foreign flow {s.flow >= 0 ? "positive" : "negative"} (
                    {s.flow >= 0 ? "+" : ""}
                    {s.flow})
                  </div>
                  <div>
                    <span className="text-[10px] uppercase tracking-wide text-muted mr-2">
                      Risk
                    </span>
                    Confirmation {s.momentum > 75 ? "high" : "moderate"}
                  </div>
                  <div>
                    <span className="text-[10px] uppercase tracking-wide text-muted mr-2">
                      Sectors
                    </span>
                    {s.sector}
                  </div>
                </div>
              </div>
              <Link to={`/stock/${s.symbol}`}>
                <Button variant="secondary">Investigate</Button>
              </Link>
            </div>
          </Panel>
        ))}
      </div>
    </Page>
  )
}
