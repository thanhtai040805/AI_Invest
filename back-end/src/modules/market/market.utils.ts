export function bestOrderBookLevel(value: unknown, highest: boolean): Record<string, unknown> | null {
  if (!Array.isArray(value)) return null;
  let best: Record<string, unknown> | null = null;
  for (const item of value) {
    if (!item || typeof item !== 'object') continue;
    const level = item as Record<string, unknown>;
    const price = Number(level.price);
    if (!Number.isFinite(price) || price <= 0) continue;
    if (!best || (highest ? price > Number(best.price) : price < Number(best.price))) best = level;
  }
  return best;
}
