"use client";

import * as React from "react";
import { Check, Copy } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { copyText } from "@/lib/clipboard";

/** Nút sao chép —— sau khi thành công chuyển nhanh sang dấu tick và toast, thống nhất tương tác sao chép toàn site. */
export function CopyButton({ text, label }: { text: string; label?: string }) {
  const t = useTranslations("CopyButton");
  const [done, setDone] = React.useState(false);
  const resolvedLabel = label ?? t("content");
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className="h-7 gap-1.5 px-2 text-xs"
      onClick={async () => {
        try {
          await copyText(text);
          setDone(true);
          toast.success(t("copied", { label: resolvedLabel }));
          setTimeout(() => setDone(false), 1500);
        } catch {
          toast.error(t("failed"));
        }
      }}
    >
      {done ? <Check /> : <Copy />}
      {t("copy")}
    </Button>
  );
}
