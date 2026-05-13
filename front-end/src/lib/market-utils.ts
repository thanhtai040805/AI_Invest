/**
 * Formats a number as a currency string for VN Market
 * @param value The number to format
 * @param unit 'B' for Billions (tỷ), 'T' for Trillions (nghìn tỷ)
 */
export function formatCurrency(value: number, unit: 'B' | 'T' | 'VND' = 'B'): string {
  if (unit === 'VND') {
    return value.toLocaleString('vi-VN') + ' ₫';
  }
  if (unit === 'B') {
    const billions = value / 1_000_000_000;
    return billions.toLocaleString('vi-VN', { maximumFractionDigits: 2 }) + ' tỷ';
  } else {
    const trillions = value / 1_000_000_000_000;
    return trillions.toLocaleString('vi-VN', { maximumFractionDigits: 3 }) + 'T';
  }
}

/**
 * Formats large volumes with separators
 */
export function formatVolume(value: number): string {
  if (value >= 1_000_000) {
    return (value / 1_000_000).toLocaleString('vi-VN', { maximumFractionDigits: 1 }) + 'M';
  }
  return value.toLocaleString('vi-VN');
}

/**
 * Determines the color for a price based on reference, ceiling, and floor
 */
export function getPriceColor(price: number, ref: number, ceiling: number, floor: number): string {
  if (price >= ceiling) return 'text-purple-500'; // Ceiling (Tím)
  if (price <= floor) return 'text-cyan-400'; // Floor (Lơ)
  if (price > ref) return 'text-secondary'; // Up (Xanh)
  if (price < ref) return 'text-error'; // Down (Đỏ)
  return 'text-yellow-400'; // Ref (Vàng)
}

/**
 * Checks if the market is currently open (9:00 - 15:00, Mon-Fri, excluding lunch 11:30-13:00)
 */
export function isMarketOpen(): boolean {
  const now = new Date();
  const day = now.getDay();
  const hours = now.getHours();
  const minutes = now.getMinutes();
  const time = hours * 100 + minutes;

  // Mon-Fri
  if (day === 0 || day === 6) return false;

  // 9:00 - 11:30
  if (time >= 900 && time <= 1130) return true;
  
  // 13:00 - 15:00
  if (time >= 1300 && time <= 1500) return true;

  return false;
}
