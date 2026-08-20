import { Library, MessageCircle, Search, type LucideIcon } from "lucide-react";

import type { WorkspaceSection } from "@/lib/workspace";

const ICONS = {
  search: Search,
  answer: MessageCircle,
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
