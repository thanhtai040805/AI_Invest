import { Router, Request, Response } from 'express';
import bcrypt from 'bcryptjs';
import jwt, { Secret } from 'jsonwebtoken';
import crypto from 'crypto';
import { z } from 'zod';
import prisma from '../../config/database';
import { config } from '../../config';
import { authMiddleware, AuthRequest } from '../../middleware/auth';

const router = Router();
const db = prisma as any;
const REFRESH_COOKIE_NAME = 'refreshToken';
const REFRESH_COOKIE_PATH = '/api/v1/auth';

const registerSchema = z.object({
  email: z.string().email(),
  password: z.string().min(6),
  displayName: z.string().optional(),
});

const loginSchema = z.object({
  email: z.string().email(),
  password: z.string(),
});

function parseDuration(duration: string) {
  const match = duration.match(/^(\d+)([smhd])$/);
  if (!match) return 0;
  const value = Number(match[1]);
  switch (match[2]) {
    case 's': return value * 1000;
    case 'm': return value * 60 * 1000;
    case 'h': return value * 60 * 60 * 1000;
    case 'd': return value * 24 * 60 * 60 * 1000;
    default: return value * 1000;
  }
}

function createAccessToken(userId: string) {
  return (jwt.sign as any)(
    { userId, type: 'access' },
    config.jwt.accessSecret,
    { expiresIn: config.jwt.accessExpiresIn },
  );
}

function createRefreshToken(userId: string) {
  const tokenId = crypto.randomUUID();
  const token = (jwt.sign as any)(
    { userId, tokenId, type: 'refresh' },
    config.jwt.refreshSecret,
    { expiresIn: config.jwt.refreshExpiresIn },
  );
  return { token, tokenId };
}

function parseCookies(cookieHeader: string | undefined): Record<string, string> {
  if (!cookieHeader) return {};
  return Object.fromEntries(cookieHeader.split(';').map((segment) => {
    const [name, ...valueParts] = segment.trim().split('=');
    return [name, decodeURIComponent(valueParts.join('='))];
  }));
}

function getRefreshTokenFromRequest(req: Request) {
  const cookies = parseCookies(req.headers.cookie);
  return cookies[REFRESH_COOKIE_NAME];
}

function setRefreshTokenCookie(res: Response, token: string) {
  const maxAge = parseDuration(config.jwt.refreshExpiresIn);
  res.cookie(REFRESH_COOKIE_NAME, token, {
    httpOnly: true,
    secure: config.nodeEnv === 'production',
    sameSite: 'lax',
    path: REFRESH_COOKIE_PATH,
    maxAge,
  });
}

function clearRefreshTokenCookie(res: Response) {
  res.clearCookie(REFRESH_COOKIE_NAME, {
    httpOnly: true,
    secure: config.nodeEnv === 'production',
    sameSite: 'lax',
    path: REFRESH_COOKIE_PATH,
  });
}

async function createSessionTokens(userId: string) {
  const accessToken = createAccessToken(userId);
  const refresh = createRefreshToken(userId);

  await db.refreshToken.create({
    data: {
      tokenId: refresh.tokenId,
      userId,
      expiresAt: new Date(Date.now() + parseDuration(config.jwt.refreshExpiresIn)),
    },
  });

  return {
    accessToken,
    refreshToken: refresh.token,
  };
}

async function revokeRefreshToken(tokenId: string) {
  await db.refreshToken.updateMany({
    where: { tokenId },
    data: { isRevoked: true },
  });
}

function buildUserResponse(user: { id: string; email: string; displayName: string | null }) {
  return { id: user.id, email: user.email, displayName: user.displayName };
}

router.post('/register', async (req: Request, res: Response) => {
  try {
    const { email, password, displayName } = registerSchema.parse(req.body);

    const existing = await prisma.user.findUnique({ where: { email } });
    if (existing) {
      res.status(409).json({ error: 'Email already registered' });
      return;
    }

    const passwordHash = await bcrypt.hash(password, 12);
    const user = await prisma.user.create({
      data: { email, passwordHash, displayName },
    });

    const tokens = await createSessionTokens(user.id);
    setRefreshTokenCookie(res, tokens.refreshToken);

    res.status(201).json({
      user: buildUserResponse(user),
      accessToken: tokens.accessToken,
    });
  } catch (err: any) {
    if (err.name === 'ZodError') {
      res.status(400).json({ error: 'Validation error', details: err.errors });
      return;
    }
    throw err;
  }
});

router.post('/login', async (req: Request, res: Response) => {
  try {
    const { email, password } = loginSchema.parse(req.body);

    const user = await prisma.user.findUnique({ where: { email } });
    if (!user) {
      res.status(401).json({ error: 'Invalid credentials' });
      return;
    }

    const valid = await bcrypt.compare(password, user.passwordHash);
    if (!valid) {
      res.status(401).json({ error: 'Invalid credentials' });
      return;
    }

    const tokens = await createSessionTokens(user.id);
    setRefreshTokenCookie(res, tokens.refreshToken);

    res.json({
      user: buildUserResponse(user),
      accessToken: tokens.accessToken,
    });
  } catch (err: any) {
    if (err.name === 'ZodError') {
      res.status(400).json({ error: 'Validation error', details: err.errors });
      return;
    }
    throw err;
  }
});

router.post('/refresh', async (req: Request, res: Response) => {
  try {
    const refreshToken = getRefreshTokenFromRequest(req);
    if (!refreshToken) {
      res.status(401).json({ error: 'Refresh token missing' });
      return;
    }

    let decoded;
    try {
      decoded = jwt.verify(refreshToken, config.jwt.refreshSecret) as { userId: string; tokenId: string; type: string };
    } catch {
      res.status(401).json({ error: 'Invalid or expired refresh token' });
      return;
    }

    if (decoded.type !== 'refresh' || !decoded.tokenId) {
      res.status(401).json({ error: 'Invalid refresh token payload' });
      return;
    }

    const storedToken = await db.refreshToken.findUnique({ where: { tokenId: decoded.tokenId } });
    if (!storedToken || storedToken.isRevoked || storedToken.expiresAt < new Date()) {
      res.status(401).json({ error: 'Refresh token revoked or expired' });
      return;
    }

    await revokeRefreshToken(decoded.tokenId);
    const tokens = await createSessionTokens(decoded.userId);
    setRefreshTokenCookie(res, tokens.refreshToken);

    res.json({
      accessToken: tokens.accessToken,
    });
  } catch (err: any) {
    if (err.name === 'ZodError') {
      res.status(400).json({ error: 'Validation error', details: err.errors });
      return;
    }
    throw err;
  }
});

router.post('/logout', async (req: Request, res: Response) => {
  try {
    const refreshToken = getRefreshTokenFromRequest(req);
    if (refreshToken) {
      try {
        const decoded = jwt.verify(refreshToken, config.jwt.refreshSecret) as { tokenId: string };
        if (decoded?.tokenId) {
          await revokeRefreshToken(decoded.tokenId);
        }
      } catch {
        // ignore invalid refresh token during logout
      }
    }

    clearRefreshTokenCookie(res);
    res.status(204).send();
  } catch (err: any) {
    if (err.name === 'ZodError') {
      res.status(400).json({ error: 'Validation error', details: err.errors });
      return;
    }
    throw err;
  }
});

router.get('/me', authMiddleware, async (req: AuthRequest, res: Response) => {
  const user = await prisma.user.findUnique({
    where: { id: req.userId },
    select: { id: true, email: true, displayName: true, createdAt: true },
  });

  if (!user) {
    res.status(404).json({ error: 'User not found' });
    return;
  }

  res.json(user);
});

export default router;
