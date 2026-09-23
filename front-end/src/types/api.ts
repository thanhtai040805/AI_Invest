export interface ApiMarketStock {
  symbol?: string
  name?: string
  price?: number
  change_pct?: number
  ref?: number
  ceiling?: number
  floor?: number
  volume?: number
  foreign_flow?: number
  momentum?: number
  rs?: number
  sparkline?: number[]
}

export interface ApiMarketIndex {
  symbol?: string
  value?: number
  change_pct?: number
  date?: string
}

export interface ApiMarketSnapshot {
  stocks?: ApiMarketStock[]
  items?: ApiMarketStock[]
}

export interface ApiNewsItem {
  title?: string
  ai_summary?: string
  article_content?: string
  published_date?: string
  doc_type?: string
  source?: string
}

