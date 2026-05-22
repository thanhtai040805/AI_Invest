import dotenv from 'dotenv';

dotenv.config();

function requireEnv(key: string, fallback?: string): string {
  const value = process.env[key] ?? fallback;
  if (!value) {
    throw new Error(`Missing required environment variable: ${key}`);
  }
  return value;
}

function requireEnvProduction(key: string): string {
  const value = process.env[key];
  if (!value) {
    if (process.env.NODE_ENV === 'production') {
      throw new Error(`Missing required environment variable in production: ${key}`);
    }
    return `dev-${key.toLowerCase()}-fallback`;
  }
  return value;
}

const isProduction = process.env.NODE_ENV === 'production';

export const config = {
  nodeEnv: process.env.NODE_ENV ?? 'development',
  port: parseInt(process.env.PORT ?? '3001', 10),
  corsOrigin: isProduction
    ? requireEnv('CORS_ORIGIN')
    : process.env.CORS_ORIGIN ?? 'http://localhost:3000',
  databaseUrl: requireEnv('DATABASE_URL', 'postgresql://postgres:password@localhost:5432/aiinvest'),
  redisUrl: process.env.REDIS_URL ?? 'redis://localhost:6379',
  aiEngineUrl: process.env.AI_ENGINE_URL ?? 'http://localhost:8000',
  dnse: {
    enabled: process.env.DNSE_ENABLED === 'true',
    redisChannelPrefix: process.env.DNSE_REDIS_CHANNEL_PREFIX ?? 'dnse:event',
  },
  jwt: {
    accessSecret: requireEnvProduction('JWT_ACCESS_SECRET') || requireEnvProduction('JWT_SECRET'),
    refreshSecret: requireEnvProduction('JWT_REFRESH_SECRET') || requireEnvProduction('JWT_SECRET'),
    accessExpiresIn: process.env.JWT_ACCESS_EXPIRES_IN ?? '30m',
    refreshExpiresIn: process.env.JWT_REFRESH_EXPIRES_IN ?? '30d',
  },
  cacheTtl: {
    indices: 2,
    breadth: 5,
    snapshot: 3,
    quote: 1,
    orderbook: 1,
    ohlcv: 60,
    profile: 3600,
    fundamentals: 21600,
    screener: 30,
    consensus: 300,
  },
} as const;
