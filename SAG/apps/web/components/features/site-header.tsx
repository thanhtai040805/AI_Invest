"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { usePathname } from "next/navigation";
import { TriangleAlert } from "lucide-react";

import { PRODUCT_NAME } from "@/lib/branding";
import {
  workspaceSectionFromPathname,
} from "@/lib/workspace";
import { useApp } from "@/components/features/app-shell";
import { LanguageToggle } from "@/components/features/language-toggle";
import { ThemeToggle } from "@/components/features/theme-toggle";
import { WindowModeToggle } from "@/components/features/window-mode-toggle";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";

function sectionLabel(
  pathname: string,
  labels: { search: string; answer: string; knowledge: string; settings: string },
): string {
  const workspaceSection = workspaceSectionFromPathname(pathname);
  if (workspaceSection) return labels[workspaceSection];
  if (pathname === "/settings" || pathname.startsWith("/settings/")) return labels.settings;
  return PRODUCT_NAME;
}

export function SiteHeader() {
  const t = useTranslations("SiteHeader");
  const nav = useTranslations("Navigation");
  const pathname = usePathname();
  const {
    capabilities,
    enterExploreMode,
    toggleWindowMode,
    windowMode,
    windowScalingEnabled,
  } = useApp();
  const label = sectionLabel(pathname, {
    search: nav("search"),
    answer: nav("answer"),
    knowledge: nav("knowledge"),
    settings: nav("settings"),
  });
  return (
    <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b bg-background/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mr-1 h-4" />
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem className="hidden sm:block">
            <BreadcrumbLink asChild>
              <Link href="/chat">{PRODUCT_NAME}</Link>
            </BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator className="hidden sm:block" />
          <BreadcrumbItem>
            <BreadcrumbPage>{label}</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>

      <div className="ml-auto flex items-center gap-1.5">
        {capabilities && !capabilities.llm_configured && (
          <Link
            href="/settings"
            className="hidden items-center gap-1.5 rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-1 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 sm:inline-flex"
          >
            <TriangleAlert className="size-3.5" />
            {t("modelNotConfigured")}
          </Link>
        )}

        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-8 px-2.5 text-xs"
          onClick={() => enterExploreMode()}
          aria-label={t("enterExploreAria")}
          title={t("enterExplore")}
        >
          {t("exploreMode")}
        </Button>
        <LanguageToggle />
        <ThemeToggle />
        <WindowModeToggle
          enabled={windowScalingEnabled}
          mode={windowMode}
          onToggle={toggleWindowMode}
        />
      </div>
    </header>
  );
}
