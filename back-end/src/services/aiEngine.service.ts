import axios, { AxiosInstance } from 'axios';
import { config } from '../config';
import { CircuitBreaker } from './circuitBreaker';

class AIEngineService {
  private client: AxiosInstance;
  private circuitBreaker: CircuitBreaker;

  constructor() {
    this.client = axios.create({
      baseURL: config.aiEngineUrl,
      timeout: 30_000,
      headers: { 'Content-Type': 'application/json' },
    });

    this.circuitBreaker = new CircuitBreaker({
      failureThreshold: 5,
      recoveryTimeoutMs: 30_000,
      halfOpenMaxAttempts: 3,
    });
  }

  async getIndices() {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get('/api/market/indices');
      return data;
    });
  }

  async getMarketBreadth() {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get('/api/market/breadth');
      return data;
    });
  }

  async getMarketSnapshot(exchange?: string) {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get('/api/market/snapshot', {
        params: exchange ? { exchange } : undefined,
      });
      return data;
    });
  }

  async getStockList(exchange?: string) {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get('/api/market/stocks', {
        params: exchange ? { exchange } : undefined,
      });
      return data;
    });
  }

  async getLiquidity() {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get('/api/market/liquidity');
      return data;
    });
  }

  async getHeatmap() {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get('/api/market/heatmap');
      return data;
    });
  }

  async searchSymbols(q: string) {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get('/api/market/search', { params: { q } });
      return data;
    });
  }

  async getProfile(symbol: string) {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get(`/api/stock/${symbol}/profile`);
      return data;
    });
  }

  async getOHLCV(symbol: string, params: { interval?: string; start?: string; end?: string }) {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get(`/api/stock/${symbol}/ohlcv`, { params });
      return data;
    });
  }

  async getQuote(symbol: string) {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get(`/api/stock/${symbol}/quote`);
      return data;
    });
  }

  async getOrderBook(symbol: string) {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get(`/api/stock/${symbol}/orderbook`);
      return data;
    });
  }

  async getTrades(symbol: string) {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get(`/api/stock/${symbol}/trades`);
      return data;
    });
  }

  async getFundamentals(symbol: string) {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get(`/api/stock/${symbol}/fundamentals`);
      return data;
    });
  }

  async getTechnicalIndicators(symbol: string) {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get(`/api/stock/${symbol}/technical-indicators`);
      return data;
    });
  }

  async screenStocks(filters: Record<string, unknown>) {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.post('/api/screener/filter', filters);
      return data;
    });
  }

  async getBuiltinPresets() {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get('/api/screener/presets/builtin');
      return data;
    });
  }

  async chatStream(prompt: string, context?: Record<string, unknown>) {
    return this.client.post(
      '/api/ai/chat',
      { prompt, context },
      { responseType: 'stream', timeout: 120_000 },
    );
  }

  async submitBacktest(body: Record<string, unknown>) {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.post('/api/ai/backtest', body);
      return data;
    });
  }

  async getBacktestStatus(jobId: string) {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get(`/api/ai/backtest/${jobId}/status`);
      return data;
    });
  }

  async getBacktestHistory() {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get('/api/ai/backtest/history');
      return data;
    });
  }

  async subscribeStreamSymbols(symbols: string[]) {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.post('/api/stream/subscribe', { symbols });
      return data;
    });
  }

  async getStreamStatus() {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get('/api/stream/status');
      return data;
    });
  }

  get circuitBreakerStats() {
    return this.circuitBreaker.stats;
  }

  async getAIContext(symbol: string) {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get(`/api/stock/${symbol}/ai-context`, { timeout: 60_000 });
      return data;
    });
  }

  async getFactorScores(symbol: string) {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get(`/api/stock/${symbol}/factor-scores`);
      return data;
    });
  }

  async getForeignFlow(symbol: string) {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get(`/api/stock/${symbol}/foreign-flow`);
      return data;
    });
  }

  async getDividends(symbol: string) {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get(`/api/stock/${symbol}/dividends`);
      return data;
    });
  }

  async getMarketExtras(symbol: string) {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get(`/api/stock/${symbol}/market-extras`);
      return data;
    });
  }

  async getSentiment(symbol: string) {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get(`/api/stock/${symbol}/sentiment`);
      return data;
    });
  }

  async getMacro() {
    return this.circuitBreaker.execute(async () => {
      const { data } = await this.client.get('/api/stock/macro');
      return data;
    });
  }
}

export const aiEngineService = new AIEngineService();
