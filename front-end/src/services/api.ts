import axios from 'axios';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:3001/api/v1';

const accessTokenKey = 'aiinvest_access_token';

function getAccessToken() {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(accessTokenKey);
}

export function setAccessToken(accessToken: string) {
  if (typeof window === 'undefined') return;
  localStorage.setItem(accessTokenKey, accessToken);
}

export function clearAccessToken() {
  if (typeof window === 'undefined') return;
  localStorage.removeItem(accessTokenKey);
}

export const apiClient = axios.create({
  baseURL: API_BASE,
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true,
});

const refreshClient = axios.create({
  baseURL: API_BASE,
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true,
});

let isRefreshing = false;
let refreshSubscribers: Array<(token: string | null) => void> = [];

function onRefreshed(token: string | null) {
  refreshSubscribers.forEach((callback) => callback(token));
  refreshSubscribers = [];
}

function addRefreshSubscriber(callback: (token: string | null) => void) {
  refreshSubscribers.push(callback);
}

// Attach JWT token to every request
apiClient.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config as { _retry?: boolean; url?: string; headers?: Record<string, string> } & Record<string, unknown>;
    if (!originalRequest || originalRequest._retry || !error.response || error.response.status !== 401) {
      return Promise.reject(error);
    }

    if (originalRequest.url?.endsWith('/auth/refresh') || originalRequest.url?.endsWith('/auth/login') || originalRequest.url?.endsWith('/auth/register')) {
      clearAccessToken();
      return Promise.reject(error);
    }

    originalRequest._retry = true;

    if (isRefreshing) {
      return new Promise((resolve, reject) => {
        addRefreshSubscriber((token) => {
          if (!token) {
            reject(error);
            return;
          }
          originalRequest.headers.Authorization = `Bearer ${token}`;
          resolve(apiClient(originalRequest));
        });
      });
    }

    isRefreshing = true;

    return refreshClient
      .post('/auth/refresh')
      .then((response) => {
        const { accessToken } = response.data;
        setAccessToken(accessToken);
        onRefreshed(accessToken);
        originalRequest.headers.Authorization = `Bearer ${accessToken}`;
        return apiClient(originalRequest);
      })
      .catch((refreshError) => {
        clearAccessToken();
        onRefreshed(null);
        return Promise.reject(refreshError);
      })
      .finally(() => {
        isRefreshing = false;
      });
  },
);

// ── Market Data API ──────────────────────────────────

export const marketAPI = {
  getIndices: () => apiClient.get('/market/indices').then(r => r.data),
  getBreadth: () => apiClient.get('/market/breadth').then(r => r.data),
  getLiquidity: () => apiClient.get('/market/liquidity').then(r => r.data),
  getSnapshot: (exchange?: string) =>
    apiClient.get('/market/snapshot', { params: { exchange } }).then(r => r.data),
  getHeatmap: () => apiClient.get('/market/heatmap').then(r => r.data),
  getNews: (params?: { symbol?: string; limit?: number }) =>
    apiClient.get('/market/news', { params }).then(r => r.data),
  search: (q: string) => apiClient.get('/market/search', { params: { q } }).then(r => r.data),
};

// ── Community API ────────────────────────────────────

export interface CreatePostParams {
  content: string;
  taggedSymbols?: string[];
}

export interface AddCommentParams {
  content: string;
  parentCommentId?: string;
}

export const communityAPI = {
  getPosts: (params?: { limit?: number; cursor?: string }) =>
    apiClient.get('/community/posts', { params }).then(r => r.data),
  createPost: (post: CreatePostParams) =>
    apiClient.post('/community/posts', post).then(r => r.data),
  getPost: (id: string) =>
    apiClient.get(`/community/posts/${id}`).then(r => r.data),
  addComment: (postId: string, comment: AddCommentParams) =>
    apiClient.post(`/community/posts/${postId}/comments`, comment).then(r => r.data),
  toggleReaction: (postId: string) =>
    apiClient.post(`/community/posts/${postId}/react`).then(r => r.data),
  toggleCommentReaction: (commentId: string) =>
    apiClient.post(`/community/comments/${commentId}/react`).then(r => r.data),
  getInsights: () =>
    apiClient.get('/community/insights').then(r => r.data),
  getTopExperts: (limit = 5) =>
    apiClient.get('/community/experts/top', { params: { limit } }).then(r => r.data),
};

// ── Stock Detail API ─────────────────────────────────

export const stockAPI = {
  getProfile: (symbol: string) =>
    apiClient.get(`/stock/${symbol}/profile`).then(r => r.data),
  getOHLCV: (symbol: string, params: { interval?: string; start?: string; end?: string } = {}) =>
    apiClient.get(`/stock/${symbol}/ohlcv`, { params }).then(r => r.data),
  getQuote: (symbol: string) =>
    apiClient.get(`/stock/${symbol}/quote`).then(r => r.data),
  getOrderBook: (symbol: string) =>
    apiClient.get(`/stock/${symbol}/orderbook`).then(r => r.data),
  getTrades: (symbol: string) =>
    apiClient.get(`/stock/${symbol}/trades`).then(r => r.data),
  getFundamentals: (symbol: string) =>
    apiClient.get(`/stock/${symbol}/fundamentals`).then(r => r.data),
  getNews: (symbol: string) =>
    apiClient.get(`/stock/${symbol}/news`).then(r => r.data),
};

// ── Screener API ─────────────────────────────────────

export interface ScreenerFilters {
  exchange?: string;
  peMin?: number;
  peMax?: number;
  pbMin?: number;
  pbMax?: number;
  roeMin?: number;
  roeMax?: number;
  rsiMin?: number;
  rsiMax?: number;
  marketCapMin?: number;
  marketCapMax?: number;
  volumeMin?: number;
  sort?: string;
  sortDir?: 'asc' | 'desc';
  limit?: number;
  offset?: number;
}

export const screenerAPI = {
  filter: (filters: ScreenerFilters) =>
    apiClient.post('/screener/filter', filters).then(r => r.data),
  getBuiltinPresets: () => apiClient.get('/screener/presets/builtin').then(r => r.data),
  getPresets: () => apiClient.get('/screener/presets').then(r => r.data),
  savePreset: (name: string, filters: ScreenerFilters) =>
    apiClient.post('/screener/presets', { name, filters }).then(r => r.data),
  deletePreset: (id: string) =>
    apiClient.delete(`/screener/presets/${id}`).then(r => r.data),
};

// ── Portfolio API ────────────────────────────────────

export const portfolioAPI = {
  getSummary: () => apiClient.get('/portfolio/summary').then(r => r.data),
  getPositions: () => apiClient.get('/portfolio/positions').then(r => r.data),
  placeOrder: (order: { symbol: string; side: 'BUY' | 'SELL'; orderType: string; price?: number; quantity: number }) =>
    apiClient.post('/portfolio/order', order).then(r => r.data),
  getOrders: () => apiClient.get('/portfolio/orders').then(r => r.data),
  getPerformance: () => apiClient.get('/portfolio/performance').then(r => r.data),
  getRiskMetrics: () => apiClient.get('/portfolio/risk-metrics').then(r => r.data),
};

// ── AI API ───────────────────────────────────────────

export const aiAPI = {
  /** Stream chat (returns EventSource-compatible URL) */
  chat: async (prompt: string, sessionId?: string) => {
    const response = await fetch(`${API_BASE}/ai/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${localStorage.getItem('aiinvest_access_token') || ''}`,
      },
      body: JSON.stringify({ prompt, sessionId }),
    });
    return response;
  },
  submitBacktest: (params: { symbol: string; strategy: string; startDate: string; endDate: string; params?: Record<string, unknown> }) =>
    apiClient.post('/ai/backtest', params).then(r => r.data),
  getBacktestStatus: (jobId: string) =>
    apiClient.get(`/ai/backtest/${jobId}/status`).then(r => r.data),
  getBacktestHistory: () => apiClient.get('/ai/backtest/history').then(r => r.data),
  getSessions: () => apiClient.get('/ai/sessions').then(r => r.data),
};

// ── Auth API ─────────────────────────────────────────

export const authAPI = {
  register: (email: string, password: string, displayName?: string) =>
    apiClient.post('/auth/register', { email, password, displayName }).then(r => r.data),
  login: (email: string, password: string) =>
    apiClient.post('/auth/login', { email, password }).then(r => r.data),
  refresh: () =>
    refreshClient.post('/auth/refresh').then(r => r.data),
  logout: () =>
    apiClient.post('/auth/logout').then(r => r.data),
  getMe: () => apiClient.get('/auth/me').then(r => r.data),
};
