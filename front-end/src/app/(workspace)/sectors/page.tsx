"use client"

import { useState } from "react"
import { Page } from "@/components/Shell"
import { Button, Panel, SegmentedTabs } from "@/components/ui"
import { marketApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import { SectorStrengthChart, type SectorStrengthPayload } from "@/components/SectorStrengthChart"

const SECTOR_TIMEFRAMES: Array<{ value: string; label: string }> = [
  { value: "1 Tháng", label: "1T" },
  { value: "3 Tháng", label: "3T" },
  { value: "6 Tháng", label: "6T" },
  { value: "1 Năm", label: "1N" },
]

export default function Sectors() {
  const [timeframe, setTimeframe] = useState<string>("1 Tháng")
  const strengthResource = useResource(() => marketApi.sectorStrength(), [])

  return (
    <Page
      title="Dòng tiền Ngành HOSE"
      meta="17 nhóm ngành · Sức mạnh tương đối (RS) so với VN-Index"
      fetching={strengthResource.fetching}
      actions={
        <SegmentedTabs
          ariaLabel="Khung thời gian sức mạnh ngành"
          options={SECTOR_TIMEFRAMES}
          value={timeframe}
          onChange={setTimeframe}
        />
      }
    >
      {strengthResource.error && !strengthResource.data ? (
        <Panel className="text-center py-10 space-y-3">
          <div className="text-[13px] text-loss">
            {strengthResource.error.message || "Không tải được dữ liệu dòng tiền ngành."}
          </div>
          <Button size="sm" onClick={() => void strengthResource.reload()}>
            Thử lại
          </Button>
        </Panel>
      ) : (
        <SectorStrengthChart
          data={strengthResource.data as SectorStrengthPayload | null}
          loading={strengthResource.loading}
          timeframe={timeframe}
          onTimeframeChange={setTimeframe}
        />
      )}
    </Page>
  )
}
