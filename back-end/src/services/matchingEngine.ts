import { performance } from 'perf_hooks';

export type OrderSide = 'BUY' | 'SELL';
export type OrderType = 'LO' | 'MP' | 'ATO' | 'ATC';
export type ExchangeCode = 'HOSE' | 'HNX' | 'UPCOM';
export type OrderLifecycleStatus =
  | 'PENDING'
  | 'PARTIALLY_FILLED'
  | 'FILLED'
  | 'CANCELLED'
  | 'REJECTED';

export interface BookPriceLevel {
  price: number;
  volume: number;
}

export interface ExternalOrderBookSnapshot {
  symbol?: string;
  bids?: BookPriceLevel[];
  asks?: BookPriceLevel[];
  lastUpdate?: string;
  receivedAt?: number;
  marketState?: string;
  stale?: boolean;
  ceiling?: number;
  floor?: number;
  ref?: number;
}

export interface FillSlice {
  price: number;
  quantity: number;
  notional: number;
  liquidity: 'INTERNAL_CLOB' | 'DNSE_LIVE_BOOK' | 'DNSE_LIVE_TRADE' | 'AUCTION_CROSS';
  makerOrderId?: string;
  makerUserId?: string;
  matchedAt: string;
}

export interface RestingOrderTicket {
  orderId: string;
  userId: string;
  symbol: string;
  exchange: ExchangeCode;
  side: OrderSide;
  orderType: OrderType;
  limitPrice: number; // 0 for pure ATO/ATC before conversion
  initialQuantity: number;
  filledQuantity: number;
  remainingQuantity: number;
  slot: number; // ArenaOrderBook slot (-1 for unpriced ATO/ATC auction queue)
  createdAtMs: number;
}

export interface EngineThroughputReport {
  ordersProcessed: number;
  fillsGenerated: number;
  elapsedMs: number;
  ordersPerSec: number;
  avgLatencyNs: number;
  p50LatencyNs: number;
  p99LatencyNs: number;
  tickResolutionVnd: number;
  arenaCapacity: number;
}

export const TICK_UNIT_VND = 10;
export const MAX_PRICE_TICKS = 65_536; // Covers 0 .. 655,350 VND at 10 VND resolution

const SIDE_FREE = 0;
const SIDE_BUY = 1;
const SIDE_SELL = 2;

export function hashOwnerId(ownerId: string): number {
  let h = 2166136261;
  for (let i = 0; i < ownerId.length; i++) {
    h ^= ownerId.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  const signed = h | 0;
  return signed === 0 ? 1 : signed;
}

export function priceToTick(priceVnd: number): number {
  const tick = Math.round(priceVnd / TICK_UNIT_VND);
  if (tick < 1) return 1;
  if (tick >= MAX_PRICE_TICKS) return MAX_PRICE_TICKS - 1;
  return tick;
}

export function tickToPrice(tick: number): number {
  return tick * TICK_UNIT_VND;
}

export function getExchangeTickStep(priceVnd: number, exchange: ExchangeCode = 'HOSE'): number {
  if (exchange === 'HNX' || exchange === 'UPCOM') return 100;
  if (priceVnd < 10_000) return 10;
  if (priceVnd < 50_000) return 50;
  return 100;
}

export function getExchangeBandRatio(exchange: ExchangeCode = 'HOSE'): number {
  if (exchange === 'HNX') return 0.10;
  if (exchange === 'UPCOM') return 0.15;
  return 0.07;
}

export function computeExchangeBands(
  refPriceVnd: number,
  exchange: ExchangeCode = 'HOSE',
): { ceiling: number; floor: number } {
  if (!Number.isFinite(refPriceVnd) || refPriceVnd <= 0) return { ceiling: 0, floor: 0 };
  const ratio = getExchangeBandRatio(exchange);
  const rawCeil = refPriceVnd * (1 + ratio);
  const rawFloor = refPriceVnd * (1 - ratio);
  const ceilStep = getExchangeTickStep(rawCeil, exchange);
  const floorStep = getExchangeTickStep(rawFloor, exchange);
  return {
    ceiling: Math.floor((rawCeil + 1e-6) / ceilStep) * ceilStep,
    floor: Math.ceil((rawFloor - 1e-6) / floorStep) * floorStep,
  };
}

export function normalizeVndPrice(raw: unknown): number {
  const n = Number(raw ?? 0);
  if (!Number.isFinite(n) || n <= 0) return 0;
  return n < 500 ? Math.round(n * 1000) : Math.round(n);
}

export function validateExchangeOrder(params: {
  symbol: string;
  exchange?: ExchangeCode;
  side: OrderSide;
  orderType: OrderType;
  price?: number;
  quantity: number;
  ceiling?: number;
  floor?: number;
}): { valid: true } | { valid: false; reason: string } {
  const exchange = params.exchange ?? 'HOSE';
  const { orderType, price, quantity } = params;

  if (!Number.isInteger(quantity) || quantity < 100 || quantity % 100 !== 0) {
    return { valid: false, reason: 'Khối lượng đặt phải là bội số lô chẵn 100 cổ phiếu.' };
  }
  if (quantity > 5_000_000) {
    return { valid: false, reason: 'Khối lượng vượt hạn mức tối đa 5.000.000 cổ phiếu mỗi lệnh.' };
  }

  if (orderType === 'LO') {
    if (price == null || !Number.isFinite(price) || price <= 0) {
      return { valid: false, reason: 'Lệnh giới hạn (LO) bắt buộc phải có mức giá hợp lệ.' };
    }
    if (!Number.isInteger(price)) {
      return { valid: false, reason: 'Mức giá đặt VND phải là số nguyên.' };
    }
    const step = getExchangeTickStep(price, exchange);
    if (price % step !== 0) {
      return {
        valid: false,
        reason: `Sai bước giá sàn ${exchange}: mức giá ${price.toLocaleString('vi-VN')} ₫ phải chia hết cho bước giá ${step.toLocaleString('vi-VN')} ₫.`,
      };
    }
    const ceiling = normalizeVndPrice(params.ceiling);
    const floor = normalizeVndPrice(params.floor);
    if (ceiling > 0 && floor > 0 && ceiling >= floor) {
      if (price > ceiling) {
        return {
          valid: false,
          reason: `Giá đặt (${price.toLocaleString('vi-VN')} ₫) vượt giá Trần phiên (${ceiling.toLocaleString('vi-VN')} ₫).`,
        };
      }
      if (price < floor) {
        return {
          valid: false,
          reason: `Giá đặt (${price.toLocaleString('vi-VN')} ₫) thấp hơn giá Sàn phiên (${floor.toLocaleString('vi-VN')} ₫).`,
        };
      }
    }
  }

  return { valid: true };
}

/**
 * ArenaOrderBook — Zero-GC TypedArray Slab + 3-Level Hierarchical Bitset CLOB
 *
 * Complexity:
 * - Best Bid / Best Ask lookup: O(1) via 3 hardware CLZ bitscans (~4 ns)
 * - Resting order insertion: O(1) intrusive tail append
 * - Cancel by slot: O(1) intrusive doubly-linked list unlink
 * - Quantity-down amend: O(1) in-place volume reduction preserving FIFO priority
 * - Hot-path memory allocations: 0 bytes (zero GC pressure)
 */
export class ArenaOrderBook {
  public readonly capacity: number;
  public readonly slotPriceTick: Int32Array;
  public readonly slotQty: Int32Array;
  public readonly slotInitialQty: Int32Array;
  public readonly slotOwner: Int32Array;
  public readonly slotSide: Uint8Array;
  public readonly slotPrev: Int32Array;
  public readonly slotNext: Int32Array;

  private readonly freeStack: Int32Array;
  private freeTop: number;

  private readonly bidHead = new Int32Array(MAX_PRICE_TICKS).fill(-1);
  private readonly bidTail = new Int32Array(MAX_PRICE_TICKS).fill(-1);
  private readonly bidVol = new Float64Array(MAX_PRICE_TICKS);

  private readonly askHead = new Int32Array(MAX_PRICE_TICKS).fill(-1);
  private readonly askTail = new Int32Array(MAX_PRICE_TICKS).fill(-1);
  private readonly askVol = new Float64Array(MAX_PRICE_TICKS);

  // 3-Level Hierarchical Bitset covering 65,536 price ticks:
  // L2: 2048 x 32-bit words -> L1: 64 x 32-bit words -> L0: 2 x 32-bit ints (Lo/Hi)
  private readonly bidL2 = new Uint32Array(2048);
  private readonly bidL1 = new Uint32Array(64);
  private bidL0Lo = 0;
  private bidL0Hi = 0;

  private readonly askL2 = new Uint32Array(2048);
  private readonly askL1 = new Uint32Array(64);
  private askL0Lo = 0;
  private askL0Hi = 0;

  // Flat pre-allocated fill output buffer for zero-GC matching
  public readonly maxFillsPerOrder: number;
  public readonly fillMakerSlot: Int32Array;
  public readonly fillPriceTick: Int32Array;
  public readonly fillQty: Int32Array;
  public lastFillCount = 0;
  public lastFilledQty = 0;
  public lastFillNotionalTicks = 0;
  public lastClearedMakerCount = 0;
  public readonly clearedMakerSlots: Int32Array;

  public totalOrdersProcessed = 0;
  public totalFillsGenerated = 0;
  public activeRestingCount = 0;

  constructor(capacity = 65_536, maxFillsPerOrder = 4_096) {
    this.capacity = capacity;
    this.maxFillsPerOrder = maxFillsPerOrder;

    this.slotPriceTick = new Int32Array(capacity);
    this.slotQty = new Int32Array(capacity);
    this.slotInitialQty = new Int32Array(capacity);
    this.slotOwner = new Int32Array(capacity);
    this.slotSide = new Uint8Array(capacity);
    this.slotPrev = new Int32Array(capacity).fill(-1);
    this.slotNext = new Int32Array(capacity).fill(-1);

    this.freeStack = new Int32Array(capacity);
    for (let i = 0; i < capacity; i++) {
      this.freeStack[i] = capacity - 1 - i;
    }
    this.freeTop = capacity;

    this.fillMakerSlot = new Int32Array(maxFillsPerOrder);
    this.fillPriceTick = new Int32Array(maxFillsPerOrder);
    this.fillQty = new Int32Array(maxFillsPerOrder);
    this.clearedMakerSlots = new Int32Array(maxFillsPerOrder);
  }

  public reset(): void {
    this.slotSide.fill(SIDE_FREE);
    this.bidHead.fill(-1);
    this.bidTail.fill(-1);
    this.bidVol.fill(0);
    this.askHead.fill(-1);
    this.askTail.fill(-1);
    this.askVol.fill(0);
    this.bidL2.fill(0);
    this.bidL1.fill(0);
    this.bidL0Lo = 0;
    this.bidL0Hi = 0;
    this.askL2.fill(0);
    this.askL1.fill(0);
    this.askL0Lo = 0;
    this.askL0Hi = 0;
    for (let i = 0; i < this.capacity; i++) {
      this.freeStack[i] = this.capacity - 1 - i;
    }
    this.freeTop = this.capacity;
    this.activeRestingCount = 0;
    this.lastFillCount = 0;
    this.lastFilledQty = 0;
    this.lastFillNotionalTicks = 0;
    this.lastClearedMakerCount = 0;
  }

  private setBidBit(tick: number): void {
    const w2 = tick >>> 5;
    const b2 = tick & 31;
    this.bidL2[w2] |= 1 << b2;
    const w1 = w2 >>> 5;
    const b1 = w2 & 31;
    this.bidL1[w1] |= 1 << b1;
    const b0 = w1 & 31;
    if (w1 < 32) this.bidL0Lo |= 1 << b0;
    else this.bidL0Hi |= 1 << b0;
  }

  private clearBidBit(tick: number): void {
    const w2 = tick >>> 5;
    const b2 = tick & 31;
    const m2 = this.bidL2[w2] & ~(1 << b2);
    this.bidL2[w2] = m2;
    if (m2 === 0) {
      const w1 = w2 >>> 5;
      const b1 = w2 & 31;
      const m1 = this.bidL1[w1] & ~(1 << b1);
      this.bidL1[w1] = m1;
      if (m1 === 0) {
        const b0 = w1 & 31;
        if (w1 < 32) this.bidL0Lo &= ~(1 << b0);
        else this.bidL0Hi &= ~(1 << b0);
      }
    }
  }

  private setAskBit(tick: number): void {
    const w2 = tick >>> 5;
    const b2 = tick & 31;
    this.askL2[w2] |= 1 << b2;
    const w1 = w2 >>> 5;
    const b1 = w2 & 31;
    this.askL1[w1] |= 1 << b1;
    const b0 = w1 & 31;
    if (w1 < 32) this.askL0Lo |= 1 << b0;
    else this.askL0Hi |= 1 << b0;
  }

  private clearAskBit(tick: number): void {
    const w2 = tick >>> 5;
    const b2 = tick & 31;
    const m2 = this.askL2[w2] & ~(1 << b2);
    this.askL2[w2] = m2;
    if (m2 === 0) {
      const w1 = w2 >>> 5;
      const b1 = w2 & 31;
      const m1 = this.askL1[w1] & ~(1 << b1);
      this.askL1[w1] = m1;
      if (m1 === 0) {
        const b0 = w1 & 31;
        if (w1 < 32) this.askL0Lo &= ~(1 << b0);
        else this.askL0Hi &= ~(1 << b0);
      }
    }
  }

  public bestBidTick(): number {
    let w1: number;
    if (this.bidL0Hi !== 0) {
      w1 = 32 + (31 - Math.clz32(this.bidL0Hi));
    } else if (this.bidL0Lo !== 0) {
      w1 = 31 - Math.clz32(this.bidL0Lo);
    } else {
      return -1;
    }
    const v1 = this.bidL1[w1];
    const w2 = (w1 << 5) | (31 - Math.clz32(v1));
    const v2 = this.bidL2[w2];
    return (w2 << 5) | (31 - Math.clz32(v2));
  }

  public bestAskTick(): number {
    let w1: number;
    if (this.askL0Lo !== 0) {
      const lsb = this.askL0Lo & -this.askL0Lo;
      w1 = 31 - Math.clz32(lsb);
    } else if (this.askL0Hi !== 0) {
      const lsb = this.askL0Hi & -this.askL0Hi;
      w1 = 32 + (31 - Math.clz32(lsb));
    } else {
      return -1;
    }
    const v1 = this.askL1[w1];
    const w2 = (w1 << 5) | (31 - Math.clz32(v1 & -v1));
    const v2 = this.askL2[w2];
    return (w2 << 5) | (31 - Math.clz32(v2 & -v2));
  }

  public nextAskTickAfter(tick: number): number {
    let next = tick + 1;
    if (next >= MAX_PRICE_TICKS) return -1;
    let w2 = next >>> 5;
    const remBits = this.askL2[w2] & (~0 << (next & 31));
    if (remBits !== 0) {
      return (w2 << 5) | (31 - Math.clz32(remBits & -remBits));
    }
    w2++;
    if (w2 >= 2048) return -1;
    let w1 = w2 >>> 5;
    const remL1 = this.askL1[w1] & (~0 << (w2 & 31));
    if (remL1 !== 0) {
      const nw2 = (w1 << 5) | (31 - Math.clz32(remL1 & -remL1));
      const v2 = this.askL2[nw2];
      return (nw2 << 5) | (31 - Math.clz32(v2 & -v2));
    }
    w1++;
    if (w1 >= 64) return -1;
    let foundW1 = -1;
    if (w1 < 32) {
      const remLo = this.askL0Lo & (~0 << w1);
      if (remLo !== 0) {
        foundW1 = 31 - Math.clz32(remLo & -remLo);
      } else if (this.askL0Hi !== 0) {
        foundW1 = 32 + (31 - Math.clz32(this.askL0Hi & -this.askL0Hi));
      }
    } else {
      const remHi = this.askL0Hi & (~0 << (w1 - 32));
      if (remHi !== 0) {
        foundW1 = 32 + (31 - Math.clz32(remHi & -remHi));
      }
    }
    if (foundW1 < 0) return -1;
    const v1 = this.askL1[foundW1];
    const nw2 = (foundW1 << 5) | (31 - Math.clz32(v1 & -v1));
    const v2 = this.askL2[nw2];
    return (nw2 << 5) | (31 - Math.clz32(v2 & -v2));
  }

  public prevBidTickBefore(tick: number): number {
    const prev = tick - 1;
    if (prev < 1) return -1;
    let w2 = prev >>> 5;
    const mask2 = (prev & 31) === 31 ? -1 : (1 << ((prev & 31) + 1)) - 1;
    const remBits = this.bidL2[w2] & mask2;
    if (remBits !== 0) {
      return (w2 << 5) | (31 - Math.clz32(remBits));
    }
    w2--;
    if (w2 < 0) return -1;
    let w1 = w2 >>> 5;
    const mask1 = (w2 & 31) === 31 ? -1 : (1 << ((w2 & 31) + 1)) - 1;
    const remL1 = this.bidL1[w1] & mask1;
    if (remL1 !== 0) {
      const nw2 = (w1 << 5) | (31 - Math.clz32(remL1));
      const v2 = this.bidL2[nw2];
      return (nw2 << 5) | (31 - Math.clz32(v2));
    }
    w1--;
    if (w1 < 0) return -1;
    let foundW1 = -1;
    if (w1 >= 32) {
      const b0 = w1 - 32;
      const maskHi = b0 === 31 ? -1 : (1 << (b0 + 1)) - 1;
      const remHi = this.bidL0Hi & maskHi;
      if (remHi !== 0) {
        foundW1 = 32 + (31 - Math.clz32(remHi));
      } else if (this.bidL0Lo !== 0) {
        foundW1 = 31 - Math.clz32(this.bidL0Lo);
      }
    } else {
      const maskLo = w1 === 31 ? -1 : (1 << (w1 + 1)) - 1;
      const remLo = this.bidL0Lo & maskLo;
      if (remLo !== 0) {
        foundW1 = 31 - Math.clz32(remLo);
      }
    }
    if (foundW1 < 0) return -1;
    const v1 = this.bidL1[foundW1];
    const nw2 = (foundW1 << 5) | (31 - Math.clz32(v1));
    const v2 = this.bidL2[nw2];
    return (nw2 << 5) | (31 - Math.clz32(v2));
  }

  private allocSlot(
    sideCode: number,
    priceTick: number,
    qty: number,
    initialQty: number,
    ownerHash: number,
  ): number {
    if (this.freeTop === 0) return -1;
    const slot = this.freeStack[--this.freeTop];
    this.slotSide[slot] = sideCode;
    this.slotPriceTick[slot] = priceTick;
    this.slotQty[slot] = qty;
    this.slotInitialQty[slot] = initialQty;
    this.slotOwner[slot] = ownerHash;
    this.slotNext[slot] = -1;

    if (sideCode === SIDE_BUY) {
      const tail = this.bidTail[priceTick];
      this.slotPrev[slot] = tail;
      if (tail === -1) {
        this.bidHead[priceTick] = slot;
        this.setBidBit(priceTick);
      } else {
        this.slotNext[tail] = slot;
      }
      this.bidTail[priceTick] = slot;
      this.bidVol[priceTick] += qty;
    } else {
      const tail = this.askTail[priceTick];
      this.slotPrev[slot] = tail;
      if (tail === -1) {
        this.askHead[priceTick] = slot;
        this.setAskBit(priceTick);
      } else {
        this.slotNext[tail] = slot;
      }
      this.askTail[priceTick] = slot;
      this.askVol[priceTick] += qty;
    }
    this.activeRestingCount++;
    return slot;
  }

  private unlinkAndFreeSlot(slot: number): void {
    const sideCode = this.slotSide[slot];
    if (sideCode === SIDE_FREE) return;
    const tick = this.slotPriceTick[slot];
    const qty = this.slotQty[slot];
    const prev = this.slotPrev[slot];
    const next = this.slotNext[slot];

    if (sideCode === SIDE_BUY) {
      if (prev === -1) this.bidHead[tick] = next;
      else this.slotNext[prev] = next;
      if (next === -1) this.bidTail[tick] = prev;
      else this.slotPrev[next] = prev;
      this.bidVol[tick] = Math.max(0, this.bidVol[tick] - qty);
      if (this.bidHead[tick] === -1) {
        this.bidVol[tick] = 0;
        this.clearBidBit(tick);
      }
    } else {
      if (prev === -1) this.askHead[tick] = next;
      else this.slotNext[prev] = next;
      if (next === -1) this.askTail[tick] = prev;
      else this.slotPrev[next] = prev;
      this.askVol[tick] = Math.max(0, this.askVol[tick] - qty);
      if (this.askHead[tick] === -1) {
        this.askVol[tick] = 0;
        this.clearAskBit(tick);
      }
    }

    this.slotSide[slot] = SIDE_FREE;
    this.slotQty[slot] = 0;
    this.slotPrev[slot] = -1;
    this.slotNext[slot] = -1;
    this.freeStack[this.freeTop++] = slot;
    this.activeRestingCount--;
  }

  /**
   * Queues a resting limit order directly onto the order book without continuous matching
   * (used during ATO/ATC Call Auction collection phase and resting OMS hydration).
   */
  public queueAuctionLimitRaw(
    isBuy: boolean,
    limitTick: number,
    quantity: number,
    ownerHash = 0,
  ): number {
    this.totalOrdersProcessed++;
    this.lastFillCount = 0;
    this.lastFilledQty = 0;
    this.lastFillNotionalTicks = 0;
    this.lastClearedMakerCount = 0;
    if (quantity <= 0 || limitTick < 1 || limitTick >= MAX_PRICE_TICKS) return -1;
    return this.allocSlot(
      isBuy ? SIDE_BUY : SIDE_SELL,
      limitTick,
      quantity,
      quantity,
      ownerHash,
    );
  }

  /**
   * Zero-GC Limit Order Submission.
   * Returns resting slot >= 0 if residual quantity is queued, or -1 if 100% filled / arena full.
   */
  public submitLimitRaw(
    isBuy: boolean,
    limitTick: number,
    quantity: number,
    ownerHash = 0,
    restRemainder = true,
  ): number {
    this.totalOrdersProcessed++;
    this.lastFillCount = 0;
    this.lastFilledQty = 0;
    this.lastFillNotionalTicks = 0;
    this.lastClearedMakerCount = 0;

    let remaining = quantity;
    let fillCount = 0;
    let clearedCount = 0;

    if (isBuy) {
      let askTick = this.bestAskTick();
      while (remaining > 0 && askTick !== -1 && askTick <= limitTick) {
        let cur = this.askHead[askTick];
        while (cur !== -1 && remaining > 0) {
          const next = this.slotNext[cur];
          if (ownerHash !== 0 && this.slotOwner[cur] === ownerHash) {
            cur = next;
            continue;
          }
          const makerQty = this.slotQty[cur];
          const execQty = remaining < makerQty ? remaining : makerQty;
          remaining -= execQty;
          this.lastFilledQty += execQty;
          this.lastFillNotionalTicks += execQty * askTick;

          if (fillCount < this.maxFillsPerOrder) {
            this.fillMakerSlot[fillCount] = cur;
            this.fillPriceTick[fillCount] = askTick;
            this.fillQty[fillCount] = execQty;
            fillCount++;
          }

          if (execQty === makerQty) {
            if (clearedCount < this.maxFillsPerOrder) {
              this.clearedMakerSlots[clearedCount++] = cur;
            }
            this.unlinkAndFreeSlot(cur);
          } else {
            this.slotQty[cur] = makerQty - execQty;
            this.askVol[askTick] -= execQty;
          }
          cur = next;
        }
        if (remaining === 0) break;
        const topNow = this.bestAskTick();
        askTick = topNow > askTick ? topNow : this.nextAskTickAfter(askTick);
      }
    } else {
      let bidTick = this.bestBidTick();
      while (remaining > 0 && bidTick !== -1 && bidTick >= limitTick) {
        let cur = this.bidHead[bidTick];
        while (cur !== -1 && remaining > 0) {
          const next = this.slotNext[cur];
          if (ownerHash !== 0 && this.slotOwner[cur] === ownerHash) {
            cur = next;
            continue;
          }
          const makerQty = this.slotQty[cur];
          const execQty = remaining < makerQty ? remaining : makerQty;
          remaining -= execQty;
          this.lastFilledQty += execQty;
          this.lastFillNotionalTicks += execQty * bidTick;

          if (fillCount < this.maxFillsPerOrder) {
            this.fillMakerSlot[fillCount] = cur;
            this.fillPriceTick[fillCount] = bidTick;
            this.fillQty[fillCount] = execQty;
            fillCount++;
          }

          if (execQty === makerQty) {
            if (clearedCount < this.maxFillsPerOrder) {
              this.clearedMakerSlots[clearedCount++] = cur;
            }
            this.unlinkAndFreeSlot(cur);
          } else {
            this.slotQty[cur] = makerQty - execQty;
            this.bidVol[bidTick] -= execQty;
          }
          cur = next;
        }
        if (remaining === 0) break;
        const topNow = this.bestBidTick();
        bidTick = topNow !== -1 && topNow < bidTick ? topNow : this.prevBidTickBefore(bidTick);
      }
    }

    this.lastFillCount = fillCount;
    this.lastClearedMakerCount = clearedCount;
    this.totalFillsGenerated += fillCount;

    if (remaining > 0 && restRemainder) {
      return this.allocSlot(
        isBuy ? SIDE_BUY : SIDE_SELL,
        limitTick,
        remaining,
        quantity,
        ownerHash,
      );
    }
    return -1;
  }

  /**
   * Market-To-Limit (MP/MTL on HOSE):
   * Sweeps all available contra liquidity; if partially filled and `mtlConvertToLimit=true`,
   * converts residual quantity to a resting LO order at `lastFillPrice ± 1 HOSE tick`.
   */
  public submitMarketRaw(
    isBuy: boolean,
    quantity: number,
    ownerHash = 0,
    mtlConvertToLimit = true,
    ceilingTick = MAX_PRICE_TICKS - 1,
    floorTick = 1,
  ): number {
    const extremeTick = isBuy ? ceilingTick : floorTick;
    this.submitLimitRaw(isBuy, extremeTick, quantity, ownerHash, false);
    const remaining = quantity - this.lastFilledQty;
    if (remaining > 0 && this.lastFillCount > 0 && mtlConvertToLimit) {
      const lastTick = this.fillPriceTick[this.lastFillCount - 1];
      const stepTicks = Math.max(1, Math.round(getExchangeTickStep(tickToPrice(lastTick), 'HOSE') / TICK_UNIT_VND));
      const mtlTick = isBuy
        ? Math.min(ceilingTick, lastTick + stepTicks)
        : Math.max(floorTick, lastTick - stepTicks);
      return this.allocSlot(isBuy ? SIDE_BUY : SIDE_SELL, mtlTick, remaining, quantity, ownerHash);
    }
    return -1;
  }

  public cancelSlot(slot: number): boolean {
    if (slot < 0 || slot >= this.capacity || this.slotSide[slot] === SIDE_FREE) return false;
    this.unlinkAndFreeSlot(slot);
    return true;
  }

  /**
   * Amend order in O(1):
   * - Same price & reduced quantity -> keeps exact FIFO queue position!
   * - New price or increased quantity -> cancels old slot and submits fresh order.
   */
  public amendSlot(slot: number, newPriceTick: number, newRemainingQty: number): number {
    if (slot < 0 || slot >= this.capacity || this.slotSide[slot] === SIDE_FREE) return -1;
    const oldTick = this.slotPriceTick[slot];
    const oldQty = this.slotQty[slot];
    const sideCode = this.slotSide[slot];
    const owner = this.slotOwner[slot];

    if (newPriceTick === oldTick && newRemainingQty > 0 && newRemainingQty <= oldQty) {
      const delta = oldQty - newRemainingQty;
      this.slotQty[slot] = newRemainingQty;
      if (sideCode === SIDE_BUY) this.bidVol[oldTick] = Math.max(0, this.bidVol[oldTick] - delta);
      else this.askVol[oldTick] = Math.max(0, this.askVol[oldTick] - delta);
      this.lastFillCount = 0;
      this.lastFilledQty = 0;
      this.lastFillNotionalTicks = 0;
      this.lastClearedMakerCount = 0;
      return slot;
    }

    this.unlinkAndFreeSlot(slot);
    return this.submitLimitRaw(sideCode === SIDE_BUY, newPriceTick, newRemainingQty, owner, true);
  }

  public getDepth(levels = 3): { bids: BookPriceLevel[]; asks: BookPriceLevel[] } {
    const bids: BookPriceLevel[] = [];
    let bt = this.bestBidTick();
    while (bt !== -1 && bids.length < levels) {
      const vol = this.bidVol[bt];
      if (vol > 0) bids.push({ price: tickToPrice(bt), volume: vol });
      bt = this.prevBidTickBefore(bt);
    }

    const asks: BookPriceLevel[] = [];
    let at = this.bestAskTick();
    while (at !== -1 && asks.length < levels) {
      const vol = this.askVol[at];
      if (vol > 0) asks.push({ price: tickToPrice(at), volume: vol });
      at = this.nextAskTickAfter(at);
    }

    return { bids, asks };
  }

  /**
   * HOSE Call Auction Equilibrium Price Finder (ATO / ATC):
   * 1. Maximize matched volume min(cumBid(P), cumAsk(P))
   * 2. Minimize absolute imbalance |cumBid(P) - cumAsk(P)|
   * 3. Closest to refPriceTick
   */
  public computeAuctionEquilibrium(
    refPriceTick: number,
    atoBuyVolume = 0,
    atoSellVolume = 0,
  ): { clearingTick: number; matchedVolume: number } {
    const highestBid = this.bestBidTick();
    const lowestAsk = this.bestAskTick();
    if (highestBid === -1 && atoBuyVolume <= 0) return { clearingTick: -1, matchedVolume: 0 };
    if (lowestAsk === -1 && atoSellVolume <= 0) return { clearingTick: -1, matchedVolume: 0 };

    const candidateTicks: number[] = [];
    const minTick = lowestAsk !== -1 ? lowestAsk : Math.max(1, refPriceTick - 500);
    const maxTick = highestBid !== -1 ? highestBid : Math.min(MAX_PRICE_TICKS - 1, refPriceTick + 500);

    let t = lowestAsk !== -1 ? lowestAsk : minTick;
    while (t !== -1 && t <= maxTick) {
      candidateTicks.push(t);
      t = this.nextAskTickAfter(t);
    }
    let b = highestBid;
    while (b !== -1 && b >= minTick) {
      if (!candidateTicks.includes(b)) candidateTicks.push(b);
      b = this.prevBidTickBefore(b);
    }
    if (refPriceTick > 0 && !candidateTicks.includes(refPriceTick)) {
      candidateTicks.push(refPriceTick);
    }
    candidateTicks.sort((x, y) => x - y);
    if (candidateTicks.length === 0) return { clearingTick: -1, matchedVolume: 0 };

    // Compute cumulative ask ascending and cumulative bid descending across candidates
    const n = candidateTicks.length;
    const cumAsk = new Float64Array(n);
    const cumBid = new Float64Array(n);

    let askAcc = atoSellVolume;
    let scanAsk = lowestAsk;
    for (let i = 0; i < n; i++) {
      const ct = candidateTicks[i];
      while (scanAsk !== -1 && scanAsk <= ct) {
        askAcc += this.askVol[scanAsk];
        scanAsk = this.nextAskTickAfter(scanAsk);
      }
      cumAsk[i] = askAcc;
    }

    let bidAcc = atoBuyVolume;
    let scanBid = highestBid;
    for (let i = n - 1; i >= 0; i--) {
      const ct = candidateTicks[i];
      while (scanBid !== -1 && scanBid >= ct) {
        bidAcc += this.bidVol[scanBid];
        scanBid = this.prevBidTickBefore(scanBid);
      }
      cumBid[i] = bidAcc;
    }

    let bestTick = -1;
    let bestMatched = 0;
    let bestImbalance = Number.POSITIVE_INFINITY;
    let bestRefDist = Number.POSITIVE_INFINITY;

    for (let i = 0; i < n; i++) {
      const matched = Math.min(cumBid[i], cumAsk[i]);
      if (matched <= 0) continue;
      const imbalance = Math.abs(cumBid[i] - cumAsk[i]);
      const refDist = refPriceTick > 0 ? Math.abs(candidateTicks[i] - refPriceTick) : 0;

      if (
        matched > bestMatched ||
        (matched === bestMatched && imbalance < bestImbalance) ||
        (matched === bestMatched && imbalance === bestImbalance && refDist < bestRefDist)
      ) {
        bestMatched = matched;
        bestImbalance = imbalance;
        bestRefDist = refDist;
        bestTick = candidateTicks[i];
      }
    }

    return { clearingTick: bestTick, matchedVolume: bestMatched };
  }
}

/**
 * Deterministic micro-benchmark measuring single-core ArenaOrderBook throughput.
 * Alternates passive limit orders across 40 HOSE price levels and aggressive sweeps/cancels.
 */
export function benchmarkEngineThroughput(orderCount = 200_000): EngineThroughputReport {
  const book = new ArenaOrderBook(131_072, 256);
  const baseTick = 2000; // 20,000 VND
  const sampleLatencies = new Float64Array(256);
  const sampleStride = Math.max(1, Math.floor(orderCount / 256));
  let sampleIdx = 0;

  // Warmup JIT
  for (let w = 0; w < 5_000; w++) {
    const isBuy = (w & 1) === 0;
    const tick = isBuy ? baseTick - ((w & 7) * 5) : baseTick + 5 + ((w & 7) * 5);
    const s = book.submitLimitRaw(isBuy, tick, 100, (w & 15) + 1, true);
    if ((w & 3) === 3 && s !== -1) book.cancelSlot(s);
  }
  book.reset();
  book.totalOrdersProcessed = 0;
  book.totalFillsGenerated = 0;

  const tStart = performance.now();
  let lastRestingSlot = -1;

  for (let i = 0; i < orderCount; i++) {
    const measure = i % sampleStride === 0 && sampleIdx < 256;
    const s0 = measure ? performance.now() : 0;

    const pattern = i & 7;
    const owner = (i & 31) + 1;
    if (pattern < 3) {
      // Passive Bid
      const tick = baseTick - (pattern * 5);
      lastRestingSlot = book.submitLimitRaw(true, tick, 200, owner, true);
    } else if (pattern < 6) {
      // Passive Ask
      const tick = baseTick + 5 + ((pattern - 3) * 5);
      lastRestingSlot = book.submitLimitRaw(false, tick, 200, owner, true);
    } else if (pattern === 6) {
      // Aggressive crossing BUY or SELL sweeping resting levels
      const aggBuy = (i & 8) === 0;
      book.submitLimitRaw(aggBuy, aggBuy ? baseTick + 15 : baseTick - 10, 300, owner + 64, true);
    } else {
      // O(1) Cancel or aggressive cross
      if (lastRestingSlot !== -1 && book.cancelSlot(lastRestingSlot)) {
        book.totalOrdersProcessed++;
        lastRestingSlot = -1;
      } else {
        book.submitLimitRaw(true, baseTick + 5, 100, owner + 128, true);
      }
    }

    if (measure) {
      sampleLatencies[sampleIdx++] = (performance.now() - s0) * 1_000_000;
    }
  }

  const elapsedMs = Math.max(0.001, performance.now() - tStart);
  const processed = book.totalOrdersProcessed;
  const ordersPerSec = Math.round((processed / elapsedMs) * 1000);
  const avgLatencyNs = Math.round((elapsedMs * 1_000_000) / Math.max(1, processed));

  const sortedSamples = Array.from(sampleLatencies.subarray(0, sampleIdx)).sort((a, b) => a - b);
  const p50LatencyNs =
    sortedSamples.length > 0
      ? Math.max(avgLatencyNs, Math.round(sortedSamples[Math.floor(sortedSamples.length * 0.5)] || avgLatencyNs))
      : avgLatencyNs;
  const p99LatencyNs =
    sortedSamples.length > 0
      ? Math.max(p50LatencyNs, Math.round(sortedSamples[Math.floor(sortedSamples.length * 0.99)] || avgLatencyNs))
      : avgLatencyNs;

  return {
    ordersProcessed: processed,
    fillsGenerated: book.totalFillsGenerated,
    elapsedMs: Number(elapsedMs.toFixed(2)),
    ordersPerSec,
    avgLatencyNs,
    p50LatencyNs,
    p99LatencyNs,
    tickResolutionVnd: TICK_UNIT_VND,
    arenaCapacity: book.capacity,
  };
}

export function isFreshContinuousOrderBook(
  book: ExternalOrderBookSnapshot | null | undefined,
  now = Date.now(),
): boolean {
  if (!book) return false;
  if (book.marketState && book.marketState !== 'continuous_morning' && book.marketState !== 'continuous_afternoon') {
    return false;
  }
  let stamp = Number(book.receivedAt ?? 0);
  if (stamp > 0 && stamp < 1e12) stamp *= 1000;
  if (!stamp && book.lastUpdate) {
    stamp = Date.parse(book.lastUpdate);
  }
  if (!Number.isFinite(stamp) || stamp <= 0) return false;
  const age = now - stamp;
  return age >= -2_000 && age <= 15_000 && book.stale !== true;
}

export function normalizeExternalSideLevels(
  rawLevels: unknown,
  side: 'BID' | 'ASK',
): BookPriceLevel[] {
  if (!Array.isArray(rawLevels)) return [];
  const merged = new Map<number, number>();
  for (const item of rawLevels) {
    if (!item || typeof item !== 'object') continue;
    const rec = item as Record<string, unknown>;
    const price = normalizeVndPrice(rec.price ?? rec.p);
    const volume = Math.floor(Number(rec.volume ?? rec.q ?? rec.quantity ?? 0));
    if (price > 0 && Number.isFinite(volume) && volume > 0) {
      merged.set(price, (merged.get(price) ?? 0) + volume);
    }
  }
  const out = Array.from(merged.entries()).map(([price, volume]) => ({ price, volume }));
  out.sort((a, b) => (side === 'BID' ? b.price - a.price : a.price - b.price));
  return out;
}

export type RestingFillSettlementCallback = (event: {
  orderId: string;
  userId: string;
  symbol: string;
  side: OrderSide;
  fillPrice: number;
  fillQuantity: number;
  remainingQuantity: number;
  liquidity: FillSlice['liquidity'];
}) => Promise<void>;

/**
 * LiveOrderGateway — Production Order Management & Matching Coordinator
 *
 * Bridges per-symbol `ArenaOrderBook` instances (resting user orders + internal CLOB)
 * with real-time DNSE L1-L3 orderbook & trade feeds.
 */
class LiveOrderGateway {
  private readonly books = new Map<string, ArenaOrderBook>();
  private readonly ticketsById = new Map<string, RestingOrderTicket>();
  private readonly slotToOrderId = new Map<string, Map<number, string>>();
  private readonly symbolTicketIds = new Map<string, Set<string>>();
  private readonly orderLocks = new Set<string>();
  private settlementHandler: RestingFillSettlementCallback | null = null;
  private cachedBenchmark: EngineThroughputReport | null = null;

  public registerSettlementHandler(handler: RestingFillSettlementCallback): void {
    this.settlementHandler = handler;
  }

  public getOrCreateBook(symbol: string): ArenaOrderBook {
    const sym = symbol.toUpperCase();
    let book = this.books.get(sym);
    if (!book) {
      book = new ArenaOrderBook(16_384, 512);
      this.books.set(sym, book);
      this.slotToOrderId.set(sym, new Map());
    }
    return book;
  }

  public hasRestingOrders(symbol: string): boolean {
    const set = this.symbolTicketIds.get(symbol.toUpperCase());
    return Boolean(set && set.size > 0);
  }

  public getTicket(orderId: string): RestingOrderTicket | undefined {
    return this.ticketsById.get(orderId);
  }

  /**
   * Evaluates an incoming order against:
   * 1. Internal CLOB contra-orders (STP protected)
   * 2. Fresh live DNSE orderbook depth (Price-Time best execution across both sources!)
   */
  public matchIncomingOrder(params: {
    userId: string;
    symbol: string;
    exchange: ExchangeCode;
    side: OrderSide;
    orderType: OrderType;
    limitPrice?: number;
    quantity: number;
    externalBook: ExternalOrderBookSnapshot | null;
    ceiling?: number;
    floor?: number;
    now?: number;
  }): {
    fills: FillSlice[];
    filledQuantity: number;
    remainingQuantity: number;
    avgFillPrice: number;
    totalNotional: number;
    restingLimitPrice: number;
    effectiveOrderType: OrderType;
    status: OrderLifecycleStatus;
  } {
    const sym = params.symbol.toUpperCase();
    const now = params.now ?? Date.now();
    const matchedIso = new Date(now).toISOString();
    const isBuy = params.side === 'BUY';
    const ownerHash = hashOwnerId(params.userId);
    const book = this.getOrCreateBook(sym);
    const slotMap = this.slotToOrderId.get(sym)!;

    // ATO / ATC orders rest in the auction queue until auction clearing unless continuous session rejects MP
    if (params.orderType === 'ATO' || params.orderType === 'ATC') {
      const anchorPrice = normalizeVndPrice(
        params.limitPrice ??
          (isBuy ? params.ceiling : params.floor) ??
          params.externalBook?.ref,
      );
      return {
        fills: [],
        filledQuantity: 0,
        remainingQuantity: params.quantity,
        avgFillPrice: 0,
        totalNotional: 0,
        restingLimitPrice: anchorPrice,
        effectiveOrderType: params.orderType,
        status: 'PENDING',
      };
    }

    const freshExternal = isFreshContinuousOrderBook(params.externalBook, now);
    const extLevels = freshExternal
      ? normalizeExternalSideLevels(
          isBuy ? params.externalBook?.asks : params.externalBook?.bids,
          isBuy ? 'ASK' : 'BID',
        )
      : [];

    const ceilingVnd = normalizeVndPrice(params.ceiling ?? params.externalBook?.ceiling);
    const floorVnd = normalizeVndPrice(params.floor ?? params.externalBook?.floor);
    const effectiveLimit =
      params.orderType === 'LO'
        ? Math.round(Number(params.limitPrice ?? 0))
        : isBuy
          ? ceilingVnd > 0
            ? ceilingVnd
            : tickToPrice(MAX_PRICE_TICKS - 1)
          : floorVnd > 0
            ? floorVnd
            : TICK_UNIT_VND;

    const fills: FillSlice[] = [];
    let remaining = params.quantity;
    let extIdx = 0;
    let extLevelRem = extLevels[0]?.volume ?? 0;

    // Best-execution merge loop across Internal ArenaOrderBook & Live DNSE OrderBook
    while (remaining > 0) {
      const intTick = isBuy ? book.bestAskTick() : book.bestBidTick();
      const intPrice = intTick !== -1 ? tickToPrice(intTick) : 0;
      const hasInt =
        intPrice > 0 && (isBuy ? intPrice <= effectiveLimit : intPrice >= effectiveLimit);

      const extLevel = extIdx < extLevels.length ? extLevels[extIdx] : undefined;
      const hasExt =
        extLevel !== undefined &&
        extLevelRem > 0 &&
        (isBuy ? extLevel.price <= effectiveLimit : extLevel.price >= effectiveLimit);

      if (!hasInt && !hasExt) break;

      // Prefer strictly better price; on tie, prefer internal resting maker (FIFO CLOB)
      const takeInternal =
        hasInt && (!hasExt || (isBuy ? intPrice <= extLevel!.price : intPrice >= extLevel!.price));

      if (takeInternal) {
        // Match against exactly this internal price tick
        book.submitLimitRaw(isBuy, intTick, remaining, ownerHash, false);
        if (book.lastFilledQty === 0) {
          // Only same-owner orders existed at intTick (STP skipped); consume external if available, else stop
          if (hasExt) {
            const exec = Math.min(remaining, extLevelRem);
            fills.push({
              price: extLevel!.price,
              quantity: exec,
              notional: exec * extLevel!.price,
              liquidity: 'DNSE_LIVE_BOOK',
              matchedAt: matchedIso,
            });
            remaining -= exec;
            extLevelRem -= exec;
            if (extLevelRem <= 0) {
              extIdx++;
              extLevelRem = extLevels[extIdx]?.volume ?? 0;
            }
            continue;
          }
          break;
        }

        for (let f = 0; f < book.lastFillCount; f++) {
          const makerSlot = book.fillMakerSlot[f];
          const fPrice = tickToPrice(book.fillPriceTick[f]);
          const fQty = book.fillQty[f];
          const makerOrderId = slotMap.get(makerSlot);
          const makerTicket = makerOrderId ? this.ticketsById.get(makerOrderId) : undefined;
          fills.push({
            price: fPrice,
            quantity: fQty,
            notional: fQty * fPrice,
            liquidity: 'INTERNAL_CLOB',
            makerOrderId,
            makerUserId: makerTicket?.userId,
            matchedAt: matchedIso,
          });
          if (makerTicket) {
            makerTicket.filledQuantity += fQty;
            makerTicket.remainingQuantity = Math.max(0, makerTicket.remainingQuantity - fQty);
            if (makerTicket.remainingQuantity === 0) {
              this.removeTicketRecord(makerTicket.orderId);
            }
            if (this.settlementHandler) {
              const mId = makerTicket.orderId;
              const mUid = makerTicket.userId;
              const mSide = makerTicket.side;
              const mRem = makerTicket.remainingQuantity;
              void this.dispatchMakerSettlement({
                orderId: mId,
                userId: mUid,
                symbol: sym,
                side: mSide,
                fillPrice: fPrice,
                fillQuantity: fQty,
                remainingQuantity: mRem,
                liquidity: 'INTERNAL_CLOB',
              });
            }
          }
        }
        remaining -= book.lastFilledQty;
      } else if (hasExt) {
        const exec = Math.min(remaining, extLevelRem);
        fills.push({
          price: extLevel!.price,
          quantity: exec,
          notional: exec * extLevel!.price,
          liquidity: 'DNSE_LIVE_BOOK',
          matchedAt: matchedIso,
        });
        remaining -= exec;
        extLevelRem -= exec;
        if (extLevelRem <= 0) {
          extIdx++;
          extLevelRem = extLevels[extIdx]?.volume ?? 0;
        }
      }
    }

    const filledQuantity = params.quantity - remaining;
    const totalNotional = fills.reduce((acc, s) => acc + s.notional, 0);
    const avgFillPrice = filledQuantity > 0 ? totalNotional / filledQuantity : 0;

    if (remaining === 0) {
      return {
        fills,
        filledQuantity,
        remainingQuantity: 0,
        avgFillPrice,
        totalNotional,
        restingLimitPrice: params.orderType === 'LO' ? effectiveLimit : Math.round(avgFillPrice),
        effectiveOrderType: params.orderType,
        status: 'FILLED',
      };
    }

    // MP order with 0 fills -> reject cleanly (no fake fills!)
    if (params.orderType === 'MP' && filledQuantity === 0) {
      return {
        fills: [],
        filledQuantity: 0,
        remainingQuantity: params.quantity,
        avgFillPrice: 0,
        totalNotional: 0,
        restingLimitPrice: 0,
        effectiveOrderType: 'MP',
        status: 'REJECTED',
      };
    }

    // MP with partial fill -> HOSE MTL rule converts remainder to LO at lastFillPrice ± 1 tick
    let restingLimitPrice = effectiveLimit;
    let effectiveOrderType: OrderType = params.orderType;
    if (params.orderType === 'MP' && fills.length > 0) {
      const lastFillPrice = fills[fills.length - 1].price;
      const step = getExchangeTickStep(lastFillPrice, params.exchange);
      restingLimitPrice = isBuy
        ? ceilingVnd > 0
          ? Math.min(ceilingVnd, lastFillPrice + step)
          : lastFillPrice + step
        : floorVnd > 0
          ? Math.max(floorVnd, lastFillPrice - step)
          : Math.max(step, lastFillPrice - step);
      effectiveOrderType = 'LO';
    }

    return {
      fills,
      filledQuantity,
      remainingQuantity: remaining,
      avgFillPrice,
      totalNotional,
      restingLimitPrice,
      effectiveOrderType,
      status: filledQuantity > 0 ? 'PARTIALLY_FILLED' : 'PENDING',
    };
  }

  /**
   * Registers a persisted PENDING / PARTIALLY_FILLED order into the in-memory ArenaOrderBook
   * so real-time DNSE ticks & contra orders match against it in O(1).
   */
  public registerRestingOrder(ticket: Omit<RestingOrderTicket, 'slot'>): RestingOrderTicket {
    const sym = ticket.symbol.toUpperCase();
    // Avoid duplicate registration
    if (this.ticketsById.has(ticket.orderId)) {
      this.cancelRestingOrder(ticket.orderId);
    }

    let slot = -1;
    if (
      (ticket.orderType === 'LO' || ticket.limitPrice > 0) &&
      ticket.orderType !== 'ATO' &&
      ticket.orderType !== 'ATC' &&
      ticket.remainingQuantity > 0
    ) {
      const book = this.getOrCreateBook(sym);
      const tick = priceToTick(ticket.limitPrice);
      const ownerHash = hashOwnerId(ticket.userId);
      // Insert directly as resting without re-matching (already matched on entry)
      slot = book.queueAuctionLimitRaw(
        ticket.side === 'BUY',
        tick,
        ticket.remainingQuantity,
        ownerHash,
      );
      if (slot !== -1) {
        this.slotToOrderId.get(sym)!.set(slot, ticket.orderId);
      }
    }

    const stored: RestingOrderTicket = { ...ticket, symbol: sym, slot };
    this.ticketsById.set(ticket.orderId, stored);
    let symSet = this.symbolTicketIds.get(sym);
    if (!symSet) {
      symSet = new Set();
      this.symbolTicketIds.set(sym, symSet);
    }
    symSet.add(ticket.orderId);
    return stored;
  }

  public cancelRestingOrder(orderId: string): boolean {
    const ticket = this.ticketsById.get(orderId);
    if (!ticket) return false;
    if (ticket.slot !== -1) {
      const book = this.books.get(ticket.symbol);
      book?.cancelSlot(ticket.slot);
      this.slotToOrderId.get(ticket.symbol)?.delete(ticket.slot);
    }
    this.removeTicketRecord(orderId);
    return true;
  }

  public amendRestingOrder(
    orderId: string,
    newLimitPrice: number,
    newRemainingQty: number,
  ): RestingOrderTicket | null {
    const ticket = this.ticketsById.get(orderId);
    if (!ticket) return null;
    const book = this.getOrCreateBook(ticket.symbol);
    const slotMap = this.slotToOrderId.get(ticket.symbol)!;

    if (ticket.slot !== -1) {
      const oldSlot = ticket.slot;
      const nextSlot = book.amendSlot(oldSlot, priceToTick(newLimitPrice), newRemainingQty);
      if (nextSlot !== oldSlot) {
        slotMap.delete(oldSlot);
        if (nextSlot !== -1) slotMap.set(nextSlot, orderId);
      }
      ticket.slot = nextSlot;
    }
    ticket.limitPrice = newLimitPrice;
    ticket.remainingQuantity = newRemainingQty;
    ticket.initialQuantity = ticket.filledQuantity + newRemainingQty;
    return ticket;
  }

  private removeTicketRecord(orderId: string): void {
    const ticket = this.ticketsById.get(orderId);
    if (!ticket) return;
    if (ticket.slot !== -1) {
      this.slotToOrderId.get(ticket.symbol)?.delete(ticket.slot);
    }
    this.ticketsById.delete(orderId);
    const symSet = this.symbolTicketIds.get(ticket.symbol);
    if (symSet) {
      symSet.delete(orderId);
      if (symSet.size === 0) this.symbolTicketIds.delete(ticket.symbol);
    }
  }

  /**
   * Merges live DNSE L1-L3 orderbook with resting user limit orders in `ArenaOrderBook`
   * so the T-Board OrderBook reflects user's own resting orders in real time!
   */
  public getMergedOrderBook(
    symbol: string,
    externalBook: ExternalOrderBookSnapshot | null | undefined,
  ): ExternalOrderBookSnapshot {
    const sym = symbol.toUpperCase();
    const extBids = normalizeExternalSideLevels(externalBook?.bids, 'BID');
    const extAsks = normalizeExternalSideLevels(externalBook?.asks, 'ASK');
    const book = this.books.get(sym);

    if (!book || book.activeRestingCount === 0) {
      return {
        ...(externalBook ?? {}),
        symbol: sym,
        bids: extBids.slice(0, 3),
        asks: extAsks.slice(0, 3),
      };
    }

    const intDepth = book.getDepth(10);
    const bidMap = new Map<number, number>();
    for (const b of extBids) bidMap.set(b.price, (bidMap.get(b.price) ?? 0) + b.volume);
    for (const b of intDepth.bids) bidMap.set(b.price, (bidMap.get(b.price) ?? 0) + b.volume);

    const askMap = new Map<number, number>();
    for (const a of extAsks) askMap.set(a.price, (askMap.get(a.price) ?? 0) + a.volume);
    for (const a of intDepth.asks) askMap.set(a.price, (askMap.get(a.price) ?? 0) + a.volume);

    const mergedBids = Array.from(bidMap.entries())
      .map(([price, volume]) => ({ price, volume }))
      .sort((a, b) => b.price - a.price)
      .slice(0, 3);
    const mergedAsks = Array.from(askMap.entries())
      .map(([price, volume]) => ({ price, volume }))
      .sort((a, b) => a.price - b.price)
      .slice(0, 3);

    return {
      ...(externalBook ?? {}),
      symbol: sym,
      bids: mergedBids,
      asks: mergedAsks,
      receivedAt: externalBook?.receivedAt ?? Date.now(),
      stale: externalBook ? externalBook.stale : false,
    };
  }

  /**
   * Real-time DNSE OrderBook tick handler (O(1) early exit when no resting user orders cross!).
   */
  public onExternalOrderBook(symbol: string, rawData: unknown): void {
    const sym = symbol.toUpperCase();
    if (!this.hasRestingOrders(sym) || !this.settlementHandler) return;
    const extBook = (rawData ?? {}) as ExternalOrderBookSnapshot;
    if (!isFreshContinuousOrderBook(extBook)) return;

    const book = this.books.get(sym);
    if (!book || book.activeRestingCount === 0) return;

    const extAsks = normalizeExternalSideLevels(extBook.asks, 'ASK');
    const extBids = normalizeExternalSideLevels(extBook.bids, 'BID');

    // Check resting BUY orders against incoming Best Asks
    if (extAsks.length > 0) {
      const topBidTick = book.bestBidTick();
      if (topBidTick !== -1 && tickToPrice(topBidTick) >= extAsks[0].price) {
        this.sweepRestingAgainstExternal(sym, 'BUY', extAsks);
      }
    }

    // Check resting SELL orders against incoming Best Bids
    if (extBids.length > 0) {
      const topAskTick = book.bestAskTick();
      if (topAskTick !== -1 && tickToPrice(topAskTick) <= extBids[0].price) {
        this.sweepRestingAgainstExternal(sym, 'SELL', extBids);
      }
    }
  }

  /**
   * Real-time DNSE Trade print handler:
   * If market prints a real trade at `tradePrice`, any resting user order priced at or better
   * than `tradePrice` (including ATO/ATC prints on session open/close) fills up to `tradeVolume`!
   */
  public onExternalTrade(symbol: string, rawData: unknown): void {
    const sym = symbol.toUpperCase();
    if (!this.hasRestingOrders(sym) || !this.settlementHandler) return;
    if (!rawData || typeof rawData !== 'object') return;
    const rec = rawData as Record<string, unknown>;
    if (rec.isSnapshot) return;

    const tradePrice = normalizeVndPrice(rec.price ?? rec.matchPrice ?? rec.lastPrice);
    const rawVol = Math.floor(Number(rec.matchVol ?? rec.lastVol ?? rec.volume ?? 0));
    const availVol = rawVol >= 100 ? Math.floor(rawVol / 100) * 100 : 0;
    if (tradePrice <= 0 || availVol <= 0) return;

    // 1. Check resting ATO/ATC tickets if session trade occurs
    const ticketIds = Array.from(this.symbolTicketIds.get(sym) ?? []);
    let remAuctionVol = availVol;
    for (const id of ticketIds) {
      if (remAuctionVol < 100) break;
      const t = this.ticketsById.get(id);
      if (!t || (t.orderType !== 'ATO' && t.orderType !== 'ATC') || this.orderLocks.has(id)) continue;
      const exec = Math.min(t.remainingQuantity, remAuctionVol);
      if (exec >= 100) {
        remAuctionVol -= exec;
        t.filledQuantity += exec;
        t.remainingQuantity -= exec;
        if (t.remainingQuantity === 0) this.removeTicketRecord(id);
        void this.dispatchMakerSettlement({
          orderId: id,
          userId: t.userId,
          symbol: sym,
          side: t.side,
          fillPrice: tradePrice,
          fillQuantity: exec,
          remainingQuantity: t.remainingQuantity,
          liquidity: 'AUCTION_CROSS',
        });
      }
    }

    // 2. Check resting LO orders crossed by the trade print
    const book = this.books.get(sym);
    if (!book || book.activeRestingCount === 0) return;
    const bestBid = book.bestBidTick();
    if (bestBid !== -1 && tickToPrice(bestBid) >= tradePrice) {
      this.sweepRestingAgainstExternal(sym, 'BUY', [{ price: tradePrice, volume: availVol }], 'DNSE_LIVE_TRADE');
    }
    const bestAsk = book.bestAskTick();
    if (bestAsk !== -1 && tickToPrice(bestAsk) <= tradePrice) {
      this.sweepRestingAgainstExternal(sym, 'SELL', [{ price: tradePrice, volume: availVol }], 'DNSE_LIVE_TRADE');
    }
  }

  private sweepRestingAgainstExternal(
    sym: string,
    restingSide: OrderSide,
    contraLevels: BookPriceLevel[],
    liquidity: FillSlice['liquidity'] = 'DNSE_LIVE_BOOK',
  ): void {
    const book = this.books.get(sym);
    const slotMap = this.slotToOrderId.get(sym);
    if (!book || !slotMap) return;

    for (const level of contraLevels) {
      if (level.volume < 100) continue;
      const levelTick = priceToTick(level.price);
      // Incoming synthetic contra-order sweeps resting user orders at `level.price`!
      book.submitLimitRaw(restingSide === 'SELL', levelTick, level.volume, 0, false);
      if (book.lastFillCount === 0) break;

      for (let f = 0; f < book.lastFillCount; f++) {
        const makerSlot = book.fillMakerSlot[f];
        const fQty = book.fillQty[f];
        // Execute at the better of resting limit or incoming external price (maker price / external cross)
        const fPrice = level.price;
        const orderId = slotMap.get(makerSlot);
        const ticket = orderId ? this.ticketsById.get(orderId) : undefined;
        if (!ticket || !orderId) continue;

        ticket.filledQuantity += fQty;
        ticket.remainingQuantity = Math.max(0, ticket.remainingQuantity - fQty);
        if (ticket.remainingQuantity === 0) {
          this.removeTicketRecord(orderId);
        }
        void this.dispatchMakerSettlement({
          orderId,
          userId: ticket.userId,
          symbol: sym,
          side: ticket.side,
          fillPrice: fPrice,
          fillQuantity: fQty,
          remainingQuantity: ticket.remainingQuantity,
          liquidity,
        });
      }
    }
  }

  private async dispatchMakerSettlement(event: Parameters<RestingFillSettlementCallback>[0]): Promise<void> {
    if (!this.settlementHandler) return;
    while (this.orderLocks.has(event.orderId)) {
      await new Promise((r) => setTimeout(r, 10));
    }
    this.orderLocks.add(event.orderId);
    try {
      await this.settlementHandler(event);
    } catch (err) {
      console.error(`[MatchingEngine] Failed settling resting order ${event.orderId}:`, err);
    } finally {
      this.orderLocks.delete(event.orderId);
    }
  }

  public getEngineStats(): {
    activeSymbols: number;
    activeRestingOrders: number;
    benchmark: EngineThroughputReport;
  } {
    if (!this.cachedBenchmark) {
      this.cachedBenchmark = benchmarkEngineThroughput(160_000);
    }
    return {
      activeSymbols: this.books.size,
      activeRestingOrders: this.ticketsById.size,
      benchmark: this.cachedBenchmark,
    };
  }
}

export const liveOrderGateway = new LiveOrderGateway();
