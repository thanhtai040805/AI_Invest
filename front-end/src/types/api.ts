export interface ApiMarketStock {
  symbol?: string
  name?: string
  price?: number
  change_pct?: number
  changePercent?: number
  ref?: number
  ceiling?: number
  floor?: number
  volume?: number
  tradingValue?: number
  industry?: string
  exchange?: string
  foreign_flow?: number
  source?: string
  stale?: boolean
  momentum?: number
  rs?: number
  sparkline?: number[]
}

export interface ApiMarketIndex {
  symbol?: string
  name?: string
  value?: number
  change_pct?: number
  change?: number
  changePercent?: number
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

