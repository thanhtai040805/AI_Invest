"use client";

import * as React from "react";
import { Lightbulb } from "lucide-react";
import { useTranslations } from "next-intl";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/** Chỉ hiển thị input đã được khóa trước khi bắt đầu chạy; kết quả của lượt này và đầu ra công cụ không thuộc hệ thống prompt. */
export function PromptPreview({ preview }: { preview: string }) {
  const t = useTranslations("PromptPreview");
  return (
    <Dialog>
      <Tooltip>
        <TooltipTrigger asChild>
          <DialogTrigger asChild>
            <button
              type="button"
              className="mt-1.5 grid size-7 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              aria-label={t("open")}
            >
              <Lightbulb className="size-3.5" />
            </button>
          </DialogTrigger>
        </TooltipTrigger>
        <TooltipContent>{t("open")}</TooltipContent>
      </Tooltip>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>
        <div className="flex flex-wrap gap-1.5 text-[11px] text-muted-foreground">
          <span className="rounded bg-muted px-2 py-1">{t("systemInstructions")}</span>
          <span className="rounded bg-muted px-2 py-1">{t("history")}</span>
          <span className="rounded bg-muted px-2 py-1">{t("question")}</span>
          <span className="rounded border border-dashed px-2 py-1">{t("toolsSeparate")}</span>
        </div>
        <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-lg border bg-muted/50 p-3 text-xs leading-relaxed text-muted-foreground">
          {preview}
        </pre>
      </DialogContent>
    </Dialog>
  );
}
