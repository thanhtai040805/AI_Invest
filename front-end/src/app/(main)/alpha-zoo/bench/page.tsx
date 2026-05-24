"use client";

import { Suspense, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  ArrowLeft,
  Play,
  Loader2,
  CheckCircle2,
  AlertTriangle,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import {
  api,
  type AlphaBenchResult,
  type AlphaBenchTopRow,
} from "@/lib/api";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";

/* ---------- Constants ---------- */

interface ZooCard {
  id: string;
  title: string;
  description: string;
  approxCount: number;
  accent: string;
}

const ZOO_CARDS: ZooCard[] = [
  {
    id: "qlib158",
    title: "Qlib 158",
    description:
      "Microsoft Qlib's full 158-feature library covering momentum, volatility, volume and rolling statistical signals.",
    approxCount: 154,
    accent: "from-sky-500/20 to-sky-500/5",
  },
  {
    id: "alpha101",
    title: "Kakushadze 101 Formulaic Alphas",
    description:
      "The 101 formulaic alphas from Kakushadze (2015); short-horizon cross-sectional signals.",
    approxCount: 101,
    accent: "from-emerald-500/20 to-emerald-500/5",
  },
  {
    id: "gtja191",
    title: "GTJA 191",
    description:
      "Guotai Junan Securities' 191 alphas; technical and microstructure signals tuned to China A-share markets.",
    approxCount: 191,
    accent: "from-amber-500/20 to-amber-500/5",
  },
  {
    id: "academic",
    title: "Academic Anomalies",
    description:
      "Curated long-horizon anomalies from the academic literature (value, momentum, quality, low-vol, etc.).",
    approxCount: 6,
    accent: "from-violet-500/20 to-violet-500/5",
  },
];

const UNIVERSE_OPTIONS = [
  { value: "csi300", label: "CSI 300 (China A)" },
  { value: "sp500", label: "S&P 500 (US)" },
  { value: "btc-usdt", label: "BTC-USDT (Crypto)" },
];

/* ---------- Helpers ---------- */

function fmtNum(v: unknown, digits = 3): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}

/* ---------- Types ---------- */

type BenchStatus = "idle" | "submitting" | "streaming" | "done" | "error";

interface BenchProgress {
  n_done: number;
  n_total: number;
  current_alpha_id?: string;
}

/* ---------- Sub-components ---------- */

function ProgressPanel({
  jobId,
  progress,
}: {
  jobId: string | null;
  progress: BenchProgress | null;
}) {
  const pct = progress && progress.n_total > 0
    ? Math.min(100, Math.round((progress.n_done / progress.n_total) * 100))
    : 0;
  return (
    <div className="border rounded-xl p-4 bg-card space-y-3">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          {jobId ? `Job ${jobId.slice(0, 12)}…` : "Submitting…"}
        </span>
        {progress && (
          <span className="font-mono tabular-nums">
            {progress.n_done} / {progress.n_total}
          </span>
        )}
      </div>
      <div className="h-2 rounded-full bg-muted overflow-hidden">
        <div
          className="h-full bg-primary transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
      {progress?.current_alpha_id && (
        <p className="text-xs text-muted-foreground font-mono truncate">
          Computing: {progress.current_alpha_id}
        </p>
      )}
    </div>
  );
}

function ResultPanel({ result }: { result: AlphaBenchResult }) {
  const { dark } = useDarkMode();
  const chartRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!chartRef.current) return;
    const theme = getChartTheme();
    const chart = echarts.init(chartRef.current);
    const themes = Object.keys(result.by_theme || {}).sort();
    const aliveSeries = themes.map((k) => result.by_theme[k].alive);
    const reversedSeries = themes.map((k) => result.by_theme[k].reversed);
    const deadSeries = themes.map((k) => result.by_theme[k].dead);

    chart.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      legend: {
        data: ["Alive", "Reversed", "Dead"],
        textStyle: { color: theme.textColor, fontSize: 11 },
        right: 8,
        top: 4,
      },
      grid: { left: 8, right: 8, top: 32, bottom: 8, containLabel: true },
      xAxis: {
        type: "category",
        data: themes,
        axisLine: { lineStyle: { color: theme.axisColor } },
        axisLabel: { color: theme.textColor, fontSize: 10, rotate: themes.length > 6 ? 30 : 0 },
      },
      yAxis: {
        type: "value",
        splitLine: { lineStyle: { color: theme.gridColor } },
        axisLabel: { color: theme.textColor, fontSize: 10 },
      },
      series: [
        { name: "Alive", type: "bar", stack: "n", data: aliveSeries, itemStyle: { color: theme.upColor } },
        { name: "Reversed", type: "bar", stack: "n", data: reversedSeries, itemStyle: { color: theme.warningColor } },
        { name: "Dead", type: "bar", stack: "n", data: deadSeries, itemStyle: { color: theme.downColor } },
      ],
    });

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(chartRef.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [result, dark]);

  const totals = [
    { label: "Alive", value: result.alive, icon: CheckCircle2, tone: "text-green-600 dark:text-green-400" },
    { label: "Reversed", value: result.reversed, icon: AlertTriangle, tone: "text-amber-600 dark:text-amber-400" },
    { label: "Dead", value: result.dead, icon: XCircle, tone: "text-red-600 dark:text-red-400" },
    { label: "Skipped", value: result.skipped ?? 0, icon: Loader2, tone: "text-muted-foreground" },
  ];

  return (
    <div className="space-y-4">
      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {totals.map(({ label, value, icon: Icon, tone }) => (
          <div key={label} className="border rounded-xl p-4 bg-card flex items-center gap-3">
            <Icon className={cn("h-5 w-5 shrink-0", tone)} aria-hidden="true" />
            <div>
              <p className="text-xs text-muted-foreground">{label}</p>
              <p className="text-xl font-bold tabular-nums">{value}</p>
            </div>
          </div>
        ))}
      </div>

      {/* Top tables */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <TopTable title="Top 5 by IR" rows={result.top5_by_ir || []} />
        <TopTable title="Most reversed" rows={(result.dead_examples || []).slice(0, 3)} />
      </div>

      {/* By-theme breakdown */}
      {result.by_theme && Object.keys(result.by_theme).length > 0 && (
        <div className="border rounded-xl p-4 bg-card">
          <h3 className="text-sm font-medium text-muted-foreground mb-2">
            By theme
          </h3>
          <div ref={chartRef} style={{ height: 240 }} />
        </div>
      )}
    </div>
  );
}

function TopTable({ title, rows }: { title: string; rows: AlphaBenchTopRow[] }) {
  return (
    <div className="border rounded-xl overflow-hidden bg-card">
      <div className="px-4 py-2.5 border-b bg-muted/40">
        <h3 className="text-sm font-medium">{title}</h3>
      </div>
      {rows.length === 0 ? (
        <div className="px-4 py-6 text-xs text-muted-foreground text-center">
          No rows.
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b">
              <th className="text-left px-4 py-2 text-xs text-muted-foreground font-medium">ID</th>
              <th className="text-right px-4 py-2 text-xs text-muted-foreground font-medium">Mean IC</th>
              <th className="text-right px-4 py-2 text-xs text-muted-foreground font-medium">IR</th>
              <th className="text-left px-4 py-2 text-xs text-muted-foreground font-medium">Theme</th>
              <th className="text-left px-4 py-2 text-xs text-muted-foreground font-medium">Category</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b last:border-0 hover:bg-muted/20">
                <td className="px-4 py-2">
                  <Link
                    href={`/alpha-zoo/${encodeURIComponent(r.id)}`}
                    className="text-primary hover:underline font-mono text-xs"
                  >
                    {r.id}
                  </Link>
                </td>
                <td className="px-4 py-2 text-right font-mono tabular-nums text-xs">{fmtNum(r.ic_mean)}</td>
                <td className="px-4 py-2 text-right font-mono tabular-nums text-xs">{fmtNum(r.ir)}</td>
                <td className="px-4 py-2 text-xs text-muted-foreground">{(r.theme || []).join(", ") || "—"}</td>
                <td className="px-4 py-2 text-xs">
                  <CategoryBadge category={r.category} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function CategoryBadge({ category }: { category: AlphaBenchTopRow["category"] }) {
  const tone =
    category === "alive"
      ? "bg-green-500/10 text-green-700 dark:text-green-300"
      : category === "reversed"
        ? "bg-amber-500/10 text-amber-700 dark:text-amber-300"
        : "bg-red-500/10 text-red-700 dark:text-red-300";
  return (
    <span className={cn("inline-block px-2 py-0.5 rounded-full text-[10px] font-medium", tone)}>
      {category}
    </span>
  );
}

/* ---------- Main content ---------- */

function BenchContent() {
  const searchParams = useSearchParams();
  const initial = useMemo(() => {
    return {
      zoo: searchParams.get("zoo") || "alpha101",
      universe: searchParams.get("universe") || "csi300",
      period: searchParams.get("period") || "2020-2025",
      top: Number(searchParams.get("top") || "20"),
    };
  }, [searchParams]);

  const [zoo, setZoo] = useState(initial.zoo);
  const [universe, setUniverse] = useState(initial.universe);
  const [period, setPeriod] = useState(initial.period);
  const [top, setTop] = useState<number>(initial.top);

  const [status, setStatus] = useState<BenchStatus>("idle");
  const [jobId, setJobId] = useState<string | null>(null);
  const [progress, setProgress] = useState<BenchProgress | null>(null);
  const [result, setResult] = useState<AlphaBenchResult | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const doneRef = useRef(false);

  useEffect(() => {
    return () => {
      sourceRef.current?.close();
      sourceRef.current = null;
    };
  }, []);

  const startBench = async (e: FormEvent) => {
    e.preventDefault();
    if (status === "submitting" || status === "streaming") return;
    setStatus("submitting");
    setProgress(null);
    setResult(null);
    setFormError(null);
    doneRef.current = false;
    sourceRef.current?.close();
    const safeTop = Number.isFinite(top) && top > 0 ? top : 20;
    try {
      const res = await api.createAlphaBench({
        zoo,
        universe,
        period,
        top: safeTop,
      });
      setJobId(res.job_id);
      attachStream(res.job_id);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to start bench";
      if (msg.toLowerCase().includes("single-asset")) {
        setFormError(
          `${msg} Try \`sp500\` or \`csi300\` for a meaningful cross-sectional IC.`,
        );
      } else {
        toast.error(msg);
      }
      setStatus("error");
    }
  };

  const attachStream = (newJobId: string) => {
    setStatus("streaming");
    const url = api.alphaBenchStreamUrl(newJobId);
    const source = new EventSource(url);
    sourceRef.current = source;

    source.addEventListener("progress", (e) => {
      try {
        const data = JSON.parse((e as MessageEvent).data) as BenchProgress;
        setProgress(data);
      } catch {
        /* ignore */
      }
    });

    source.addEventListener("result", (e) => {
      try {
        const data = JSON.parse((e as MessageEvent).data) as AlphaBenchResult;
        setResult(data);
      } catch {
        /* ignore */
      }
    });

    source.addEventListener("done", () => {
      doneRef.current = true;
      setStatus("done");
      source.close();
      sourceRef.current = null;
    });

    source.addEventListener("error", (e) => {
      if (doneRef.current) {
        source.close();
        sourceRef.current = null;
        return;
      }
      let msg = "Bench stream error";
      try {
        const data = JSON.parse((e as MessageEvent).data || "{}");
        if (typeof data.message === "string") msg = data.message;
      } catch {
        /* network-level error, no payload */
      }
      toast.error(msg);
      setStatus("error");
      source.close();
      sourceRef.current = null;
    });
  };

  const busy = status === "submitting" || status === "streaming";

  return (
    <div className="p-4 md:p-8 max-w-5xl mx-auto space-y-6">
      <Link
        href="/alpha-zoo"
        className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
      >
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" /> Back to Alpha Zoo
      </Link>

      <div className="space-y-1">
        <div className="flex items-center gap-2 text-xs text-muted-foreground uppercase tracking-wide">
          <Play className="h-3.5 w-3.5" aria-hidden="true" /> Bench runner
        </div>
        <h1 className="text-2xl md:text-3xl font-bold tracking-tight">
          Score a zoo on a universe
        </h1>
        <p className="text-sm text-muted-foreground max-w-2xl">
          Computes IC / IR for every alpha in the selected zoo over the chosen
          universe and period, then bucketizes them as alive / reversed / dead.
        </p>
      </div>

      {/* Form */}
      <form
        onSubmit={startBench}
        className="border rounded-xl p-4 bg-card grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3 items-end"
      >
        <div>
          <label htmlFor="bench-zoo" className="text-xs text-muted-foreground block mb-1">Zoo</label>
          <select
            id="bench-zoo"
            value={zoo}
            onChange={(e) => setZoo(e.target.value)}
            disabled={busy}
            className="w-full px-3 py-2 rounded-lg border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 disabled:opacity-50"
          >
            {ZOO_CARDS.map((z) => (
              <option key={z.id} value={z.id}>
                {z.title}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="bench-universe" className="text-xs text-muted-foreground block mb-1">Universe</label>
          <select
            id="bench-universe"
            value={universe}
            onChange={(e) => setUniverse(e.target.value)}
            disabled={busy}
            className="w-full px-3 py-2 rounded-lg border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 disabled:opacity-50"
          >
            {UNIVERSE_OPTIONS.map((u) => (
              <option key={u.value} value={u.value}>
                {u.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="bench-period" className="text-xs text-muted-foreground block mb-1">Period</label>
          <input
            id="bench-period"
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            disabled={busy}
            placeholder="2020-2025"
            className="w-full px-3 py-2 rounded-lg border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 disabled:opacity-50"
          />
        </div>
        <div>
          <label htmlFor="bench-top" className="text-xs text-muted-foreground block mb-1">Top</label>
          <input
            id="bench-top"
            type="number"
            min={1}
            max={500}
            value={Number.isFinite(top) ? top : ""}
            onChange={(e) =>
              setTop(e.target.value === "" ? 20 : Number(e.target.value))
            }
            disabled={busy}
            className="w-full px-3 py-2 rounded-lg border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 disabled:opacity-50"
          />
        </div>
        <div className="flex flex-col gap-1">
          <button
            type="submit"
            disabled={busy}
            className="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 transition disabled:opacity-50"
          >
            {busy ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> Running…
              </>
            ) : (
              <>
                <Play className="h-3.5 w-3.5" aria-hidden="true" /> Run bench
              </>
            )}
          </button>
        </div>
        {formError && (
          <p
            className="sm:col-span-2 lg:col-span-5 text-xs text-red-600 dark:text-red-400"
            role="alert"
          >
            {formError}
          </p>
        )}
      </form>

      {/* Progress */}
      {(status === "submitting" || status === "streaming") && (
        <ProgressPanel jobId={jobId} progress={progress} />
      )}

      {/* Result */}
      {result && <ResultPanel result={result} />}
    </div>
  );
}

export default function AlphaZooBench() {
  return (
    <Suspense fallback={
      <div className="p-8 flex items-center justify-center text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin mr-2" aria-hidden="true" /> Loading bench…
      </div>
    }>
      <BenchContent />
    </Suspense>
  );
}
