import { Router, Request, Response, NextFunction } from 'express';
import { z } from 'zod';
import { config } from '../../config';
import { aiEngineService } from '../../services/aiEngine.service';
import { authMiddleware, AuthRequest, optionalAuth } from '../../middleware/auth';
import { cached } from '../../utils/cache';
import prisma from '../../config/database';

const router = Router();

const chatSchema = z.object({
  prompt: z.string().min(1),
  sessionId: z.string().uuid().optional(),
});

const backtestSchema = z.object({
  symbol: z.string().min(1),
  strategy: z.string().min(1),
  startDate: z.string(),
  endDate: z.string(),
  params: z.record(z.unknown()).optional(),
});

router.post('/chat', optionalAuth, async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const { prompt, sessionId } = chatSchema.parse(req.body);

    if (req.userId && sessionId) {
      await prisma.chatMessage.create({
        data: { sessionId, role: 'user', content: prompt },
      });
    }

    const upstream = await aiEngineService.chatStream(prompt);
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');

    upstream.data.pipe(res);
    upstream.data.on('error', (err: Error) => next(err));
  } catch (err) {
    next(err);
  }
});

router.get('/consensus/:symbol', (req, res, next) => {
  const symbol = req.params.symbol.toUpperCase();
  return handle(req, res, next, () =>
    cached(`ai:consensus:${symbol}`, config.cacheTtl.consensus, () =>
      aiEngineService.getConsensus(symbol),
    ),
  );
});

router.post('/backtest', authMiddleware, async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const body = backtestSchema.parse(req.body);
    const result = await aiEngineService.submitBacktest({
      symbol: body.symbol.toUpperCase(),
      strategy: body.strategy,
      startDate: body.startDate,
      endDate: body.endDate,
      params: body.params,
    });
    res.status(202).json(result);
  } catch (err) {
    next(err);
  }
});

router.get('/backtest/:id/status', authMiddleware, async (req, res, next) => {
  try {
    const result = await aiEngineService.getBacktestStatus(req.params.id);
    res.json(result);
  } catch (err) {
    next(err);
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
