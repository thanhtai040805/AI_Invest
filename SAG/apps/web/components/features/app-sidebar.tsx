"use client";

import Image from "next/image";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { usePathname } from "next/navigation";
import { ChevronsUpDown, LogOut, Settings } from "lucide-react";

import { PRODUCT_NAME } from "@/lib/branding";
import { WORKSPACE_SECTIONS, workspaceSectionFromPathname } from "@/lib/workspace";
import { useApp } from "@/components/features/app-shell";
import { WorkspaceSectionIcon } from "@/components/features/workspace-section-icon";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar";

function Brand() {
  const t = useTranslations("AppSidebar");
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton size="lg" className="h-14 gap-2.5" tooltip={PRODUCT_NAME} asChild>
          <Link href="/knowledge">
            <Image
              src="/sag-icon.png"
              alt=""
              width={32}
              height={32}
              priority
              aria-hidden="true"
              className="size-8 shrink-0 rounded-[9px] shadow-sm ring-1 ring-black/10 dark:ring-white/10"
            />
            <div className="grid flex-1 text-left leading-tight group-data-[collapsible=icon]:hidden">
              <span className="truncate text-base font-semibold">{PRODUCT_NAME}</span>
              <span className="truncate text-xs text-muted-foreground">{t("brandTagline")}</span>
            </div>
          </Link>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}

function NavUser() {
  const t = useTranslations("AppSidebar");
  const { user, logout } = useApp();
  const initial = (user?.name || user?.email || "?").slice(0, 1).toUpperCase();
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton
              size="lg"
              className="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground"
            >
              <div className="flex aspect-square size-8 items-center justify-center rounded-full bg-muted text-xs font-semibold">
                {initial}
              </div>
              <div className="grid flex-1 text-left text-sm leading-tight">
                <span className="truncate font-medium">{user?.name}</span>
                <span className="truncate text-xs text-muted-foreground">
                  {user?.email || t("localIdentity")}
                </span>
              </div>
              <ChevronsUpDown className="ml-auto size-4 text-muted-foreground" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="w-[--radix-dropdown-menu-trigger-width] min-w-56"
            side="top"
            align="end"
            sideOffset={4}
          >
            <DropdownMenuLabel className="font-normal">
              <div className="flex flex-col">
                <span className="truncate text-sm font-medium">{user?.name}</span>
                <span className="truncate text-xs text-muted-foreground">
                  {user?.email || t("localIdentity")}
                </span>
              </div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <Link href="/settings">
                <Settings className="size-4" />
                {t("identitySettings")}
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem onClick={logout} className="text-destructive focus:text-destructive">
              <LogOut className="size-4" />
              {t("signOut")}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}

export function AppSidebar({ contained = false }: { contained?: boolean }) {
  const nav = useTranslations("Navigation");
  const pathname = usePathname();
  const activeSection = workspaceSectionFromPathname(pathname);

  return (
    <Sidebar collapsible="icon" className={contained ? "absolute" : undefined}>
      <SidebarHeader>
        <Brand />
      </SidebarHeader>
      <SidebarContent className="overflow-hidden">
        <SidebarGroup className="shrink-0">
          <SidebarMenu>
            {WORKSPACE_SECTIONS.map((item) => (
              <SidebarMenuItem key={item.id}>
                <SidebarMenuButton asChild isActive={activeSection === item.id} tooltip={nav(item.id)}>
                  <Link href={item.href}>
                    <WorkspaceSectionIcon section={item.id} />
                    <span>{nav(item.id)}</span>
                    {item.shortcut && (
                      <kbd className="ml-auto hidden rounded border border-sidebar-border px-1 py-0.5 text-[10px] font-medium leading-none text-muted-foreground opacity-0 transition-opacity group-hover/menu-item:opacity-100 group-data-[collapsible=icon]:hidden sm:inline-flex">
                        {item.shortcut}
                      </kbd>
                    )}
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <NavUser />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
