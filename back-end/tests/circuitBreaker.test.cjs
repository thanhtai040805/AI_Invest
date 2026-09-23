const { test } = require('node:test');
const assert = require('node:assert/strict');
const { CircuitBreaker } = require('../dist/services/circuitBreaker');

test('an open circuit remains observable while calls fail closed', async () => {
  const breaker = new CircuitBreaker({ failureThreshold: 1, recoveryTimeoutMs: 60_000 });
  await assert.rejects(breaker.execute(async () => { throw new Error('upstream failed'); }));
  assert.deepEqual(breaker.stats, { state: 'OPEN', failures: 1, successes: 0 });
  await assert.rejects(breaker.execute(async () => 'unreachable'), /Circuit breaker is OPEN/);
});
