const test = require('node:test');
const assert = require('node:assert/strict');
const {
  ArenaOrderBook,
  priceToTick,
  tickToPrice,
  validateExchangeOrder,
  computeExchangeBands,
  benchmarkEngineThroughput,
  liveOrderGateway,
} = require('../../dist/services/matchingEngine');

test('ArenaOrderBook enforces strict Price-Time FIFO priority and O(1) bitscan best bid/ask', () => {
  const book = new ArenaOrderBook(1024, 64);

  // Two resting asks at 21,500 VND (FIFO: makerA then makerB), plus deeper ask at 21,550 VND
  const slotA = book.submitLimitRaw(false, priceToTick(21_500), 200, 101, true);
  const slotB = book.submitLimitRaw(false, priceToTick(21_500), 300, 102, true);
  const slotC = book.submitLimitRaw(false, priceToTick(21_550), 400, 103, true);
  assert.ok(slotA >= 0 && slotB >= 0 && slotC >= 0);
  assert.equal(tickToPrice(book.bestAskTick()), 21_500);

  // Incoming aggressive BUY 600 @ 21,550 sweeps slotA (200 @ 21,500), slotB (300 @ 21,500), and 100 of slotC (21,550)
  const takerSlot = book.submitLimitRaw(true, priceToTick(21_550), 600, 999, true);
  assert.equal(takerSlot, -1); // 100% filled
  assert.equal(book.lastFillCount, 3);
  assert.equal(book.lastFilledQty, 600);
  assert.equal(book.fillMakerSlot[0], slotA);
  assert.equal(book.fillQty[0], 200);
  assert.equal(tickToPrice(book.fillPriceTick[0]), 21_500);
  assert.equal(book.fillMakerSlot[1], slotB);
  assert.equal(book.fillQty[1], 300);
  assert.equal(book.fillMakerSlot[2], slotC);
  assert.equal(book.fillQty[2], 100);
  assert.equal(tickToPrice(book.fillPriceTick[2]), 21_550);

  // Remaining ask depth is 300 @ 21,550
  const depth = book.getDepth(3);
  assert.deepEqual(depth.asks, [{ price: 21_550, volume: 300 }]);
  assert.deepEqual(depth.bids, []);
});

test('ArenaOrderBook supports O(1) cancel, FIFO-preserving qty-down amend, STP, and MTL conversion', () => {
  const book = new ArenaOrderBook(1024, 64);

  // STP: owner 777 rests ask @ 20,000; owner 777 submits crossing buy @ 20,100 -> must NOT self-trade
  const selfAsk = book.submitLimitRaw(false, priceToTick(20_000), 100, 777, true);
  const otherAsk = book.submitLimitRaw(false, priceToTick(20_050), 200, 888, true);
  assert.ok(selfAsk >= 0 && otherAsk >= 0);

  const stpBuy = book.submitLimitRaw(true, priceToTick(20_100), 200, 777, true);
  assert.equal(stpBuy, -1);
  assert.equal(book.lastFilledQty, 200);
  assert.equal(book.fillMakerSlot[0], otherAsk); // Matched owner 888, skipped selfAsk!
  assert.equal(book.slotQty[selfAsk], 100); // selfAsk untouched
  assert.equal(book.cancelSlot(selfAsk), true);
  assert.equal(book.bestAskTick(), -1);

  // FIFO-preserving quantity reduction amend
  const bid1 = book.submitLimitRaw(true, priceToTick(30_000), 500, 11, true);
  const bid2 = book.submitLimitRaw(true, priceToTick(30_000), 300, 22, true);
  const amendedSlot = book.amendSlot(bid1, priceToTick(30_000), 200);
  assert.equal(amendedSlot, bid1); // Exact same slot preserved in front of bid2!

  book.submitLimitRaw(false, priceToTick(30_000), 200, 33, false);
  assert.equal(book.fillMakerSlot[0], bid1); // bid1 filled first because FIFO priority was preserved!
  assert.equal(book.slotQty[bid2], 300);

  // MTL Market order: sweeps remaining 300 @ 30,000 and converts residual 200 to LO @ 30,000 - 50 = 29,950
  const mtlSlot = book.submitMarketRaw(false, 500, 44, true, priceToTick(32_100), priceToTick(27_900));
  assert.ok(mtlSlot >= 0);
  assert.equal(book.lastFilledQty, 300);
  assert.equal(tickToPrice(book.bestAskTick()), 29_950);
  assert.equal(book.slotQty[mtlSlot], 200);
});

test('HOSE Call Auction equilibrium maximizes matched volume and minimizes imbalance', () => {
  const book = new ArenaOrderBook(1024, 64);
  book.queueAuctionLimitRaw(true, priceToTick(21_600), 500, 1);
  book.queueAuctionLimitRaw(true, priceToTick(21_500), 400, 2);
  book.queueAuctionLimitRaw(false, priceToTick(21_450), 300, 3);
  book.queueAuctionLimitRaw(false, priceToTick(21_500), 500, 4);

  const eq = book.computeAuctionEquilibrium(priceToTick(21_500));
  assert.equal(tickToPrice(eq.clearingTick), 21_500);
  assert.equal(eq.matchedVolume, 800);
});

test('Vietnam Exchange rules enforce HOSE/HNX/UPCOM tick sizes, lot 100, and ceiling/floor bands', () => {
  const bands = computeExchangeBands(21_500, 'HOSE');
  assert.equal(bands.ceiling, 23_000);
  assert.equal(bands.floor, 20_000);

  assert.equal(validateExchangeOrder({
    symbol: 'HPG', exchange: 'HOSE', side: 'BUY', orderType: 'LO', price: 21_520, quantity: 100,
    ceiling: bands.ceiling, floor: bands.floor,
  }).valid, false); // 21,520 violates 50 VND tick step

  assert.equal(validateExchangeOrder({
    symbol: 'HPG', exchange: 'HOSE', side: 'BUY', orderType: 'LO', price: 23_050, quantity: 100,
    ceiling: bands.ceiling, floor: bands.floor,
  }).valid, false); // Above ceiling

  assert.equal(validateExchangeOrder({
    symbol: 'HPG', exchange: 'HOSE', side: 'BUY', orderType: 'LO', price: 21_550, quantity: 150,
    ceiling: bands.ceiling, floor: bands.floor,
  }).valid, false); // Odd lot

  assert.equal(validateExchangeOrder({
    symbol: 'HPG', exchange: 'HOSE', side: 'BUY', orderType: 'LO', price: 21_550, quantity: 200,
    ceiling: bands.ceiling, floor: bands.floor,
  }).valid, true);
});

test('LiveOrderGateway handles partial sweep + resting book merge cleanly', () => {
  const now = Date.now();
  const res = liveOrderGateway.matchIncomingOrder({
    userId: 'trader-1',
    symbol: 'ZZZTEST',
    exchange: 'HOSE',
    side: 'BUY',
    orderType: 'LO',
    limitPrice: 25_050,
    quantity: 500,
    externalBook: {
      marketState: 'continuous_morning',
      receivedAt: now,
      asks: [
        { price: 25_000, volume: 200 },
        { price: 25_050, volume: 100 },
        { price: 25_100, volume: 400 },
      ],
    },
    ceiling: 26_700,
    floor: 23_300,
    now,
  });

  assert.equal(res.status, 'PARTIALLY_FILLED');
  assert.equal(res.filledQuantity, 300);
  assert.equal(res.remainingQuantity, 200);
  assert.equal(res.totalNotional, 200 * 25_000 + 100 * 25_050);

  liveOrderGateway.registerRestingOrder({
    orderId: 'ord-zzz-1',
    userId: 'trader-1',
    symbol: 'ZZZTEST',
    exchange: 'HOSE',
    side: 'BUY',
    orderType: 'LO',
    limitPrice: 25_050,
    initialQuantity: 500,
    filledQuantity: 300,
    remainingQuantity: 200,
    createdAtMs: now,
  });
  const merged = liveOrderGateway.getMergedOrderBook('ZZZTEST', { bids: [{ price: 24_950, volume: 300 }], asks: [] });
  assert.deepEqual(merged.bids[0], { price: 25_050, volume: 200 });
  assert.equal(liveOrderGateway.cancelRestingOrder('ord-zzz-1'), true);
});

test('ArenaOrderBook hot-path benchmark sustains > 1,000,000 orders/sec single-core throughput', () => {
  const stats = benchmarkEngineThroughput(300_000);
  console.log('[MatchingEngine Benchmark]', JSON.stringify(stats));
  assert.ok(
    stats.ordersPerSec >= 1_000_000,
    `Expected >= 1,000,000 orders/sec, got ${stats.ordersPerSec.toLocaleString()} orders/sec`,
  );
});
