"use client";

import { cn } from "@/lib/utils";
import { DocumentDetailContent } from "@/components/features/detail-panel";

/**
 * Compact document detail viewport shared by the knowledge workspace and mini
 * entry points. It owns the flex/overflow contract so previews always receive
 * the remaining panel height and manage their own scrolling.
 */
export function CompactDocumentDetailWorkspace({
  sourceId,
  documentId,
  className,
}: {
  sourceId: string;
  documentId: string;
  className?: string;
}) {
  return (
    <div
      data-document-detail-workspace="compact"
      className={cn(
        "flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden p-3",
        className,
      )}
    >
      <DocumentDetailContent
        sourceId={sourceId}
        documentId={documentId}
        compact
      />
    </div>
  );
}
