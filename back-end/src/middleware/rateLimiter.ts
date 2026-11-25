import rateLimit from 'express-rate-limit';

export const apiLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 120,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Too many requests, please try again later' },
});

export const authLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 20,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Too many auth attempts, please try again later' },
});

export const aiLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 10,
  standardHeaders: true,
  legacyHeaders: false,
  skip: (req) => {
    const isResearchChat =
      req.path.startsWith('/research-chat/') ||
      req.originalUrl.startsWith('/api/v1/ai/research-chat/');
    if (!isResearchChat) return false;
    if (req.method === 'GET') return true;
    // Lightweight non-LLM user CRUD (already governed by 120/min apiLimiter) must not consume LLM stream quota
    const isMemoryCrud =
      req.path.startsWith('/research-chat/memories') ||
      req.originalUrl.startsWith('/api/v1/ai/research-chat/memories');
    if (isMemoryCrud && (req.method === 'PATCH' || req.method === 'DELETE' || req.method === 'POST')) {
      return true;
    }
    const isSessionDelete =
      req.method === 'DELETE' &&
      (req.path.startsWith('/research-chat/sessions/') ||
        req.originalUrl.startsWith('/api/v1/ai/research-chat/sessions/'));
    return isSessionDelete;
  },
  message: { error: 'AI rate limit exceeded. Please wait before sending more requests.' },
});

export const marketLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 300,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Market data rate limit exceeded.' },
});

export const screenerLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 60,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Screener rate limit exceeded.' },
});

export const portfolioLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 120,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Portfolio rate limit exceeded.' },
});
