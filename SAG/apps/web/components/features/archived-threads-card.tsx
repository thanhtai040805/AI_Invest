"use client";

import * as React from "react";
import { Archive, ArchiveRestore, ChevronDown, RotateCw, Trash2 } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import { useApp } from "@/components/features/app-shell";
import { SettingsSection } from "@/components/features/settings-section";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api, ApiError } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { ARCHIVED_THREADS_PAGE_SIZE } from "@/lib/settings-config";
import type { Thread } from "@/lib/types";

/** Phiên đã lưu trữ —— mới nhất trước, tải theo từng trang, hỗ trợ khôi phục hoặc xóa hẳn. */
export function ArchivedThreadsCard() {
  const t = useTranslations("ArchivedThreads");
  const locale = useLocale();
  const { agent, refreshThreads, timezone } = useApp();
  const [rows, setRows] = React.useState<Thread[]>([]);
  const [initialLoading, setInitialLoading] = React.useState(true);
  const [loadingMore, setLoadingMore] = React.useState(false);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [hasMore, setHasMore] = React.useState(false);
  const [restoringId, setRestoringId] = React.useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = React.useState<Thread | null>(null);

  const loadPage = React.useCallback(
    async (offset: number, append: boolean) => {
      if (!agent) return;
      if (append) setLoadingMore(true);
      else setInitialLoading(true);
      setLoadError(null);

      try {
        const page = await api.listThreads(agent.id, {
          archived: true,
          limit: ARCHIVED_THREADS_PAGE_SIZE + 1,
          offset,
        });
        const visible = page.slice(0, ARCHIVED_THREADS_PAGE_SIZE);
        setHasMore(page.length > ARCHIVED_THREADS_PAGE_SIZE);
        setRows((current) => {
          if (!append) return visible;
          const knownIds = new Set(current.map((thread) => thread.id));
          return [...current, ...visible.filter((thread) => !knownIds.has(thread.id))];
        });
      } catch (error) {
        setLoadError(error instanceof ApiError ? error.message : t("loadFailed"));
      } finally {
        if (append) setLoadingMore(false);
        else setInitialLoading(false);
      }
    },
    [agent, t],
  );

  React.useEffect(() => {
    setRows([]);
    setHasMore(false);
    setLoadError(null);
    void loadPage(0, false);
  }, [loadPage]);

  if (!agent) return null;
  const agentId = agent.id;

  async function restore(thread: Thread) {
    setRestoringId(thread.id);
    try {
      await api.updateThread(agentId, thread.id, { archived: false });
      setRows((current) => current.filter((item) => item.id !== thread.id));
      await refreshThreads();
      toast.success(t("restored"));
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : t("restoreFailed"));
    } finally {
      setRestoringId(null);
    }
  }

  async function remove(thread: Thread) {
    try {
      await api.deleteThread(agentId, thread.id);
      setRows((current) => current.filter((item) => item.id !== thread.id));
      toast.success(t("deleted"));
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : t("deleteFailed"));
      throw error;
    }
  }

  const footer = rows.length ? (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-0 text-xs text-muted-foreground">
        {loadError ? (
          <span className="text-destructive">{loadError}</span>
        ) : hasMore ? (
          t("shownRecent", { count: rows.length })
        ) : (
          t("allLoaded", { count: rows.length })
        )}
      </div>
      {hasMore && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={loadingMore}
          onClick={() => void loadPage(rows.length, true)}
        >
          {loadingMore ? <Spinner /> : loadError ? <RotateCw /> : <ChevronDown />}
          {loadingMore ? t("loading") : loadError ? t("retryLoad") : t("loadMore")}
        </Button>
      )}
    </div>
  ) : undefined;

  return (
    <>
      <SettingsSection
        title={t("title")}
        description={t("description")}
        footer={footer}
      >
        {initialLoading ? (
          <ArchivedThreadsSkeleton />
        ) : loadError && rows.length === 0 ? (
          <div className="p-4 sm:p-5">
            <Alert variant="destructive">
              <AlertTitle>{t("loadErrorTitle")}</AlertTitle>
              <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
                <span>{loadError}</span>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => void loadPage(0, false)}
                >
                  <RotateCw />
                  {t("retry")}
                </Button>
              </AlertDescription>
            </Alert>
          </div>
        ) : rows.length === 0 ? (
          <Empty className="min-h-44 rounded-none p-8">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Archive />
              </EmptyMedia>
              <EmptyTitle className="text-base">{t("emptyTitle")}</EmptyTitle>
              <EmptyDescription>{t("emptyDescription")}</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <div>
            {rows.map((thread) => {
              const restoring = restoringId === thread.id;
              return (
                <div
                  key={thread.id}
                  className="flex items-center gap-3 border-t px-4 py-3 first:border-t-0 sm:px-5"
                >
                  <div className="grid size-8 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
                    <Archive className="size-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium" title={thread.title}>
                      {thread.title}
                    </div>
                    <div className="mt-0.5 text-xs tabular-nums text-muted-foreground">
                      {relativeTime(thread.updated_at, timezone, locale)}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="size-8"
                          disabled={restoring}
                          onClick={() => void restore(thread)}
                          aria-label={t("restoreAria", { title: thread.title })}
                          title={t("restore")}
                        >
                          {restoring ? <Spinner /> : <ArchiveRestore />}
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>{t("restore")}</TooltipContent>
                    </Tooltip>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="size-8 text-muted-foreground hover:text-destructive"
                          disabled={restoring}
                          onClick={() => setDeleteTarget(thread)}
                          aria-label={t("deleteAria", { title: thread.title })}
                          title={t("deletePermanently")}
                        >
                          <Trash2 />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>{t("deletePermanently")}</TooltipContent>
                    </Tooltip>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </SettingsSection>

      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title={t("deleteDialogTitle")}
        description={t("deleteDialogDescription", {
          title: deleteTarget?.title ?? t("thisConversation"),
        })}
        confirmLabel={t("deletePermanently")}
        onConfirm={() => (deleteTarget ? remove(deleteTarget) : undefined)}
      />
    </>
  );
}

function ArchivedThreadsSkeleton() {
  const t = useTranslations("ArchivedThreads");
  return (
    <div aria-label={t("loadingAria")}>
      {Array.from({ length: 3 }).map((_, index) => (
        <div
          key={index}
          className="flex items-center gap-3 border-t px-4 py-3 first:border-t-0 sm:px-5"
        >
          <Skeleton className="size-8 shrink-0" />
          <div className="grid min-w-0 flex-1 gap-2">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-3 w-20" />
          </div>
          <Skeleton className="size-8 shrink-0" />
        </div>
      ))}
    </div>
  );
}
