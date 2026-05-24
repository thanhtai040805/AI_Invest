"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft,
  AlertTriangle,
  Play,
  Loader2,
} from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import {
  api,
  type AlphaDetailResponse,
} from "@/lib/api";

function metaString(meta: Record<string, unknown>, key: string): string {
  const v = meta[key];
  if (v === undefined || v === null || v === "") return "—";
  if (Array.isArray(v)) return v.join(", ");
  return String(v);
}

function MetaRow({ label, value, last }: { label: string; value: string; last?: boolean }) {
  return (
    <tr className={cn(!last && "border-b", "hover:bg-muted/20")}>
      <td className="px-4 py-2 text-xs text-muted-foreground w-1/3">{label}</td>
      <td className="px-4 py-2 text-xs font-mono break-all">{value}</td>
    </tr>
  );
}

export default function AlphaZooDetail() {
  const params = useParams<{ alphaId: string }>();
  const router = useRouter();
  const alphaId = params.alphaId ?? "";

  const [detail, setDetail] = useState<AlphaDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    api
      .getAlpha(alphaId)
      .then((res) => {
        if (alive) setDetail(res);
      })
      .catch((err: unknown) => {
        if (!alive) return;
        const msg = err instanceof Error ? err.message : "Failed to load alpha";
        setError(msg);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [alphaId]);

  if (loading) {
    return (
      <div className="p-8 flex items-center justify-center text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin mr-2" aria-hidden="true" /> Loading {alphaId}…
      </div>
    );
  }

  if (error || !detail) {
    return (
      <div className="p-8 max-w-3xl mx-auto space-y-4">
        <Link href="/alpha-zoo" className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1">
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" /> Back to Alpha Zoo
        </Link>
        <div className="border rounded-xl p-6 bg-card">
          <h2 className="font-semibold text-sm mb-1 flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-warning" aria-hidden="true" /> Could not load alpha
          </h2>
          <p className="text-sm text-muted-foreground">{error || "Unknown error"}</p>
        </div>
      </div>
    );
  }

  const a = detail.alpha;
  const meta = a.meta || {};
  const formulaLatex = (meta["formula_latex"] as string | undefined) || "";
  const nickname = (meta["nickname"] as string | undefined) || "";
  const firstUniverse = ((meta["universe"] as string[] | undefined) || [])[0] || "";

  const benchHref = firstUniverse
    ? `/alpha-zoo/bench?zoo=${encodeURIComponent(a.zoo)}&universe=${encodeURIComponent(firstUniverse)}&period=2020-2025`
    : `/alpha-zoo/bench?zoo=${encodeURIComponent(a.zoo)}&period=2020-2025`;

  return (
    <div className="p-4 md:p-8 max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <Link
          href="/alpha-zoo"
          className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" /> Back to Alpha Zoo
        </Link>
        <button
          type="button"
          onClick={() => router.push(benchHref)}
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md bg-primary text-primary-foreground text-xs font-medium hover:opacity-90 transition"
        >
          <Play className="h-3.5 w-3.5" aria-hidden="true" /> Run bench
        </button>
      </div>

      {/* Title */}
      <div className="space-y-1">
        <div className="flex items-center gap-2 flex-wrap">
          <h1 className="font-mono text-xl md:text-2xl font-bold tracking-tight">
            {a.id}
          </h1>
          <span className="text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary font-medium">
            {a.zoo}
          </span>
        </div>
        {nickname && (
          <p className="text-sm text-muted-foreground">{nickname}</p>
        )}
      </div>

      {/* Formula */}
      <section className="space-y-2">
        <h2 className="text-sm font-medium text-muted-foreground">Formula</h2>
        <pre className="border rounded-xl bg-muted/30 p-4 overflow-x-auto text-xs leading-relaxed">
          <code>{formulaLatex || "(no formula provided)"}</code>
        </pre>
      </section>

      {/* Metadata */}
      <section className="space-y-2">
        <h2 className="text-sm font-medium text-muted-foreground">Metadata</h2>
        <div className="border rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <tbody>
              <MetaRow label="Theme" value={metaString(meta, "theme")} />
              <MetaRow label="Universe" value={metaString(meta, "universe")} />
              <MetaRow label="Frequency" value={metaString(meta, "frequency")} />
              <MetaRow label="Decay horizon" value={metaString(meta, "decay_horizon")} />
              <MetaRow label="Min warm-up bars" value={metaString(meta, "min_warmup_bars")} />
              <MetaRow label="Requires sector" value={metaString(meta, "requires_sector")} />
              <MetaRow label="Module path" value={a.module_path || "—"} />
              <MetaRow label="Notes" value={metaString(meta, "notes")} last />
            </tbody>
          </table>
        </div>
      </section>

      {/* Source code */}
      <section className="space-y-2">
        <h2 className="text-sm font-medium text-muted-foreground">Source code</h2>
        <details className="border rounded-xl bg-card group">
          <summary className="cursor-pointer px-4 py-3 text-sm font-medium hover:bg-muted/40 select-none">
            View source ({(detail.source_code || "").split("\n").length} lines)
          </summary>
          <pre className="border-t bg-muted/30 p-4 overflow-x-auto text-xs leading-relaxed">
            <code>{detail.source_code || "(no source available)"}</code>
          </pre>
        </details>
      </section>
    </div>
  );
}
