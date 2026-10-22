import { apiClient } from "./client"

interface ClientCacheEntry {
  value: unknown
  expiresAt: number
  staleUntil: number
}

const memCache = new Map<string, ClientCacheEntry>()
const inFlight = new Map<string, Promise<unknown>>()

function clientCached<T>(key: string, ttlMs: number, fetcher: () => Promise<T>): Promise<T> {
  const now = Date.now()
  const hit = memCache.get(key)
  const run = (): Promise<T> => {
    const active = inFlight.get(key) as Promise<T> | undefined
    if (active) return active
    const p = fetcher()
      .then((val) => {
        if (val !== null && val !== undefined) {
          memCache.set(key, {
            value: val,
            expiresAt: Date.now() + ttlMs,
            staleUntil: Date.now() + Math.max(120_000, ttlMs * 6),
          })
        }
        return val
      })
      .finally(() => {
        inFlight.delete(key)
      })
    inFlight.set(key, p)
    return p
  }

  if (hit) {
    if (now < hit.expiresAt) return Promise.resolve(hit.value as T)
    if (now < hit.staleUntil) {
      void run().catch(() => undefined)
      return Promise.resolve(hit.value as T)
    }
    memCache.delete(key)
  }

  return run()
}

export const authApi = { me: () => apiClient.get("/auth/me").then(r => r.data), login: (email: string, password: string) => apiClient.post("/auth/login", { email, password }).then(r => r.data), register: (email: string, password: string, displayName: string) => apiClient.post("/auth/register", { email, password, displayName }).then(r => r.data), logout: () => apiClient.post("/auth/logout") }
export const marketApi = {
  indices: () => clientCached("market:indices", 8_000, () => apiClient.get("/market/indices").then(r => r.data)),
  breadth: () => clientCached("market:breadth", 8_000, () => apiClient.get("/market/breadth").then(r => r.data)),
  liquidity: () => clientCached("market:liquidity", 10_000, () => apiClient.get("/market/liquidity").then(r => r.data)),
  snapshot: () => clientCached("market:snapshot", 10_000, () => apiClient.get("/market/snapshot").then(r => r.data)),
  orderbooks: () => clientCached("market:orderbooks", 5_000, () => apiClient.get("/market/orderbooks").then(r => r.data)),
  heatmap: () => clientCached("market:heatmap", 20_000, () => apiClient.get("/market/heatmap").then(r => r.data)),
  sectorStrength: () => clientCached("market:sector-strength", 60_000, () => apiClient.get("/market/sector-strength").then(r => r.data)),
  superStocks: (symbol?: string) => clientCached(`market:super-stocks:${symbol ? symbol.toUpperCase() : "ALL"}`, 20_000, () => apiClient.get("/market/super-stocks", { params: symbol ? { symbol } : undefined }).then(r => r.data)),
  news: (symbol?: string) => clientCached(`market:news:${symbol ?? "ALL"}`, 30_000, () => apiClient.get("/market/news", { params: { symbol } }).then(r => r.data)),
  search: (q: string) => apiClient.get("/market/search", { params: { q } }).then(r => r.data),
}
export const stockApi = {
  profile: (s: string) => clientCached(`stock:profile:${s}`, 120_000, () => apiClient.get(`/stock/${s}/profile`).then(r => r.data)),
  quote: (s: string) => clientCached(`stock:quote:${s}`, 5_000, () => apiClient.get(`/stock/${s}/quote`).then(r => r.data)),
  ohlcv: (s: string, params?: { limit?: number; interval?: string; start?: string; end?: string }) =>
    clientCached(`stock:ohlcv:${s}:${JSON.stringify(params ?? {})}`, 30_000, () => apiClient.get(`/stock/${s}/ohlcv`, { params }).then(r => r.data)),
  orderbook: (s: string) => apiClient.get(`/stock/${s}/orderbook`).then(r => r.data),
  trades: (s: string) => apiClient.get(`/stock/${s}/trades`).then(r => r.data),
  technical: (s: string) => clientCached(`stock:technical:${s}`, 30_000, () => apiClient.get(`/stock/${s}/technical-indicators`).then(r => r.data)),
  news: (s: string) => clientCached(`stock:news:${s}`, 60_000, () => apiClient.get(`/stock/${s}/news`).then(r => r.data)),
}
export const portfolioApi = { snapshot: () => apiClient.get("/portfolio/snapshot").then(r => r.data), summary: () => apiClient.get("/portfolio/summary").then(r => r.data), positions: () => apiClient.get("/portfolio/positions").then(r => r.data), performance: () => apiClient.get("/portfolio/performance").then(r => r.data), risks: () => apiClient.get("/portfolio/risk-metrics").then(r => r.data), orders: () => apiClient.get("/portfolio/orders").then(r => r.data), order: (body: unknown) => apiClient.post("/portfolio/order", body).then(r => r.data) }
export const workspaceApi = {
  agentPortfolio: () => clientCached("workspace:agentPortfolio", 15_000, () => apiClient.get("/workspace/agent/portfolio").then(r => r.data)),
  thesisHistory: (params: { page: number; ticker?: string; date?: string }) => apiClient.get("/workspace/research/thesis-history", { params }).then(r => r.data),
  overview: () => clientCached("workspace:overview", 30_000, () => apiClient.get("/workspace/overview").then(r => r.data)),
  agent: (date?: string) => apiClient.get("/workspace/agent", { params: { date } }).then(r => r.data),
  mlFund: (date?: string, range?: { from?: string; to?: string }) => apiClient.get("/workspace/ml-fund", { params: { date, ...range } }).then(r => r.data),
  research: () => clientCached("workspace:research", 30_000, () => apiClient.get("/workspace/research").then(r => r.data)),
  watchlists: () => apiClient.get("/workspace/watchlists").then(r => r.data),
  createWatchlist: (body: { name: string; symbols: string[] }) => apiClient.post("/workspace/watchlists", body).then(r => r.data),
  updateWatchlist: (id: string, body: { name?: string; symbols?: string[] }) => apiClient.patch(`/workspace/watchlists/${id}`, body).then(r => r.data),
  deleteWatchlist: (id: string) => apiClient.delete(`/workspace/watchlists/${id}`),
}
export const screenerApi = { filter: (filters: Record<string, unknown> = {}) => apiClient.post("/screener/filter", filters, { timeout: 120_000 }).then(r => r.data), presets: () => apiClient.get("/screener/presets/builtin").then(r => r.data), myPresets: () => apiClient.get("/screener/presets").then(r => r.data), savePreset: (name: string, filters: Record<string, unknown>) => apiClient.post("/screener/presets", { name, filters }).then(r => r.data) }
export const aiApi = { backtest: (body: Record<string, unknown>) => apiClient.post("/ai/backtest", body, { timeout: 120_000 }).then(r => r.data), backtestHistory: () => apiClient.get("/ai/backtest/history").then(r => r.data), backtestResults: (id: string) => apiClient.get(`/ai/backtest/${encodeURIComponent(id)}/results`).then(r => r.data), backtestStatus: (id: string) => apiClient.get(`/ai/backtest/${encodeURIComponent(id)}/status`).then(r => r.data), pipelineStatus: () => apiClient.get("/ai/pipeline/status").then(r => r.data) }
export const communityApi = { posts: (params: { limit?: number; cursor?: string } = {}) => apiClient.get("/community/posts", { params }).then(r => r.data), insights: () => apiClient.get("/community/insights").then(r => r.data), experts: () => apiClient.get("/community/experts/top").then(r => r.data), createPost: (body: Record<string, unknown>) => apiClient.post("/community/posts", body).then(r => r.data), react: (id: string, type = "LIKE") => apiClient.post(`/community/posts/${id}/react`, { type }).then(r => r.data) }
