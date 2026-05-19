import express from 'express';
import http from 'http';
import cors from 'cors';
import helmet from 'helmet';
import morgan from 'morgan';
import compression from 'compression';
import { config } from './config';
import { errorHandler } from './middleware/errorHandler';
import { apiLimiter } from './middleware/rateLimiter';
import { socketService } from './services/socket.service';
import { redisService } from './services/redis.service';
import { initScheduler, shutdownScheduler } from './services/scheduler.service';
import { dnseRelayService } from './services/dnseRelay.service';

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

// ── Middleware ─────────────────────────────────────────
app.use(helmet({ contentSecurityPolicy: false }));
app.use(cors({ origin: config.corsOrigin, credentials: true }));
app.use(compression());
app.use(express.json({ limit: '10mb' }));
app.use(morgan('dev'));

// ── Health Check ──────────────────────────────────────
app.get('/api/health', (_req, res) => {
  res.json({
    status: 'ok',
    timestamp: new Date().toISOString(),
    dataProvider: config.dnse.enabled ? 'dnse' : 'legacy-poll',
    dnseRelay: config.dnse.enabled,
  });
});

// ── API Routes ────────────────────────────────────────
app.use('/api/v1', apiLimiter);
app.use('/api/v1/auth', authRoutes);
app.use('/api/v1/market', marketRoutes);
app.use('/api/v1/stock', stockRoutes);
app.use('/api/v1/stock', stockAdminRoutes);
app.use('/api/v1/screener', screenerRoutes);
app.use('/api/v1/portfolio', portfolioRoutes);
app.use('/api/v1/ai', aiRoutes);
app.use('/api/v1/community', communityRoutes);

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

  server.listen(config.port, () => {
    console.log(`
  ╔══════════════════════════════════════════════╗
  ║   AIInvest Backend v1.0                      ║
  ║   Port: ${config.port}                              ║
  ║   Env:  ${config.nodeEnv}                     ║
  ║   CORS: ${config.corsOrigin}            ║
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
