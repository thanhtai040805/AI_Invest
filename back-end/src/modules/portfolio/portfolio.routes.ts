import { Router, Response, NextFunction } from 'express';
import { z } from 'zod';
import { authMiddleware, AuthRequest } from '../../middleware/auth';
import * as portfolioService from '../../services/portfolio.service';

const router = Router();
router.use(authMiddleware);

const orderSchema = z.object({
  symbol: z.string().min(1),
  side: z.enum(['BUY', 'SELL']),
  orderType: z.string().default('LO'),
  price: z.number().optional(),
  quantity: z.number().int().positive(),
});

router.get('/summary', async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    res.json(await portfolioService.getSummary(req.userId!));
  } catch (err) {
    next(err);
  }
});

router.get('/positions', async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    res.json(await portfolioService.getPositions(req.userId!));
  } catch (err) {
    next(err);
  }
});

router.post('/order', async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const body = orderSchema.parse(req.body);
    const order = await portfolioService.placeOrder(req.userId!, body);
    res.status(201).json(order);
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : 'Order failed';
    if (message.includes('Insufficient')) {
      res.status(400).json({ error: message });
      return;
    }
    next(err);
  }
});

router.get('/orders', async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    res.json(await portfolioService.getOrders(req.userId!));
  } catch (err) {
    next(err);
  }
});

router.get('/performance', async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    res.json(await portfolioService.getPerformance(req.userId!));
  } catch (err) {
    next(err);
  }
});

router.get('/risk-metrics', async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    res.json(await portfolioService.getRiskMetrics(req.userId!));
  } catch (err) {
    next(err);
  }
});

export default router;
