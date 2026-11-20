const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');

// Mock Prisma & auth before importing compiled modules
const mockState = {
  sessions: [],
  messages: [],
  feedbacks: [],
  nextId: 1,
};

function resetMocks() {
  mockState.sessions = [];
  mockState.messages = [];
  mockState.feedbacks = [];
  mockState.nextId = 1;
}

const mockPrisma = {
  chatSession: {
    create: async ({ data }) => {
      const row = {
        id: `00000000-0000-4000-8000-${String(mockState.nextId++).padStart(12, '0')}`,
        userId: data.userId,
        title: data.title ?? null,
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
    findMany: async ({ where }) => {
      return mockState.messages.filter((m) => {
        if (where.sessionId && m.sessionId !== where.sessionId) return false;
        if (where.role && m.role !== where.role) return false;
        if (where.session?.userId) {
          const sess = mockState.sessions.find((s) => s.id === m.sessionId);
          if (!sess || sess.userId !== where.session.userId) return false;
        }
        return true;
      });
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
} = require('../dist/services/sagResearchChat.service');
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

test('SAG research timeout is 60s (bypasses 800ms aiEngineService) and BFF headers handle single vs multi-ticker', () => {
  assert.equal(SAG_RESEARCH_TIMEOUT_MS, 60_000);

  const service = new SagResearchChatService({
    prismaClient: mockPrisma,
    sagApiUrl: 'http://sag-api:8000',
    sagBffServiceToken: 'test-bff-token',
  });

  const single = service.buildBffHeaders('user-1', 'pvt');
  assert.equal(single['X-SAG-BFF-Service-Token'], 'test-bff-token');
  assert.equal(single['X-Continuum-User-Id'], 'user-1');
  assert.equal(single['X-Continuum-Organization-Id'], 'aiinvest-default');
  assert.equal(single['X-Continuum-Project-Id'], 'PVT');
  assert.equal(single['X-Continuum-Security-Partition'], 'PVT');
  assert.equal(single['X-Continuum-Allowed-Project-Ids'], undefined);

  const multi = service.buildBffHeaders('user-1', 'pvt', ['pvp', 'vto', 'PVT']);
  assert.equal(multi['X-Continuum-Project-Id'], 'all');
  assert.equal(multi['X-Continuum-Security-Partition'], 'all');
  assert.equal(multi['X-Continuum-Allowed-Project-Ids'], 'PVT,PVP,VTO');
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
  assert.equal(capturedRequest.init.headers['X-Continuum-Allowed-Project-Ids'], 'PVT,PVP');

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
