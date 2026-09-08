import { Library, Search, type LucideIcon } from "lucide-react";

import type { WorkspaceSection } from "@/lib/workspace";

const ICONS = {
  search: Search,
  knowledge: Library,
} satisfies Record<WorkspaceSection, LucideIcon>;

export function WorkspaceSectionIcon({
  section,
  className,
}: {
  section: WorkspaceSection;
  className?: string;
}) {
  const Icon = ICONS[section];
  return <Icon className={className} />;
}
