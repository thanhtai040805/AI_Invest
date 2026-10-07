type QuotePayload = Record<string, unknown>;

function quoteTimestamp(quote: QuotePayload): number | null {
  for (const candidate of [quote.receivedAt, quote.timestamp, quote.time, quote.lastUpdate, quote.asOf]) {
    if (candidate == null) continue;

    const numeric = Number(candidate);
    if (Number.isFinite(numeric) && numeric > 0) {
      return numeric < 1_000_000_000_000 ? numeric * 1000 : numeric;
    }

    const parsed = Date.parse(String(candidate));
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

export function hasMarketPrice(value: unknown): value is QuotePayload {
  if (!value || typeof value !== 'object') return false;
  const quote = value as QuotePayload;
  return [quote.price, quote.close].some((price) => Number.isFinite(Number(price)) && Number(price) > 0);
}

export function marketQuoteSnapshot(quote: QuotePayload, now = Date.now()): QuotePayload {
  const timestamp = quoteTimestamp(quote);
  const age = timestamp == null ? Infinity : now - timestamp;
  const stale = quote.stale === true || String(quote.source ?? '').startsWith('postgres')
    || age < -5_000 || age >= 30_000;

  return { ...quote, stale, isSnapshot: true };
}

export function mergeMarketQuote(stock: QuotePayload, quote?: QuotePayload | null, security?: QuotePayload | null): QuotePayload {
  const merged = hasMarketPrice(quote) ? { ...stock, ...marketQuoteSnapshot(quote) } : { ...stock };
  // Price bands arrive separately from trades; a cached EOD null must not erase them.
  const bands = [security, merged, stock].find((value) => value
    && [value.ceiling, value.floor].every((price) => Number.isFinite(Number(price)) && Number(price) > 0)
    && Number(value.ceiling) >= Number(value.floor));
  if (bands) {
    merged.ceiling = Number(bands.ceiling);
    merged.floor = Number(bands.floor);
    merged.priceBandAsOf = bands === security ? bands.lastUpdate : bands.priceBandAsOf;
  }
  return merged;
}
