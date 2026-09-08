"use client";

import * as React from "react";
import { Search } from "lucide-react";

import { api, ApiError } from "@/lib/api";
import type { Doc, GilAssessment, MoatAssessment } from "@/lib/types";
import { useApp } from "@/components/features/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

type EvidenceHit = Awaited<ReturnType<typeof api.searchTickerEvidence>>["hits"][number];

const REQUIRED_ROLES = ["ANNUAL_BACKBONE", "LATEST_QUARTER", "GOVERNANCE_REPORT"] as const;

export default function KnowledgePage() {
  const { appMode } = useApp();
  const [ticker, setTicker] = React.useState("HPG");
  const [query, setQuery] = React.useState("công ty con tỷ lệ sở hữu giao dịch bên liên quan");
  const [documents, setDocuments] = React.useState<Doc[]>([]);
  const [moat, setMoat] = React.useState<MoatAssessment | null>(null);
  const [gil, setGil] = React.useState<GilAssessment | null>(null);
  const [hits, setHits] = React.useState<EvidenceHit[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");

  const normalizedTicker = ticker.trim().toUpperCase();

  const load = React.useCallback(async () => {
    if (!normalizedTicker) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    try {
      const [docs, moatAssessment, gilAssessment] = await Promise.all([
        api.listTickerDocuments(normalizedTicker, controller.signal),
        api.getMoatByTicker(normalizedTicker, controller.signal),
        api.getGilByTicker(normalizedTicker, controller.signal),
      ]);
      setDocuments(docs);
      setMoat(moatAssessment);
      setGil(gilAssessment);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Không tải được dữ liệu SAG v2");
    } finally {
      setLoading(false);
    }
    return () => controller.abort();
  }, [normalizedTicker]);

  React.useEffect(() => {
    if (appMode === "normal") void load();
  }, [appMode, load]);

  async function runSearch() {
    if (!normalizedTicker || !query.trim()) return;
    setLoading(true);
    setError("");
    try {
      const result = await api.searchTickerEvidence(normalizedTicker, query.trim(), 8);
      setHits(result.hits);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Không tìm được evidence");
    } finally {
      setLoading(false);
    }
  }

  const activeRoles = new Set(documents.filter((doc) => doc.is_active).map((doc) => doc.doc_role));

  return (
    <main className="mx-auto flex w-full max-w-7xl flex-col gap-6 p-6">
      <section className="flex flex-col gap-4 rounded-2xl border bg-card p-5 shadow-sm">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">SAG v2 admin</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight">Financial Evidence Engine</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Theo dõi bộ ba tài liệu active, trạng thái MOAT/GIL và evidence retrieval trực tiếp từ API v2.
          </p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            value={ticker}
            onChange={(event) => setTicker(event.target.value.toUpperCase())}
            className="sm:max-w-48"
            placeholder="Ticker"
          />
          <Button type="button" onClick={() => void load()} disabled={loading || !normalizedTicker}>
            Load ticker
          </Button>
        </div>
        {error && <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error}</div>}
      </section>

      <section className="grid gap-4 lg:grid-cols-3">
        {REQUIRED_ROLES.map((role) => {
          const doc = documents.find((item) => item.doc_role === role && item.is_active);
          return (
            <div key={role} className="rounded-xl border bg-card p-4">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-sm font-semibold">{role}</h2>
                <Badge variant={doc ? "default" : "secondary"}>{doc ? "ACTIVE" : "MISSING"}</Badge>
              </div>
              {doc ? (
                <dl className="mt-4 space-y-2 text-xs text-muted-foreground">
                  <div className="flex justify-between gap-3"><dt>Status</dt><dd>{doc.status}</dd></div>
                  <div className="flex justify-between gap-3"><dt>Structure</dt><dd>{doc.structure_status}</dd></div>
                  <div className="flex justify-between gap-3"><dt>Extraction</dt><dd>{doc.extraction_status}</dd></div>
                  <div className="flex justify-between gap-3"><dt>Embedding</dt><dd>{doc.embedding_status}</dd></div>
                  <div className="flex justify-between gap-3"><dt>Facts</dt><dd>{doc.fact_count ?? 0}</dd></div>
                </dl>
              ) : (
                <p className="mt-4 text-sm text-muted-foreground">Chưa có active document cho role này.</p>
              )}
            </div>
          );
        })}
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <AssessmentCard
          title="MOAT"
          status={moat?.assessment_status ?? "INSUFFICIENT"}
          primary={moat?.moat_score == null ? "score=null" : `score=${moat.moat_score}`}
          details={[
            `coverage=${moat?.coverage_ratio ?? 0}`,
            `multiplier=${moat?.multiplier ?? "null"}`,
            `active_roles=${Array.from(activeRoles).length}/3`,
          ]}
        />
        <AssessmentCard
          title="GIL"
          status={gil?.analysis_status ?? "DATA_INSUFFICIENT"}
          primary={gil?.gil_flag ?? "DATA_INSUFFICIENT"}
          details={[
            `equity=${gil?.equity_vnd ?? "null"}`,
            `capital_cycles=${gil?.capital_flow_cycles?.length ?? 0}`,
            `rpt=${gil?.related_party_exposure_vnd ?? 0}`,
          ]}
        />
      </section>

      <section className="rounded-2xl border bg-card p-5">
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Evidence query" />
          <Button type="button" onClick={() => void runSearch()} disabled={loading || !query.trim()}>
            <Search className="mr-2 size-4" />
            Search evidence
          </Button>
        </div>
        <div className="mt-4 space-y-3">
          {loading && <Skeleton className="h-20 rounded-xl" />}
          {!loading && hits.length === 0 && <p className="text-sm text-muted-foreground">Chưa có evidence hit.</p>}
          {hits.map((hit) => (
            <article key={`${hit.document_id}:${hit.node_id}:${hit.quote_hash}`} className="rounded-xl border bg-background p-4">
              <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <Badge variant="secondary">{hit.doc_role}</Badge>
                <span>{hit.node_id}</span>
                <span>lines {hit.line_span[0]}-{hit.line_span[1]}</span>
                <span>score {hit.score.toFixed(3)}</span>
              </div>
              <p className="whitespace-pre-wrap text-sm">{hit.content}</p>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}

function AssessmentCard({
  title,
  status,
  primary,
  details,
}: {
  title: string;
  status: string;
  primary: string;
  details: string[];
}) {
  return (
    <div className="rounded-xl border bg-card p-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold">{title}</h2>
        <Badge variant={status.includes("COMPLETE") || status === "PASS" ? "default" : "secondary"}>{status}</Badge>
      </div>
      <p className="mt-4 text-2xl font-semibold">{primary}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {details.map((detail) => (
          <Badge key={detail} variant="outline">{detail}</Badge>
        ))}
      </div>
    </div>
  );
}
