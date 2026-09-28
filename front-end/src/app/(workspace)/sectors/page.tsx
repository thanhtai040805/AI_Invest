"use client"

import { useMemo } from "react"
import { Page } from "@/components/Shell"
import {
  Panel,
  PercentChange,
  MarketLineChart,
} from "@/components/ui"
import { marketApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import { useRealtimeMarket } from "@/lib/use-realtime"

interface ApiSector { name?: string; sector?: string; foreign_flow?: number | null; count?: number; liveCount?: number; weight?: number; change?: number; change_pct?: number; sparkline?: number[]; totalVolume?: number }
interface ApiHeatmap { sectors?: ApiSector[] }

export default function Sectors() {
  const resource = useResource(() => marketApi.heatmap().catch(() => null), [])
  const { heatmap: liveHeatmap, heatmapLive } = useRealtimeMarket()

  const sectorList = useMemo(() => {
    const raw = (liveHeatmap ?? resource.data) as ApiHeatmap | null
    const apiSectors = Array.isArray(raw?.sectors) ? raw.sectors : []
    if (!apiSectors.length) return []
    const latestForeignFlow = new Map(
      ((resource.data as ApiHeatmap | null)?.sectors ?? []).map((sector) => [
        String(sector.name || sector.sector || ""),
        sector.foreign_flow,
      ]),
    )
    const totalWeight = apiSectors.reduce((sum, sector) => sum + Number(sector.weight ?? sector.count ?? 0), 0)

    return (apiSectors as ApiSector[]).slice(0, 12).map((s) => {
      const sectorName = String(s.name || s.sector || "")
      const foreignRaw = s.foreign_flow ?? latestForeignFlow.get(sectorName)
      const foreignBn = foreignRaw != null ? Number(foreignRaw) : null
      const change = Number(s.change ?? s.change_pct ?? 0)
      const sectorWeight = Number(s.weight ?? s.count ?? 0)
      return {
        name: sectorName || "General",
        vn: sectorName || "Ngành",
        weight: totalWeight > 0 ? (sectorWeight / totalWeight) * 100 : 0,
        changePct: Number(change.toFixed(2)),
        foreign: foreignBn,
        count: s.count,
        liveCount: s.liveCount,
        sparkline: Array.isArray(s.sparkline) ? s.sparkline : [],
      }
    })
  }, [resource.data, liveHeatmap])

  return (
    <Page
      title="Luân chuyển dòng tiền ngành"
      sub={`Giá ngành cập nhật theo giao dịch HOSE; khối ngoại từ dữ liệu DNSE.${heatmapLive ? " · Đang nhận tick DNSE" : ""}`}
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
                  {s.vn} · {s.weight.toFixed(1)}% tỷ trọng{s.liveCount != null && s.count != null ? ` · ${s.liveCount}/${s.count} mã có tick` : ""}
                </div>
              </div>
              <div className="text-right">
                <PercentChange value={s.changePct} />
                <div
                  className={`text-[12px] tnum font-mono mt-0.5 ${s.foreign == null ? "text-muted" : s.foreign >= 0 ? "text-gain" : "text-loss"}`}
                >
                  Khối ngoại {s.foreign == null ? "—" : `${s.foreign >= 0 ? "+" : ""}${s.foreign.toFixed(1)}B`}
                </div>
              </div>
            </div>
            <div className="mt-4 border-t border-line pt-3">
              {s.sparkline.length > 1 ? <MarketLineChart
                height={88}
                series={[
                  {
                    label: s.name,
                    data: s.sparkline,
                    color:
                      s.changePct >= 0
                        ? "var(--color-gain)"
                        : "var(--color-loss)",
                  },
                ]}
              /> : <div className="text-[12px] text-muted">Chưa có lịch sử ngành để vẽ xu hướng.</div>}
            </div>
          </Panel>
        ))}
      </div>
    </Page>
  )
}
