"use client";

import * as React from "react";
import { ArrowLeft, FileText, Plus, RefreshCw, RotateCw } from "lucide-react";
import { useTranslations } from "next-intl";
import { AnimatePresence, motion } from "motion/react";

import { api, ApiError } from "@/lib/api";
import type { GilAssessment, MoatAssessment, Source } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useApp } from "@/components/features/app-shell";
import { CompactDocumentDetailWorkspace } from "@/components/features/document-detail-workspace";
import { DocumentList } from "@/components/features/document-list";
import { SyncPanel } from "@/components/features/sync-panel";
import { UploadZone } from "@/components/features/upload-zone";
import { useSourceContent } from "@/components/features/use-source-content";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

type SourceScreen = "documents" | "add";

function tickerFromSource(source: Source | null): string | null {
  const raw = source?.name?.trim();
  if (!raw) return null;
  const match = /^BCTC[_\s-]+([A-Z0-9]{2,12})$/i.exec(raw) ?? /^([A-Z0-9]{2,12})$/i.exec(raw);
  return match?.[1]?.toUpperCase() ?? null;
}

function formatScore(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "n/a";
  return value.toFixed(1);
}

function formatPercent(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "n/a";
  const normalized = value <= 1 ? value * 100 : value;
  return `${Math.max(0, Math.min(100, normalized)).toFixed(0)}%`;
}

function SourceV2Analysis({ source }: { source: Source | null }) {
  const ticker = tickerFromSource(source);
  const [moat, setMoat] = React.useState<MoatAssessment | null>(null);
  const [gil, setGil] = React.useState<GilAssessment | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    if (!ticker) {
      setMoat(null);
      setGil(null);
      setError("");
      setLoading(false);
      return;
    }
    let alive = true;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    Promise.all([
      api.getMoatByTicker(ticker, controller.signal),
      api.getGilByTicker(ticker, controller.signal),
    ])
      .then(([nextMoat, nextGil]) => {
        if (!alive) return;
        setMoat(nextMoat);
        setGil(nextGil);
      })
      .catch((reason) => {
        if (!alive || controller.signal.aborted) return;
        setMoat(null);
        setGil(null);
        setError(reason instanceof ApiError ? reason.message : "Không tải được MOAT/GIL v2.");
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
      controller.abort();
    };
  }, [ticker]);

  if (!ticker) return null;

  return (
    <div className="mb-2 rounded-lg border bg-card/80 p-2.5 text-xs">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="font-medium">SAG v2 · {ticker}</span>
        {loading && <span className="text-muted-foreground">loading</span>}
      </div>
      {error ? (
        <p className="text-destructive">{error}</p>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2">
          <div className="rounded-md bg-muted/50 p-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">MOAT</span>
              <span>{moat?.assessment_status ?? "n/a"}</span>
            </div>
            <div className="mt-1 text-[11px] text-muted-foreground">
              score {formatScore(moat?.moat_score)} · coverage {formatPercent(moat?.coverage_ratio)}
            </div>
          </div>
          <div className="rounded-md bg-muted/50 p-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">GIL</span>
              <span>{gil?.gil_flag ?? "n/a"}</span>
            </div>
            <div className="mt-1 text-[11px] text-muted-foreground">
              status {gil?.analysis_status ?? "n/a"} · cycles {gil?.cycles_detected ?? 0} · RPT {formatPercent(gil?.rpt_ratio)}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function KnowledgeSourceWorkspace({
  sourceId,
  active = true,
  initialScreen = "documents",
  onBack,
  onSourceChanged,
}: {
  sourceId: string;
  active?: boolean;
  initialScreen?: SourceScreen;
  onBack: () => void;
  onSourceChanged?: () => void | Promise<void>;
}) {
  const t = useTranslations("Knowledge");
  const { capabilities } = useApp();
  const {
    source,
    documents,
    documentActivities,
    error,
    notFound,
    refreshing,
    refresh,
    mutateDocument,
  } = useSourceContent(sourceId, active);
  const [screen, setScreen] = React.useState<SourceScreen>(initialScreen);
  const [documentId, setDocumentId] = React.useState<string | null>(null);

  React.useEffect(() => {
    setScreen(initialScreen);
    setDocumentId(null);
  }, [initialScreen, sourceId]);

  const isFileSource = !source || source.connector_kind === "file_upload";
  const screenKey = documentId ? `document:${documentId}` : screen;

  const goBack = () => {
    if (documentId) {
      setDocumentId(null);
      return;
    }
    if (screen === "add") {
      setScreen("documents");
      return;
    }
    onBack();
  };

  const finishMutation = async () => {
    setScreen("documents");
    await refresh();
    await onSourceChanged?.();
  };

  const title = documentId
    ? source?.name || t("documentDetails")
    : screen === "add"
      ? isFileSource
        ? t("addDocument")
        : t("syncSource")
      : source?.name || t("source");
  const subtitle = documentId
    ? t("documentDetails")
    : screen === "add"
      ? source?.name || t("loading")
      : documents === null
        ? t("syncing")
        : t("documentsCount", { count: documents.length });

  return (
    <motion.div
      initial={{ opacity: 0, x: 8 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: -8 }}
      className="flex h-full min-h-0 flex-col bg-background/30"
    >
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b px-2.5">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-7"
          onClick={goBack}
          aria-label={documentId || screen === "add" ? t("backToSource") : t("back")}
          title={t("backAction")}
        >
          <ArrowLeft className="size-3.5" />
        </Button>
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium">{title}</p>
          <p className="truncate text-[10px] text-muted-foreground">{subtitle}</p>
        </div>
        {!documentId && screen === "documents" && (
          <>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={() => setScreen("add")}
              disabled={!source}
              aria-label={isFileSource ? t("addDocument") : t("syncSource")}
              title={isFileSource ? t("addDocument") : t("syncSource")}
            >
              {isFileSource ? (
                <Plus className="size-3.5" />
              ) : (
                <RefreshCw className="size-3.5" />
              )}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={() => void refresh()}
              disabled={refreshing}
              aria-label={t("refreshDocuments")}
              title={t("refreshDocuments")}
            >
              <RotateCw className={cn("size-3.5", refreshing && "animate-spin")} />
            </Button>
          </>
        )}
      </div>

      <AnimatePresence mode="wait" initial={false}>
        {documentId ? (
          <motion.div
            key={screenKey}
            initial={{ opacity: 0, x: 8 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -8 }}
            className="flex min-h-0 flex-1 flex-col overflow-hidden"
          >
            <CompactDocumentDetailWorkspace
              sourceId={sourceId}
              documentId={documentId}
            />
          </motion.div>
        ) : screen === "add" ? (
          <motion.div
            key={screenKey}
            initial={{ opacity: 0, x: 8 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -8 }}
            className="min-h-0 flex-1 overflow-y-auto p-4"
          >
            <p className="mb-3 text-xs leading-5 text-muted-foreground">
              {isFileSource
                ? t("uploadDescription")
                : t("syncDescription")}
            </p>
            {source &&
              (isFileSource ? (
                <UploadZone
                  sourceId={sourceId}
                  onUploaded={() => void finishMutation()}
                  maxMb={capabilities?.max_upload_mb ?? 25}
                  allowedExts={capabilities?.allowed_upload_exts}
                  compact
                />
              ) : (
                <SyncPanel sourceId={sourceId} onSynced={() => void finishMutation()} />
              ))}
          </motion.div>
        ) : (
          <motion.div
            key={screenKey}
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 8 }}
            className="min-h-0 flex-1 overflow-y-auto p-2"
          >
            {error && (
              <div className="mb-2 flex items-center justify-between gap-2 rounded-md bg-destructive/8 px-3 py-2 text-xs text-destructive">
                <span className="min-w-0 flex-1">{error}</span>
                {!notFound && (
                  <button type="button" onClick={() => void refresh()} className="font-medium">
                    {t("retry")}
                  </button>
                )}
              </div>
            )}
            <SourceV2Analysis source={source} />
            {notFound ? (
              <div className="flex min-h-48 flex-col items-center justify-center px-6 text-center">
                <p className="text-sm font-medium">{t("sourceNotFound")}</p>
                <Button size="sm" variant="outline" className="mt-3" onClick={onBack}>
                  {t("back")}
                </Button>
              </div>
            ) : documents === null || !source ? (
              <div className="space-y-2 p-1">
                {Array.from({ length: 4 }).map((_, index) => (
                  <Skeleton key={index} className="h-14 rounded-lg" />
                ))}
              </div>
            ) : documents.length === 0 ? (
              <div className="flex min-h-56 flex-col items-center justify-center px-6 text-center">
                <div className="grid size-10 place-items-center rounded-xl bg-muted text-muted-foreground">
                  <FileText className="size-4" />
                </div>
                <p className="mt-3 text-sm font-medium">{t("emptyDocuments")}</p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  {isFileSource ? t("emptyDocumentsUpload") : t("emptyDocumentsSync")}
                </p>
                <Button size="sm" className="mt-4" onClick={() => setScreen("add")}>
                  {isFileSource ? (
                    <Plus className="size-3.5" />
                  ) : (
                    <RefreshCw className="size-3.5" />
                  )}
                  {isFileSource ? t("addDocument") : t("syncSource")}
                </Button>
              </div>
            ) : (
              <DocumentList
                sourceId={sourceId}
                documents={documents}
                activities={documentActivities}
                onAction={mutateDocument}
                variant="compact"
                onOpenDocument={(document) => setDocumentId(document.id)}
              />
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
