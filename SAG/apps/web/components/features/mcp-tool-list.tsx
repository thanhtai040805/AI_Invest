import { Wrench } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { McpToolDetail } from "@/lib/types";

export function McpToolList({ tools }: { tools: McpToolDetail[] }) {
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {tools.map((tool) => (
        <div key={tool.name} className="min-w-0 rounded-lg border bg-muted/20 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <Wrench className="size-3.5 text-muted-foreground" />
            <span className="text-sm font-medium">{tool.label}</span>
            <Badge variant="outline" className="font-mono text-[11px] font-normal">
              {tool.name}
            </Badge>
          </div>
          <p className="mt-1.5 text-xs leading-5 text-muted-foreground">{tool.description}</p>
        </div>
      ))}
    </div>
  );
}
