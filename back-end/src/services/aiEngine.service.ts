import axios, { AxiosInstance } from 'axios';
import { config } from '../config';

class AIEngineService {
  private client: AxiosInstance;

  constructor() {
    this.client = axios.create({
      baseURL: config.aiEngineUrl,
      timeout: 60_000,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  async getIndices() {
    const { data } = await this.client.get('/api/market/indices');
    return data;
  }

  async getMarketBreadth() {
    const { data } = await this.client.get('/api/market/breadth');
    return data;
  }

  async getMarketSnapshot(exchange?: string) {
    const { data } = await this.client.get('/api/market/snapshot', {
      params: exchange ? { exchange } : undefined,
    });
    return data;
  }

  async getStockList(exchange?: string) {
    const { data } = await this.client.get('/api/market/stocks', {
      params: exchange ? { exchange } : undefined,
    });
    return data;
  }

  async getLiquidity() {
    const { data } = await this.client.get('/api/market/liquidity');
    return data;
  }

  async getHeatmap() {
    const { data } = await this.client.get('/api/market/heatmap');
    return data;
  }

  async searchSymbols(q: string) {
    const { data } = await this.client.get('/api/market/search', { params: { q } });
    return data;
  }

  async getProfile(symbol: string) {
    const { data } = await this.client.get(`/api/stock/${symbol}/profile`);
    return data;
  }

  async getOHLCV(symbol: string, params: { interval?: string; start?: string; end?: string }) {
    const { data } = await this.client.get(`/api/stock/${symbol}/ohlcv`, { params });
    return data;
  }

  async getQuote(symbol: string) {
    const { data } = await this.client.get(`/api/stock/${symbol}/quote`);
    return data;
  }

  async getOrderBook(symbol: string) {
    const { data } = await this.client.get(`/api/stock/${symbol}/orderbook`);
    return data;
  }

  async getTrades(symbol: string) {
    const { data } = await this.client.get(`/api/stock/${symbol}/trades`);
    return data;
  }

  async getFundamentals(symbol: string) {
    const { data } = await this.client.get(`/api/stock/${symbol}/fundamentals`);
    return data;
  }

  async screenStocks(filters: Record<string, unknown>) {
    const { data } = await this.client.post('/api/screener/filter', filters);
    return data;
  }

  async getBuiltinPresets() {
    const { data } = await this.client.get('/api/screener/presets/builtin');
    return data;
  }

  async chatStream(prompt: string, context?: Record<string, unknown>) {
    return this.client.post(
      '/api/ai/chat',
      { prompt, context },
      { responseType: 'stream', timeout: 120_000 },
    );
  }

  async getConsensus(symbol: string) {
    const { data } = await this.client.get(`/api/ai/consensus/${symbol}`);
    return data;
  }

  async submitBacktest(body: Record<string, unknown>) {
    const { data } = await this.client.post('/api/ai/backtest', body);
    return data;
  }

  async getBacktestStatus(jobId: string) {
    const { data } = await this.client.get(`/api/ai/backtest/${jobId}/status`);
    return data;
  }

  /** Register symbols on DNSE WebSocket hub (ai-engine) */
  async subscribeStreamSymbols(symbols: string[]) {
    const { data } = await this.client.post('/api/stream/subscribe', { symbols });
    return data;
  }

  async getStreamStatus() {
    const { data } = await this.client.get('/api/stream/status');
    return data;
  }
}

export const aiEngineService = new AIEngineService();
