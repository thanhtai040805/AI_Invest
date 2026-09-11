import type { Citation } from "./market"

export interface AgentMetric {
  label: string
  value?: string
  val?: string
  tone?: "gain" | "loss" | "warning" | "teal" | "mineral" | "neutral"
  sub?: string
}

export interface AgentStep {
  order: number
  id: string
  name: string
  vn?: string
  phase?: "Detection" | "Analysis" | "Decision" | "Action"
  does: string
  handoff: string
  headline: AgentMetric[]
  detail: string[]
  role?: string
  time?: string
  status?: "done" | "running" | "wait"
  desc?: string
  confidence?: number
  metrics?: AgentMetric[]
  sources?: string[]
}

export type CaseStatus = "New" | "Updated" | "Confirmed" | "Watching" | "Flagged"

export interface StockCase {
  symbol: string
  company: string
  thesis: string
  status: CaseStatus
  sentiment: "Bullish" | "Bearish" | "Neutral"
  upside: string
  confidence: number
  riskLevel: "Low" | "Medium" | "Elevated" | "High"
  catalyst: string
  targetPrice: number
  currentPrice: number
  beneishScore: number
  beneishVerdict: "CLEAN" | "FLAGGED"
  citations: Citation[]
  keyRisks: string[]
}

export interface RunCase {
  symbol: string
  status: CaseStatus
  note: string
}

export interface AnalysisRun {
  id: string
  title: string
  date: string
  time: string
  agentCount: number
  duration: string
  thesisCount: number
  cases: RunCase[]
}

export interface RunContext {
  id: string
  title: string
  time: string
  stages: {
    name: string
    agent: string
    detail: string
  }[]
  theses: {
    symbol: string
    cioDecision: "BUY" | "ACCUMULATE" | "REDUCE" | "AVOID"
    css: number
    cts: number
    targetVnd: number
    horizonDays: number
    reason: string
    risks: string[]
  }[]
  drawdownRisk: {
    overall: "LOW" | "ELEVATED" | "CRITICAL"
    value: string
    note: string
  }
}
