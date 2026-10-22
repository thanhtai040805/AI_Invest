interface SectorWeightInput {
  weight?: unknown; marketWeight?: unknown; market_cap?: unknown; marketCap?: unknown;
  count?: unknown; marketCapCount?: unknown; market_cap_count?: unknown;
}

export const SECTOR_LABELS_VI: Record<string, string> = {
  BANKS: "Ngân hàng",
  FINANCIAL_SERVICES: "Chứng khoán",
  REAL_ESTATE: "Bất động sản",
  CONSTRUCTION: "Xây dựng",
  CONSTRUCTION_MATERIALS: "Vật liệu xây dựng",
  BASIC_RESOURCES: "Thép & Tài nguyên",
  CHEMICALS: "Phân đạm & Hóa chất",
  OIL_GAS: "Dầu khí",
  FOOD_BEVERAGE: "Thực phẩm & Đồ uống",
  TECHNOLOGY: "Công nghệ",
  INDUSTRIAL_GOODS: "Hàng công nghiệp",
  TRANSPORTATION: "Vận tải & Cảng biển",
  RETAIL_TRADE: "Bán lẻ",
  HEALTHCARE: "Y tế & Dược",
  UTILITIES: "Điện & Tiện ích",
  AGRICULTURE: "Nông - Thủy sản",
  CONSUMER_SERVICES: "Dịch vụ tiêu dùng",
  OTHER_INDUSTRIALS: "Công nghiệp khác",
  "Basic Materials": "Nguyên vật liệu",
  Industrials: "Công nghiệp",
  "Consumer Cyclical": "Tiêu dùng không thiết yếu",
  "Consumer Defensive": "Tiêu dùng thiết yếu",
  "Financial Services": "Dịch vụ tài chính",
  "Real Estate": "Bất động sản",
  Technology: "Công nghệ",
  "Communication Services": "Truyền thông",
  Energy: "Năng lượng",
  Utilities: "Tiện ích",
  Healthcare: "Y tế",
};

export const SECTOR_COLOR_PALETTE: string[] = [
  "#F56565", "#06B6D4", "#F43F5E", "#22C55E", "#3B82F6",
  "#F59E0B", "#6366F1", "#14B8A6", "#EC4899", "#EF4444",
  "#8B5CF6", "#F97316", "#818CF8", "#10B981", "#FB7185",
  "#D946EF", "#0EA5E9", "#EAB308", "#A855F7", "#2DD4BF",
];

export function formatSectorName(raw?: string | null): string {
  if (!raw) return "Khác";
  const trimmed = String(raw).trim();
  return SECTOR_LABELS_VI[trimmed] || SECTOR_LABELS_VI[trimmed.toUpperCase()] || trimmed;
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
