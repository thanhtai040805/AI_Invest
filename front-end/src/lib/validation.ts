import { z } from 'zod';

const PriceSchema = z.number().finite().nonnegative();
const VolumeSchema = z.number().finite().nonnegative().int();
const SymbolSchema = z.string().min(1).max(10);
const TimestampSchema = z.string().or(z.number());

export const OrderBookLevelSchema = z.object({
  price: PriceSchema,
  volume: VolumeSchema,
});

export const OrderBookSchema = z.object({
  symbol: SymbolSchema,
  bids: z.array(OrderBookLevelSchema),
  asks: z.array(OrderBookLevelSchema),
  lastUpdate: TimestampSchema.optional(),
});

export const TradeSchema = z.object({
  symbol: SymbolSchema,
  price: PriceSchema,
  volume: VolumeSchema,
  change: z.number().finite().optional().default(0),
  changePercent: z.number().finite().optional().default(0),
  tradingValue: z.number().finite().optional().default(0),
  open: PriceSchema.optional(),
  high: PriceSchema.optional(),
  low: PriceSchema.optional(),
  prevClose: PriceSchema.optional(),
  ceiling: PriceSchema.optional().default(0),
  floor: PriceSchema.optional().default(0),
  trend: z.enum(['up', 'down', 'steady']).optional().default('steady'),
  lastUpdate: TimestampSchema.optional(),
});

export const TradeExtraSchema = z.object({
  symbol: SymbolSchema,
  price: PriceSchema,
  volume: VolumeSchema,
  orderId: z.string().optional().default(''),
  matchType: z.string().optional().default(''),
  receivedAt: z.number().optional().nullable(),
  lastUpdate: TimestampSchema.optional(),
});

export const ExpectedPriceSchema = z.object({
  symbol: SymbolSchema,
  expectedPrice: PriceSchema,
  matchedVolume: VolumeSchema,
  receivedAt: z.number().optional().nullable(),
  lastUpdate: TimestampSchema.optional(),
});

export const ForeignTradingSchema = z.object({
  symbol: SymbolSchema,
  buyVolume: VolumeSchema,
  sellVolume: VolumeSchema,
  netVolume: z.number().finite(),
  buyValue: z.number().finite(),
  sellValue: z.number().finite(),
  netValue: z.number().finite(),
  lastUpdate: TimestampSchema.optional(),
});

export const OhlcSchema = z.object({
  symbol: SymbolSchema,
  open: PriceSchema,
  high: PriceSchema,
  low: PriceSchema,
  close: PriceSchema,
  volume: VolumeSchema,
  resolution: z.string().optional().default('1'),
  timestamp: z.number().optional().nullable(),
  lastUpdate: TimestampSchema.optional(),
});

export const SecurityDefSchema = z.object({
  symbol: SymbolSchema,
  name: z.string().optional().default(''),
  exchange: z.string().optional().default(''),
  ceiling: PriceSchema,
  floor: PriceSchema,
  prevClose: PriceSchema,
  lastUpdate: TimestampSchema.optional(),
});

export const MarketIndexSchema = z.object({
  name: z.string(),
  value: PriceSchema,
  change: z.number().finite(),
  changePercent: z.number().finite(),
  volume: VolumeSchema,
  lastUpdate: TimestampSchema.optional(),
});

export const MarketBreadthSchema = z.object({
  advancers: z.number().int().nonnegative(),
  decliners: z.number().int().nonnegative(),
  unchanged: z.number().int().nonnegative(),
  lastUpdate: TimestampSchema.optional(),
});

export const MarketSnapshotSchema = z.object({
  stocks: z.array(z.unknown()),
  total: z.number().int().nonnegative(),
});

// Safe parse helpers — return null instead of throwing
export function safeParse<T>(schema: z.ZodSchema<T>, data: unknown): T | null {
  const result = schema.safeParse(data);
  if (!result.success) {
    console.warn('[Validation] Schema reject:', result.error.issues[0]?.message);
    return null;
  }
  return result.data;
}
