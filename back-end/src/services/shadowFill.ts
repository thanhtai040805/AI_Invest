type Level = { price: number; volume: number };
type OrderBook = { bids?: Level[]; asks?: Level[]; lastUpdate?: string; marketState?: string };

export function shadowFill(
  book: OrderBook,
  side: 'BUY' | 'SELL',
  quantity: number,
  limitPrice?: number,
  now = Date.now(),
): { price: number; notional: number } {
  if (book?.marketState !== 'continuous_morning' && book?.marketState !== 'continuous_afternoon') {
    throw new Error('Shadow fills require a continuous trading session');
  }
  const age = now - Date.parse(book?.lastUpdate ?? '');
  if (!Number.isFinite(age) || age < 0 || age > 10_000) throw new Error('Live order book is unavailable or stale');
  if (!Number.isInteger(quantity) || quantity <= 0) throw new Error('Invalid order quantity');
  const levels = side === 'BUY' ? book.asks : book.bids;
  if (!Array.isArray(levels) || !levels.length) throw new Error('Live order book has no executable depth');

  let remaining = quantity;
  let notional = 0;
  const executable = levels.map((level) => ({
    price: Number(level.price) > 0 && Number(level.price) < 500 ? Math.round(Number(level.price) * 1000) : Number(level.price),
    volume: Number(level.volume),
  })).sort((a, b) => side === 'BUY' ? a.price - b.price : b.price - a.price);
  for (const level of executable) {
    const { price, volume } = level;
    if (!Number.isFinite(price) || price <= 0 || !Number.isInteger(volume) || volume <= 0) continue;
    if (limitPrice != null && (side === 'BUY' ? price > limitPrice : price < limitPrice)) break;
    const filled = Math.min(remaining, volume);
    notional += filled * price;
    remaining -= filled;
    if (remaining === 0) return { price: notional / quantity, notional };
  }
  throw new Error('Insufficient executable depth at the requested price');
}
