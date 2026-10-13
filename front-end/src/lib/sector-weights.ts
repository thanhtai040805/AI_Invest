interface SectorWeightInput {
  weight?: unknown; marketWeight?: unknown; market_cap?: unknown; marketCap?: unknown;
  count?: unknown; marketCapCount?: unknown; market_cap_count?: unknown;
}

export function sectorWeights(sectors: SectorWeightInput[]) {
  const positive = (value: unknown) => Number.isFinite(Number(value)) && Number(value) > 0 ? Number(value) : 0;
  const capitals = sectors.map((sector) => [sector.market_cap, sector.marketCap, sector.weight, sector.marketWeight]
    .map(positive).find((value) => value > 0) ?? 0);
  const weightByCount = !sectors.length || sectors.some((sector, index) => {
    const coverage = positive(sector.market_cap) > 0
      ? sector.market_cap_count ?? sector.marketCapCount : sector.marketCapCount ?? sector.market_cap_count;
    return capitals[index] <= 0 || positive(sector.count) <= 0
      || positive(coverage) !== positive(sector.count);
  });
  const values = weightByCount ? sectors.map((sector) => positive(sector.count)) : capitals;
  const total = values.reduce((sum, value) => sum + value, 0);
  return { weights: values.map((value) => total > 0 ? value / total * 100 : 0), weightByCount };
}
