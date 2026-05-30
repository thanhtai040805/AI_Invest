export type AgentMessageType = 
  | "user"
  | "answer"
  | "error"
  | "thinking"
  | "tool_call"
  | "tool_result"
  | "compact"
  | "run_complete";

export interface PriceBar {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface AgentMessage {
  id: string;
  type: AgentMessageType;
  content: string;
  timestamp: number;
  tool?: string;
  args?: Record<string, string>;
  status?: "ok" | "error" | "running";
  elapsed_ms?: number;
  runId?: string;
  metrics?: Record<string, number>;
  equityCurve?: Array<{ time: string; equity: number }>;
  priceSeries?: Record<string, PriceBar[]>;
  shadowId?: string;
}

export interface ToolCallEntry {
  id: string;
  tool: string;
  arguments: Record<string, string>;
  status: "running" | "ok" | "error";
  timestamp: number;
  preview?: string;
  elapsed_ms?: number;
  elapsed_s?: number;
  progress?: {
    stage?: string;
    message?: string;
    current?: number;
    total?: number;
  };
}
