export interface Broker {
  id: string
  name: string
  handle: string
  avatar: string
  bio: string
  badge: "Broker KIS" | "SSI Analyst" | "VNDIRECT Lead" | "HSC Quant"
  firm: string
  followers: number
  postsCount: number
  performance12M: number
  topPicks: string[]
  verified: boolean
}

export interface Post {
  id: string
  author: {
    name: string
    handle: string
    badge: string
    firm: string
    avatar: string
  }
  time: string
  ticker?: string
  stance?: "Bullish" | "Bearish" | "Neutral"
  content: string
  thesis?: string
  catalysts?: string[]
  priceTarget?: number
  currentPrice?: number
  likes: number
  liked?: boolean
  comments: number
  reposts: number
  views: number
  pinned?: boolean
  tags: string[]
}
