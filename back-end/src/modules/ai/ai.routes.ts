import { Router, Request, Response, NextFunction } from 'express';
import { z } from 'zod';
import { config } from '../../config';
import { aiEngineService } from '../../services/aiEngine.service';
import { authMiddleware, AuthRequest } from '../../middleware/auth';
import { cached } from '../../utils/cache';
import prisma from '../../config/database';

const router = Router();

const backtestSchema = z.object({
  symbol: z.string().trim().regex(/^[A-Za-z0-9.-]{1,16}$/),
  strategy: z.enum(['sma_cross', 'rsi', 'bollinger']),
  startDate: z.string().date(),
  endDate: z.string().date(),
  params: z.record(z.unknown()).optional(),
  capital: z.coerce.number().finite().positive().optional(),
});

router.post('/backtest', authMiddleware, async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const body = backtestSchema.parse(req.body);
    const result = await aiEngineService.submitBacktest({
      symbol: body.symbol.toUpperCase(),
      start_date: body.startDate,
      end_date: body.endDate,
      strategy_config: { type: body.strategy, params: body.params ?? {} },
      initial_capital: body.capital,
      source: 'auto',
      user_id: req.userId!,
    });
    res.status(202).json(result);
  } catch (err) {
    next(err);
  }
});

router.get('/backtest/history', authMiddleware, async (req: AuthRequest, res, next) => {
  try {
    const result = await aiEngineService.getBacktestHistory(req.userId!);
    res.json(result);
  } catch (err) {
    next(err);
  }
});

router.get('/backtest/:id/results', authMiddleware, async (req: AuthRequest, res, next) => {
  try {
    if (!/^vn_[A-Z0-9.-]{1,16}_\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}(?:_[a-f0-9]{32})?$/.test(req.params.id)) {
      return res.status(400).json({ error: 'Invalid backtest run id' });
    }
    const result = await aiEngineService.getBacktestResults(req.params.id, req.userId!);
    res.json(result);
  } catch (err) {
    next(err);
  }
});

router.get('/backtest/:id/status', authMiddleware, async (req: AuthRequest, res, next) => {
  try {
    if (!/^vn_[A-Z0-9.-]{1,16}_\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}(?:_[a-f0-9]{32})?$/.test(req.params.id)) {
      return res.status(400).json({ error: 'Invalid backtest run id' });
    }
    const result = await aiEngineService.getBacktestStatus(req.params.id, req.userId!);
    res.json(result);
  } catch (err) {
    next(err);
  }
});

router.get('/pipeline/status', authMiddleware, async (_req, res, next) => {
  try {
    const status = await aiEngineService.getDailyPipelineStatus();
    res.json(status);
  } catch (_err) {
    res.json({ status: 'STANDBY', message: 'Autonomous pipeline daemon standby' });
  }
});

router.get('/sessions', authMiddleware, async (req: AuthRequest, res, next) => {
  try {
    const sessions = await prisma.chatSession.findMany({
      where: { userId: req.userId! },
      orderBy: { createdAt: 'desc' },
      include: { messages: { take: 1, orderBy: { createdAt: 'desc' } } },
    });
    res.json(sessions);
  } catch (err) {
    next(err);
  }
});

async function handle(
  req: Request,
  res: Response,
  next: NextFunction,
  fn: () => Promise<unknown>,
): Promise<void> {
  try {
    res.json(await fn());
  } catch (err) {
    next(err);
  }
}

export default router;
