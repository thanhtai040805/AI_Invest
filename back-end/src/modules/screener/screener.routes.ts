import { Router, Request, Response, NextFunction } from 'express';
import { createHash } from 'crypto';
import { z } from 'zod';
import { config } from '../../config';
import { aiEngineService } from '../../services/aiEngine.service';
import { authMiddleware, AuthRequest } from '../../middleware/auth';
import { cached } from '../../utils/cache';
import prisma from '../../config/database';

const router = Router();

const filterSchema = z.object({
  exchange: z.string().optional(),
  peMin: z.number().optional(),
  peMax: z.number().optional(),
  pbMin: z.number().optional(),
  pbMax: z.number().optional(),
  roeMin: z.number().optional(),
  roeMax: z.number().optional(),
  rsiMin: z.number().optional(),
  rsiMax: z.number().optional(),
  marketCapMin: z.number().optional(),
  marketCapMax: z.number().optional(),
  volumeMin: z.number().optional(),
  sort: z.string().optional(),
  sortDir: z.enum(['asc', 'desc']).optional(),
  limit: z.number().optional(),
  offset: z.number().optional(),
});

const presetSchema = z.object({
  name: z.string().min(1),
  filters: filterSchema,
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

router.get('/presets/builtin', (req, res, next) =>
  handle(req, res, next, () => aiEngineService.getBuiltinPresets()),
);

router.post('/filter', (req, res, next) => {
  const filters = filterSchema.parse(req.body);
  const hash = createHash('md5').update(JSON.stringify(filters)).digest('hex');
  return handle(req, res, next, () =>
    cached(`screener:${hash}`, config.cacheTtl.screener, () => aiEngineService.screenStocks(filters)),
  );
});

router.get('/presets', authMiddleware, async (req: AuthRequest, res, next) => {
  try {
    const presets = await prisma.screenerPreset.findMany({
      where: { userId: req.userId! },
      orderBy: { createdAt: 'desc' },
    });
    res.json(presets);
  } catch (err) {
    next(err);
  }
});

router.post('/presets', authMiddleware, async (req: AuthRequest, res, next) => {
  try {
    const { name, filters } = presetSchema.parse(req.body);
    const preset = await prisma.screenerPreset.create({
      data: { userId: req.userId!, name, filters },
    });
    res.status(201).json(preset);
  } catch (err) {
    next(err);
  }
});

router.delete('/presets/:id', authMiddleware, async (req: AuthRequest, res, next) => {
  try {
    await prisma.screenerPreset.deleteMany({
      where: { id: req.params.id, userId: req.userId! },
    });
    res.status(204).send();
  } catch (err) {
    next(err);
  }
});

export default router;
