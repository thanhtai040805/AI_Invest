const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');

// Mock Prisma & auth before importing compiled modules
const mockState = {
  sessions: [],
  messages: [],
  feedbacks: [],
  positions: [],
  watchlists: [],
  screenerPresets: [],
  cascadeOutbox: [],
  nextId: 1,
};

function resetMocks() {
  mockState.sessions = [];
  mockState.messages = [];
  mockState.feedbacks = [];
  mockState.positions = [];
  mockState.watchlists = [];
  mockState.screenerPresets = [];
  mockState.cascadeOutbox = [];
  mockState.nextId = 1;
}

const mockPrisma = {
  $transaction: async (fn) => fn(mockPrisma),
  chatSession: {
    create: async ({ data }) => {
      const row = {
        id: `00000000-0000-4000-8000-${String(mockState.nextId++).padStart(12, '0')}`,
        userId: data.userId,
        title: data.title ?? null,
        parentSessionId: data.parentSessionId ?? null,
        createdAt: new Date('2026-11-20T12:00:00Z'),
      };
      mockState.sessions.push(row);
      return row;
    },
    findFirst: async ({ where, include }) => {
      const found = mockState.sessions.find(
        (s) => s.id === where.id && (!where.userId || s.userId === where.userId),
      );
      if (!found) return null;
      if (!include?.messages) return found;
      const msgs = mockState.messages
        .filter((m) => m.sessionId === found.id)
        .map((m) => ({
          ...m,
          evalFeedbacks: mockState.feedbacks.filter((f) => f.messageId === m.id),
        }));
      return { ...found, messages: msgs };
    },
    findMany: async ({ where }) => {
      return mockState.sessions
        .filter((s) => s.userId === where.userId)
        .map((s) => {
          const msgs = mockState.messages.filter((m) => m.sessionId === s.id);
          return {
            ...s,
            messages: [...msgs].reverse().slice(0, 5),
            _count: { messages: msgs.length },
          };
        });
    },
    count: async ({ where }) => mockState.sessions.filter((s) => s.userId === where.userId).length,
    delete: async ({ where }) => {
      const idx = mockState.sessions.findIndex((s) => s.id === where.id);
      if (idx === -1) throw new Error('Record not found');
      const [removed] = mockState.sessions.splice(idx, 1);
      return removed;
    },
    deleteMany: async ({ where }) => {
      const before = mockState.sessions.length;
      mockState.sessions = mockState.sessions.filter(
        (s) => !(s.id === where.id && (!where.userId || s.userId === where.userId)),
      );
      return { count: before - mockState.sessions.length };
    },
  },
  chatMessage: {
    create: async ({ data }) => {
      const row = {
        id: `10000000-0000-4000-8000-${String(mockState.nextId++).padStart(12, '0')}`,
        sessionId: data.sessionId,
        role: data.role,
        content: data.content,
        type: data.type ?? 'text',
        metadata: data.metadata ?? null,
        createdAt: new Date(Date.now() + mockState.nextId * 1000),
      };
      mockState.messages.push(row);
      return row;
    },
    findMany: async ({ where, orderBy, take }) => {
      let rows = mockState.messages.filter((m) => {
        if (where?.sessionId && m.sessionId !== where.sessionId) return false;
        if (where?.role && m.role !== where.role) return false;
        if (where?.session?.userId) {
          const sess = mockState.sessions.find((s) => s.id === m.sessionId);
          if (!sess || sess.userId !== where.session.userId) return false;
        }
        return true;
      });
      if (orderBy?.createdAt === 'desc') {
        rows = [...rows].sort((a, b) => b.createdAt.getTime() - a.createdAt.getTime());
      } else if (orderBy?.createdAt === 'asc') {
        rows = [...rows].sort((a, b) => a.createdAt.getTime() - b.createdAt.getTime());
      }
      if (typeof take === 'number') {
        rows = rows.slice(0, take);
      }
      return rows;
    },
    findFirst: async ({ where }) => {
      return (
        mockState.messages.find((m) => {
          if (where.id && m.id !== where.id) return false;
          if (where.role && m.role !== where.role) return false;
          if (where.session?.userId) {
            const sess = mockState.sessions.find((s) => s.id === m.sessionId);
            if (!sess || sess.userId !== where.session.userId) return false;
          }
          return true;
        }) ?? null
      );
    },
    deleteMany: async ({ where }) => {
      const removedIds = new Set(
        mockState.messages
          .filter((m) => !where?.sessionId || m.sessionId === where.sessionId)
          .map((m) => m.id),
      );
      mockState.messages = mockState.messages.filter((m) => !removedIds.has(m.id));
      mockState.feedbacks = mockState.feedbacks.filter((f) => !removedIds.has(f.messageId));
      return { count: removedIds.size };
    },
  },
  position: {
    findMany: async ({ where, take }) => {
      let rows = mockState.positions.filter((p) => {
        if (where?.userId && p.userId !== where.userId) return false;
        if (where?.quantity?.gt !== undefined && !(p.quantity > where.quantity.gt)) return false;
        return true;
      });
      if (typeof take === 'number') rows = rows.slice(0, take);
      return rows;
    },
  },
  watchlist: {
    findMany: async ({ where, take }) => {
      let rows = mockState.watchlists.filter((w) => !where?.userId || w.userId === where.userId);
      if (typeof take === 'number') rows = rows.slice(0, take);
      return rows;
    },
  },
  screenerPreset: {
    findMany: async ({ where, take }) => {
      let rows = mockState.screenerPresets.filter((p) => !where?.userId || p.userId === where.userId);
      if (typeof take === 'number') rows = rows.slice(0, take);
      return rows;
    },
  },
  sagMemoryCascadeOutbox: {
    create: async ({ data }) => {
      const row = {
        id: `30000000-0000-4000-8000-${String(mockState.nextId++).padStart(12, '0')}`,
        sessionId: data.sessionId,
        userId: data.userId,
        reason: data.reason ?? 'USER_DELETED_SESSION',
        status: data.status ?? 'PENDING',
        attempts: data.attempts ?? 0,
        lastError: data.lastError ?? null,
        createdAt: new Date(Date.now() + mockState.nextId * 1000),
        updatedAt: new Date(Date.now() + mockState.nextId * 1000),
      };
      mockState.cascadeOutbox.push(row);
      return row;
    },
    findMany: async ({ where, take }) => {
      let rows = mockState.cascadeOutbox.filter((r) => {
        if (where?.status && r.status !== where.status) return false;
        if (where?.userId && r.userId !== where.userId) return false;
        return true;
      });
      if (typeof take === 'number') rows = rows.slice(0, take);
      return rows;
    },
    update: async ({ where, data }) => {
      const idx = mockState.cascadeOutbox.findIndex((r) => r.id === where.id);
      if (idx === -1) throw new Error('Outbox row not found');
      mockState.cascadeOutbox[idx] = {
        ...mockState.cascadeOutbox[idx],
        ...data,
        updatedAt: new Date(),
      };
      return mockState.cascadeOutbox[idx];
    },
    updateMany: async ({ where, data }) => {
      let count = 0;
      for (let i = 0; i < mockState.cascadeOutbox.length; i += 1) {
        const row = mockState.cascadeOutbox[i];
        if (where?.id && row.id !== where.id) continue;
        if (where?.status && row.status !== where.status) continue;
        mockState.cascadeOutbox[i] = {
          ...row,
          ...data,
          updatedAt: new Date(),
        };
        count += 1;
      }
      return { count };
    },
  },
  sagChatEvalFeedback: {
    upsert: async ({ where, create, update }) => {
      const key = where.messageId_userId;
      const existingIdx = mockState.feedbacks.findIndex(
        (f) => f.messageId === key.messageId && f.userId === key.userId,
      );
      if (existingIdx >= 0) {
        mockState.feedbacks[existingIdx] = { ...mockState.feedbacks[existingIdx], ...update };
        return mockState.feedbacks[existingIdx];
      }
      const row = {
        id: `20000000-0000-4000-8000-${String(mockState.nextId++).padStart(12, '0')}`,
        ...create,
        createdAt: new Date('2026-11-20T12:05:00Z'),
      };
      mockState.feedbacks.push(row);
      return row;
    },
    findMany: async ({ where }) => {
      return mockState.feedbacks.filter((f) => f.userId === where.userId);
    },
  },
};

require.cache[require.resolve('../dist/config/database')] = {
  loaded: true,
  exports: { __esModule: true, default: mockPrisma },
};
require.cache[require.resolve('../dist/middleware/auth')] = {
  loaded: true,
  exports: {
    authMiddleware: (req, _res, next) => {
      req.userId = 'user-uuid-1';
      next();
    },
    optionalAuth: (_req, _res, next) => next(),
  },
};

const {
  SagResearchChatService,
  SagGatewayError,
  SAG_RESEARCH_TIMEOUT_MS,
  parseSseBlock,
  isHistoricalBacktestDate,
  toValidIsoDate,
  flushPendingCascadeOutbox,
} = require('../dist/services/sagResearchChat.service');
const { Prisma } = require('@prisma/client');
const { aiLimiter } = require('../dist/middleware/rateLimiter');
const aiRouter = require('../dist/modules/ai/ai.routes').default;
const { errorHandler } = require('../dist/middleware/errorHandler');

function createMockSseResponse() {
  const res = new EventEmitter();
  res.statusCode = 200;
  res.headers = {};
  res.chunks = [];
  res.headersSent = false;
  res.writableEnded = false;
  res.flushHeadersCalled = false;
  res.status = (code) => {
    res.statusCode = code;
    return res;
  };
  res.setHeader = (name, value) => {
    res.headers[name.toLowerCase()] = value;
  };
  res.flushHeaders = () => {
    res.headersSent = true;
    res.flushHeadersCalled = true;
  };
  res.write = (chunk) => {
    res.chunks.push(String(chunk));
    return true;
  };
  res.end = () => {
    res.writableEnded = true;
    res.emit('finish');
  };
  res.json = (body) => {
    res.body = body;
    res.writableEnded = true;
  };
  return res;
}

function makeSseStream(chunks) {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) {
        controller.enqueue(encoder.encode(c));
      }
      controller.close();
    },
  });
}

function parseEmittedSseEvents(rawText) {
  return rawText
    .split('\n\n')
    .filter((block) => block.trim().length > 0)
    .map((block) => {
      const parsed = parseSseBlock(block);
      return parsed ? { event: parsed.event, data: JSON.parse(parsed.data) } : null;
    })
    .filter(Boolean);
}

test('SAG research timeout is 150s (bypasses 800ms aiEngineService) and BFF headers enforce org-scoped read-only access without single-ticker lock', () => {
  assert.equal(SAG_RESEARCH_TIMEOUT_MS, 150_000);

  const service = new SagResearchChatService({
    prismaClient: mockPrisma,
    sagApiUrl: 'http://sag-api:8000',
    sagBffServiceToken: 'test-bff-token',
  });

  const single = service.buildBffHeaders('user-1', 'pvt');
  assert.equal(single['X-SAG-BFF-Service-Token'], 'test-bff-token');
  assert.equal(single['X-Continuum-User-Id'], 'user-1');
  assert.equal(single['X-Continuum-Organization-Id'], 'aiinvest-default');
  assert.equal(single['X-Continuum-Project-Id'], 'all');
  assert.equal(single['X-Continuum-Security-Partition'], 'all');
  assert.equal(single['X-Continuum-Allowed-Project-Ids'], '*');

  const freeForm = service.buildBffHeaders('user-1');
  assert.equal(freeForm['X-Continuum-Organization-Id'], 'aiinvest-default');
  assert.equal(freeForm['X-Continuum-Project-Id'], 'all');
  assert.equal(freeForm['X-Continuum-Security-Partition'], 'all');
  assert.equal(freeForm['X-Continuum-Allowed-Project-Ids'], '*');

  const customOrg = new SagResearchChatService({
    prismaClient: mockPrisma,
    sagApiUrl: 'http://sag-api:8000',
    sagBffServiceToken: 'test-bff-token',
    sagResearchOrgId: 'custom-org-id',
  });
  assert.equal(customOrg.buildBffHeaders('user-1')['X-Continuum-Organization-Id'], 'custom-org-id');
});

test('fail-closed when SAG_BFF_SERVICE_TOKEN is missing', async () => {
  resetMocks();
  const service = new SagResearchChatService({
    prismaClient: mockPrisma,
    sagBffServiceToken: '',
    nodeEnv: 'production',
  });

  const res = createMockSseResponse();
  await assert.rejects(
    () =>
      service.streamResearchChat(
        'user-1',
        { query: 'Phan tich MOAT PVT', ticker: 'PVT' },
        res,
      ),
    (err) => err instanceof SagGatewayError && err.statusCode === 503 && err.code === 'SAG_NOT_CONFIGURED',
  );
  assert.equal(res.flushHeadersCalled, false);
  assert.equal(mockState.messages.length, 0);
});

test('streamResearchChat proxies chunked SSE, forwards BFF headers, and persists user + assistant messages', async () => {
  resetMocks();
  let capturedRequest = null;

  const donePayload = {
    answer: 'Bien gop PVT giam chu yeu do mang thuong mai [C1].',
    metadata: {
      mode: 'RESEARCH_CHAT_V2',
      primary_ticker: 'PVT',
      expanded_tickers: ['PVT', 'PVP'],
      as_of_date: '2026-11-20',
      citations: [
        {
          citation_id: 'C1',
          sub_doc_slot: 'S07',
          page: 168,
          published_date: '2026-04-15',
          quote: 'Kinh doanh thuong mai dat bien gop 0.43%',
          quote_hash: 'abc123',
          verified: true,
        },
      ],
      hydrated_tables: [
        { table_id: 'tbl-1', title: 'TM 28', verification_status: 'VERIFIED' },
      ],
      calculator_traces: [
        { calc_id: 'CALC-1', formula: '100/200', inputs: { a: 100 }, result: 0.5, formatted: '50.00%' },
      ],
      graph_hops: [
        { layer: 'MACRO', effective_from: '2026-04-15', verification_tier: 'CONFIRMED_BOTH' },
      ],
      discrepancy_alerts: [],
      declared_blindspots: [],
      telemetry: { routing_slot: 'S07', input_tokens: 1200, output_tokens: 300, latency_ms: 1500 },
    },
  };

  const mockFetch = async (url, init) => {
    capturedRequest = { url, init };
    return new Response(
      makeSseStream([
        'event: stage\ndata: {"stage":"routing","status":"running"}\n\n',
        'event: delta\ndata: {"text":"Bien gop PVT "}\n\n',
        `event: done\ndata: ${JSON.stringify(donePayload).slice(0, 40)}`,
        `${JSON.stringify(donePayload).slice(40)}\n\n`,
      ]),
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    );
  };

  const service = new SagResearchChatService({
    prismaClient: mockPrisma,
    fetchImpl: mockFetch,
    sagApiUrl: 'http://sag-api:8000',
    sagBffServiceToken: 'secret-token',
  });

  const session = await service.createSession('user-1', {
    ticker: 'pvt',
    asOfDate: '2026-11-20',
    enableKg: true,
  });

  const res = createMockSseResponse();
  await service.streamResearchChat(
    'user-1',
    {
      sessionId: session.id,
      query: 'Bien gop PVT ra sao?',
      ticker: 'pvt',
      compareTickers: ['pvp'],
      asOfDate: '2026-11-20',
      enableKg: true,
    },
    res,
  );

  assert.equal(capturedRequest.url, 'http://sag-api:8000/api/v1/research-chat/stream');
  assert.equal(capturedRequest.init.headers['X-SAG-BFF-Service-Token'], 'secret-token');
  assert.equal(capturedRequest.init.headers['X-Continuum-Project-Id'], 'all');
  assert.equal(capturedRequest.init.headers['X-Continuum-Allowed-Project-Ids'], '*');
  const parsedBody = JSON.parse(capturedRequest.init.body);
  assert.equal(parsedBody.mode, 'GENERAL');
  assert.equal(parsedBody.ticker, 'PVT');
  assert.deepEqual(parsedBody.compare_tickers, ['PVP']);

  assert.equal(res.headers['content-type'], 'text/event-stream; charset=utf-8');
  assert.equal(res.headers['cache-control'], 'no-cache, no-transform');
  assert.equal(res.headers['x-accel-buffering'], 'no');
  assert.equal(res.flushHeadersCalled, true);
  assert.equal(res.writableEnded, true);

  const events = parseEmittedSseEvents(res.chunks.join(''));
  assert.equal(events.length, 3);
  assert.equal(events[0].event, 'stage');
  assert.equal(events[1].event, 'delta');
  assert.equal(events[2].event, 'done');
  assert.ok(events[2].data.messageId);
  assert.equal(events[2].data.sessionId, session.id);
  assert.equal(events[2].data.answer, donePayload.answer);

  assert.equal(mockState.messages.length, 2);
  assert.equal(mockState.messages[0].role, 'user');
  assert.equal(mockState.messages[1].role, 'assistant');
  assert.equal(mockState.messages[1].type, 'research_chat_v2');
  assert.equal(mockState.messages[1].id, events[2].data.messageId);
});

test('streamResearchChat fails closed on malformed done payload and upstream HTTP 502 without saving assistant message', async () => {
  resetMocks();
  const serviceMalformed = new SagResearchChatService({
    prismaClient: mockPrisma,
    fetchImpl: async () =>
      new Response(makeSseStream(['event: done\ndata: {broken_json\n\n']), { status: 200 }),
    sagApiUrl: 'http://sag-api:8000',
    sagBffServiceToken: 'secret-token',
  });

  const res1 = createMockSseResponse();
  await serviceMalformed.streamResearchChat('user-1', { query: 'test', ticker: 'VCB' }, res1);
  const events1 = parseEmittedSseEvents(res1.chunks.join(''));
  assert.equal(events1[0].event, 'error');
  assert.equal(events1[0].data.code, 'INVALID_SAG_PAYLOAD');
  assert.equal(mockState.messages.filter((m) => m.role === 'assistant').length, 0);

  const service502 = new SagResearchChatService({
    prismaClient: mockPrisma,
    fetchImpl: async () => new Response('Bad Gateway', { status: 502 }),
    sagApiUrl: 'http://sag-api:8000',
    sagBffServiceToken: 'secret-token',
  });
  const res2 = createMockSseResponse();
  await service502.streamResearchChat('user-1', { query: 'test 2', ticker: 'VCB' }, res2);
  const events2 = parseEmittedSseEvents(res2.chunks.join(''));
  assert.equal(events2[0].event, 'error');
  assert.equal(events2[0].data.code, 'SAG_UPSTREAM_ERROR');
  assert.equal(mockState.messages.filter((m) => m.role === 'assistant').length, 0);
});

test('streamResearchChat emits SAG_TIMEOUT when upstream exceeds timeoutMs', async () => {
  resetMocks();
  const service = new SagResearchChatService({
    prismaClient: mockPrisma,
    sagApiUrl: 'http://sag-api:8000',
    sagBffServiceToken: 'secret-token',
    timeoutMs: 25,
    fetchImpl: (_url, init) =>
      new Promise((_resolve, reject) => {
        init.signal.addEventListener('abort', () => {
          const err = new Error('Aborted');
          err.name = 'AbortError';
          reject(err);
        });
      }),
  });

  const res = createMockSseResponse();
  await service.streamResearchChat('user-1', { query: 'slow query', ticker: 'FPT' }, res);
  const events = parseEmittedSseEvents(res.chunks.join(''));
  assert.equal(events[0].event, 'error');
  assert.equal(events[0].data.code, 'SAG_TIMEOUT');
});

test('eval-summary computes G1-G5 KPIs and detects PIT leaks', async () => {
  resetMocks();
  const service = new SagResearchChatService({
    prismaClient: mockPrisma,
    sagBffServiceToken: 'secret-token',
  });

  const session = await service.createSession('user-1', { ticker: 'VCB', asOfDate: '2025-06-30' });
  const msg1 = await mockPrisma.chatMessage.create({
    data: {
      sessionId: session.id,
      role: 'assistant',
      content: 'Verified answer',
      type: 'research_chat_v2',
      metadata: {
        mode: 'RESEARCH_CHAT_V2',
        primary_ticker: 'VCB',
        as_of_date: '2025-06-30',
        citations: [
          { verified: true, published_date: '2025-04-10' },
          { verified: false, published_date: '2026-01-15' }, // future PIT leak!
        ],
        hydrated_tables: [{ verification_status: 'VERIFIED' }],
        calculator_traces: [{ inputs: { a: 1 }, result: 2 }],
        graph_hops: [{ effective_from: '2025-05-01' }],
      },
    },
  });

  await service.submitFeedback('user-1', {
    messageId: msg1.id,
    verdict: 'PASS',
    citationOk: true,
    tableCalcOk: true,
    graphContextOk: true,
    hallucinationFlag: false,
    notes: 'Good',
  });

  const summary = await service.getEvalSummary('user-1');
  assert.equal(summary.totalSessions, 1);
  assert.equal(summary.totalAssistantMessages, 1);
  assert.equal(summary.feedbackCount, 1);
  assert.equal(summary.gates.g1CitationVerifiedRate, 0.5);
  assert.equal(summary.gates.g2TableCalcVerifiedRate, 1);
  assert.equal(summary.gates.g3AbstentionComplianceRate, 1);
  assert.equal(summary.gates.g4PitZeroLeakCount, 1);
  assert.equal(summary.gates.g5HumanPassRate, 1);
  assert.equal(summary.readyForPhase2, false);
});

test('Express routes validate inputs and handle sessions, feedback, eval-summary, kg, and tables', async () => {
  const getRoute = (path, method) =>
    aiRouter.stack.find((l) => l.route?.path === path && l.route.methods[method])?.route;

  const postSessionRoute = getRoute('/research-chat/sessions', 'post');
  const listSessionsRoute = getRoute('/research-chat/sessions', 'get');
  const getSessionRoute = getRoute('/research-chat/sessions/:id', 'get');
  const postFeedbackRoute = getRoute('/research-chat/feedback', 'post');
  const evalSummaryRoute = getRoute('/research-chat/eval-summary', 'get');
  const kgRoute = getRoute('/research-chat/kg/:ticker', 'get');
  const tableRoute = getRoute('/research-chat/tables/:tableId', 'get');

  assert.ok(postSessionRoute && listSessionsRoute && getSessionRoute && postFeedbackRoute && evalSummaryRoute && kgRoute && tableRoute);

  // Invalid UUID on GET /research-chat/sessions/:id triggers Zod 400
  const invalidRes = createMockSseResponse();
  let capturedErr = null;
  await getSessionRoute.stack.at(-1).handle(
    { params: { id: 'not-a-uuid' }, userId: 'user-uuid-1' },
    invalidRes,
    (err) => {
      capturedErr = err;
    },
  );
  assert.ok(capturedErr);
  const errRes = {
    status(c) {
      this.code = c;
      return this;
    },
    json(b) {
      this.body = b;
    },
  };
  errorHandler(capturedErr, {}, errRes, () => {});
  assert.equal(errRes.code, 400);

  // Verify POST /research-chat/sessions returns 201 and GET /research-chat/sessions returns { sessions }
  resetMocks();
  const createRes = createMockSseResponse();
  await postSessionRoute.stack.at(-1).handle(
    { body: { ticker: 'fpt', asOfDate: '2026-11-20', enableKg: false }, userId: 'user-uuid-1' },
    createRes,
    (err) => {
      throw err;
    },
  );
  assert.equal(createRes.statusCode, 201);
  assert.equal(createRes.body.ticker, 'FPT');
  assert.equal(createRes.body.asOfDate, '2026-11-20');
  assert.equal(createRes.body.enableKg, false);

  const listRes = createMockSseResponse();
  await listSessionsRoute.stack.at(-1).handle(
    { userId: 'user-uuid-1' },
    listRes,
    (err) => {
      throw err;
    },
  );
  assert.equal(listRes.body.sessions.length, 1);
  assert.equal(listRes.body.sessions[0].id, createRes.body.id);

  const detailRes = createMockSseResponse();
  await getSessionRoute.stack.at(-1).handle(
    { params: { id: createRes.body.id }, userId: 'user-uuid-1' },
    detailRes,
    (err) => {
      throw err;
    },
  );
  assert.equal(detailRes.body.session.id, createRes.body.id);
  assert.deepEqual(detailRes.body.messages, []);

  // Verify POST /research-chat/feedback returns 201 for owned assistant message
  const assistantMsg = await mockPrisma.chatMessage.create({
    data: {
      sessionId: createRes.body.id,
      role: 'assistant',
      content: 'MOAT answer',
      type: 'research_chat_v2',
      metadata: { mode: 'RESEARCH_CHAT_V2', primary_ticker: 'FPT' },
    },
  });
  const fbRes = createMockSseResponse();
  await postFeedbackRoute.stack.at(-1).handle(
    { body: { messageId: assistantMsg.id, verdict: 'PASS', notes: 'Solid' }, userId: 'user-uuid-1' },
    fbRes,
    (err) => {
      throw err;
    },
  );
  assert.equal(fbRes.statusCode, 201);
  assert.equal(fbRes.body.feedback.verdict, 'PASS');

  const summaryRes = createMockSseResponse();
  await evalSummaryRoute.stack.at(-1).handle(
    { userId: 'user-uuid-1' },
    summaryRes,
    (err) => {
      throw err;
    },
  );
  assert.equal(summaryRes.body.feedbackCount, 1);
  assert.equal(summaryRes.body.gates.g5HumanPassRate, 1);
});

test('aiLimiter exempts GET /research-chat/* routes while still rate-limiting POST routes', async () => {
  const invokeLimiter = (method, path, originalUrl, ip) =>
    new Promise((resolve) => {
      const req = {
        method,
        path,
        originalUrl,
        ip,
        headers: {},
        app: { get: () => false },
      };
      const res = {
        statusCode: 200,
        headers: {},
        setHeader(k, v) {
          this.headers[k.toLowerCase()] = v;
        },
        status(code) {
          this.statusCode = code;
          return this;
        },
        send(body) {
          resolve({ blocked: true, statusCode: this.statusCode, body });
        },
        json(body) {
          resolve({ blocked: true, statusCode: this.statusCode, body });
        },
      };
      aiLimiter(req, res, () => resolve({ blocked: false }));
    });

  // 15 GETs on /research-chat/sessions must never be blocked
  for (let i = 0; i < 15; i += 1) {
    const out = await invokeLimiter('GET', '/research-chat/sessions', '/api/v1/ai/research-chat/sessions', '10.0.0.1');
    assert.equal(out.blocked, false);
  }

  // 11th POST on /research-chat/stream from a fresh IP must be blocked (max=10)
  for (let i = 0; i < 10; i += 1) {
    const out = await invokeLimiter('POST', '/research-chat/stream', '/api/v1/ai/research-chat/stream', '10.0.0.2');
    assert.equal(out.blocked, false);
  }
  const blockedOut = await invokeLimiter('POST', '/research-chat/stream', '/api/v1/ai/research-chat/stream', '10.0.0.2');
  assert.equal(blockedOut.blocked, true);
  assert.equal(blockedOut.statusCode, 429);
});

test('streamResearchChat fails closed on truncated stream without done and KG/table JSON proxy fails closed on invalid JSON', async () => {
  resetMocks();
  const truncatedSvc = new SagResearchChatService({
    prismaClient: mockPrisma,
    fetchImpl: async () =>
      new Response(makeSseStream(['event: delta\ndata: {"text":"partial"}\n\n']), { status: 200 }),
    sagApiUrl: 'http://sag-api:8000',
    sagBffServiceToken: 'secret-token',
  });
  const res = createMockSseResponse();
  await truncatedSvc.streamResearchChat('user-1', { query: 'test', ticker: 'HPG' }, res);
  const events = parseEmittedSseEvents(res.chunks.join(''));
  assert.equal(events.at(-1).event, 'error');
  assert.equal(events.at(-1).data.code, 'STREAM_INCOMPLETE');
  assert.equal(mockState.messages.filter((m) => m.role === 'assistant').length, 0);

  const badJsonSvc = new SagResearchChatService({
    prismaClient: mockPrisma,
    fetchImpl: async () => new Response('not-json', { status: 200 }),
    sagApiUrl: 'http://sag-api:8000',
    sagBffServiceToken: 'secret-token',
  });
  await assert.rejects(
    () => badJsonSvc.getKgSubgraph('user-1', 'hpg', '2026-11-20'),
    (err) => err instanceof SagGatewayError && err.statusCode === 502 && err.code === 'INVALID_SAG_PAYLOAD',
  );
  await assert.rejects(
    () => badJsonSvc.getHydratedTable('user-1', 'tbl-1', 'hpg'),
    (err) => err instanceof SagGatewayError && err.statusCode === 502 && err.code === 'INVALID_SAG_PAYLOAD',
  );
});

test('createSession and streamResearchChat support free-form chat without primary ticker and forward MOAT mode', async () => {
  resetMocks();
  let capturedBody = null;
  const service = new SagResearchChatService({
    prismaClient: mockPrisma,
    fetchImpl: async (_url, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(
        makeSseStream([
          `event: done\ndata: ${JSON.stringify({
            answer: 'FPT va MWG deu co du lieu PIT [C1].',
            metadata: { mode: 'RESEARCH_CHAT_V2', chat_mode: 'MOAT', expanded_tickers: ['FPT', 'MWG'] },
          })}\n\n`,
        ]),
        { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
      );
    },
    sagApiUrl: 'http://sag-api:8000',
    sagBffServiceToken: 'secret-token',
  });

  const session = await service.createSession('user-1', { title: 'So sanh FPT va MWG' });
  assert.equal(session.ticker, null);

  const res = createMockSseResponse();
  await service.streamResearchChat(
    'user-1',
    {
      sessionId: session.id,
      query: 'So sanh bien gop @FPT va @MWG',
      mode: 'MOAT',
    },
    res,
  );
  assert.equal(capturedBody.ticker ?? null, null);
  assert.equal(capturedBody.mode, 'MOAT');
  const events = parseEmittedSseEvents(res.chunks.join(''));
  assert.equal(events.at(-1).event, 'done');
  assert.equal(events.at(-1).data.metadata.chat_mode, 'MOAT');
});

test('eval-summary computes TRUE Gate G1 (including rejected_citations) and TRUE Gate G3 (no fake 100% at 0 feedback)', async () => {
  resetMocks();
  const service = new SagResearchChatService({
    prismaClient: mockPrisma,
    sagBffServiceToken: 'secret-token',
  });

  const session = await service.createSession('user-1', { ticker: 'FPT', asOfDate: '2026-06-30' });
  // Case 1: Clean message with 0 human feedback => G3 MUST be null (unmeasured), NEVER fake 1.0!
  await mockPrisma.chatMessage.create({
    data: {
      sessionId: session.id,
      role: 'assistant',
      content: 'Doanh thu FPT tang 19.4% [C1].',
      type: 'research_chat_v2',
      metadata: {
        mode: 'RESEARCH_CHAT_V2',
        primary_ticker: 'FPT',
        as_of_date: '2026-06-30',
        citations: [{ citation_id: 'C1', verified: true, published_date: '2026-04-10' }],
        rejected_citations: [],
        declared_blindspots: [],
        telemetry: { rejected_citations_count: 0, unsupported_numeric_claims_count: 0 },
      },
    },
  });
  const cleanSummary = await service.getEvalSummary('user-1');
  assert.equal(cleanSummary.gates.g1CitationVerifiedRate, 1);
  assert.equal(cleanSummary.gates.g3AbstentionComplianceRate, null);
  assert.equal(cleanSummary.autoHallucinationCount, 0);
  assert.equal(cleanSummary.readyForPhase2, false);

  // Case 2: Message with 1 verified citation + 1 rejected citation + UNSUPPORTED_NUMERIC_CLAIM
  // => G1 MUST drop to 2/3 (0.6667) instead of fake 100%, and G3 MUST drop to 0.5!
  await mockPrisma.chatMessage.create({
    data: {
      sessionId: session.id,
      role: 'assistant',
      content: 'Doanh thu dat 99,999 ty [C1] [C99 UNVERIFIED_CLAIM].',
      type: 'research_chat_v2',
      metadata: {
        mode: 'RESEARCH_CHAT_V2',
        primary_ticker: 'FPT',
        as_of_date: '2026-06-30',
        citations: [{ citation_id: 'C1', verified: true, published_date: '2026-04-10' }],
        rejected_citations: [
          { citation_id: 'C99', verified: false, rejection_reason: 'Hallucinated citation ID' },
        ],
        declared_blindspots: [
          { code: 'HALLUCINATED_CITATION_ID', reason: 'C99 not found', scope: 'C99' },
          { code: 'UNSUPPORTED_NUMERIC_CLAIM', reason: '99,999 ty unsupported', scope: 'numeric_audit' },
        ],
        telemetry: {
          rejected_citations_count: 1,
          hallucinated_citation_ids_count: 1,
          unsupported_numeric_claims_count: 1,
        },
      },
    },
  });

  const penalizedSummary = await service.getEvalSummary('user-1');
  assert.equal(penalizedSummary.gates.g1CitationVerifiedRate, 0.6667);
  assert.equal(penalizedSummary.gates.g3AbstentionComplianceRate, 0.5);
  assert.equal(penalizedSummary.autoHallucinationCount, 1);
  assert.equal(penalizedSummary.readyForPhase2, false);
});

test('GET /research-chat/tickers route exists and proxies org-scoped ingested tickers', async () => {
  const tickersRoute = aiRouter.stack.find(
    (l) => l.route?.path === '/research-chat/tickers' && l.route.methods.get,
  )?.route;
  assert.ok(tickersRoute);

  let capturedUrl = null;
  let capturedHeaders = null;
  const service = new SagResearchChatService({
    prismaClient: mockPrisma,
    sagApiUrl: 'http://sag-api:8000',
    sagBffServiceToken: 'secret-token',
    fetchImpl: async (url, init) => {
      capturedUrl = url;
      capturedHeaders = init.headers;
      return new Response(
        JSON.stringify({
          organization_id: 'aiinvest-default',
          tickers: ['CTG', 'DCM', 'FPT', 'HPG', 'MWG', 'NT2', 'PVT', 'VCI'],
          pilot_fallback: ['CTG', 'DCM', 'FPT', 'HPG', 'MWG', 'NT2', 'PVT', 'VCI'],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    },
  });

  const res = await service.getIngestedTickers('user-1');
  assert.equal(capturedUrl, 'http://sag-api:8000/api/v1/research-chat/tickers');
  assert.equal(capturedHeaders['X-SAG-BFF-Service-Token'], 'secret-token');
  assert.equal(capturedHeaders['X-Continuum-Organization-Id'], 'aiinvest-default');
  assert.deepEqual(res.tickers, ['CTG', 'DCM', 'FPT', 'HPG', 'MWG', 'NT2', 'PVT', 'VCI']);

  const resPit = await service.getIngestedTickers('user-1', '2024-06-30');
  assert.equal(capturedUrl, 'http://sag-api:8000/api/v1/research-chat/tickers?as_of_date=2024-06-30');
  assert.deepEqual(resPit.tickers, ['CTG', 'DCM', 'FPT', 'HPG', 'MWG', 'NT2', 'PVT', 'VCI']);
});

test('eval-summary excludes eval_eligible=false short-circuits from G3 and catches screening_results PIT leaks in G4', async () => {
  resetMocks();
  const service = new SagResearchChatService({
    prismaClient: mockPrisma,
    sagBffServiceToken: 'secret-token',
  });

  const session = await service.createSession('user-1', { ticker: 'FPT', asOfDate: '2024-06-30' });

  // Short-circuit chitchat message (eval_eligible: false) must NOT dilute G3 or inflate eval count
  await mockPrisma.chatMessage.create({
    data: {
      sessionId: session.id,
      role: 'assistant',
      content: 'Xin chao! Toi co the giup gi cho ban?',
      type: 'research_chat_v2',
      metadata: {
        mode: 'RESEARCH_CHAT_V2',
        route_intent: 'chitchat_casual',
        eval_eligible: false,
        as_of_date: '2024-06-30',
        citations: [],
        declared_blindspots: [],
        telemetry: { eval_eligible: false },
      },
    },
  });

  // Real research turn with an unsupported numeric claim + a screening_results PIT leak (2025-04-01 > 2024-06-30)
  await mockPrisma.chatMessage.create({
    data: {
      sessionId: session.id,
      role: 'assistant',
      content: 'Top ROE la FPT [SCR:FPT].',
      type: 'research_chat_v2',
      metadata: {
        mode: 'RESEARCH_CHAT_V2',
        route_intent: 'screening_ranking',
        eval_eligible: true,
        primary_ticker: 'FPT',
        as_of_date: '2024-06-30',
        citations: [],
        screening_results: [
          { ticker: 'FPT', roe: 0.25, valid_from: '2025-04-01' },
        ],
        declared_blindspots: [
          { code: 'UNSUPPORTED_NUMERIC_CLAIM', reason: 'Unsupported value', scope: 'numeric_audit' },
        ],
        telemetry: { eval_eligible: true, unsupported_numeric_claims_count: 1 },
      },
    },
  });

  const summary = await service.getEvalSummary('user-1');
  assert.equal(summary.totalAssistantMessages, 1);
  assert.equal(summary.gates.g3AbstentionComplianceRate, 0);
  assert.equal(summary.gates.g4PitZeroLeakCount, 1);
});

test('streamResearchChat forwards session_id and structured PIT history metadata projection to SAG', async () => {
  resetMocks();
  const service = new SagResearchChatService({
    prismaClient: mockPrisma,
    sagApiUrl: 'http://sag-api:8000',
    sagBffServiceToken: 'secret-token',
    fetchImpl: async (_url, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(
        makeSseStream([
          'event: done\ndata: {"answer":"OK","metadata":{"mode":"RESEARCH_CHAT_V2","as_of_date":"2024-06-30"}}\n\n',
        ]),
        { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
      );
    },
  });
  let capturedBody = null;

  const session = await service.createSession('user-1', { ticker: 'FPT', asOfDate: '2024-06-30' });
  await mockPrisma.chatMessage.create({
    data: {
      sessionId: session.id,
      role: 'user',
      content: 'Loc top co phieu ROE cao',
      type: 'text',
      metadata: { as_of_date: '2026-06-30' },
    },
  });
  await mockPrisma.chatMessage.create({
    data: {
      sessionId: session.id,
      role: 'assistant',
      content: 'Top 1 la FPT.',
      type: 'research_chat_v2',
      metadata: {
        as_of_date: '2026-06-30',
        primary_ticker: 'FPT',
        expanded_tickers: ['FPT', 'MWG'],
        route_intent: 'screening_ranking',
        eval_eligible: true,
        screening_results: [{ ticker: 'FPT', fiscal_year: 2025, roe: 0.28 }],
      },
    },
  });

  const res = createMockSseResponse();
  await service.streamResearchChat(
    'user-1',
    { sessionId: session.id, query: 'Con no vay thi sao?', asOfDate: '2024-06-30' },
    res,
  );

  assert.equal(capturedBody.session_id, session.id);
  assert.equal(capturedBody.history.length, 2);
  assert.equal(capturedBody.history[1].turn_as_of_date, '2026-06-30');
  assert.equal(capturedBody.history[1].primary_ticker, 'FPT');
  assert.deepEqual(capturedBody.history[1].screened_tickers, ['FPT']);
  assert.equal(capturedBody.history[1].route_intent, 'screening_ranking');
});

test('buildLiveUserPortfolioSnapshot enforces strict PIT gating (live vs historical backtest vs incognito) and user isolation', async () => {
  resetMocks();
  mockState.positions.push(
    { userId: 'user-1', symbol: 'fpt', quantity: 1000, avgPrice: '125000.00' },
    { userId: 'user-1', symbol: 'HPG', quantity: 0, avgPrice: '25000.00' }, // zero qty excluded
    { userId: 'user-2', symbol: 'MWG', quantity: 500, avgPrice: '60000.00' }, // other user excluded
  );
  mockState.watchlists.push(
    { userId: 'user-1', name: 'Core', symbols: ['fpt', 'vcb'] },
    { userId: 'user-1', name: 'Tech', symbols: ['FPT', 'cmg'] },
    { userId: 'user-2', name: 'Other', symbols: ['SSI'] },
  );
  mockState.screenerPresets.push(
    { userId: 'user-1', id: 'preset-1', name: 'Quality ROE', filters: { roe_min: 15, pe_max: 15 } },
    { userId: 'user-2', id: 'preset-2', name: 'Other', filters: { pb_max: 1 } },
  );

  const service = new SagResearchChatService({
    prismaClient: mockPrisma,
    sagBffServiceToken: 'secret-token',
  });

  const refNow = new Date('2026-11-24T10:00:00Z');
  assert.equal(isHistoricalBacktestDate(undefined, refNow), false);
  assert.equal(isHistoricalBacktestDate('2026-11-24', refNow), false);
  assert.equal(isHistoricalBacktestDate('2026-11-23', refNow), false); // <= 1 day -> live
  assert.equal(isHistoricalBacktestDate('2026-11-25', refNow), false); // VN UTC+7 tomorrow -> live
  assert.equal(isHistoricalBacktestDate('2026-11-22', refNow), true); // 2 days ago -> historical backtest!
  assert.equal(isHistoricalBacktestDate('2024-06-30', refNow), true);
  assert.equal(isHistoricalBacktestDate('invalid-date', refNow), true); // fail-closed

  // Live snapshot (asOfDate omitted or <= 1 day) includes only user-1's active positions/watchlists/presets
  const liveSnap = await service.buildLiveUserPortfolioSnapshot('user-1', '2026-11-24', 'auto', refNow);
  assert.ok(liveSnap);
  assert.equal(liveSnap.as_of_timestamp, '2026-11-24T10:00:00.000Z');
  assert.deepEqual(liveSnap.positions, [{ symbol: 'FPT', quantity: 1000, avg_cost: 125000 }]);
  assert.deepEqual(liveSnap.watchlists, ['FPT', 'VCB', 'CMG']);
  assert.deepEqual(liveSnap.screener_presets, [
    { id: 'preset-1', name: 'Quality ROE', filters: { roe_min: 15, pe_max: 15 } },
  ]);

  // Live in read_only still attaches portfolio snapshot
  const readOnlySnap = await service.buildLiveUserPortfolioSnapshot('user-1', '2026-11-23', 'read_only', refNow);
  assert.ok(readOnlySnap);
  assert.equal(readOnlySnap.positions.length, 1);

  // Historical backtest (>1 day old) MUST return null (0% PIT leakage!)
  const btSnap = await service.buildLiveUserPortfolioSnapshot('user-1', '2026-11-22', 'auto', refNow);
  assert.equal(btSnap, null);

  // Incognito mode MUST return null even when live
  const incogSnap = await service.buildLiveUserPortfolioSnapshot('user-1', '2026-11-24', 'incognito', refNow);
  assert.equal(incogSnap, null);

  // DB failure gracefully degrades to null (never fails chat stream)
  const brokenPrisma = {
    position: { findMany: async () => { throw new Error('DB connection lost'); } },
    watchlist: { findMany: async () => [] },
    screenerPreset: { findMany: async () => [] },
  };
  const resilientSvc = new SagResearchChatService({
    prismaClient: brokenPrisma,
    sagBffServiceToken: 'secret-token',
  });
  const degraded = await resilientSvc.buildLiveUserPortfolioSnapshot('user-1', '2026-11-24', 'auto', refNow);
  assert.equal(degraded, null);

  // Stream wire verification: backtest sends user_portfolio_snapshot: null; live sends snapshot
  const capturedPayloads = [];
  const wireSvc = new SagResearchChatService({
    prismaClient: mockPrisma,
    sagApiUrl: 'http://sag-api:8000',
    sagBffServiceToken: 'secret-token',
    fetchImpl: async (url, init) => {
      if (String(url).endsWith('/stream')) {
        capturedPayloads.push(JSON.parse(init.body));
        return new Response(
          makeSseStream(['event: done\ndata: {"answer":"OK","metadata":{"mode":"RESEARCH_CHAT_V2"}}\n\n']),
          { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
        );
      }
      return new Response(JSON.stringify({ accepted: true }), { status: 200 });
    },
  });

  await wireSvc.streamResearchChat(
    'user-1',
    { query: 'Backtest FPT', asOfDate: '2024-06-30', memoryMode: 'auto' },
    createMockSseResponse(),
  );
  assert.equal(capturedPayloads[0].memory_mode, 'auto');
  assert.equal(capturedPayloads[0].user_portfolio_snapshot, null);

  await wireSvc.streamResearchChat(
    'user-1',
    { query: 'Live incognito FPT', memoryMode: 'incognito' },
    createMockSseResponse(),
  );
  assert.equal(capturedPayloads[1].memory_mode, 'incognito');
  assert.equal(capturedPayloads[1].user_portfolio_snapshot, null);

  await wireSvc.streamResearchChat(
    'user-1',
    { query: 'Live auto FPT', memoryMode: 'auto' },
    createMockSseResponse(),
  );
  assert.equal(capturedPayloads[2].memory_mode, 'auto');
  assert.ok(capturedPayloads[2].user_portfolio_snapshot);
  assert.equal(capturedPayloads[2].user_portfolio_snapshot.positions[0].symbol, 'FPT');
});

test('getEvalSummary Gate G3 counts HALLUCINATED_MEMORY_ID (blindspots & telemetry) and CITATION_CONTENT_MISMATCH while excluding eval_eligible=false', async () => {
  resetMocks();
  const service = new SagResearchChatService({
    prismaClient: mockPrisma,
    sagBffServiceToken: 'secret-token',
  });

  const session = await service.createSession('user-1', { ticker: 'FPT', asOfDate: '2026-06-30' });

  // 1. Clean eligible turn
  await mockPrisma.chatMessage.create({
    data: {
      sessionId: session.id,
      role: 'assistant',
      content: 'FPT dat ROE 28% [C1].',
      type: 'research_chat_v2',
      metadata: {
        mode: 'RESEARCH_CHAT_V2',
        eval_eligible: true,
        as_of_date: '2026-06-30',
        citations: [{ citation_id: 'C1', verified: true, published_date: '2026-04-10' }],
        declared_blindspots: [],
        telemetry: { eval_eligible: true, hallucinated_memory_ids_count: 0 },
      },
    },
  });

  // 2. Eligible turn with HALLUCINATED_MEMORY_ID in declared_blindspots
  await mockPrisma.chatMessage.create({
    data: {
      sessionId: session.id,
      role: 'assistant',
      content: 'Theo [MEM:M99 UNVERIFIED_CLAIM], tieu chi la P/E < 15x.',
      type: 'research_chat_v2',
      metadata: {
        mode: 'RESEARCH_CHAT_V2',
        eval_eligible: true,
        as_of_date: '2026-06-30',
        citations: [],
        declared_blindspots: [
          { code: 'HALLUCINATED_MEMORY_ID', reason: 'M99 not in recalled memories', scope: 'MEM:M99' },
        ],
        telemetry: { eval_eligible: true },
      },
    },
  });

  // 3. Eligible turn flagged ONLY via telemetry.hallucinated_memory_ids_count > 0
  await mockPrisma.chatMessage.create({
    data: {
      sessionId: session.id,
      role: 'assistant',
      content: 'Danh muc [PORT:XYZ UNVERIFIED_CLAIM].',
      type: 'research_chat_v2',
      metadata: {
        mode: 'RESEARCH_CHAT_V2',
        eval_eligible: true,
        as_of_date: '2026-06-30',
        citations: [],
        declared_blindspots: [],
        telemetry: { eval_eligible: true, hallucinated_memory_ids_count: 1 },
      },
    },
  });

  // 4. Eligible turn with CITATION_CONTENT_MISMATCH
  await mockPrisma.chatMessage.create({
    data: {
      sessionId: session.id,
      role: 'assistant',
      content: 'Claim mismatch [C1].',
      type: 'research_chat_v2',
      metadata: {
        mode: 'RESEARCH_CHAT_V2',
        eval_eligible: true,
        as_of_date: '2026-06-30',
        citations: [{ citation_id: 'C1', verified: true, published_date: '2026-04-10' }],
        declared_blindspots: [
          { code: 'CITATION_CONTENT_MISMATCH', reason: 'Quote does not match claim', scope: 'C1' },
        ],
        telemetry: { eval_eligible: true },
      },
    },
  });

  // 5. Short-circuit turn (eval_eligible: false) MUST be excluded even if blindspot present
  await mockPrisma.chatMessage.create({
    data: {
      sessionId: session.id,
      role: 'assistant',
      content: 'Chao ban!',
      type: 'research_chat_v2',
      metadata: {
        mode: 'RESEARCH_CHAT_V2',
        eval_eligible: false,
        as_of_date: '2026-06-30',
        declared_blindspots: [
          { code: 'HALLUCINATED_MEMORY_ID', reason: 'Should be excluded', scope: 'MEM:M99' },
        ],
        telemetry: { eval_eligible: false, hallucinated_memory_ids_count: 1 },
      },
    },
  });

  const summary = await service.getEvalSummary('user-1');
  assert.equal(summary.totalAssistantMessages, 4);
  assert.equal(summary.autoHallucinationCount, 3);
  assert.equal(summary.gates.g3AbstentionComplianceRate, 0.25);
  assert.equal(summary.readyForPhase2, false);
});

test('enqueueMemoryIngestTurn skips incognito/read_only and fires exact MemoryIngestTurnRequest payload post-commit for auto', async () => {
  resetMocks();
  const ingestCalls = [];
  const service = new SagResearchChatService({
    prismaClient: mockPrisma,
    sagApiUrl: 'http://sag-api:8000',
    sagBffServiceToken: 'secret-token',
    fetchImpl: async (url, init) => {
      if (String(url).endsWith('/memories/ingest-turn')) {
        ingestCalls.push({ url: String(url), headers: init.headers, body: JSON.parse(init.body) });
        return new Response(JSON.stringify({ accepted: true, job_id: 'job-1' }), { status: 200 });
      }
      return new Response(
        makeSseStream([
          `event: done\ndata: ${JSON.stringify({
            answer: 'FPT dat ROE 28% [C1].',
            metadata: {
              mode: 'RESEARCH_CHAT_V2',
              chat_mode: 'MOAT',
              primary_ticker: 'FPT',
              expanded_tickers: ['FPT', 'CMG'],
              as_of_date: '2026-11-24',
              citations: [
                { citation_id: 'C1', ticker: 'FPT', document_version_id: 'doc-ver-1', verified: true },
              ],
              screening_results: [{ ticker: 'FPT', sector: 'Công nghệ' }],
              graph_hops: [{ layer: 'MACRO', src_label: 'USD/VND', dst_label: 'FPT', effective_from: '2026-04-01' }],
              declared_blindspots: [{ code: 'NO_QUARTERLY_NOTES', reason: 'Missing Q3', scope: 'FPT' }],
              telemetry: { hallucinated_memory_ids_count: 0 },
            },
          })}\n\n`,
        ]),
        { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
      );
    },
  });

  // Direct guard check: incognito and read_only make 0 outbound calls
  const skipIncog = await service.enqueueMemoryIngestTurn('user-1', {
    session_id: 'sess-1',
    parent_session_id: null,
    session_title: 'Test',
    chat_mode: 'GENERAL',
    user_message_id: 'u1',
    assistant_message_id: 'a1',
    user_content: 'Ghi nho P/E < 15x',
    assistant_content: 'Da hieu',
    turn_index: 1,
    turn_as_of_date: '2026-11-24',
    memory_mode: 'incognito',
    tickers: [],
    sectors: [],
    macro_factors: [],
    cited_doc_version_ids: [],
    blindspot_codes: [],
    stage7_hallucinated: false,
    stream_interrupted: false,
  });
  const skipReadOnly = await service.enqueueMemoryIngestTurn('user-1', {
    session_id: 'sess-1',
    parent_session_id: null,
    session_title: 'Test',
    chat_mode: 'GENERAL',
    user_message_id: 'u1',
    assistant_message_id: 'a1',
    user_content: 'Ghi nho P/E < 15x',
    assistant_content: 'Da hieu',
    turn_index: 1,
    turn_as_of_date: '2026-11-24',
    memory_mode: 'read_only',
    tickers: [],
    sectors: [],
    macro_factors: [],
    cited_doc_version_ids: [],
    blindspot_codes: [],
    stage7_hallucinated: false,
    stream_interrupted: false,
  });
  assert.equal(skipIncog, false);
  assert.equal(skipReadOnly, false);
  assert.equal(ingestCalls.length, 0);

  // Stream with read_only & incognito must NOT fire ingest-turn
  const parentSess = await service.createSession('user-1', { title: 'Phien goc FPT', ticker: 'FPT' });
  await service.streamResearchChat(
    'user-1',
    { query: 'Test read_only', memoryMode: 'read_only', ticker: 'FPT' },
    createMockSseResponse(),
  );
  await service.streamResearchChat(
    'user-1',
    { query: 'Test incognito', memoryMode: 'incognito', ticker: 'FPT' },
    createMockSseResponse(),
  );
  await new Promise((r) => setImmediate(r));
  assert.equal(ingestCalls.length, 0);

  // Stream with auto + parentSessionId fires exact MemoryIngestTurnRequest after both msgs commit
  const autoRes = createMockSseResponse();
  await service.streamResearchChat(
    'user-1',
    {
      query: 'Tu nay toi chi loc ROE > 15%, xem FPT giup toi',
      ticker: 'FPT',
      mode: 'MOAT',
      asOfDate: '2026-11-24',
      memoryMode: 'auto',
      parentSessionId: parentSess.id,
    },
    autoRes,
  );
  await new Promise((r) => setImmediate(r));

  assert.equal(ingestCalls.length, 1);
  assert.equal(ingestCalls[0].url, 'http://sag-api:8000/api/v1/research-chat/memories/ingest-turn');
  assert.equal(ingestCalls[0].headers['X-Continuum-User-Id'], 'user-1');

  const body = ingestCalls[0].body;
  assert.ok(body.session_id);
  assert.equal(body.parent_session_id, parentSess.id);
  assert.equal(body.chat_mode, 'MOAT');
  assert.ok(body.user_message_id);
  assert.ok(body.assistant_message_id);
  assert.equal(body.user_content, 'Tu nay toi chi loc ROE > 15%, xem FPT giup toi');
  assert.equal(body.assistant_content, 'FPT dat ROE 28% [C1].');
  assert.equal(body.turn_index, 1);
  assert.equal(body.turn_as_of_date, '2026-11-24');
  assert.equal(body.memory_mode, 'auto');
  assert.deepEqual(body.tickers, ['FPT', 'CMG']);
  assert.deepEqual(body.sectors, ['Công nghệ']);
  assert.deepEqual(body.macro_factors, ['USD/VND']);
  assert.deepEqual(body.cited_doc_version_ids, ['doc-ver-1']);
  assert.deepEqual(body.blindspot_codes, ['NO_QUARTERLY_NOTES']);
  assert.equal(body.stage7_hallucinated, false);
  assert.equal(body.stream_interrupted, false);
});

test('deleteResearchSession enforces IDOR ownership, writes durable cascade outbox, survives SAG outage, and flushes cleanly on retry', async () => {
  resetMocks();
  let sagOnline = true;
  const cascadeCalls = [];

  const service = new SagResearchChatService({
    prismaClient: mockPrisma,
    sagApiUrl: 'http://sag-api:8000',
    sagBffServiceToken: 'secret-token',
    fetchImpl: async (url, init) => {
      cascadeCalls.push({ url: String(url), headers: init.headers, body: JSON.parse(init.body) });
      if (!sagOnline) {
        throw new Error('ECONNREFUSED sag-api:8000');
      }
      return new Response(
        JSON.stringify({ session_id: JSON.parse(init.body).session_id, tombstoned: true, retracted_memories: 3 }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    },
  });

  const s1 = await service.createSession('user-1', { title: 'FPT Session 1' });
  const s2 = await service.createSession('user-1', { title: 'HPG Session 2' });
  await mockPrisma.chatMessage.create({ data: { sessionId: s1.id, role: 'user', content: 'Hi FPT' } });
  await mockPrisma.chatMessage.create({ data: { sessionId: s2.id, role: 'user', content: 'Hi HPG' } });

  // 1. IDOR guard: user-2 CANNOT delete user-1's session
  await assert.rejects(
    () => service.deleteResearchSession('user-2', s1.id),
    (err) => err instanceof SagGatewayError && err.statusCode === 404 && err.code === 'SESSION_NOT_FOUND',
  );
  assert.equal(mockState.sessions.length, 2);
  assert.equal(mockState.cascadeOutbox.length, 0);
  assert.equal(cascadeCalls.length, 0);

  // 2. SAG online: deletes session + messages, creates outbox row and marks SENT
  const del1 = await service.deleteResearchSession('user-1', s1.id);
  assert.equal(del1.deleted, true);
  assert.equal(del1.sessionId, s1.id);
  assert.equal(del1.cascadeStatus, 'SENT');
  assert.equal(mockState.sessions.some((s) => s.id === s1.id), false);
  assert.equal(mockState.messages.some((m) => m.sessionId === s1.id), false);
  assert.equal(mockState.cascadeOutbox.length, 1);
  assert.equal(mockState.cascadeOutbox[0].status, 'SENT');
  assert.equal(mockState.cascadeOutbox[0].attempts, 1);
  assert.equal(mockState.cascadeOutbox[0].lastError, null);
  assert.equal(cascadeCalls[0].url, 'http://sag-api:8000/api/v1/research-chat/memories/cascade-delete-session');
  assert.equal(cascadeCalls[0].headers['X-Continuum-User-Id'], 'user-1');

  // 3. SAG outage: delete STILL succeeds for user, outbox row remains PENDING with error & attempts=1
  sagOnline = false;
  const del2 = await service.deleteResearchSession('user-1', s2.id);
  assert.equal(del2.deleted, true);
  assert.equal(del2.cascadeStatus, 'PENDING');
  assert.equal(mockState.sessions.length, 0);
  assert.equal(mockState.cascadeOutbox.length, 2);
  assert.equal(mockState.cascadeOutbox[1].status, 'PENDING');
  assert.equal(mockState.cascadeOutbox[1].attempts, 1);
  assert.match(mockState.cascadeOutbox[1].lastError, /ECONNREFUSED/);

  // 4. SAG recovers -> flushPendingCascadeOutbox retries PENDING row and marks SENT
  sagOnline = true;
  const flushRes = await service.flushPendingCascadeOutbox('user-1');
  assert.deepEqual(flushRes, { processed: 1, sent: 1, failed: 0 });
  assert.equal(mockState.cascadeOutbox[1].status, 'SENT');
  assert.equal(mockState.cascadeOutbox[1].attempts, 2);
  assert.equal(mockState.cascadeOutbox[1].lastError, null);

  // Module-level flushPendingCascadeOutbox export is callable
  assert.equal(typeof flushPendingCascadeOutbox, 'function');
});

test('Memory CRUD & Related Sessions Express routes validate inputs, enforce IDOR ownership, and forward X-Continuum-User-Id', async () => {
  resetMocks();
  const getRoute = (path, method) =>
    aiRouter.stack.find((l) => l.route?.path === path && l.route.methods[method])?.route;

  const deleteSessionRoute = getRoute('/research-chat/sessions/:id', 'delete');
  const relatedSessionsRoute = getRoute('/research-chat/sessions/:id/related', 'get');
  const listMemoriesRoute = getRoute('/research-chat/memories', 'get');
  const patchMemoryRoute = getRoute('/research-chat/memories/:memoryId', 'patch');
  const deleteMemoryRoute = getRoute('/research-chat/memories/:memoryId', 'delete');
  const clearMemoriesRoute = getRoute('/research-chat/memories/clear', 'post');

  assert.ok(
    deleteSessionRoute &&
      relatedSessionsRoute &&
      listMemoriesRoute &&
      patchMemoryRoute &&
      deleteMemoryRoute &&
      clearMemoriesRoute,
  );

  // Invalid memoryId on PATCH /memories/:memoryId -> Zod 400
  let capturedErr = null;
  await patchMemoryRoute.stack.at(-1).handle(
    { params: { memoryId: '../bad/id' }, body: { pinned_by_user: true }, userId: 'user-uuid-1' },
    createMockSseResponse(),
    (err) => {
      capturedErr = err;
    },
  );
  assert.ok(capturedErr);
  const errRes1 = { status(c) { this.code = c; return this; }, json(b) { this.body = b; } };
  errorHandler(capturedErr, {}, errRes1, () => {});
  assert.equal(errRes1.code, 400);

  // Empty PATCH body -> Zod 400
  capturedErr = null;
  await patchMemoryRoute.stack.at(-1).handle(
    { params: { memoryId: 'mem-123' }, body: {}, userId: 'user-uuid-1' },
    createMockSseResponse(),
    (err) => {
      capturedErr = err;
    },
  );
  assert.ok(capturedErr);
  const errRes2 = { status(c) { this.code = c; return this; }, json(b) { this.body = b; } };
  errorHandler(capturedErr, {}, errRes2, () => {});
  assert.equal(errRes2.code, 400);

  // Service-level proxy + IDOR test on getRelatedSessions and memory CRUD
  const sagCalls = [];
  const service = new SagResearchChatService({
    prismaClient: mockPrisma,
    sagApiUrl: 'http://sag-api:8000',
    sagBffServiceToken: 'secret-token',
    fetchImpl: async (url, init) => {
      sagCalls.push({ url: String(url), method: init.method, headers: init.headers, body: init.body });
      return new Response(JSON.stringify({ ok: true, items: [], total: 0, related_sessions: [] }), { status: 200 });
    },
  });

  const ownedSession = await service.createSession('user-1', { title: 'Owned' });

  // Non-owner MUST get 404 without calling SAG
  await assert.rejects(
    () => service.getRelatedSessions('user-2', ownedSession.id),
    (err) => err instanceof SagGatewayError && err.statusCode === 404,
  );
  assert.equal(sagCalls.length, 0);

  await service.getRelatedSessions('user-1', ownedSession.id, 5);
  await service.listUserMemories('user-1', { status: 'stale', ticker: 'fpt', limit: 20 });
  await service.patchUserMemory('user-1', 'mem-1', { content_text: 'Trinh bay ngan gon', pinned_by_user: true });
  await service.deleteUserMemory('user-1', 'mem-1');
  await service.clearAllUserMemories('user-1');

  assert.equal(sagCalls.length, 5);
  assert.equal(sagCalls[0].url, `http://sag-api:8000/api/v1/research-chat/sessions/${ownedSession.id}/related?limit=5`);
  assert.equal(sagCalls[0].headers['X-Continuum-User-Id'], 'user-1');
  assert.equal(sagCalls[1].url, 'http://sag-api:8000/api/v1/research-chat/memories?ticker=FPT&status=stale&limit=20');
  assert.equal(sagCalls[2].method, 'PATCH');
  assert.equal(sagCalls[2].url, 'http://sag-api:8000/api/v1/research-chat/memories/mem-1');
  assert.deepEqual(JSON.parse(sagCalls[2].body), { content_text: 'Trinh bay ngan gon', pinned_by_user: true });
  assert.equal(sagCalls[3].method, 'DELETE');
  assert.equal(sagCalls[3].url, 'http://sag-api:8000/api/v1/research-chat/memories/mem-1');
  assert.equal(sagCalls[4].method, 'POST');
  assert.equal(sagCalls[4].url, 'http://sag-api:8000/api/v1/research-chat/memories/clear');
});

test('Wave 2 hardening: strict date validation, Prisma.Decimal snapshot, Gate G3 unreviewed hallucination, outbox concurrency & retry cap, and aiLimiter CRUD exemption', async () => {
  resetMocks();

  // 1. Strict date validation: reject trailing garbage (fail-closed as historical backtest)
  const refNow = new Date('2026-11-24T10:00:00Z');
  assert.equal(toValidIsoDate('2026-11-24'), '2026-11-24');
  assert.equal(toValidIsoDate('2026-11-24T10:00:00Z'), '2026-11-24');
  assert.equal(toValidIsoDate('2026-11-24garbage'), null);
  assert.equal(toValidIsoDate('   '), null);
  assert.equal(isHistoricalBacktestDate('2026-11-24garbage', refNow), true);

  // Real Prisma.Decimal coercion on avgPrice + symbol clamp
  mockState.positions.push({
    userId: 'user-1',
    symbol: 'fpt',
    quantity: 200,
    avgPrice: new Prisma.Decimal('128500.50'),
  });
  const baseSvc = new SagResearchChatService({
    prismaClient: mockPrisma,
    sagBffServiceToken: 'secret-token',
  });
  const decSnap = await baseSvc.buildLiveUserPortfolioSnapshot('user-1', '2026-11-24', 'auto', refNow);
  assert.ok(decSnap);
  assert.deepEqual(decSnap.positions, [{ symbol: 'FPT', quantity: 200, avg_cost: 128500.5 }]);

  // 2. Gate G3 survivorship-bias guard: 1 clean PASS feedback CANNOT mask an unreviewed HALLUCINATED_MEMORY_ID turn
  const sessG3 = await baseSvc.createSession('user-1', { ticker: 'FPT', asOfDate: '2026-06-30' });
  const cleanMsg = await mockPrisma.chatMessage.create({
    data: {
      sessionId: sessG3.id,
      role: 'assistant',
      content: 'Clean [C1]',
      type: 'research_chat_v2',
      metadata: { mode: 'RESEARCH_CHAT_V2', eval_eligible: true, as_of_date: '2026-06-30', citations: [{ verified: true }] },
    },
  });
  await mockPrisma.chatMessage.create({
    data: {
      sessionId: sessG3.id,
      role: 'assistant',
      content: 'Bad [MEM:M99 UNVERIFIED_CLAIM]',
      type: 'research_chat_v2',
      metadata: {
        mode: 'RESEARCH_CHAT_V2',
        eval_eligible: true,
        as_of_date: '2026-06-30',
        declared_blindspots: [{ code: 'HALLUCINATED_MEMORY_ID', reason: 'M99 invalid', scope: 'MEM:M99' }],
        telemetry: { eval_eligible: true, hallucinated_memory_ids_count: 1 },
      },
    },
  });
  await baseSvc.submitFeedback('user-1', { messageId: cleanMsg.id, verdict: 'PASS', hallucinationFlag: false });
  const g3Summary = await baseSvc.getEvalSummary('user-1');
  assert.equal(g3Summary.autoHallucinationCount, 1);
  assert.equal(g3Summary.gates.g3AbstentionComplianceRate, 0.5);
  assert.equal(g3Summary.readyForPhase2, false);

  // 3. Concurrent outbox flush dedup + SENT->PENDING protection + FAILED cap at 20 attempts
  let releaseFetch = null;
  let fetchCount = 0;
  const slowSvc = new SagResearchChatService({
    prismaClient: mockPrisma,
    sagApiUrl: 'http://sag-api:8000',
    sagBffServiceToken: 'secret-token',
    fetchImpl: async () => {
      fetchCount += 1;
      await new Promise((resolve) => {
        releaseFetch = resolve;
      });
      return new Response(JSON.stringify({ tombstoned: true }), { status: 200 });
    },
  });
  await mockPrisma.sagMemoryCascadeOutbox.create({
    data: { sessionId: 'sess-concurrent', userId: 'user-1', status: 'PENDING', attempts: 1 },
  });
  const p1 = slowSvc.flushPendingCascadeOutbox('user-1');
  const p2 = slowSvc.flushPendingCascadeOutbox('user-1');
  // Let microtasks run so p1 enters fetchImpl and p2 skips the in-flight row
  await new Promise((r) => setImmediate(r));
  assert.equal(fetchCount, 1);
  releaseFetch();
  const [r1, r2] = await Promise.all([p1, p2]);
  assert.deepEqual(r1, { processed: 1, sent: 1, failed: 0 });
  assert.deepEqual(r2, { processed: 0, sent: 0, failed: 0 });
  assert.equal(mockState.cascadeOutbox[0].status, 'SENT');

  // Outbox row at attempt 19 failing -> transitions to FAILED on attempt 20
  const failingSvc = new SagResearchChatService({
    prismaClient: mockPrisma,
    sagApiUrl: 'http://sag-api:8000',
    sagBffServiceToken: 'secret-token',
    fetchImpl: async () => new Response('down', { status: 503 }),
  });
  const exhaustedRow = await mockPrisma.sagMemoryCascadeOutbox.create({
    data: { sessionId: 'sess-exhausted', userId: 'user-1', status: 'PENDING', attempts: 19 },
  });
  await failingSvc.flushPendingCascadeOutbox('user-1');
  const updatedExhausted = mockState.cascadeOutbox.find((r) => r.id === exhaustedRow.id);
  assert.equal(updatedExhausted.status, 'FAILED');
  assert.equal(updatedExhausted.attempts, 20);

  // 4. Atomic double-delete returns 404 on second call without crashing 500 or duplicating outbox
  const ownedForDel = await failingSvc.createSession('user-1', { title: 'Double delete test' });
  const delFirst = await failingSvc.deleteResearchSession('user-1', ownedForDel.id);
  assert.equal(delFirst.deleted, true);
  await assert.rejects(
    () => failingSvc.deleteResearchSession('user-1', ownedForDel.id),
    (err) => err instanceof SagGatewayError && err.statusCode === 404 && err.code === 'SESSION_NOT_FOUND',
  );

  // 5. aiLimiter exempts lightweight memory CRUD & session DELETE while keeping POST /stream capped at 10/min
  const invokeLimiter = (method, path, originalUrl, ip) =>
    new Promise((resolve) => {
      const req = { method, path, originalUrl, ip, headers: {}, app: { get: () => false } };
      const res = {
        statusCode: 200,
        headers: {},
        setHeader(k, v) { this.headers[k.toLowerCase()] = v; },
        status(code) { this.statusCode = code; return this; },
        send(body) { resolve({ blocked: true, statusCode: this.statusCode, body }); },
        json(body) { resolve({ blocked: true, statusCode: this.statusCode, body }); },
      };
      aiLimiter(req, res, () => resolve({ blocked: false }));
    });

  for (let i = 0; i < 15; i += 1) {
    const patchOut = await invokeLimiter('PATCH', '/research-chat/memories/m1', '/api/v1/ai/research-chat/memories/m1', '10.0.0.99');
    const delMemOut = await invokeLimiter('DELETE', '/research-chat/memories/m1', '/api/v1/ai/research-chat/memories/m1', '10.0.0.99');
    const clearOut = await invokeLimiter('POST', '/research-chat/memories/clear', '/api/v1/ai/research-chat/memories/clear', '10.0.0.99');
    const delSessOut = await invokeLimiter('DELETE', '/research-chat/sessions/abc', '/api/v1/ai/research-chat/sessions/abc', '10.0.0.99');
    assert.equal(patchOut.blocked, false);
    assert.equal(delMemOut.blocked, false);
    assert.equal(clearOut.blocked, false);
    assert.equal(delSessOut.blocked, false);
  }
  // Stream on the same IP still has its full 10/min budget and blocks on 11th request
  for (let i = 0; i < 10; i += 1) {
    const sOut = await invokeLimiter('POST', '/research-chat/stream', '/api/v1/ai/research-chat/stream', '10.0.0.99');
    assert.equal(sOut.blocked, false);
  }
  const streamBlocked = await invokeLimiter('POST', '/research-chat/stream', '/api/v1/ai/research-chat/stream', '10.0.0.99');
  assert.equal(streamBlocked.blocked, true);
  assert.equal(streamBlocked.statusCode, 429);

  // 6. Express POST /research-chat/stream rejects invalid memoryMode with 400
  const streamRoute = aiRouter.stack.find(
    (l) => l.route?.path === '/research-chat/stream' && l.route.methods.post,
  )?.route;
  assert.ok(streamRoute);
  let streamErr = null;
  await streamRoute.stack.at(-1).handle(
    { body: { query: 'Test', memoryMode: 'invalid_mode' }, userId: 'user-uuid-1' },
    createMockSseResponse(),
    (err) => { streamErr = err; },
  );
  assert.ok(streamErr);
  const streamErrRes = { status(c) { this.code = c; return this; }, json(b) { this.body = b; } };
  errorHandler(streamErr, {}, streamErrRes, () => {});
  assert.equal(streamErrRes.code, 400);
});
