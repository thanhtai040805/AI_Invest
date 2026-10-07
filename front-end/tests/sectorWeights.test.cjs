const test = require('node:test');
const assert = require('node:assert/strict');
const { sectorWeights } = require('../src/lib/sector-weights.ts');

test('partial capital data uses stock counts for every sector, with a total of 100%', () => {
  const sectors = [
    { weight: 58000000000000, count: 27, marketCapCount: 1 },
    { weight: 0, count: 23, marketCapCount: 0 },
    { weight: 0, count: 60, marketCapCount: 0 },
  ];
  const result = sectorWeights(sectors);
  assert.equal(result.weightByCount, true);
  assert.deepEqual(result.weights, sectors.map((sector) => sector.count / 110 * 100));
  assert(Math.abs(result.weights.reduce((sum, weight) => sum + weight, 0) - 100) < 1e-10);
  assert.equal(sectorWeights([{ weight: 100, count: 2, marketCapCount: 1 }, { weight: 50, count: 1, marketCapCount: 1 }]).weightByCount, true);
});

test('complete capital data uses the same percentages for REST and realtime payloads', () => {
  const rest = [{ market_cap: 60, count: 3, market_cap_count: 3 }, { market_cap: 40, count: 2, market_cap_count: 2 }];
  const live = [{ weight: 60, count: 3, marketCapCount: 3 }, { weight: 40, count: 2, marketCapCount: 2 }];
  assert.deepEqual(sectorWeights(rest), { weights: [60, 40], weightByCount: false });
  assert.deepEqual(sectorWeights(live), sectorWeights(rest));
  assert.deepEqual(sectorWeights([{ market_cap: 0, weight: 60, count: 3 }, { market_cap: 0, weight: 40, count: 2 }]), sectorWeights(rest));
  assert.deepEqual(sectorWeights([{ weight: NaN, count: 1 }, { weight: Infinity, count: 3 }]).weights, [25, 75]);
  assert.deepEqual(sectorWeights([]).weights, []);
});
