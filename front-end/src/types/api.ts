export interface ApiMarketStock {
  symbol?: string
  name?: string
  price?: number | null
  change_pct?: number | null
  changePercent?: number | null
  ref?: number | null
  ceiling?: number
  floor?: number
  volume?: number | null
  tradingValue?: number
  industry?: string
  exchange?: string
  foreign_flow?: number | null
  foreignFlow?: number | null
  source?: string
  stale?: boolean
  momentum?: number | null
  rs?: number
  sparkline?: number[]
}

export interface ApiMarketIndex {
  symbol?: string
  name?: string
  value?: number | null
  change_pct?: number | null
  change?: number | null
  changePercent?: number | null
  lastUpdate?: string
  receivedAt?: number
  date?: string
}

export interface ApiMarketSnapshot {
  stocks?: ApiMarketStock[]
  items?: ApiMarketStock[]
  total?: number
  liveSymbols?: number
}

export interface ApiNewsItem {
  title?: string
  ai_summary?: string
  article_content?: string
  published_date?: string
  doc_type?: string
  source?: string
}

