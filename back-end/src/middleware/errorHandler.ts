import { Request, Response, NextFunction } from 'express';

export function errorHandler(err: Error, _req: Request, res: Response, _next: NextFunction): void {
  console.error('[Error]', err.message, err.stack);

  if (err.name === 'ZodError') {
    res.status(400).json({ error: 'Validation error', details: (err as any).errors });
    return;
  }

  if (err.name === 'PrismaClientKnownRequestError') {
    const prismaErr = err as any;
    if (prismaErr.code === 'P2002') {
      res.status(409).json({ error: 'Resource already exists' });
      return;
    }
    if (prismaErr.code === 'P2025') {
      res.status(404).json({ error: 'Resource not found' });
      return;
    }
  }

  res.status(500).json({
    error: process.env.NODE_ENV === 'production' ? 'Internal server error' : err.message,
  });
}
