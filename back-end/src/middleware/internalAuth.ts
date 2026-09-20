import crypto from 'crypto';
import { Request, Response, NextFunction } from 'express';
import { config } from '../config';

export function internalAuth(req: Request, res: Response, next: NextFunction): void {
  const provided = req.header('x-internal-token') ?? req.header('authorization')?.replace(/^Bearer\s+/i, '');
  const expected = config.internalServiceToken;
  const valid = provided != null
    && provided.length === expected.length
    && crypto.timingSafeEqual(Buffer.from(provided), Buffer.from(expected));

  if (!valid) {
    res.status(401).json({ error: 'Unauthorized internal service' });
    return;
  }
  next();
}
