export type Trend = "up" | "down" | "flat"

export interface Stock {
  symbol: string
  name: string
  sector: string
  price: number
  changePct: number
  ref: number
  ceiling: number
  floor: number
  volume: string
  foreign: number // net foreign flow in billions VND
  weight: number // index/sector weight
  momentum: number // 0-100 percentile
  rs: number // relative strength percentile
  flow: number // -100..100
  factor: string
  risk: "Low" | "Moderate" | "Elevated" | "High"
  beneish: "PASS" | "WARNING"
  spark: number[]
  rsi?: number
  pe?: number
}

export interface Sector {
  name: string
  vn: string
  weight: number
  changePct: number
  foreign: number
}

export interface Surveillance {
  symbol: string
  trigger: string
  severity: "info" | "warn" | "critical"
  time: string
  detail: string
  action: string
}

export interface Citation {
  id: string
  ticker: string
  source: string
  headline: string
  time: string
  domain: "filing" | "news" | "macro" | "alternative"
  reliability: number
  verdict: "supports" | "neutral" | "contradicts"
  extract: string
  url: string
}

export interface Position {
  symbol: string
  shares: number
  avgPrice: number
  currentPrice: number
  pnlPct: number
  pnlVnd: number
  weightPct: number
  status: "active" | "target_reached" | "stop_hit"
  side: "BUY" | "SELL"
}
