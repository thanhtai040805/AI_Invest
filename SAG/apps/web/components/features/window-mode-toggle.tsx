"use client";

import { Expand, Shrink } from "lucide-react";
import { useTranslations } from "next-intl";

import type { WindowMode } from "@/lib/window-layout";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface WindowModeToggleProps {
  enabled: boolean;
  mode: WindowMode;
  onToggle: () => void;
}

export function WindowModeToggle({
  enabled,
  mode,
  onToggle,
}: WindowModeToggleProps) {
  const t = useTranslations("SiteHeader");
  if (!enabled) return null;

  const windowed = mode === "window";
  const label = windowed ? t("fullscreen") : t("window");
  const ariaLabel = windowed ? t("fullscreenAria") : t("windowAria");

  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="hidden size-8 md:inline-flex"
            onClick={onToggle}
            aria-label={ariaLabel}
          >
            {windowed ? <Expand className="size-4" /> : <Shrink className="size-4" />}
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
