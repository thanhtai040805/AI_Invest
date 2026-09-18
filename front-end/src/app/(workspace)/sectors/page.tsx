"use client"

import { useMemo } from "react"
import { Page } from "@/components/Shell"
import type { Sector } from "@/types"
import {
  Panel,
  PercentChange,
  MarketLineChart,
} from "@/components/ui"
import { marketApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"

export default function Sectors() {
  const resource = useResource(() => marketApi.heatmap().catch(() => null), [])

  const sectorList = useMemo(() => {
    const raw = resource.data as any
    const apiSectors = Array.isArray(raw?.sectors) ? raw.sectors : []
    if (!apiSectors.length) return []

    return apiSectors.slice(0, 12).map((s: any) => {
      const foreignBn = Math.round(Number(s.foreign_flow ?? 0) / 1e9)
      return {
        name: String(s.sector || "General"),
        vn: String(s.sector || "Ngành"),
        weight: Number(s.count ?? 5),
        changePct: Number((Number(s.change_pct ?? 0)).toFixed(2)),
        foreign: foreignBn,
        sparkline: Array.isArray(s.sparkline) && s.sparkline.length > 0 ? s.sparkline : [100, 100 + Number(s.change_pct ?? 0)],
      } as Sector & { sparkline?: number[] }
    })
  }, [resource.data])

  return (
    <Page
      title="Luân chuyển dòng tiền ngành"
      sub="Hiệu suất 1 tháng, độ rộng dòng tiền và giao dịch khối ngoại theo ngành."
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {sectorList.map((s) => (
          <Panel key={s.name}>
            <div className="flex items-center justify-between">
              <div>
                <div className="text-[15px] font-semibold text-ink">
                  {s.name}
                </div>
                <div className="text-[12px] text-muted">
                  {s.vn} · {s.weight}% tỷ trọng
                </div>
              </div>
              <div className="text-right">
                <PercentChange value={s.changePct} />
                <div
                  className={`text-[12px] tnum font-mono mt-0.5 ${s.foreign >= 0 ? "text-gain" : "text-loss"}`}
                >
                  Khối ngoại {s.foreign >= 0 ? "+" : ""}
                  {s.foreign}B
                </div>
              </div>
            </div>
            <div className="mt-4 border-t border-line pt-3">
              <MarketLineChart
                height={88}
                series={[
                  {
                    label: s.name,
                    data: (s as any).sparkline && (s as any).sparkline.length > 1
                      ? (s as any).sparkline
                      : [100, 100 + s.changePct],
                    color:
                      s.changePct >= 0
                        ? "var(--color-gain)"
                        : "var(--color-loss)",
                  },
                ]}
              />
            </div>
          </Panel>
        ))}
      </div>
    </Page>
  )
}
