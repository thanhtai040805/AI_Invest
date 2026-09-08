"use client";

import * as React from "react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { usePathname } from "next/navigation";
import {
  ArrowUpRight,
  ChevronsLeft,
  ChevronsRight,
  Code2,
  Download,
  Eye,
  X,
} from "lucide-react";

import { api, ApiError } from "@/lib/api";
import { getToken } from "@/lib/auth";
import type { CitationEventRef, Doc, DocumentNodeContentResponse, DocumentTreeResponse, Source } from "@/lib/types";
import { formatBytes, formatDate, formatTokenCount, relativeTime } from "@/lib/format";
import { cleanCitationText, stripCitationTransportTokens } from "@/lib/citation-presentation";
import { cn } from "@/lib/utils";
import { MarkdownContent } from "@/components/features/markdown-content";
import { useApp } from "@/components/features/app-shell";
import { DocStatusBadge } from "@/components/features/status-badge";
import { Button } from "@/components/ui/button";
import type { ImperativePanelHandle } from "react-resizable-panels";

import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

/** Mục tiêu của panel chi tiết: phân đoạn nguyên văn của trích dẫn/kết quả tìm kiếm, hoặc tài liệu kho tri thức (gồm cả xem trước file gốc). */
export type DetailTarget =
  | {
      kind: "chunk";
      sourceId: string;
      chunkId: string;
      heading?: string;
      sourceName?: string;
      eventRefs?: CitationEventRef[];
    }
  | { kind: "document"; sourceId: string; documentId: string; title?: string };

interface PanelCtx {
  target: DetailTarget | null;
  maximized: boolean;
  open: (target: DetailTarget) => void;
  close: () => void;
  toggleMaximize: () => void;
  /** Handle lệnh của ResizablePanel chi tiết (phóng to/khôi phục qua resize API chính thức) */
  panelRef: React.RefObject<ImperativePanelHandle | null>;
}

const Ctx = React.createContext<PanelCtx>({
  target: null,
  maximized: false,
  open: () => {},
  close: () => {},
  toggleMaximize: () => {},
  panelRef: { current: null },
});

const DEFAULT_PANEL_SIZE = 34;

function tickerFromSource(source: Source | null): string | null {
  const raw = source?.name?.trim();
  if (!raw) return null;
  const match = /^BCTC[_\s-]+([A-Z0-9]{2,12})$/i.exec(raw) ?? /^([A-Z0-9]{2,12})$/i.exec(raw);
  return match?.[1]?.toUpperCase() ?? null;
}

function shortHash(value?: string | null): string | null {
  if (!value) return null;
  return value.length > 12 ? value.slice(0, 12) : value;
}

function formatCoverage(value?: number | { coverage_ratio?: number } | null): string | null {
  const raw = typeof value === "number" ? value : value?.coverage_ratio;
  if (raw == null || !Number.isFinite(raw)) return null;
  const normalized = raw <= 1 ? raw * 100 : raw;
  return `${Math.max(0, Math.min(100, normalized)).toFixed(0)}%`;
}

export function useDetailPanel() {
  return React.useContext(Ctx);
}

export function DetailPanelProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [target, setTarget] = React.useState<DetailTarget | null>(null);
  const [maximized, setMaximized] = React.useState(false);

  const open = React.useCallback((t: DetailTarget) => {
    setTarget(t);
  }, []);
  const panelRef = React.useRef<ImperativePanelHandle | null>(null);
  const resetPanelSize = React.useCallback(() => {
    panelRef.current?.resize(DEFAULT_PANEL_SIZE);
  }, []);
  const close = React.useCallback(() => {
    resetPanelSize();
    setTarget(null);
    setMaximized(false);
  }, [resetPanelSize]);
  const toggleMaximize = React.useCallback(() => {
    setMaximized((m) => {
      const next = !m;
      const panel = panelRef.current;
      if (panel) {
        if (next) {
          panel.resize(100);
        } else {
          panel.resize(DEFAULT_PANEL_SIZE);
        }
      }
      return next;
    });
  }, []);

  // Thu gọn panel khi chuyển điều hướng chính (/chat ↔ /search ↔ /knowledge…)
  const section = pathname.split("/")[1];
  const prevSection = React.useRef(section);
  React.useEffect(() => {
    if (prevSection.current !== section) {
      prevSection.current = section;
      close();
    }
  }, [section, close]);

  return (
    <Ctx.Provider value={{ target, maximized, open, close, toggleMaximize, panelRef }}>
      {children}
    </Ctx.Provider>
  );
}

/** Khu vực nội dung chính: ẩn khi panel phóng to (chỉ giữ menu trái + panel). */
export function DetailPanelMain({ children }: { children: React.ReactNode }) {
  return <div className="h-full min-w-0 overflow-y-auto overscroll-contain">{children}</div>;
}

// ── Dạng xem nội dung ─────────────────────────────────────────────────────────

/** Nút chuyển giữa bản xem trước Markdown và nội dung gốc, biểu tượng thể hiện chế độ hiện tại. */
function RenderModeToggle({
  mode,
  onChange,
}: {
  mode: "md" | "raw";
  onChange: (m: "md" | "raw") => void;
}) {
  const t = useTranslations("DetailPanel");
  const isPreview = mode === "md";
  const label = isPreview ? t("renderMode.raw") : t("renderMode.preview");

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="size-8 bg-background"
          aria-label={label}
          onClick={() => onChange(isPreview ? "raw" : "md")}
        >
          {isPreview ? <Eye /> : <Code2 />}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="bottom">{label}</TooltipContent>
    </Tooltip>
  );
}

function TextBody({ text, mode }: { text: string; mode: "md" | "raw" }) {
  if (mode === "md") {
    return (
      <div className="min-h-0 min-w-0 flex-1 overflow-y-auto rounded-md border bg-muted/30 p-4">
        <MarkdownContent content={text} />
      </div>
    );
  }
  return (
    <pre className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden whitespace-pre-wrap break-words rounded-md border bg-muted/30 p-4 font-mono text-xs leading-relaxed">
      {text}
    </pre>
  );
}

function ChunkView({
  target,
}: {
  target: Extract<DetailTarget, { kind: "chunk" }>;
}) {
  const t = useTranslations("DetailPanel");
  const locale = useLocale();
  const { timezone } = useApp();
  const [content, setContent] = React.useState<string | null>(null);
  const [meta, setMeta] = React.useState<{ heading: string; sourceName: string } | null>(null);
  const [mode, setMode] = React.useState<"md" | "raw">("md");
  const [error, setError] = React.useState("");
  const citationEvent = React.useMemo(
    () =>
      (target.eventRefs ?? []).find((event) => cleanCitationText(event.title)),
    [target.eventRefs],
  );
  const eventTitle = cleanCitationText(citationEvent?.title);
  const eventBody = cleanCitationText(citationEvent?.content);
  const eventCategory = cleanCitationText(citationEvent?.category);
  const eventTime = citationEvent?.start_time
    ? formatDate(citationEvent.start_time, timezone, { dateStyle: "medium" }, locale)
    : "";

  React.useEffect(() => {
    let alive = true;
    setContent(null);
    setError("");
    api
      .getChunk(target.sourceId, target.chunkId)
      .then((c) => {
        if (!alive) return;
        setContent(c.content);
        setMeta({ heading: c.heading || target.heading || t("chunk.fallbackHeading"), sourceName: c.source_name });
      })
      .catch((e) => alive && setError(e instanceof ApiError ? e.message : t("chunk.loadFailed")));
    return () => {
      alive = false;
    };
  }, [t, target]);

  if (error) {
    return <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>;
  }
  if (content === null) {
    return (
      <div className="flex flex-col gap-2">
        <Skeleton className="h-5 w-2/3" />
        <Skeleton className="h-32" />
      </div>
    );
  }
  if (citationEvent && eventTitle) {
    const evidenceHeading = meta?.heading || target.heading || t("chunk.fallbackHeading");
    const evidenceSource = meta?.sourceName ?? target.sourceName ?? t("chunk.source");
    return (
      <div className="flex min-w-0 flex-col gap-5">
        <section className="rounded-lg border border-amber-500/20 bg-amber-500/[0.07] p-3.5 shadow-sm dark:border-amber-300/20 dark:bg-amber-300/[0.07]">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            <Link
              href={`/knowledge/${target.sourceId}`}
              className="min-w-0 truncate hover:text-foreground"
            >
              {evidenceSource}
            </Link>
            {eventCategory && (
              <span className="rounded bg-background/70 px-1.5 py-0.5 text-[11px] text-amber-700 dark:text-amber-200">
                {eventCategory}
              </span>
            )}
            {eventTime && <span>{eventTime}</span>}
          </div>
          <h3 className="mt-2 font-display text-base font-medium leading-6">
            {eventTitle}
          </h3>
          {eventBody && (
            <div className="mt-3">
              <p className="text-[11px] font-medium tracking-wide text-muted-foreground/75">
                {t("chunk.eventDetail")}
              </p>
              <p className="mt-1 whitespace-pre-wrap break-words text-sm leading-6 text-foreground/75">
                {eventBody}
              </p>
            </div>
          )}
        </section>

        <section className="rounded-lg border bg-background/75 p-3.5 shadow-sm">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <h4 className="text-xs font-medium text-muted-foreground">
                {t("chunk.sourceEvidence")}
              </h4>
              {evidenceHeading && (
                <p className="mt-1 truncate text-sm font-medium text-foreground">
                  {evidenceHeading}
                </p>
              )}
            </div>
            <RenderModeToggle mode={mode} onChange={setMode} />
          </div>
          <div className="mt-3">
            <TextBody text={stripCitationTransportTokens(content)} mode={mode} />
          </div>
        </section>
      </div>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="font-display text-base font-medium">{meta?.heading}</h3>
          <Link
            href={`/knowledge/${target.sourceId}`}
            className="mt-0.5 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            {t("chunk.from", { source: meta?.sourceName ?? target.sourceName ?? t("chunk.source") })}
            <ArrowUpRight className="size-3" />
          </Link>
        </div>
        <RenderModeToggle mode={mode} onChange={setMode} />
      </div>
      <TextBody text={content} mode={mode} />
    </div>
  );
}

function OriginalDocumentPreview({ doc }: { doc: Doc }) {
  const locale = useLocale();
  const t = useTranslations("DetailPanel");
  const [state, setState] = React.useState<
    | { phase: "loading" }
    | { phase: "blob"; url: string; kind: "pdf" | "image" }
    | { phase: "text"; text: string }
    | { phase: "none" }
    | { phase: "error"; message: string }
  >({ phase: "loading" });

  const [textMode, setTextMode] = React.useState<"md" | "raw">("md");
  const fileUrl = doc.source_id ? api.documentFileUrl(doc.source_id, doc.id) : null;
  const previewUrl = doc.source_id ? api.documentPreviewUrl(doc.source_id, doc.id) : null;

  React.useEffect(() => {
    if (!previewUrl) {
      setState({
        phase: doc.object_uri ? "text" : "none",
        text: doc.object_uri ? `Object URI: ${doc.object_uri}\nSHA-256: ${doc.content_sha256 ?? "n/a"}` : undefined,
      } as { phase: "text"; text: string } | { phase: "none" });
      return;
    }
    let alive = true;
    let objectUrl: string | null = null;
    setState({ phase: "loading" });
    (async () => {
      try {
        const res = await fetch(previewUrl, {
          headers: {
            Authorization: `Bearer ${getToken() ?? ""}`,
            "Accept-Language": locale,
          },
        });
        if (!res.ok) throw new Error(t("original.unavailable", { status: res.status }));
        const ct = (res.headers.get("content-type") || doc.content_type || "").toLowerCase();
        if (ct.includes("pdf")) {
          objectUrl = URL.createObjectURL(await res.blob());
          if (alive) setState({ phase: "blob", url: objectUrl, kind: "pdf" });
        } else if (ct.startsWith("image/")) {
          objectUrl = URL.createObjectURL(await res.blob());
          if (alive) setState({ phase: "blob", url: objectUrl, kind: "image" });
        } else if (
          ct.startsWith("text/") ||
          ct.includes("markdown") ||
          ct.includes("json") ||
          ct.includes("csv")
        ) {
          const text = await res.text();
          if (alive) setState({ phase: "text", text: text.slice(0, 200_000) });
        } else {
          if (alive) setState({ phase: "none" });
        }
      } catch (e) {
        if (alive) setState({ phase: "error", message: e instanceof Error ? e.message : t("original.loadFailed") });
      }
    })();
    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [doc.content_sha256, doc.content_type, doc.id, doc.object_uri, doc.source_id, locale, previewUrl, t]);

  async function download() {
    if (!fileUrl) return;
    try {
      const res = await fetch(fileUrl, {
        headers: {
          Authorization: `Bearer ${getToken() ?? ""}`,
          "Accept-Language": locale,
        },
      });
      if (!res.ok) throw new Error(t("original.downloadFailed"));
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = doc.filename ?? doc.title ?? doc.id;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      /* Gợi ý do trình duyệt xử lý nền (không tự nhắc thêm) */
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">{t("original.title")}</span>
        <span className="flex items-center gap-1.5">
          {state.phase === "text" && <RenderModeToggle mode={textMode} onChange={setTextMode} />}
          <Button variant="ghost" size="sm" className="h-7 gap-1.5 px-2 text-xs" onClick={download} disabled={!fileUrl}>
            <Download />
            {t("original.download")}
          </Button>
        </span>
      </div>
      {state.phase === "loading" && (
        <div className="grid flex-1 place-items-center rounded-md border">
          <Spinner />
        </div>
      )}
      {state.phase === "error" && (
        <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {state.message}
        </p>
      )}
      {state.phase === "none" && (
        <p className="rounded-md border border-dashed px-3 py-6 text-center text-sm text-muted-foreground">
          {t("original.unsupported")}
        </p>
      )}
      {state.phase === "text" && (
        <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-auto">
          <TextBody text={state.text} mode={textMode} />
        </div>
      )}
      {state.phase === "blob" && state.kind === "pdf" && (
        <iframe title={doc.filename ?? doc.title ?? doc.id} src={state.url} className="min-h-0 flex-1 rounded-md border" />
      )}
      {state.phase === "blob" && state.kind === "image" && (
        <div className="min-h-0 flex-1 overflow-auto rounded-md border bg-muted/30 p-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={state.url} alt={doc.filename ?? doc.title ?? doc.id} className="mx-auto max-w-full" />
        </div>
      )}
    </div>
  );
}

type ParsedPreviewState =
  | { phase: "loading" }
  | { phase: "text"; text: string; truncated: boolean }
  | { phase: "none"; message: string }
  | { phase: "error"; message: string };

function ParsedDocumentPreview({ doc }: { doc: Doc }) {
  const locale = useLocale();
  const t = useTranslations("DetailPanel");
  const [state, setState] = React.useState<ParsedPreviewState>({ phase: "loading" });
  const [textMode, setTextMode] = React.useState<"md" | "raw">("md");
  const parsedUrl = doc.source_id ? api.documentParsedUrl(doc.source_id, doc.id) : null;

  React.useEffect(() => {
    const status = String(doc.status ?? "").toUpperCase();
    if (!parsedUrl) {
      setState({
        phase: "none",
        message: "Parsed Markdown legacy chỉ dùng cho document v1; với v2 hãy dùng tab Tree để hydrate node.",
      });
      return;
    }
    if (status !== "READY") {
      setState({
        phase: "none",
        message:
          status === "FAILED"
            ? doc.error || t("parsed.failed")
            : t("parsed.processing"),
      });
      return;
    }

    let alive = true;
    const controller = new AbortController();
    setState({ phase: "loading" });
    fetch(parsedUrl, {
      headers: {
        Authorization: `Bearer ${getToken() ?? ""}`,
        "Accept-Language": locale,
      },
      signal: controller.signal,
    })
      .then(async (res) => {
        if (res.status === 404) {
          if (alive) {
            setState({ phase: "none", message: t("parsed.notFound") });
          }
          return;
        }
        if (res.status === 409) {
          if (alive) setState({ phase: "none", message: t("parsed.notReady") });
          return;
        }
        if (!res.ok) throw new Error(t("parsed.unavailable", { status: res.status }));
        const text = await res.text();
        if (!alive) return;
        if (!text.trim()) {
          setState({ phase: "none", message: t("parsed.empty") });
          return;
        }
        const limit = 500_000;
        setState({ phase: "text", text: text.slice(0, limit), truncated: text.length > limit });
      })
      .catch((error) => {
        if (!alive || controller.signal.aborted) return;
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : t("parsed.loadFailed"),
        });
      });
    return () => {
      alive = false;
      controller.abort();
    };
  }, [doc.error, doc.status, locale, parsedUrl, t]);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">{t("parsed.title")}</span>
        {state.phase === "text" && <RenderModeToggle mode={textMode} onChange={setTextMode} />}
      </div>
      {state.phase === "loading" && (
        <div className="grid flex-1 place-items-center rounded-md border">
          <Spinner />
        </div>
      )}
      {state.phase === "error" && (
        <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {state.message}
        </p>
      )}
      {state.phase === "none" && (
        <p className="rounded-md border border-dashed px-3 py-6 text-center text-sm text-muted-foreground">
          {state.message}
        </p>
      )}
      {state.phase === "text" && (
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          {state.truncated && (
            <p className="text-xs text-muted-foreground">{t("parsed.truncated")}</p>
          )}
          <TextBody text={state.text} mode={textMode} />
        </div>
      )}
    </div>
  );
}

function DocumentV2Tree({ doc, ticker }: { doc: Doc; ticker: string | null }) {
  const [tree, setTree] = React.useState<DocumentTreeResponse | null>(null);
  const [selectedNodeId, setSelectedNodeId] = React.useState<string | null>(null);
  const [content, setContent] = React.useState<DocumentNodeContentResponse | null>(null);
  const [state, setState] = React.useState<"idle" | "loading" | "ready" | "none" | "error">(
    ticker ? "loading" : "none",
  );
  const [message, setMessage] = React.useState("");

  React.useEffect(() => {
    if (!ticker || String(doc.structure_status ?? "").toUpperCase() !== "COMPLETE") {
      setTree(null);
      setSelectedNodeId(null);
      setState("none");
      setMessage(!ticker ? "Tree v2 chỉ khả dụng cho source dạng BCTC_{ticker}." : "Cây v2 chưa sẵn sàng.");
      return;
    }
    let alive = true;
    const controller = new AbortController();
    setState("loading");
    setMessage("");
    api
      .getDocumentTreeByTicker(ticker, doc.id, controller.signal)
      .then((result) => {
        if (!alive) return;
        setTree(result);
        setSelectedNodeId(result.nodes[0]?.node_id ?? null);
        setState("ready");
      })
      .catch((error) => {
        if (!alive || controller.signal.aborted) return;
        setState("error");
        setMessage(error instanceof ApiError ? error.message : "Không tải được cây v2.");
      });
    return () => {
      alive = false;
      controller.abort();
    };
  }, [doc.id, doc.structure_status, ticker]);

  React.useEffect(() => {
    if (!ticker || !selectedNodeId) {
      setContent(null);
      return;
    }
    let alive = true;
    const controller = new AbortController();
    api
      .getDocumentNodeContentByTicker(ticker, doc.id, selectedNodeId, controller.signal)
      .then((result) => alive && setContent(result))
      .catch(() => alive && setContent(null));
    return () => {
      alive = false;
      controller.abort();
    };
  }, [doc.id, selectedNodeId, ticker]);

  if (state === "loading") {
    return (
      <div className="grid flex-1 place-items-center rounded-md border">
        <Spinner />
      </div>
    );
  }
  if (state === "none" || state === "error") {
    return (
      <p className={cn(
        "rounded-md px-3 py-6 text-center text-sm",
        state === "error" ? "bg-destructive/10 text-destructive" : "border border-dashed text-muted-foreground",
      )}>
        {message || "Cây v2 chưa khả dụng."}
      </p>
    );
  }

  return (
    <div className="grid min-h-0 flex-1 gap-3 md:grid-cols-[minmax(220px,0.9fr)_minmax(0,1.1fr)]">
      <div className="min-h-0 overflow-auto rounded-md border">
        <div className="sticky top-0 border-b bg-background px-3 py-2 text-xs text-muted-foreground">
          {tree?.nodes.length ?? 0} nodes · coverage {formatCoverage(tree?.coverage) ?? "n/a"}
        </div>
        <div className="p-1.5">
          {tree?.nodes.map((node) => (
            <button
              key={node.node_id}
              type="button"
              onClick={() => setSelectedNodeId(node.node_id)}
              className={cn(
                "block w-full rounded px-2 py-1.5 text-left text-xs hover:bg-muted",
                selectedNodeId === node.node_id && "bg-muted text-foreground",
              )}
              style={{ paddingLeft: `${Math.min(32, Math.max(0, node.level - 1) * 10 + 8)}px` }}
              title={`${node.start_line}-${node.end_line}`}
            >
              <span className="line-clamp-2">{node.heading || "(root)"}</span>
              <span className="text-[10px] text-muted-foreground">
                L{node.start_line}-{node.end_line}
              </span>
            </button>
          ))}
        </div>
      </div>
      <div className="min-h-0 overflow-auto rounded-md border bg-muted/20 p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
          <span>{ticker}</span>
          <span>·</span>
          <span>{doc.doc_role ?? tree?.doc_role ?? "NO_ROLE"}</span>
          {content && (
            <>
              <span>·</span>
              <span>L{content.start_line}-{content.end_line}</span>
              <span>·</span>
              <span>{shortHash(content.content_hash)}</span>
            </>
          )}
        </div>
        {content ? (
          <TextBody text={content.content} mode="md" />
        ) : (
          <p className="text-sm text-muted-foreground">Chọn một node để hydrate nội dung nguyên văn.</p>
        )}
      </div>
    </div>
  );
}

function DocumentPreview({ doc, ticker }: { doc: Doc; ticker: string | null }) {
  const t = useTranslations("DetailPanel");
  const [previewMode, setPreviewMode] = React.useState<"parsed" | "original" | "tree">(
    doc.status === "ready" ? "parsed" : "original",
  );

  return (
    <Tabs
      value={previewMode}
      onValueChange={(value) => setPreviewMode(value as "parsed" | "original" | "tree")}
      className="flex min-h-0 flex-1 flex-col"
    >
      <TabsList className="grid w-full grid-cols-3">
        <TabsTrigger value="parsed">{t("tabs.parsed")}</TabsTrigger>
        <TabsTrigger value="tree">Tree v2</TabsTrigger>
        <TabsTrigger value="original">{t("tabs.original")}</TabsTrigger>
      </TabsList>
      <TabsContent
        value="parsed"
        className="mt-2 min-h-0 flex-1 data-[state=active]:flex data-[state=active]:flex-col"
      >
        <ParsedDocumentPreview doc={doc} />
      </TabsContent>
      <TabsContent
        value="tree"
        className="mt-2 min-h-0 flex-1 data-[state=active]:flex data-[state=active]:flex-col"
      >
        <DocumentV2Tree doc={doc} ticker={ticker} />
      </TabsContent>
      <TabsContent
        value="original"
        className="mt-2 min-h-0 flex-1 data-[state=active]:flex data-[state=active]:flex-col"
      >
        <OriginalDocumentPreview doc={doc} />
      </TabsContent>
    </Tabs>
  );
}

export function DocumentDetailContent({
  sourceId,
  documentId,
  compact = false,
}: {
  sourceId: string;
  documentId: string;
  compact?: boolean;
}) {
  const locale = useLocale();
  const t = useTranslations("DetailPanel");
  const [doc, setDoc] = React.useState<Doc | null>(null);
  const [source, setSource] = React.useState<Source | null>(null);
  const [error, setError] = React.useState("");
  const { timezone } = useApp();

  React.useEffect(() => {
    let alive = true;
    setDoc(null);
    setSource(null);
    setError("");
    Promise.all([
      api.getDocument(sourceId, documentId),
      api.getSource(sourceId).catch(() => null),
    ])
      .then(([d, s]) => {
        if (!alive) return;
        setDoc(d);
        setSource(s);
      })
      .catch((e) => alive && setError(e instanceof ApiError ? e.message : t("document.loadFailed")));
    return () => {
      alive = false;
    };
  }, [documentId, sourceId, t]);

  if (error) {
    return <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>;
  }
  if (!doc) {
    return (
      <div className="flex flex-col gap-2">
        <Skeleton className="h-5 w-2/3" />
        <Skeleton className="h-64" />
      </div>
    );
  }
  const ticker = tickerFromSource(source);
  const coverage = formatCoverage(doc.coverage);
  return (
    <TooltipProvider delayDuration={300}>
      <div className={cn("flex min-h-0 flex-1 flex-col", compact ? "gap-3" : "gap-4")}>
        <div className="flex flex-col gap-2">
          <h3
            className={cn(
              "break-all font-display font-medium",
              compact ? "text-sm" : "text-base",
            )}
          >
            {doc.filename}
          </h3>
          <div
            className={cn(
              "flex flex-wrap items-center gap-2 text-muted-foreground",
              compact ? "text-[11px]" : "text-xs",
            )}
          >
            <DocStatusBadge status={doc.status} />
            <span>
              {Math.min(100, Math.max(0, Math.round(doc.progress)))}% ·{" "}
              {t("document.tokens", { count: formatTokenCount(doc.token_usage, locale) })}
            </span>
            <span>·</span>
            <span>{formatBytes(doc.size_bytes, locale)}</span>
            <span>·</span>
            <span>{t("document.chunks", { count: doc.chunk_count })}</span>
            <span>·</span>
            <span>{t("document.events", { count: doc.event_count })}</span>
            <span>·</span>
            <span>{relativeTime(doc.created_at, timezone, locale)}</span>
          </div>
          {(doc.doc_role || doc.processing_version || doc.structure_status || doc.extraction_status || doc.embedding_status || doc.fact_count || coverage || doc.content_sha256) && (
            <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
              {ticker && <span className="rounded bg-muted px-1.5 py-0.5">ticker {ticker}</span>}
              {doc.doc_role && <span className="rounded bg-muted px-1.5 py-0.5">{doc.doc_role}</span>}
              {doc.processing_version != null && (
                <span className="rounded bg-muted px-1.5 py-0.5">pv{doc.processing_version}</span>
              )}
              {doc.structure_status && (
                <span className="rounded bg-muted px-1.5 py-0.5">structure {doc.structure_status}</span>
              )}
              {doc.extraction_status && (
                <span className="rounded bg-muted px-1.5 py-0.5">extraction {doc.extraction_status}</span>
              )}
              {doc.embedding_status && (
                <span className="rounded bg-muted px-1.5 py-0.5">embedding {doc.embedding_status}</span>
              )}
              {doc.fact_count != null && (
                <span className="rounded bg-muted px-1.5 py-0.5">facts {doc.fact_count}</span>
              )}
              {coverage && <span className="rounded bg-muted px-1.5 py-0.5">coverage {coverage}</span>}
              {doc.content_sha256 && (
                <span className="rounded bg-muted px-1.5 py-0.5">sha {shortHash(doc.content_sha256)}</span>
              )}
            </div>
          )}
          {doc.error && (
            <p className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {doc.error}
            </p>
          )}
        </div>
        <DocumentPreview doc={doc} ticker={ticker} />
      </div>
    </TooltipProvider>
  );
}

// ── Vỏ ngoài panel ─────────────────────────────────────────────────────────

function PanelBody({ target }: { target: DetailTarget }) {
  return target.kind === "chunk" ? (
    <ChunkView target={target} />
  ) : (
    <DocumentDetailContent sourceId={target.sourceId} documentId={target.documentId} />
  );
}

/** Điểm ngắt lg (ranh giới giữa dạng gắn trong / Sheet của panel chi tiết). */
export function useIsLgUp(): boolean {
  const [isLg, setIsLg] = React.useState(true);
  React.useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)");
    const update = () => setIsLg(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  return isLg;
}

/** Chi tiết màn hình nhỏ: lớp phủ Sheet. */
export function DetailPanelSheet() {
  const t = useTranslations("DetailPanel");
  const { target, close } = useDetailPanel();
  if (!target) return null;
  return (
    <Sheet open onOpenChange={(o) => !o && close()}>
      <SheetContent side="right" className="flex w-full flex-col gap-4 sm:max-w-lg">
        <SheetTitle className="text-sm font-medium">
          {target.kind === "chunk" ? t("panel.chunkTitle") : t("panel.documentTitle")}
        </SheetTitle>
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
          <PanelBody target={target} />
        </div>
      </SheetContent>
    </Sheet>
  );
}

/** Chi tiết desktop: nội dung trong Resizable panel (chiều rộng do component chính thức bên ngoài quản lý). */
export function DetailPanelOutlet() {
  const t = useTranslations("DetailPanel");
  const { target, maximized, close, toggleMaximize } = useDetailPanel();
  if (!target) return null;

  return (
    <aside className="flex min-h-0 min-w-0 flex-1 flex-col bg-background">
      <div className="flex h-12 shrink-0 items-center gap-1 border-b px-3">
        <span className="min-w-0 flex-1 truncate text-sm font-medium">
          {target.kind === "chunk" ? t("panel.chunkTitle") : t("panel.documentTitle")}
        </span>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={toggleMaximize}
              aria-label={maximized ? t("panel.restore") : t("panel.maximize")}
            >
              {maximized ? <ChevronsRight /> : <ChevronsLeft />}
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            {maximized ? t("panel.restore") : t("panel.expandReading")}
          </TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon" className="size-7" onClick={close} aria-label={t("panel.close")}>
              <X />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">{t("panel.close")}</TooltipContent>
        </Tooltip>
      </div>
      <div
        className={cn(
          "flex min-h-0 flex-1 flex-col overflow-y-auto overflow-x-hidden p-4",
          maximized && "mx-auto w-full max-w-4xl",
        )}
      >
        <PanelBody target={target} />
      </div>
    </aside>
  );
}
