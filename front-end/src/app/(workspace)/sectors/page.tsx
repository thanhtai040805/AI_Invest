"use client"

import { Page } from "@/components/Shell"
import { marketApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import { useRealtimeMarket } from "@/lib/use-realtime"
import { SectorStrengthChart, type SectorStrengthPayload } from "@/components/SectorStrengthChart"

export default function Sectors() {
  const strengthResource = useResource(() => marketApi.sectorStrength().catch(() => null), [])
  const { heatmapLive } = useRealtimeMarket()

  return (
    <Page
      title="Sức mạnh & Luân chuyển dòng tiền ngành"
      sub={`So sánh sức mạnh tương đối 18 nhóm ngành HOSE (1T / 3T / 6T / 1N), cổ phiếu dẫn dắt từng ngành và dòng tiền khối ngoại.${heatmapLive ? " · Biến động phiên đang nhận tick DNSE" : ""}`}
    >
      <SectorStrengthChart
        data={strengthResource.data as SectorStrengthPayload | null}
        loading={strengthResource.loading}
      />
    </Page>
  )
}
