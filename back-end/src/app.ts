import express from 'express';
import http from 'http';
import cors from 'cors';
import helmet from 'helmet';
import morgan from 'morgan';
import compression from 'compression';
import { config } from './config';
import { errorHandler } from './middleware/errorHandler';
import {
  apiLimiter,
  authLimiter,
  aiLimiter,
  marketLimiter,
  screenerLimiter,
  portfolioLimiter,
} from './middleware/rateLimiter';
import { socketService } from './services/socket.service';
import { redisService } from './services/redis.service';
import { initScheduler, shutdownScheduler } from './services/scheduler.service';
import { dnseRelayService } from './services/dnseRelay.service';
import { aiEngineService } from './services/aiEngine.service';
import { autoBackfillIfNeeded } from './services/backfill.service';

// Route modules
import authRoutes from './modules/auth/auth.routes';
import marketRoutes from './modules/market/market.routes';
import stockRoutes from './modules/stock/stock.routes';
import stockAdminRoutes from './modules/stock/stock.admin.routes';
import screenerRoutes from './modules/screener/screener.routes';
import portfolioRoutes from './modules/portfolio/portfolio.routes';
import aiRoutes from './modules/ai/ai.routes';
import communityRoutes from './modules/community/community.routes';

const app = express();
const server = http.createServer(app);

// ── Security Middleware ────────────────────────────────
const isProduction = config.nodeEnv === 'production';

app.use(helmet({
  contentSecurityPolicy: isProduction
    ? {
        directives: {
          defaultSrc: ["'self'"],
          scriptSrc: ["'self'", "'unsafe-inline'"],
          styleSrc: ["'self'", "'unsafe-inline'", 'https://fonts.googleapis.com'],
          fontSrc: ["'self'", 'https://fonts.gstatic.com'],
          imgSrc: ["'self'", 'data:', 'https:'],
          connectSrc: ["'self'", config.aiEngineUrl, config.corsOrigin],
          frameSrc: ["'none'"],
          objectSrc: ["'none'"],
        },
      }
    : false,
  crossOriginEmbedderPolicy: false,
  hsts: isProduction ? { maxAge: 31536000, includeSubDomains: true, preload: true } : false,
}));

const corsOrigins = isProduction
  ? config.corsOrigin.split(',').map((o) => o.trim())
  : config.corsOrigin;

app.use(cors({
  origin: corsOrigins,
  credentials: true,
  methods: ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'],
}));

app.use(compression());
app.use(express.json({ limit: '10mb' }));
app.use(morgan(isProduction ? 'combined' : 'dev'));

// ── Health Check ──────────────────────────────────────
app.get('/api/health', (_req, res) => {
  res.json({
    status: 'ok',
    timestamp: new Date().toISOString(),
    dataProvider: config.dnse.enabled ? 'dnse' : 'legacy-poll',
    dnseRelay: config.dnse.enabled,
    aiEngine: aiEngineService.circuitBreakerStats,
    socketClients: socketService.getActiveConnections(),
  });
});

app.get('/api/health/detailed', async (_req, res) => {
  try {
    const streamStatus = await aiEngineService.getStreamStatus().catch(() => null);
    res.json({
      status: 'ok',
      timestamp: new Date().toISOString(),
      dataProvider: config.dnse.enabled ? 'dnse' : 'legacy-poll',
      dnseRelay: config.dnse.enabled,
      aiEngine: {
        circuitBreaker: aiEngineService.circuitBreakerStats,
        stream: streamStatus,
      },
      socket: {
        activeConnections: socketService.getActiveConnections(),
      },
    });
  } catch {
    res.json({
      status: 'degraded',
      timestamp: new Date().toISOString(),
      aiEngine: 'unreachable',
    });
  }
});

// ── API Routes (per-route rate limiting) ───────────────
app.use('/api/v1', apiLimiter);
app.use('/api/v1/auth', authLimiter, authRoutes);
app.use('/api/v1/market', marketLimiter, marketRoutes);
app.use('/api/v1/stock', marketLimiter, stockRoutes);
app.use('/api/v1/stock', marketLimiter, stockAdminRoutes);
app.use('/api/v1/screener', screenerLimiter, screenerRoutes);
app.use('/api/v1/portfolio', portfolioLimiter, portfolioRoutes);
app.use('/api/v1/ai', aiLimiter, aiRoutes);
app.use('/api/v1/community', portfolioLimiter, communityRoutes);

// ── Error Handler ─────────────────────────────────────
app.use(errorHandler);

// ── Socket.IO ─────────────────────────────────────────
socketService.init(server);

// ── Bootstrap ─────────────────────────────────────────
async function bootstrap(): Promise<void> {
  try {
    await redisService.connect();
    await dnseRelayService.start();
    initScheduler();
  } catch (err) {
    console.warn('[Bootstrap] Redis/scheduler unavailable — API runs without cache/jobs:', err);
  }

  // Idempotency check: auto-backfill if today's session data is missing
  try {
    const result = await autoBackfillIfNeeded();
    console.log(`[Bootstrap] ${result.reason}`);
  } catch (err: any) {
    console.warn('[Bootstrap] Auto-backfill check failed:', err.message);
  }

  server.listen(config.port, () => {
    console.log(`
  ╔══════════════════════════════════════════════╗
  ║   AIInvest Backend v1.0                      ║
  ║   Port: ${config.port}                              ║
  ║   Env:  ${config.nodeEnv}                     ║
  ║   CORS: ${config.corsOrigin}            ║
  ║   Security: ${isProduction ? 'production' : 'development'}                    ║
  ╚══════════════════════════════════════════════╝
    `);
  });
}

bootstrap().catch((err) => {
  console.error('Failed to start server:', err);
  process.exit(1);
});

// Graceful shutdown
async function shutdown(): Promise<void> {
  console.log('Shutting down...');
  shutdownScheduler();
  await dnseRelayService.stop();
  socketService.shutdown();
  await redisService.disconnect();
  server.close(() => process.exit(0));
}

process.on('SIGTERM', () => void shutdown());
process.on('SIGINT', () => void shutdown());

export default app;
