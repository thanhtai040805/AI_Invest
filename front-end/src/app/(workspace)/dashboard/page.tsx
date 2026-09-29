"use client"

import { Page } from "@/components/Shell";
import { Link } from "@/lib/router";
type Sector = { name: string; vn: string; weight: number; count: number; changePct: number; foreign: number };
import { marketApi, workspaceApi } from "@/lib/api";
import { useResource } from "@/lib/api/use-resource";
import { DataState } from "@/components/data-state";
import { useRealtimeMarket } from "@/lib/use-realtime";
import {
  Button,
  Panel,
  PanelHead,
  PercentChange,
  Pill,
  Sparkline,
} from "@/components/ui";
import { KLineChart } from "@/components/KLineChart";
import type { ApiMarketStock } from "@/types";

type DashboardSector = Sector & { sparkline?: number[]; foreignKnown?: boolean };
interface ApiSectorRow { name?: string; sector?: string; nameVi?: string; weight?: number; marketWeight?: number; market_cap?: number; count?: number; changePct?: number; change_pct?: number; change?: number; foreign?: number; foreignFlow?: number; foreign_flow?: number; sparkline?: number[] }

function MarketMap({ sectors }: { sectors: DashboardSector[] }) {
  const hasMarketCap = sectors.some((sector) => sector.weight > 0);
  const total = sectors.reduce((sum, sector) => sum + (hasMarketCap ? sector.weight : sector.count), 0);
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 auto-rows-[118px] gap-1.5">
      {sectors.map((sector) => {
        const weight = hasMarketCap ? sector.weight : sector.count;
        const positive = sector.changePct >= 0;
        const strength = Math.min(Math.abs(sector.changePct) / 4, 1);
        const ground = positive
          ? `color-mix(in srgb, var(--color-gain) ${10 + strength * 30}%, var(--color-surface))`
          : `color-mix(in srgb, var(--color-loss) ${10 + strength * 30}%, var(--color-surface))`;
        const data = Array.isArray(sector.sparkline) && sector.sparkline.length > 1 ? sector.sparkline : null;
        return (
          <div
            key={sector.name}
            className={`${weight >= 18 ? "sm:col-span-2" : ""}`}
            style={{ background: ground }}
          >
            <Link
              to="/sectors"
              className="relative block h-full overflow-hidden border border-line rounded-[8px] p-3 transition-colors hover:border-ink/35"
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="text-[13px] font-semibold text-ink leading-tight">
                    {sector.name}
                  </div>
                  <div className="mt-0.5 text-[10px] text-secondary">
                    {total > 0 ? `${((weight / total) * 100).toFixed(0)}% ${hasMarketCap ? "tỷ trọng" : "theo số mã"}` : "—"}
                  </div>
                </div>
                <PercentChange
                  value={sector.changePct}
                  arrow={false}
                  className="text-[12px] shrink-0"
                />
              </div>
              <div className="absolute inset-x-3 bottom-3 flex items-end justify-between gap-2">
                {data ? <Sparkline data={data} up={positive} width={72} height={25} /> : <span className="text-[10px] text-muted">—</span>}
                <span
                  className={`text-[10px] font-mono ${sector.foreign >= 0 ? "text-gain" : "text-loss"}`}
                >
                  {sector.foreignKnown ? `${sector.foreign >= 0 ? "+" : ""}${sector.foreign.toFixed(1)}B NN` : "—"}
                </span>
              </div>
            </Link>
          </div>
        );
      })}
    </div>
  );
}

function DashboardView({
  sectors,
  indices,
  pulse,
  isLive,
}: {
  sectors: Sector[];
  indices: Record<string, number>;
  isLive: boolean;
  pulse?: {
    state: string;
    liquidity: string;
    breadth: string;
    foreign: string;
    foreignTone: string;
    leadership: string;
  };
}) {
  return (
    <Page
      title="Tổng quan thị trường"
      sub="Thị trường chứng khoán Việt Nam · Phiên giao dịch HOSE · Chỉ số trực tiếp"
      actions={
        <>
          <Button variant="secondary">Xuất báo cáo</Button>
          <Link to="/discovery">
            <Button variant="primary">Khám phá Alpha</Button>
          </Link>
        </>
      }
    >
      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.65fr)_350px] gap-4">
        <Panel>
          <PanelHead
            title="VN-Index"
            sub="Nến thời gian thực · Phiên HOSE · Con trỏ, phóng to & chỉ báo"
            action={
              <Pill tone={isLive ? "teal" : "neutral"}>
                <i className={`h-1.5 w-1.5 rounded-full ${isLive ? "bg-gain animate-pulse" : "bg-muted"}`} />
                {isLive ? "Trực tiếp" : "Dữ liệu gần nhất"}
              </Pill>
            }
          />
          <div className="grid grid-cols-1 gap-5 border-b border-line pb-4 mb-4">
            <div>
              <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted">
                VN-Index
              </div>
              <div className="mt-1 flex items-baseline gap-3">
                <span className="font-mono text-[30px] font-semibold tracking-tight text-ink">
                  {indices.vnindex > 0 ? indices.vnindex.toLocaleString("vi-VN") : "—"}
                </span>
                {indices.vnindex > 0 && <PercentChange value={indices.vnindexChange ?? 0} arrow={false} className="text-[14px]" />}
              </div>
            </div>
          </div>
          <KLineChart
            ticker="VNINDEX"
            name="VN-Index"
            precision={2}
            height={360}
          />
        </Panel>
        <Panel>
          <PanelHead title="Nhịp đập thị trường" sub="Cấu trúc dòng tiền & thị trường thời gian thực" />
          <div className="divide-y divide-line">
            {[
              ["Trạng thái thị trường", pulse?.state || "—", "teal"],
              ["Thanh khoản", pulse?.liquidity || "—", "ink"],
              ["Độ rộng thị trường", pulse?.breadth || "—", "ink"],
              ["Khối ngoại", pulse?.foreign || "—", pulse?.foreignTone || "gain"],
              ["Nhóm dẫn dắt", pulse?.leadership || "—", "ink"],
            ].map(([label, value, tone]) => (
              <div
                key={label}
                className="flex items-center justify-between gap-4 py-3"
              >
                <span className="text-[12px] text-secondary">{label}</span>
                <span
                  className={`text-right text-[12px] font-medium ${tone === "teal" ? "text-teal" : tone === "gain" ? "text-gain" : "text-ink"}`}
                >
                  {value}
                </span>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 mt-4">
        <Panel>
          <PanelHead
            title="Bản đồ nhiệt ngành"
            sub="Màu sắc = biến động ngày · Diện tích = vốn hóa khi có dữ liệu, nếu thiếu dùng số mã · Đường kẻ = lịch sử ngành khi có dữ liệu"
            action={
              <Link to="/markets">
                <Button variant="ghost">Mở bảng giá</Button>
              </Link>
            }
          />
          <MarketMap sectors={sectors} />
        </Panel>
      </div>
    </Page>
  );
}

export default function Dashboard() {
  const { heatmap: liveHeatmap, indices: liveIndices, isLive, breadth: liveBreadth, liquidity: liveLiquidity, snapshot: liveSnapshot } = useRealtimeMarket();
  const resource = useResource(async () => {
    const [overview, indexPayload, heatmap, snap] = await Promise.all([
      workspaceApi.overview().catch(() => null),
      marketApi.indices().catch(() => null),
      marketApi.heatmap().catch(() => null),
      marketApi.snapshot().catch(() => null),
    ]);
    const indexRows = Array.isArray(indexPayload) ? indexPayload : indexPayload?.data || indexPayload?.indices || [];
    const findIndex = (name: string) => indexRows.find((row: Record<string, unknown>) => String(row.symbol || row.code || row.name).toUpperCase().includes(name));
    const vn = findIndex("VNINDEX") || findIndex("VN-INDEX") || {};
    const sectorRows: ApiSectorRow[] = Array.isArray(heatmap) ? heatmap : heatmap?.sectors || heatmap?.data || [];
    const sectors: DashboardSector[] = sectorRows.map((row) => ({
      name: String(row.name || row.sector || "—"),
      vn: String(row.nameVi || row.name || row.sector || "—"),
      weight: Number(row.weight ?? row.marketWeight ?? row.market_cap ?? 0),
      count: Number(row.count ?? 0),
      changePct: Number(row.changePct || row.change_pct || row.change || 0),
      foreign: Number(row.foreign || row.foreignFlow || row.foreign_flow || 0),
      foreignKnown: row.foreign_flow != null || row.foreignFlow != null || row.foreign != null,
      sparkline: row.sparkline,
    }));
    const rawStocks: ApiMarketStock[] = Array.isArray(snap?.stocks) ? snap.stocks : [];
    const advancing = rawStocks.filter((s) => Number(s.change_pct) > 0).length;
    const declining = rawStocks.filter((s) => Number(s.change_pct) < 0).length;
    const hasForeign = rawStocks.some((s) => s.foreign_flow != null);
    const foreignSum = Math.round(rawStocks.reduce((sum, s) => sum + Number(s.foreign_flow ?? 0), 0));
    const topLeaders = sectorRows.slice(0, 3).map((r) => r.sector || r.name).filter(Boolean).join(" · ");

    const regimeLabel = overview?.regime?.regime_label || overview?.regime?.dominant_regime;
    const stateStr = regimeLabel ? `${regimeLabel} · Tin cậy ${(Number(overview?.regime?.confidence ?? 0) * 100).toFixed(0)}%` : "—";

    const pulse = {
      state: stateStr,
      liquidity: "—",
      breadth: rawStocks.length > 0 ? `${advancing} tăng / ${declining} giảm` : "—",
      foreign: hasForeign ? `${foreignSum >= 0 ? "+" : ""}${foreignSum}B ròng` : "—",
      foreignTone: foreignSum >= 0 ? "gain" : "loss",
      leadership: topLeaders || "—",
    };

    return {
      sectors,
      pulse,
      indices: {
        vnindex: Number(vn.value || vn.indexValue || vn.close || 0),
        vnindexChange: Number(vn.changePct || vn.change_pct || vn.change || 0),
      },
    };
  }, []);
  const liveRows = liveHeatmap?.sectors;
  const liveTotal = liveRows?.reduce((sum, item) => sum + Number(item.weight ?? item.count ?? 0), 0) ?? 0;
  const latestForeignFlow = new Map((resource.data?.sectors ?? []).map((sector) => [sector.name, sector.foreign]));
  const realtimeSectors: DashboardSector[] | undefined = liveRows?.map((row) => {
    const count = Number(row.weight ?? row.count ?? 0);
    return {
      name: String(row.name || row.sector || "—"),
      vn: String(row.nameVi || row.name || row.sector || "—"),
      weight: liveTotal > 0 ? (count / liveTotal) * 100 : 0,
      count: Number(row.count ?? 0),
      changePct: Number(row.changePct || row.change_pct || row.change || 0),
      foreign: Number(row.foreign_flow ?? row.foreignFlow ?? row.foreign ?? latestForeignFlow.get(String(row.name || row.sector || "")) ?? 0),
      foreignKnown: row.foreign_flow != null || row.foreignFlow != null || row.foreign != null,
      sparkline: Array.isArray(row.sparkline) ? row.sparkline as number[] : undefined,
    };
  });
  const liveStocks = liveSnapshot?.stocks;
  const liveForeign = liveStocks?.some((stock) => stock.foreign_flow != null)
    ? liveStocks.reduce((sum, stock) => sum + Number(stock.foreign_flow ?? 0), 0)
    : undefined;
  const seedPulse = resource.data?.pulse;
  const pulse = {
    state: seedPulse?.state ?? "—",
    breadth: liveBreadth ? `${liveBreadth.advancers ?? 0} tăng / ${liveBreadth.decliners ?? 0} giảm` : seedPulse?.breadth ?? "—",
    liquidity: liveLiquidity ? `${(Number(liveLiquidity.totalValueBillion) / 1000).toFixed(1)}T VNĐ` : seedPulse?.liquidity ?? "—",
    foreign: liveForeign != null ? `${liveForeign >= 0 ? "+" : ""}${liveForeign.toFixed(0)}B ròng` : seedPulse?.foreign ?? "—",
    foreignTone: liveForeign != null ? (liveForeign >= 0 ? "gain" : "loss") : seedPulse?.foreignTone ?? "gain",
    leadership: seedPulse?.leadership ?? "—",
  };
  const seedIndices = resource.data?.indices;
  const indices = {
    vnindex: liveIndices?.vnIndexVal ? Number(liveIndices.vnIndexVal.replaceAll(",", "")) : seedIndices?.vnindex ?? 0,
    vnindexChange: liveIndices?.vnIndexPct ?? seedIndices?.vnindexChange ?? 0,
  };
  const sectors = realtimeSectors ?? resource.data?.sectors ?? [];
  return <DataState loading={resource.loading && !sectors.length} error={sectors.length ? null : resource.error} empty={!sectors.length} retry={() => void resource.reload()}><DashboardView sectors={sectors} indices={indices} pulse={pulse} isLive={isLive} /></DataState>;
}
