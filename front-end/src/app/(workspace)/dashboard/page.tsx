"use client"

import { Page } from "@/components/Shell";
import { Link } from "@/lib/router";
type Sector = { name: string; vn: string; weight: number; changePct: number; foreign: number };
import { marketApi, workspaceApi } from "@/lib/api";
import { useResource } from "@/lib/api/use-resource";
import { DataState } from "@/components/data-state";
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

type DashboardSector = Sector & { sparkline?: number[] };
interface ApiSectorRow { name?: string; sector?: string; nameVi?: string; weight?: number; marketWeight?: number; market_cap?: number; changePct?: number; change_pct?: number; change?: number; foreign?: number; foreignFlow?: number; foreign_flow?: number; sparkline?: number[] }

function MarketMap({ sectors }: { sectors: DashboardSector[] }) {
  const total = sectors.reduce((sum, sector) => sum + sector.weight, 0);
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 auto-rows-[118px] gap-1.5">
      {sectors.map((sector) => {
        const positive = sector.changePct >= 0;
        const strength = Math.min(Math.abs(sector.changePct) / 4, 1);
        const ground = positive
          ? `color-mix(in srgb, var(--color-gain) ${10 + strength * 30}%, var(--color-surface))`
          : `color-mix(in srgb, var(--color-loss) ${10 + strength * 30}%, var(--color-surface))`;
        const data = Array.isArray(sector.sparkline) && sector.sparkline.length > 1
          ? sector.sparkline
          : [100, 100 + sector.changePct];
        return (
          <div
            key={sector.name}
            className={`${sector.weight >= 18 ? "sm:col-span-2" : ""}`}
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
                    {((sector.weight / total) * 100).toFixed(0)}% tỷ trọng
                  </div>
                </div>
                <PercentChange
                  value={sector.changePct}
                  arrow={false}
                  className="text-[12px] shrink-0"
                />
              </div>
              <div className="absolute inset-x-3 bottom-3 flex items-end justify-between gap-2">
                <Sparkline data={data} up={positive} width={72} height={25} />
                <span
                  className={`text-[10px] font-mono ${sector.foreign >= 0 ? "text-gain" : "text-loss"}`}
                >
                  {sector.foreign >= 0 ? "+" : ""}
                  {sector.foreign}B NN
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
}: {
  sectors: Sector[];
  indices: Record<string, number>;
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
            title="VN-Index & VN30"
            sub="Nến thời gian thực · Phiên HOSE · Con trỏ, phóng to & chỉ báo"
            action={
              <Pill tone="teal">
                <i className="h-1.5 w-1.5 rounded-full bg-gain animate-pulse" />
                Trực tiếp
              </Pill>
            }
          />
          <div className="grid grid-cols-2 gap-5 border-b border-line pb-4 mb-4">
            <div>
              <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted">
                VN-Index
              </div>
              <div className="mt-1 flex items-baseline gap-3">
                <span className="font-mono text-[30px] font-semibold tracking-tight text-ink">
                  {(indices.vnindex ?? 0).toLocaleString("vi-VN")}
                </span>
                <PercentChange
                  value={indices.vnindexChange ?? 0}
                  arrow={false}
                  className="text-[14px]"
                />
              </div>
            </div>
            <div className="border-l border-line pl-5">
              <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted">
                VN30
              </div>
              <div className="mt-1 flex items-baseline gap-3">
                <span className="font-mono text-[30px] font-semibold tracking-tight text-ink">
                  {(indices.vn30 ?? 0).toLocaleString("vi-VN")}
                </span>
                <PercentChange
                  value={indices.vn30Change ?? 0}
                  arrow={false}
                  className="text-[14px]"
                />
              </div>
            </div>
          </div>
          <KLineChart
            ticker="VNINDEX"
            name="VN-Index"
            basePrice={indices.vnindex || 1}
            precision={2}
            height={360}
          />
        </Panel>
        <Panel>
          <PanelHead title="Nhịp đập thị trường" sub="Cấu trúc dòng tiền & thị trường thời gian thực" />
          <div className="divide-y divide-line">
            {[
              ["Trạng thái thị trường", pulse?.state || "Xu hướng tăng · Biến động thấp", "teal"],
              ["Thanh khoản", pulse?.liquidity || "18.7T VNĐ", "ink"],
              ["Độ rộng thị trường", pulse?.breadth || "246 tăng / 118 giảm", "ink"],
              ["Khối ngoại", pulse?.foreign || "+412B ròng", pulse?.foreignTone || "gain"],
              ["Nhóm dẫn dắt", pulse?.leadership || "Ngân hàng · Chứng khoán · Thép", "ink"],
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
            sub="Màu sắc = Biến động ngày · Diện tích = Vốn hóa · Đường kẻ = Xu hướng 1 tháng"
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
    const vn30 = findIndex("VN30") || {};
    const sectorRows: ApiSectorRow[] = Array.isArray(heatmap) ? heatmap : heatmap?.sectors || heatmap?.data || [];
    const sectors: DashboardSector[] = sectorRows.map((row) => ({
      name: String(row.name || row.sector || "—"),
      vn: String(row.nameVi || row.name || row.sector || "—"),
      weight: Number(row.weight || row.marketWeight || row.market_cap || 1),
      changePct: Number(row.changePct || row.change_pct || row.change || 0),
      foreign: Number(row.foreign || row.foreignFlow || row.foreign_flow || 0),
      sparkline: row.sparkline,
    }));
    const rawStocks: ApiMarketStock[] = Array.isArray(snap?.stocks) ? snap.stocks : [];
    const advancing = rawStocks.filter((s) => Number(s.change_pct) > 0).length;
    const declining = rawStocks.filter((s) => Number(s.change_pct) < 0).length;
    const totalVal = rawStocks.reduce((sum, s) => sum + Number(s.price ?? 0) * Number(s.volume ?? 0), 0);
    const foreignSum = Math.round(rawStocks.reduce((sum, s) => sum + Number(s.foreign_flow ?? 0), 0));
    const topLeaders = sectorRows.slice(0, 3).map((r) => r.sector || r.name).filter(Boolean).join(" · ");

    const regimeLabel = overview?.regime?.regime_label || overview?.regime?.dominant_regime;
    const stateStr = regimeLabel ? `${regimeLabel} · Tin cậy ${(Number(overview?.regime?.confidence ?? 0.8) * 100).toFixed(0)}%` : "Tích lũy · Biên độ hẹp";

    const pulse = {
      state: stateStr,
      liquidity: totalVal > 0 ? `${(totalVal / 1e12).toFixed(1)}T VNĐ` : "18.5T VNĐ",
      breadth: rawStocks.length > 0 ? `${advancing} tăng / ${declining} giảm` : "Cân bằng",
      foreign: `${foreignSum >= 0 ? "+" : ""}${foreignSum}B ròng`,
      foreignTone: foreignSum >= 0 ? "gain" : "loss",
      leadership: topLeaders || "Ngân hàng · Thép · Công nghệ",
    };

    return {
      sectors,
      pulse,
      indices: {
        vnindex: Number(vn.value || vn.indexValue || vn.close || 0),
        vnindexChange: Number(vn.changePct || vn.change_pct || vn.change || 0),
        vn30: Number(vn30.value || vn30.indexValue || vn30.close || 0),
        vn30Change: Number(vn30.changePct || vn30.change_pct || vn30.change || 0),
      },
    };
  }, []);
  return <DataState loading={resource.loading} error={resource.error} empty={!resource.data?.sectors.length} retry={() => void resource.reload()}>{resource.data && <DashboardView {...resource.data} />}</DataState>;
}
