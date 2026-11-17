const test = require('node:test');
const assert = require('node:assert/strict');
const {
  normalizeRawCandles,
  resolveSeriesPriceScale,
  scaleCandlePrice,
} = require('../src/lib/tv-chart/adapters/candle-scale.ts');

test('already-VND series spanning <1000 and >=1000 keeps 740..990..1030 VND without 1000x spike', () => {
  const alreadyVnd = [
    { date: '2012-04-12', open: 980, high: 1010, low: 960, close: 990, volume: 1000000 },
    { date: '2012-04-16', open: 980, high: 1030, low: 970, close: 1030, volume: 1200000 },
    { date: '2012-05-15', open: 740, high: 760, low: 730, close: 740, volume: 800000 },
    { date: '2026-11-16', open: 29500, high: 29800, low: 29400, close: 29690, volume: 15000000 },
  ];
  assert.equal(resolveSeriesPriceScale(alreadyVnd, false), 1);
  const candles = normalizeRawCandles(alreadyVnd, '1D', false);
  assert.equal(candles.length, 4);
  assert.deepEqual(
    candles.map((c) => [c.open, c.high, c.low, c.close]),
    [
      [980, 1010, 960, 990],
      [980, 1030, 970, 1030],
      [740, 760, 730, 740],
      [29500, 29800, 29400, 29690],
    ]
  );
  // Zero 980,000 / 990,000 spikes
  assert(Math.max(...candles.map((c) => c.high)) < 35000);
});

test('raw kVND series spanning 0.74..29.69 scales uniformly by 1000 to VND', () => {
  const rawKvnd = [
    { date: '2012-04-12', open: 0.98, high: 1.01, low: 0.96, close: 0.99, volume: 1000000 },
    { date: '2012-04-16', open: 0.98, high: 1.03, low: 0.97, close: 1.03, volume: 1200000 },
    { date: '2012-05-15', open: 0.74, high: 0.76, low: 0.73, close: 0.74, volume: 800000 },
    { date: '2026-11-16', open: 29.5, high: 29.8, low: 29.4, close: 29.69, volume: 15000000 },
  ];
  assert.equal(resolveSeriesPriceScale(rawKvnd, false), 1000);
  const candles = normalizeRawCandles(rawKvnd, '1D', false);
  assert.deepEqual(
    candles.map((c) => [c.open, c.high, c.low, c.close]),
    [
      [980, 1010, 960, 990],
      [980, 1030, 970, 1030],
      [740, 760, 730, 740],
      [29500, 29800, 29400, 29690],
    ]
  );
});

test('index series keeps 2-decimal precision without 1000x scaling', () => {
  const vnindex = [
    { date: '2012-04-12', open: 380.25, high: 385.1, low: 378.4, close: 384.9, volume: 50000000 },
    { date: '2026-11-16', open: 1740.1, high: 1752.3, low: 1738.0, close: 1746.9, volume: 700000000 },
  ];
  assert.equal(resolveSeriesPriceScale(vnindex, true), 1);
  const candles = normalizeRawCandles(vnindex, '1D', true);
  assert.deepEqual(
    candles.map((c) => [c.open, c.high, c.low, c.close]),
    [
      [380.25, 385.1, 378.4, 384.9],
      [1740.1, 1752.3, 1738, 1746.9],
    ]
  );
  assert.equal(scaleCandlePrice(92.15, 0, 1, true), 92.15);
});
