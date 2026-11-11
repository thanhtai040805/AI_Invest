import { Router, Response, NextFunction } from 'express';
import { z } from 'zod';
import { authMiddleware, AuthRequest } from '../../middleware/auth';
import * as portfolioService from '../../services/portfolio.service';

const router = Router();
router.use(authMiddleware);

router.get('/snapshot', async (req: AuthRequest, res: Response, next: NextFunction) => {
  try { res.json(await portfolioService.getSnapshot(req.userId!)); } catch (err) { next(err); }
});

const orderSchema = z.object({
  symbol: z.string().trim().regex(/^[A-Za-z0-9.-]{1,16}$/).transform((value) => value.toUpperCase()),
  side: z.enum(['BUY', 'SELL']),
  orderType: z.enum(['LO', 'MP', 'ATO', 'ATC']).default('LO'),
  price: z.number().finite().positive().optional(),
  quantity: z.number().int().positive().multipleOf(100).max(5_000_000),
}).superRefine((order, ctx) => {
  if (order.orderType === 'LO' && order.price == null) {
    ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['price'], message: 'Limit price is required' });
  }
});

const amendOrderSchema = z.object({
  price: z.number().finite().positive().optional(),
  quantity: z.number().int().positive().multipleOf(100).max(5_000_000).optional(),
}).refine((val) => val.price != null || val.quantity != null, {
  message: 'Cần cung cấp giá mới hoặc khối lượng mới để sửa lệnh.',
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

router.get('/engine-stats', async (_req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    res.json(await portfolioService.getEngineStats());
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
    if (err instanceof portfolioService.PortfolioError) {
      res.status(400).json({ error: message });
      return;
    }
    next(err);
  }
});

router.patch('/order/:id', async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const patch = amendOrderSchema.parse(req.body);
    const updated = await portfolioService.amendOrder(req.userId!, String(req.params.id), patch);
    res.json(updated);
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : 'Amend order failed';
    if (err instanceof portfolioService.PortfolioError) {
      res.status(400).json({ error: message });
      return;
    }
    next(err);
  }
});

router.delete('/order/:id', async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const cancelled = await portfolioService.cancelOrder(req.userId!, String(req.params.id));
    res.json(cancelled);
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : 'Cancel order failed';
    if (err instanceof portfolioService.PortfolioError) {
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
