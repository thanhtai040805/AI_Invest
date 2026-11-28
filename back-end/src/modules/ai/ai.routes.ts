import { Router, Request, Response, NextFunction } from 'express';
import { z } from 'zod';
import { config } from '../../config';
import { aiEngineService } from '../../services/aiEngine.service';
import {
  SagGatewayError,
  sagResearchChatService,
} from '../../services/sagResearchChat.service';
import { authMiddleware, AuthRequest } from '../../middleware/auth';
import { cached } from '../../utils/cache';
import prisma from '../../config/database';

const router = Router();

const tickerRegex = /^[A-Za-z0-9.-]{1,16}$/;
const tableIdRegex = /^[A-Za-z0-9_-]{1,64}$/;
const memoryIdRegex = /^[A-Za-z0-9_-]{1,128}$/;

const chatModeSchema = z.enum(['GENERAL', 'MOAT']);
const memoryModeSchema = z.enum(['auto', 'read_only', 'incognito']);
const memoryTypeSchema = z.enum([
  'STYLE_PREF_SAFE',
  'USER_INVESTING_CRITERIA',
  'USER_EXTERNAL_HOLDING',
  'INVESTMENT_THESIS',
  'SESSION_EPISODE_DIGEST',
]);
const memoryStatusSchema = z.enum(['active', 'stale', 'superseded', 'all']);

const createResearchSessionSchema = z
  .object({
    ticker: z.string().trim().regex(tickerRegex).optional().nullable(),
    title: z.string().trim().min(1).max(200).optional(),
    mode: chatModeSchema.optional(),
    asOfDate: z.string().date().optional(),
    as_of_date: z.string().date().optional(),
    enableKg: z.boolean().optional(),
    enable_kg: z.boolean().optional(),
    parentSessionId: z.string().uuid().optional(),
    parent_session_id: z.string().uuid().optional(),
  })
  .transform((val) => ({
    ticker: val.ticker ?? undefined,
    title: val.title,
    mode: val.mode,
    asOfDate: val.asOfDate ?? val.as_of_date,
    enableKg: val.enableKg ?? val.enable_kg,
    parentSessionId: val.parentSessionId ?? val.parent_session_id,
  }));

const streamResearchChatSchema = z
  .object({
    sessionId: z.string().uuid().optional(),
    session_id: z.string().uuid().optional(),
    parentSessionId: z.string().uuid().optional(),
    parent_session_id: z.string().uuid().optional(),
    query: z.string().trim().min(1).max(4000),
    ticker: z.string().trim().regex(tickerRegex).optional().nullable(),
    compareTickers: z.array(z.string().trim().regex(tickerRegex)).max(5).optional(),
    compare_tickers: z.array(z.string().trim().regex(tickerRegex)).max(5).optional(),
    mode: chatModeSchema.optional(),
    asOfDate: z.string().date().optional(),
    as_of_date: z.string().date().optional(),
    enableKg: z.boolean().optional(),
    enable_kg: z.boolean().optional(),
    memoryMode: memoryModeSchema.optional(),
    memory_mode: memoryModeSchema.optional(),
  })
  .transform((val) => ({
    sessionId: val.sessionId ?? val.session_id,
    parentSessionId: val.parentSessionId ?? val.parent_session_id,
    query: val.query,
    ticker: val.ticker ?? undefined,
    compareTickers: val.compareTickers ?? val.compare_tickers,
    mode: val.mode,
    asOfDate: val.asOfDate ?? val.as_of_date,
    enableKg: val.enableKg ?? val.enable_kg,
    memoryMode: val.memoryMode ?? val.memory_mode,
  }));

const listUserMemoriesQuerySchema = z
  .object({
    memoryType: memoryTypeSchema.optional(),
    memory_type: memoryTypeSchema.optional(),
    ticker: z.string().trim().regex(tickerRegex).optional(),
    status: memoryStatusSchema.optional(),
    limit: z.coerce.number().int().min(1).max(200).optional(),
    offset: z.coerce.number().int().min(0).optional(),
  })
  .transform((val) => ({
    memoryType: val.memoryType ?? val.memory_type,
    ticker: val.ticker,
    status: val.status,
    limit: val.limit,
    offset: val.offset,
  }));

const patchUserMemorySchema = z
  .object({
    contentText: z.string().trim().min(1).max(2000).optional(),
    content_text: z.string().trim().min(1).max(2000).optional(),
    pinnedByUser: z.boolean().optional(),
    pinned_by_user: z.boolean().optional(),
    reason: z.string().trim().min(1).max(256).optional(),
  })
  .refine(
    (val) =>
      (val.contentText ?? val.content_text) !== undefined ||
      (val.pinnedByUser ?? val.pinned_by_user) !== undefined,
    {
      message: 'At least one of content_text or pinned_by_user must be provided',
    },
  )
  .transform((val) => ({
    content_text: val.contentText ?? val.content_text,
    pinned_by_user: val.pinnedByUser ?? val.pinned_by_user,
    reason: val.reason,
  }));

const relatedSessionsQuerySchema = z.object({
  limit: z.coerce.number().int().min(1).max(20).optional(),
});

const researchFeedbackSchema = z
  .object({
    messageId: z.string().uuid().optional(),
    message_id: z.string().uuid().optional(),
    verdict: z.enum(['PASS', 'NEEDS_FIX', 'FAIL']),
    citationOk: z.boolean().optional(),
    citation_ok: z.boolean().optional(),
    tableCalcOk: z.boolean().optional(),
    table_calc_ok: z.boolean().optional(),
    graphContextOk: z.boolean().optional(),
    graph_context_ok: z.boolean().optional(),
    hallucinationFlag: z.boolean().optional(),
    hallucination_flag: z.boolean().optional(),
    notes: z.string().max(2000).optional(),
  })
  .refine((val) => Boolean(val.messageId ?? val.message_id), {
    message: 'messageId is required',
    path: ['messageId'],
  })
  .transform((val) => ({
    messageId: (val.messageId ?? val.message_id)!,
    verdict: val.verdict,
    citationOk: val.citationOk ?? val.citation_ok,
    tableCalcOk: val.tableCalcOk ?? val.table_calc_ok,
    graphContextOk: val.graphContextOk ?? val.graph_context_ok,
    hallucinationFlag: val.hallucinationFlag ?? val.hallucination_flag,
    notes: val.notes,
  }));

function handleGatewayError(err: unknown, res: Response, next: NextFunction): void {
  if (err instanceof SagGatewayError) {
    res.status(err.statusCode).json({ error: err.message, code: err.code });
    return;
  }
  next(err);
}

const backtestSchema = z.object({
  symbol: z.string().trim().regex(/^[A-Za-z0-9.-]{1,16}$/),
  strategy: z.enum(['sma_cross', 'rsi', 'bollinger']),
  startDate: z.string().date(),
  endDate: z.string().date(),
  params: z.record(z.string(), z.unknown()).optional(),
  capital: z.coerce.number().finite().positive().optional(),
});

router.post('/backtest', authMiddleware, async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const body = backtestSchema.parse(req.body);
    const result = await aiEngineService.submitBacktest({
      symbol: body.symbol.toUpperCase(),
      start_date: body.startDate,
      end_date: body.endDate,
      strategy_config: { type: body.strategy, params: body.params ?? {} },
      initial_capital: body.capital,
      source: 'auto',
      user_id: req.userId!,
    });
    res.status(202).json(result);
  } catch (err) {
    next(err);
  }
});

router.get('/backtest/history', authMiddleware, async (req: AuthRequest, res, next) => {
  try {
    const result = await aiEngineService.getBacktestHistory(req.userId!);
    res.json(result);
  } catch (err) {
    next(err);
  }
});

router.get('/backtest/:id/results', authMiddleware, async (req: AuthRequest, res, next) => {
  try {
    if (!/^vn_[A-Z0-9.-]{1,16}_\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}(?:_[a-f0-9]{32})?$/.test(req.params.id)) {
      return res.status(400).json({ error: 'Invalid backtest run id' });
    }
    const result = await aiEngineService.getBacktestResults(req.params.id, req.userId!);
    res.json(result);
  } catch (err) {
    next(err);
  }
});

router.get('/backtest/:id/status', authMiddleware, async (req: AuthRequest, res, next) => {
  try {
    if (!/^vn_[A-Z0-9.-]{1,16}_\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}(?:_[a-f0-9]{32})?$/.test(req.params.id)) {
      return res.status(400).json({ error: 'Invalid backtest run id' });
    }
    const result = await aiEngineService.getBacktestStatus(req.params.id, req.userId!);
    res.json(result);
  } catch (err) {
    next(err);
  }
});

router.get('/pipeline/status', authMiddleware, async (_req, res, next) => {
  try {
    const status = await aiEngineService.getDailyPipelineStatus();
    res.json(status);
  } catch (_err) {
    res.json({ status: 'STANDBY', message: 'Autonomous pipeline daemon standby' });
  }
});

router.get('/sessions', authMiddleware, async (req: AuthRequest, res, next) => {
  try {
    const sessions = await prisma.chatSession.findMany({
      where: { userId: req.userId! },
      orderBy: { createdAt: 'desc' },
      include: { messages: { take: 1, orderBy: { createdAt: 'desc' } } },
    });
    res.json(sessions);
  } catch (err) {
    next(err);
  }
});

// ── Phase 1 Stock Research Chat (SAG v2 SSE Gateway) ─────────────────────────

router.post('/research-chat/sessions', authMiddleware, async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const body = createResearchSessionSchema.parse(req.body);
    const session = await sagResearchChatService.createSession(req.userId!, body);
    res.status(201).json(session);
  } catch (err) {
    handleGatewayError(err, res, next);
  }
});

router.get('/research-chat/sessions', authMiddleware, async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const sessions = await sagResearchChatService.listSessions(req.userId!);
    res.json({ sessions });
  } catch (err) {
    handleGatewayError(err, res, next);
  }
});

router.get('/research-chat/sessions/:id', authMiddleware, async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const sessionId = z.string().uuid().parse(req.params.id);
    const detail = await sagResearchChatService.getSession(req.userId!, sessionId);
    res.json(detail);
  } catch (err) {
    handleGatewayError(err, res, next);
  }
});

router.delete('/research-chat/sessions/:id', authMiddleware, async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const sessionId = z.string().uuid().parse(req.params.id);
    const result = await sagResearchChatService.deleteResearchSession(req.userId!, sessionId);
    res.json(result);
  } catch (err) {
    handleGatewayError(err, res, next);
  }
});

router.get('/research-chat/sessions/:id/related', authMiddleware, async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const sessionId = z.string().uuid().parse(req.params.id);
    const query = relatedSessionsQuerySchema.parse(req.query ?? {});
    const data = await sagResearchChatService.getRelatedSessions(req.userId!, sessionId, query.limit);
    res.json(data);
  } catch (err) {
    handleGatewayError(err, res, next);
  }
});

router.get('/research-chat/memories', authMiddleware, async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const query = listUserMemoriesQuerySchema.parse(req.query ?? {});
    const data = await sagResearchChatService.listUserMemories(req.userId!, query);
    res.json(data);
  } catch (err) {
    handleGatewayError(err, res, next);
  }
});

router.post('/research-chat/memories/clear', authMiddleware, async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const data = await sagResearchChatService.clearAllUserMemories(req.userId!);
    res.json(data);
  } catch (err) {
    handleGatewayError(err, res, next);
  }
});

router.patch('/research-chat/memories/:memoryId', authMiddleware, async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const memoryId = z.string().trim().regex(memoryIdRegex).parse(req.params.memoryId);
    const body = patchUserMemorySchema.parse(req.body);
    const data = await sagResearchChatService.patchUserMemory(req.userId!, memoryId, body);
    res.json(data);
  } catch (err) {
    handleGatewayError(err, res, next);
  }
});

router.delete('/research-chat/memories/:memoryId', authMiddleware, async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const memoryId = z.string().trim().regex(memoryIdRegex).parse(req.params.memoryId);
    const data = await sagResearchChatService.deleteUserMemory(req.userId!, memoryId);
    res.json(data);
  } catch (err) {
    handleGatewayError(err, res, next);
  }
});

router.post('/research-chat/stream', authMiddleware, async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const body = streamResearchChatSchema.parse(req.body);
    await sagResearchChatService.streamResearchChat(req.userId!, body, res);
  } catch (err) {
    if (!res.headersSent) {
      handleGatewayError(err, res, next);
    } else if (!res.writableEnded) {
      res.end();
    }
  }
});

router.post('/research-chat/feedback', authMiddleware, async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const body = researchFeedbackSchema.parse(req.body);
    const feedback = await sagResearchChatService.submitFeedback(req.userId!, body);
    res.status(201).json({ feedback });
  } catch (err) {
    handleGatewayError(err, res, next);
  }
});

router.get('/research-chat/eval-summary', authMiddleware, async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const summary = await sagResearchChatService.getEvalSummary(req.userId!);
    res.json(summary);
  } catch (err) {
    handleGatewayError(err, res, next);
  }
});

router.get('/research-chat/tickers', authMiddleware, async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const rawAsOfDate = req.query.asOfDate ?? req.query.as_of_date;
    const asOfDate = rawAsOfDate !== undefined ? z.string().date().parse(rawAsOfDate) : undefined;
    const data = await sagResearchChatService.getIngestedTickers(req.userId!, asOfDate);
    res.json(data);
  } catch (err) {
    handleGatewayError(err, res, next);
  }
});

router.get('/research-chat/kg/:ticker', authMiddleware, async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const ticker = z.string().trim().regex(tickerRegex).parse(req.params.ticker);
    const rawAsOfDate = req.query.asOfDate ?? req.query.as_of_date;
    const asOfDate = rawAsOfDate !== undefined ? z.string().date().parse(rawAsOfDate) : undefined;
    const data = await sagResearchChatService.getKgSubgraph(req.userId!, ticker, asOfDate);
    res.json(data);
  } catch (err) {
    handleGatewayError(err, res, next);
  }
});

router.get('/research-chat/tables/:tableId', authMiddleware, async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const tableId = z.string().trim().regex(tableIdRegex).parse(req.params.tableId);
    const rawTicker = req.query.ticker;
    const ticker = rawTicker !== undefined ? z.string().trim().regex(tickerRegex).parse(rawTicker) : undefined;
    const rawAsOfDate = req.query.asOfDate ?? req.query.as_of_date;
    const asOfDate = rawAsOfDate !== undefined ? z.string().date().parse(rawAsOfDate) : undefined;
    const data = await sagResearchChatService.getHydratedTable(req.userId!, tableId, ticker, asOfDate);
    res.json(data);
  } catch (err) {
    handleGatewayError(err, res, next);
  }
});

async function handle(
  req: Request,
  res: Response,
  next: NextFunction,
  fn: () => Promise<unknown>,
): Promise<void> {
  try {
    res.json(await fn());
  } catch (err) {
    next(err);
  }
}

export default router;
