const test = require('node:test');
const assert = require('node:assert/strict');
const { z } = require('zod');

const updates = [];
require.cache[require.resolve('../../dist/config/database')] = { loaded: true, exports: { __esModule: true, default: {
  watchlist: {
    findFirst: async () => ({ id: 'owned' }),
    update: async ({ data }) => { updates.push(data); return data; },
    create: async ({ data }) => data,
  },
} } };
require.cache[require.resolve('../../dist/middleware/auth')] = { loaded: true, exports: {
  authMiddleware: (req, _res, next) => { req.userId = 'owner'; next(); },
  optionalAuth: (_req, _res, next) => next(),
} };
require.cache[require.resolve('../../dist/services/portfolio.service')] = { loaded: true, exports: {} };
require.cache[require.resolve('../../dist/utils/cache')] = { loaded: true, exports: {} };
const router = require('../../dist/modules/workspace/workspace.routes').default;
const { errorHandler } = require('../../dist/middleware/errorHandler');

test('validation errors keep issue paths in the HTTP 400 response', () => {
  const result = z.object({ quantity: z.number().positive() }).safeParse({ quantity: -1 });
  const res = { status(code) { this.code = code; return this; }, json(body) { this.body = body; } };
  errorHandler(result.error, {}, res, () => assert.fail('must handle validation error'));
  assert.equal(res.code, 400);
  assert.equal(res.body.error, 'Validation error');
  assert.deepEqual(res.body.details[0].path, ['quantity']);
});

test('renaming a watchlist preserves symbols; explicit empty symbols clears it', async () => {
  const route = router.stack.find(layer => layer.route?.path === '/watchlists/:id' && layer.route.methods.patch).route;
  const handler = route.stack.at(-1).handle;
  for (const body of [{ name: 'Renamed' }, { symbols: [] }]) {
    await handler({ body, params: { id: 'owned' }, userId: 'owner' }, { json() {} }, error => { throw error; });
  }
  assert.equal(updates[0].name, 'Renamed');
  assert.equal(updates[0].symbols, undefined);
  assert.deepEqual(updates[1].symbols, []);
});

test('creating a watchlist without symbols still defaults to an empty list', async () => {
  const route = router.stack.find(layer => layer.route?.path === '/watchlists' && layer.route.methods.post).route;
  let response;
  const res = { status(code) { assert.equal(code, 201); return this; }, json(body) { response = body; } };
  await route.stack.at(-1).handle({ body: { name: 'New' }, userId: 'owner' }, res, error => { throw error; });
  assert.deepEqual(response.symbols, []);
});
