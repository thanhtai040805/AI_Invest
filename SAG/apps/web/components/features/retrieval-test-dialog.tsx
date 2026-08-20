"use client";

import * as React from "react";
import { FlaskConical, Search } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { api, ApiError } from "@/lib/api";
import type { Section } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function RetrievalTestDialog({
  sourceId,
  sourceName,
  open,
  onOpenChange,
}: {
  sourceId: string;
  sourceName: string;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const t = useTranslations("RetrievalTest");
  const [query, setQuery] = React.useState("");
  const [topK, setTopK] = React.useState(8);
  const [results, setResults] = React.useState<Section[] | null>(null);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    if (!open) return;
    setResults(null);
    setQuery("");
  }, [open]);

  async function run(e?: React.FormEvent) {
    e?.preventDefault();
    if (!query.trim()) return;
    setBusy(true);
    try {
      const r = await api.globalSearch({ query, source_ids: [sourceId], top_k: topK });
      setResults(r.sections);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FlaskConical className="size-4 text-foreground" />
            {t("title", { source: sourceName })}
          </DialogTitle>
          <DialogDescription>
            {t("description")}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={run} className="flex items-end gap-2">
          <div className="flex flex-1 flex-col gap-1.5">
            <Label htmlFor="rt-q">{t("query")}</Label>
            <Input
              id="rt-q"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("queryPlaceholder")}
              autoFocus
            />
          </div>
          <div className="flex w-20 flex-col gap-1.5">
            <Label htmlFor="rt-k">top_k</Label>
            <Input
              id="rt-k"
              type="number"
              min={1}
              max={50}
              value={topK}
              onChange={(e) => setTopK(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
            />
          </div>
          <Button type="submit" disabled={busy || !query.trim()}>
            {busy ? <Spinner /> : <Search className="size-4" />}
            {t("run")}
          </Button>
        </form>

        {results && (
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>{t("resultCount", { count: results.length })}</span>
            </div>

            <div className="max-h-[22rem] overflow-y-auto rounded-lg border">
              {results.length === 0 ? (
                <p className="px-3 py-8 text-center text-sm text-muted-foreground">
                  {t("empty")}
                </p>
              ) : (
                results.map((s, i) => (
                  <div key={`${s.chunk_id}-${i}`} className="border-t p-3 first:border-t-0">
                    <div className="mb-1 flex items-center gap-2">
                      <span className="grid size-5 shrink-0 place-items-center rounded-[6px] bg-muted text-[11px] font-semibold text-foreground">
                        {i + 1}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                        {s.heading || t("section")}
                      </span>
                      <span className="shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">
                        {s.score.toFixed(4)}
                      </span>
                    </div>
                    {/* Thanh điểm: trực quan so sánh độ mạnh yếu của độ thu hồi */}
                    <div className="mb-1.5 ml-7 h-1 overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-primary"
                        style={{ width: `${Math.max(4, Math.min(100, s.score * 100))}%` }}
                      />
                    </div>
                    <p className="ml-7 line-clamp-2 text-xs text-muted-foreground">{s.content}</p>
                  </div>
                ))
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
