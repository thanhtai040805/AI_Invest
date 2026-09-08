export type WorkspaceSection = "search" | "knowledge";

export interface WorkspaceSectionDefinition {
  id: WorkspaceSection;
  href: string;
  shortcut?: string;
}

/**
 * Cấu hình điểm vào duy nhất cho các phân khu của workspace. normal và mini chỉ đổi cách hiển thị,
 * không còn duy trì menu riêng cho từng dạng.
 */
export const WORKSPACE_SECTIONS: readonly WorkspaceSectionDefinition[] = [
  { id: "knowledge", href: "/knowledge" },
  { id: "search", href: "/search", shortcut: "⌘K" },
];

export function isWorkspaceSection(value: unknown): value is WorkspaceSection {
  return value === "search" || value === "knowledge";
}

export function workspaceSectionFromPathname(pathname: string): WorkspaceSection | null {
  if (pathname === "/search" || pathname.startsWith("/search/")) return "search";
  if (pathname === "/knowledge" || pathname.startsWith("/knowledge/")) return "knowledge";
  return null;
}

export function workspaceSectionDefinition(section: WorkspaceSection) {
  return WORKSPACE_SECTIONS.find((item) => item.id === section)!;
}
