"use client"

import { useMemo } from "react"
import { Page } from "@/components/Shell"
import { Panel, PercentChange, MarketLineChart } from "@/components/ui"
import { marketApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import { useRealtimeMarket } from "@/lib/use-realtime"
import { sectorWeights, formatSectorName } from "@/lib/sector-weights"
import { SectorStrengthChart, type SectorStrengthPayload } from "@/components/SectorStrengthChart"

interface ApiSector { name?: string; sector?: string; foreign_flow?: number | null; count?: number; liveCount?: number; weight?: number; market_cap?: number; marketCap?: number; marketCapCount?: number; market_cap_count?: number; change?: number; change_pct?: number; sparkline?: number[]; totalVolume?: number }
interface ApiHeatmap { sectors?: ApiSector[] }

export default function Sectors() {
  const resource = useResource(() => marketApi.heatmap().catch(() => null), [])
  const strengthResource = useResource(() => marketApi.sectorStrength().catch(() => null), [])
  const { heatmap: liveHeatmap, heatmapLive } = useRealtimeMarket()

  const sectorList = useMemo(() => {
    const raw = (liveHeatmap ?? resource.data) as ApiHeatmap | null
    const apiSectors = Array.isArray(raw?.sectors) ? raw.sectors : []
    if (!apiSectors.length) return []
    const historyByName = new Map(
      ((resource.data as ApiHeatmap | null)?.sectors ?? []).map((sector) => [
        String(sector.name || sector.sector || ""),
        sector,
      ]),
    )
    const { weights, weightByCount } = sectorWeights(apiSectors)

    return apiSectors.map((s, index) => {
      const sectorName = String(s.name || s.sector || "")
      const history = historyByName.get(sectorName)
      const foreignRaw = history?.foreign_flow
      const foreignBn = foreignRaw != null ? Number(foreignRaw) : null
      const rawChange = s.change ?? s.change_pct
      const change = rawChange == null ? NaN : Number(rawChange)
      return {
        name: sectorName || "General",
        vn: formatSectorName(sectorName || "Ngành"),
        weight: weights[index],
        weightByCount,
        changePct: Number.isFinite(change) ? Number(change.toFixed(2)) : null,
        foreign: foreignBn,
        count: s.count,
        liveCount: s.liveCount,
        sparkline: Array.isArray(s.sparkline) && s.sparkline.length > 1
          ? s.sparkline
          : Array.isArray(history?.sparkline) ? history.sparkline : [],
      }
    })
  }, [resource.data, liveHeatmap])
  const hasHistory = sectorList.some(sector => sector.sparkline.length > 1)

  return (
    <Page
      title="Sức mạnh & Luân chuyển dòng tiền ngành"
      sub={`So sánh sức mạnh tương đối 18 nhóm ngành HOSE (1T / 3T / 6T / 1N), cổ phiếu dẫn dắt từng ngành và dòng tiền khối ngoại.${heatmapLive ? " · Biến động phiên đang nhận tick DNSE" : ""}`}
    >
      <div className="mb-5">
        <SectorStrengthChart
          data={strengthResource.data as SectorStrengthPayload | null}
          loading={strengthResource.loading}
        />
      </div>

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-surface px-4 py-3 text-xs text-secondary">
        <span>Chi tiết từng nhóm ngành: biến động phiên, tỷ trọng vốn hóa HOSE, dòng tiền khối ngoại và đường xu hướng 60 phiên.</span>
        <span className="flex items-center gap-2"><span className="text-gain">▲ Tăng</span><span className="text-loss">▼ Giảm</span><span className="text-muted">· số 0 cân bằng</span></span>
        {!hasHistory && <span className="w-full text-muted">Nguồn heatmap hiện chỉ trả biến động phiên; chưa có chuỗi lịch sử ngành.</span>}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {sectorList.map((s) => (
          <Panel key={s.name}>
            <div className="flex items-center justify-between">
              <div>
                <div className="text-[15px] font-semibold text-ink">
                  {formatSectorName(s.name)}
                </div>
                <div className="text-[12px] text-muted">
                  {s.name} · {s.weight.toFixed(1)}% {s.weightByCount ? "theo số mã" : "vốn hóa"}{s.liveCount != null && s.count != null ? ` · ${s.liveCount}/${s.count} mã có tick` : ""}
                </div>
              </div>
              <div className="text-right">
                {s.changePct !== null ? <PercentChange value={s.changePct} /> : <span className="text-muted">—</span>}
                <div
                  className={`text-[12px] tnum font-mono mt-0.5 ${s.foreign == null ? "text-muted" : s.foreign >= 0 ? "text-gain" : "text-loss"}`}
                >
                  Khối ngoại {s.foreign == null ? "—" : `${s.foreign >= 0 ? "+" : ""}${s.foreign.toFixed(1)}B`}
                </div>
              </div>
            </div>
            {s.sparkline.length > 1 && <div className="mt-4 border-t border-line pt-3">
              <MarketLineChart
                height={88}
                series={[
                  {
                    label: formatSectorName(s.name),
                    data: s.sparkline,
                    color:
                      s.changePct === null || s.changePct >= 0
                        ? "var(--color-gain)"
                        : "var(--color-loss)",
                  },
                ]}
              />
            </div>}
          </Panel>
        ))}
      </div>
    </Page>
  )
}
