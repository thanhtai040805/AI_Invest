"use client"

import { Page } from "@/components/Shell";
import { Link } from "@/lib/router";
type Sector = { name: string; vn: string; weight: number; weightByCount: boolean; count: number; changePct: number | null; foreign: number | null };
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
} from "@/components/ui";
import { KLineChart } from "@/components/KLineChart";
import type { ApiMarketStock } from "@/types";
import { sectorWeights, formatSectorName } from "@/lib/sector-weights";
import { SuperStocksTable, type SuperStocksPayload } from "@/components/SuperStocksTable";

type DashboardSector = Sector & { sparkline?: number[]; foreignKnown?: boolean };
interface ApiSectorRow { name?: string; sector?: string; nameVi?: string; weight?: number; marketWeight?: number; market_cap?: number; marketCap?: number; marketCapCount?: number; market_cap_count?: number; count?: number; changePct?: number; change_pct?: number; change?: number; foreign?: number; foreignFlow?: number; foreign_flow?: number; sparkline?: number[] }

function formatBreadth(advancers: number, decliners: number, available?: number, total?: number) {
  if (available === 0) return total ? `Chưa có dữ liệu biến động · 0/${total} mã` : "—";
  const coverage = available != null && total != null && total > 0 && available < total
    ? ` · ${available}/${total} mã có dữ liệu`
    : "";
  return `${advancers} tăng / ${decliners} giảm${coverage}`;
}

function DashboardView({
  sectors,
  indices,
  pulse,
  isLive,
  superStocks,
  extraLoading,
}: {
  sectors: Sector[];
  indices: { vnindex: number; vnindexChange: number | null };
  isLive: boolean;
  superStocks?: SuperStocksPayload | null;
  extraLoading?: boolean;
  pulse?: {
    state: string;
    liquidity: string;
    breadth: string;
    foreign: string;
    foreignTone: string;
    leadership: string;
  };
}) {
  const exportReport = () => {
    const rows: (string | number | null)[][] = [
      ["Báo cáo thị trường AIInvest", new Date().toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" })],
      ["Trạng thái dữ liệu", isLive ? "Đang nhận tick" : "Dữ liệu gần nhất"],
      ["VN-Index", indices.vnindex > 0 ? indices.vnindex : ""],
      ["Thay đổi VN-Index (%)", indices.vnindexChange],
      ["Trạng thái thị trường", pulse?.state ?? ""],
      ["Thanh khoản", pulse?.liquidity ?? ""],
      ["Độ rộng", pulse?.breadth ?? ""],
      ["Khối ngoại", pulse?.foreign ?? ""],
      ["Nhóm dẫn dắt", pulse?.leadership ?? ""],
      [],
      ["Ngành", "Mã", "Tỷ trọng (%)", "Biến động (%)", "Khối ngoại"],
      ...sectors.map((sector) => [formatSectorName(sector.name), sector.count, sector.weight, sector.changePct, sector.foreign] as (string | number | null)[]),
    ];
    const csv = rows.map((row) => row.map((value) => {
      const text = String(value ?? "");
      const safe = typeof value === "string" && /^[=+\-@]/.test(text) ? `'${text}` : text;
      return `"${safe.replaceAll('"', '""')}"`;
    }).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `aiinvest-market-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
  };
  return (
    <Page
      title="Tổng quan thị trường"
      sub="Thị trường chứng khoán Việt Nam · Phiên giao dịch HOSE · Danh mục Siêu phẩm VSA & Dòng tiền"
      actions={
        <>
          <Button variant="secondary" onClick={exportReport}>Xuất báo cáo CSV</Button>
          <Link to="/sectors">
            <Button variant="secondary">Sức mạnh ngành</Button>
          </Link>
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
                {indices.vnindex > 0 && (indices.vnindexChange !== null ? <PercentChange value={indices.vnindexChange} arrow={false} className="text-[14px]" /> : <span className="text-sm text-muted">—</span>)}
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

      <div className="mt-4">
        <SuperStocksTable data={superStocks} loading={extraLoading} showVolumeSurge />
      </div>
    </Page>
  );
}

export default function Dashboard() {
  const { heatmap: liveHeatmap, indices: liveIndices, isLive, breadth: liveBreadth, liquidity: liveLiquidity, snapshot: liveSnapshot } = useRealtimeMarket();
  const extraResource = useResource(async () => {
    const superStocks = await marketApi.superStocks().catch(() => null);
    return { superStocks: superStocks as SuperStocksPayload | null };
  }, []);
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
    const { weights, weightByCount } = sectorWeights(sectorRows);
    const optionalNumber = (value: unknown) => {
      if (value == null) return null;
      const number = Number(value);
      return Number.isFinite(number) ? number : null;
    };
    const sectors: DashboardSector[] = sectorRows.map((row, index) => ({
      name: String(row.name || row.sector || "—"),
      vn: String(row.nameVi || row.name || row.sector || "—"),
      weight: weights[index],
      weightByCount,
      count: Number(row.count ?? 0),
      changePct: optionalNumber(row.changePct ?? row.change_pct ?? row.change),
      foreign: optionalNumber(row.foreign ?? row.foreignFlow ?? row.foreign_flow),
      foreignKnown: optionalNumber(row.foreign ?? row.foreignFlow ?? row.foreign_flow) !== null,
      sparkline: row.sparkline,
    }));
    const rawStocks: ApiMarketStock[] = Array.isArray(snap?.stocks) ? snap.stocks : [];
    const stockChange = (stock: ApiMarketStock) => optionalNumber(stock.changePercent ?? stock.change_pct);
    const stockForeign = (stock: ApiMarketStock) => optionalNumber(stock.foreignFlow ?? stock.foreign_flow);
    const haveAllChanges = rawStocks.length > 0 && rawStocks.every((stock) => stockChange(stock) !== null);
    const haveAllForeign = rawStocks.length > 0 && rawStocks.every((stock) => stockForeign(stock) !== null);
    const advancing = rawStocks.filter((stock) => Number(stockChange(stock)) > 0).length;
    const declining = rawStocks.filter((stock) => Number(stockChange(stock)) < 0).length;
    const foreignSum = haveAllForeign ? Math.round(rawStocks.reduce((sum, stock) => sum + Number(stockForeign(stock)), 0)) : null;
    const topLeaders = [...sectorRows]
      .filter((row) => {
        const change = optionalNumber(row.changePct ?? row.change_pct ?? row.change);
        return change !== null && change > 0;
      })
      .sort((a, b) => Number(b.changePct ?? b.change_pct ?? b.change) - Number(a.changePct ?? a.change_pct ?? a.change))
      .slice(0, 3).map((row) => formatSectorName(String(row.sector || row.name || ""))).filter(Boolean).join(" · ");

    const regimeLabel = overview?.regime?.regime_label || overview?.regime?.dominant_regime;
    const confidence = overview?.regime?.confidence;
    const stateStr = regimeLabel ? `${regimeLabel}${confidence == null || !Number.isFinite(Number(confidence)) ? "" : ` · Tin cậy ${(Number(confidence) * 100).toFixed(0)}%`}` : "—";

    const pulse = {
      state: stateStr,
      liquidity: "—",
      breadth: haveAllChanges ? `${advancing} tăng / ${declining} giảm` : "—",
      foreign: foreignSum !== null ? `${foreignSum >= 0 ? "+" : ""}${foreignSum}B ròng` : "—",
      foreignTone: foreignSum === null ? "neutral" : foreignSum >= 0 ? "gain" : "loss",
      leadership: topLeaders || "—",
    };

    return {
      sectors,
      pulse,
      indices: {
        vnindex: optionalNumber(vn.value ?? vn.indexValue ?? vn.close) ?? 0,
        vnindexChange: optionalNumber(vn.changePct ?? vn.change_pct ?? vn.change),
      },
    };
  }, []);
  const liveRows = liveHeatmap?.sectors;
  const liveWeights = sectorWeights(liveRows ?? []);
  const optionalNumber = (value: unknown) => {
    if (value == null) return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  };
  const dailyForeignBySector = new Map((resource.data?.sectors ?? []).map((sector) => [sector.name, sector.foreign]));
  const realtimeSectors: DashboardSector[] | undefined = liveRows?.map((row, index) => {
    const name = String(row.name || row.sector || "—");
    const changePct = optionalNumber(row.changePct ?? row.change_pct ?? row.change);
    const foreign = optionalNumber(dailyForeignBySector.get(name));
    return {
      name,
      vn: String(row.nameVi || name),
      weight: liveWeights.weights[index],
      weightByCount: liveWeights.weightByCount,
      count: Number(row.count ?? 0),
      changePct,
      foreign,
      foreignKnown: foreign !== null,
      sparkline: Array.isArray(row.sparkline) ? row.sparkline as number[] : undefined,
    };
  });
  const liveStocks = liveSnapshot?.stocks;
  const liveForeign = liveStocks?.length && liveStocks.every((stock) => stock.foreign_flow != null && Number.isFinite(Number(stock.foreign_flow)))
    ? liveStocks.reduce((sum, stock) => sum + Number(stock.foreign_flow), 0)
    : undefined;
  const seedPulse = resource.data?.pulse;
  const liveBreadthSummary = liveBreadth && typeof liveBreadth.advancers === "number" && typeof liveBreadth.decliners === "number"
    ? formatBreadth(liveBreadth.advancers, liveBreadth.decliners, liveBreadth.available, liveBreadth.total)
    : null;
  const pulse = {
    state: seedPulse?.state ?? "—",
    breadth: liveBreadthSummary ?? seedPulse?.breadth ?? "—",
    liquidity: liveLiquidity?.totalValueBillion != null && Number.isFinite(Number(liveLiquidity.totalValueBillion)) ? `${(Number(liveLiquidity.totalValueBillion) / 1000).toFixed(1)}T VNĐ` : seedPulse?.liquidity ?? "—",
    foreign: liveForeign != null ? `${liveForeign >= 0 ? "+" : ""}${liveForeign.toFixed(0)}B ròng` : seedPulse?.foreign ?? "—",
    foreignTone: liveForeign != null ? (liveForeign >= 0 ? "gain" : "loss") : seedPulse?.foreignTone ?? "neutral",
    leadership: seedPulse?.leadership ?? "—",
  };
  const seedIndices = resource.data?.indices;
  const liveIndexValue = Number(liveIndices?.vnIndexVal?.replaceAll(".", "").replace(",", "."));
  const indices = {
    vnindex: Number.isFinite(liveIndexValue) && liveIndexValue > 0 ? liveIndexValue : seedIndices?.vnindex ?? 0,
    vnindexChange: liveIndices?.vnIndexPct ?? seedIndices?.vnindexChange ?? null,
  };
  const sectors = realtimeSectors ?? resource.data?.sectors ?? [];
  return <DataState loading={resource.loading && !sectors.length} error={sectors.length ? null : resource.error} empty={!sectors.length} retry={() => void resource.reload()}><DashboardView sectors={sectors} indices={indices} pulse={pulse} isLive={isLive} superStocks={extraResource.data?.superStocks} extraLoading={extraResource.loading} /></DataState>;
}
