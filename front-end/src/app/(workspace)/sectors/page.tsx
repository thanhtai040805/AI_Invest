"use client"

import { useMemo } from "react"
import { Page } from "@/components/Shell"
import { Panel, PercentChange, MarketLineChart } from "@/components/ui"
import { marketApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import { useRealtimeMarket } from "@/lib/use-realtime"
import { sectorWeights } from "@/lib/sector-weights"

interface ApiSector { name?: string; sector?: string; foreign_flow?: number | null; count?: number; liveCount?: number; weight?: number; market_cap?: number; marketCap?: number; marketCapCount?: number; market_cap_count?: number; change?: number; change_pct?: number; sparkline?: number[]; totalVolume?: number }
interface ApiHeatmap { sectors?: ApiSector[] }

const sectorLabels: Record<string, string> = {
  "Basic Materials": "Nguyên vật liệu", Industrials: "Công nghiệp", "Consumer Cyclical": "Hàng tiêu dùng không thiết yếu",
  "Consumer Defensive": "Hàng tiêu dùng thiết yếu", "Financial Services": "Dịch vụ tài chính", "Real Estate": "Bất động sản",
  Technology: "Công nghệ", "Communication Services": "Dịch vụ truyền thông", Energy: "Năng lượng",
  Utilities: "Tiện ích", Healthcare: "Y tế",
}

export default function Sectors() {
  const resource = useResource(() => marketApi.heatmap().catch(() => null), [])
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
        vn: sectorName || "Ngành",
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
      title="Luân chuyển dòng tiền ngành"
      sub={`Xu hướng ngành tính từ giá đóng cửa 60 phiên, chuẩn hóa điểm gốc 100; khối ngoại theo dữ liệu gần nhất trong DB.${heatmapLive ? " · Biến động phiên đang nhận tick DNSE" : ""}`}
    >
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-surface px-4 py-3 text-xs text-secondary">
        <span>Biến động phiên dùng màu và dấu mũi tên; tỷ trọng theo vốn hóa khi đủ dữ liệu, nếu thiếu dùng số mã toàn bộ HOSE.</span>
        <span className="flex items-center gap-2"><span className="text-gain">▲ Tăng</span><span className="text-loss">▼ Giảm</span><span className="text-muted">· số 0 cân bằng</span></span>
        {!hasHistory && <span className="w-full text-muted">Nguồn heatmap hiện chỉ trả biến động phiên; chưa có chuỗi lịch sử ngành.</span>}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {sectorList.map((s) => (
          <Panel key={s.name}>
            <div className="flex items-center justify-between">
              <div>
                <div className="text-[15px] font-semibold text-ink">
                  {sectorLabels[s.name] || s.name}
                </div>
                <div className="text-[12px] text-muted">
                  {s.vn} · {s.weight.toFixed(1)}% {s.weightByCount ? "theo số mã" : "vốn hóa"}{s.liveCount != null && s.count != null ? ` · ${s.liveCount}/${s.count} mã có tick` : ""}
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
                    label: sectorLabels[s.name] || s.name,
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
