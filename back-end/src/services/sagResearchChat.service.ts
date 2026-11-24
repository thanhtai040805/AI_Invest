import { Prisma, PrismaClient } from '@prisma/client';
import { Response } from 'express';
import { config } from '../config';
import prisma from '../config/database';

export const SAG_RESEARCH_TIMEOUT_MS = 150_000;
const MAX_COMPARE_TICKERS = 5;

export class SagGatewayError extends Error {
  statusCode: number;
  code: string;

  constructor(statusCode: number, code: string, message: string) {
    super(message);
    this.name = 'SagGatewayError';
    this.statusCode = statusCode;
    this.code = code;
  }
}

export type ResearchChatMode = 'GENERAL' | 'MOAT';

export interface CreateSessionInput {
  ticker?: string | null;
  title?: string;
  mode?: ResearchChatMode;
  asOfDate?: string;
  enableKg?: boolean;
}

export interface StreamResearchChatInput {
  sessionId?: string;
  query: string;
  ticker?: string | null;
  compareTickers?: string[];
  mode?: ResearchChatMode;
  asOfDate?: string;
  enableKg?: boolean;
}

export interface SubmitFeedbackInput {
  messageId: string;
  verdict: 'PASS' | 'NEEDS_FIX' | 'FAIL';
  citationOk?: boolean;
  tableCalcOk?: boolean;
  graphContextOk?: boolean;
  hallucinationFlag?: boolean;
  notes?: string;
}

export interface ResearchChatCitation {
  citation_id?: string;
  n?: number;
  ticker?: string;
  doc_id?: string;
  document_version_id?: string;
  sub_doc_slot?: string;
  page?: number;
  published_date?: string;
  quote?: string;
  quote_hash?: string;
  verified?: boolean;
  evidence_origin?: 'PRIMARY' | 'CROSS_REF';
  rejection_reason?: string | null;
  [key: string]: unknown;
}

export interface ResearchChatHydratedTable {
  table_id?: string;
  project_id?: string | null;
  title?: string;
  verification_status?: string;
  [key: string]: unknown;
}

export interface ResearchChatCalculatorTrace {
  calc_id?: string;
  formula?: string;
  inputs?: unknown;
  result?: unknown;
  formatted?: string;
  [key: string]: unknown;
}

export interface ResearchChatGraphHop {
  layer?: string;
  effective_from?: string;
  [key: string]: unknown;
}

export interface ResearchChatBlindspot {
  code?: string;
  reason?: string;
  scope?: string;
}

export interface ResearchChatMetadata {
  mode?: string;
  chat_mode?: ResearchChatMode;
  primary_ticker?: string;
  expanded_tickers?: string[];
  resolved_query?: string | null;
  sub_queries?: string[];
  as_of_date?: string | null;
  enable_kg?: boolean;
  citations?: ResearchChatCitation[];
  rejected_citations?: ResearchChatCitation[];
  hydrated_tables?: ResearchChatHydratedTable[];
  calculator_traces?: ResearchChatCalculatorTrace[];
  graph_hops?: ResearchChatGraphHop[];
  discrepancy_alerts?: unknown[];
  declared_blindspots?: Array<ResearchChatBlindspot | string>;
  pit_leak_count?: number;
  telemetry?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface SagResearchChatServiceDeps {
  prismaClient?: PrismaClient;
  fetchImpl?: typeof fetch;
  sagApiUrl?: string;
  sagBffServiceToken?: string;
  sagResearchOrgId?: string;
  nodeEnv?: string;
  timeoutMs?: number;
}

function normalizeTicker(ticker?: string | null): string {
  return (ticker ?? '').trim().toUpperCase();
}

function normalizeOptionalTicker(ticker?: string | null): string | null {
  const clean = normalizeTicker(ticker);
  return clean || null;
}

function extractSessionContextFromMessages(
  title: string | null | undefined,
  messages: Array<{ metadata?: unknown }>,
): { ticker: string | null; mode: ResearchChatMode; asOfDate: string | null; enableKg: boolean } {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const meta = messages[i]?.metadata;
    if (meta && typeof meta === 'object' && !Array.isArray(meta)) {
      const m = meta as Record<string, unknown>;
      const rawTicker = typeof m.primary_ticker === 'string' && m.primary_ticker.trim() ? m.primary_ticker.trim().toUpperCase() : null;
      const rawMode: ResearchChatMode = m.chat_mode === 'MOAT' ? 'MOAT' : 'GENERAL';
      if (rawTicker || m.as_of_date || typeof m.enable_kg === 'boolean' || m.chat_mode) {
        return {
          ticker: rawTicker,
          mode: rawMode,
          asOfDate: typeof m.as_of_date === 'string' ? m.as_of_date : null,
          enableKg: typeof m.enable_kg === 'boolean' ? m.enable_kg : true,
        };
      }
    }
  }
  const bracketMatch = title?.trim().match(/^\[([A-Z0-9.-]{2,10})\]/);
  const legacyMatch = title?.trim().match(/^([A-Z]{2,5})\s+(?:Research|·|:)/);
  return {
    ticker: bracketMatch ? bracketMatch[1].toUpperCase() : legacyMatch ? legacyMatch[1].toUpperCase() : null,
    mode: 'GENERAL',
    asOfDate: null,
    enableKg: true,
  };
}

function writeSseEvent(res: Response, event: string, data: unknown): void {
  if (res.writableEnded) return;
  res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
  const flushable = res as Response & { flush?: () => void };
  if (typeof flushable.flush === 'function') {
    flushable.flush();
  }
}

export function parseSseBlock(rawBlock: string): { event: string; data: string } | null {
  const lines = rawBlock.split('\n');
  let eventName = 'message';
  const dataLines: string[] = [];

  for (const rawLine of lines) {
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine;
    if (!line || line.startsWith(':')) continue;
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim() || 'message';
    } else if (line.startsWith('data:')) {
      dataLines.push(line.startsWith('data: ') ? line.slice(6) : line.slice(5));
    }
  }

  if (dataLines.length === 0) {
    return null;
  }

  return {
    event: eventName,
    data: dataLines.join('\n'),
  };
}

export class SagResearchChatService {
  private readonly prisma: PrismaClient;
  private readonly fetchImpl: typeof fetch;
  private readonly sagApiUrl: string;
  private readonly sagBffServiceToken: string;
  private readonly sagResearchOrgId: string;
  private readonly nodeEnv: string;
  private readonly timeoutMs: number;

  constructor(deps: SagResearchChatServiceDeps = {}) {
    this.prisma = deps.prismaClient ?? prisma;
    this.fetchImpl = deps.fetchImpl ?? fetch;
    this.sagApiUrl = (deps.sagApiUrl ?? config.sagApiUrl).replace(/\/+$/, '');
    this.sagBffServiceToken = deps.sagBffServiceToken ?? config.sagBffServiceToken;
    this.sagResearchOrgId = (deps.sagResearchOrgId ?? config.sagResearchOrgId).trim() || 'aiinvest-default';
    this.nodeEnv = deps.nodeEnv ?? config.nodeEnv;
    this.timeoutMs = deps.timeoutMs ?? SAG_RESEARCH_TIMEOUT_MS;
  }

  private assertServiceToken(): string {
    if (!this.sagBffServiceToken) {
      throw new SagGatewayError(
        503,
        'SAG_NOT_CONFIGURED',
        this.nodeEnv === 'production'
          ? 'Research chat gateway is not configured in production'
          : 'SAG_BFF_SERVICE_TOKEN is not configured',
      );
    }
    return this.sagBffServiceToken;
  }

  buildBffHeaders(
    userId: string,
    primaryTicker?: string | null,
    compareTickers: string[] = [],
  ): Record<string, string> {
    const token = this.assertServiceToken();
    // Phase 0 Contract: grant org-scoped read-only access (no single-ticker lock),
    // strictly isolated by X-Continuum-Organization-Id (tenant_id == organization_id).
    // Explicit ticker hints are passed in the request body for routing priority while
    // allowing free-form multi-ticker follow-ups and automatic True-Peer KG expansion.
    void primaryTicker;
    void compareTickers;
    return {
      'X-SAG-BFF-Service-Token': token,
      'X-Continuum-User-Id': userId,
      'X-Continuum-Organization-Id': this.sagResearchOrgId,
      'X-Continuum-Project-Id': 'all',
      'X-Continuum-Security-Partition': 'all',
      'X-Continuum-Allowed-Project-Ids': '*',
    };
  }

  async createSession(userId: string, input: CreateSessionInput = {}) {
    const ticker = normalizeOptionalTicker(input.ticker);
    const mode: ResearchChatMode = input.mode === 'MOAT' ? 'MOAT' : 'GENERAL';
    const title = input.title?.trim() || (ticker ? `${ticker} Research` : 'Cuộc trò chuyện nghiên cứu mới');
    const session = await this.prisma.chatSession.create({
      data: {
        userId,
        title,
      },
    });

    return {
      id: session.id,
      title: session.title,
      ticker,
      mode,
      asOfDate: input.asOfDate ?? null,
      enableKg: input.enableKg ?? true,
      createdAt: session.createdAt,
    };
  }

  async listSessions(userId: string) {
    const sessions = await this.prisma.chatSession.findMany({
      where: { userId },
      orderBy: { createdAt: 'desc' },
      take: 50,
      include: {
        messages: {
          orderBy: { createdAt: 'desc' },
          take: 5,
        },
        _count: {
          select: { messages: true },
        },
      },
    });

    return sessions.map((session) => {
      const ctx = extractSessionContextFromMessages(session.title, session.messages);
      const lastMsg = session.messages[0];
      return {
        id: session.id,
        title: session.title,
        ticker: ctx.ticker,
        mode: ctx.mode,
        asOfDate: ctx.asOfDate,
        enableKg: ctx.enableKg,
        messageCount: session._count.messages,
        lastMessagePreview: lastMsg ? lastMsg.content.slice(0, 160) : null,
        lastMessageAt: lastMsg?.createdAt ?? session.createdAt,
        createdAt: session.createdAt,
      };
    });
  }

  async getSession(userId: string, sessionId: string) {
    const session = await this.prisma.chatSession.findFirst({
      where: { id: sessionId, userId },
      include: {
        messages: {
          orderBy: { createdAt: 'asc' },
          include: {
            evalFeedbacks: {
              where: { userId },
              take: 1,
            },
          },
        },
      },
    });

    if (!session) {
      throw new SagGatewayError(404, 'SESSION_NOT_FOUND', 'Research chat session not found');
    }

    const ctx = extractSessionContextFromMessages(session.title, session.messages);

    return {
      session: {
        id: session.id,
        title: session.title,
        ticker: ctx.ticker,
        mode: ctx.mode,
        asOfDate: ctx.asOfDate,
        enableKg: ctx.enableKg,
        createdAt: session.createdAt,
      },
      messages: session.messages.map((msg) => ({
        id: msg.id,
        sessionId: msg.sessionId,
        role: msg.role,
        content: msg.content,
        type: msg.type,
        metadata: msg.metadata,
        createdAt: msg.createdAt,
        feedback: msg.evalFeedbacks[0] ?? null,
      })),
    };
  }

  async streamResearchChat(
    userId: string,
    input: StreamResearchChatInput,
    res: Response,
  ): Promise<void> {
    // Fail-closed before opening SSE stream if service token is missing
    const ticker = normalizeOptionalTicker(input.ticker);
    const chatMode: ResearchChatMode = input.mode === 'MOAT' ? 'MOAT' : 'GENERAL';
    const compareTickers = (input.compareTickers ?? [])
      .map(normalizeTicker)
      .filter((t) => t && t !== ticker)
      .slice(0, MAX_COMPARE_TICKERS);
    const bffHeaders = this.buildBffHeaders(userId, ticker, compareTickers);
    const enableKg = input.enableKg ?? true;

    let sessionId = input.sessionId;
    if (sessionId) {
      const existing = await this.prisma.chatSession.findFirst({
        where: { id: sessionId, userId },
      });
      if (!existing) {
        throw new SagGatewayError(404, 'SESSION_NOT_FOUND', 'Research chat session not found');
      }
    } else {
      const autoTitle = ticker
        ? `${ticker}: ${input.query.slice(0, 48)}`
        : input.query.slice(0, 60) || 'Cuộc trò chuyện nghiên cứu';
      const created = await this.prisma.chatSession.create({
        data: {
          userId,
          title: autoTitle,
        },
      });
      sessionId = created.id;
    }

    const priorMessages = await this.prisma.chatMessage.findMany({
      where: { sessionId },
      orderBy: { createdAt: 'desc' },
      take: 10,
      select: { role: true, content: true },
    });
    const history = priorMessages
      .reverse()
      .filter((m) => m.role === 'user' || m.role === 'assistant')
      .map((m) => ({ role: m.role, content: m.content }));

    const userMetadata: ResearchChatMetadata = {
      mode: 'RESEARCH_CHAT_V2',
      chat_mode: chatMode,
      primary_ticker: ticker ?? '',
      expanded_tickers: ticker ? [ticker, ...compareTickers] : compareTickers,
      as_of_date: input.asOfDate ?? null,
      enable_kg: enableKg,
    };

    await this.prisma.chatMessage.create({
      data: {
        sessionId,
        role: 'user',
        content: input.query,
        type: 'text',
        metadata: userMetadata as Prisma.InputJsonValue,
      },
    });

    // Switch response to SSE mode (bypass compression via Cache-Control: no-transform)
    res.status(200);
    res.setHeader('Content-Type', 'text/event-stream; charset=utf-8');
    res.setHeader('Cache-Control', 'no-cache, no-transform');
    res.setHeader('Connection', 'keep-alive');
    res.setHeader('X-Accel-Buffering', 'no');
    res.flushHeaders?.();

    const abortController = new AbortController();
    let timedOut = false;
    const timeoutHandle = setTimeout(() => {
      timedOut = true;
      abortController.abort();
    }, this.timeoutMs);

    const onResponseClose = () => {
      if (!res.writableEnded) {
        abortController.abort();
      }
    };
    res.on('close', onResponseClose);

    try {
      const upstreamPayload: Record<string, unknown> = {
        query: input.query,
        mode: chatMode,
        compare_tickers: compareTickers.length > 0 ? compareTickers : undefined,
        as_of_date: input.asOfDate || undefined,
        enable_kg: enableKg,
        history,
      };
      if (ticker) {
        upstreamPayload.ticker = ticker;
      }

      const upstreamRes = await this.fetchImpl(`${this.sagApiUrl}/api/v1/research-chat/stream`, {
        method: 'POST',
        headers: {
          ...bffHeaders,
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
        },
        body: JSON.stringify(upstreamPayload),
        signal: abortController.signal,
      });

      if (!upstreamRes.ok || !upstreamRes.body) {
        const errorDetail = await upstreamRes.text().catch(() => '');
        writeSseEvent(res, 'error', {
          code: 'SAG_UPSTREAM_ERROR',
          message: `SAG upstream returned status ${upstreamRes.status}${errorDetail ? `: ${errorDetail.slice(0, 200)}` : ''}`,
        });
        return;
      }

      const reader = upstreamRes.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      let doneEmitted = false;
      let errorEmitted = false;

      const processParsedEvent = async (parsed: { event: string; data: string }) => {
        if (parsed.event === 'done') {
          let payload: { answer?: unknown; metadata?: unknown };
          try {
            payload = JSON.parse(parsed.data) as { answer?: unknown; metadata?: unknown };
          } catch {
            errorEmitted = true;
            writeSseEvent(res, 'error', {
              code: 'INVALID_SAG_PAYLOAD',
              message: 'Malformed JSON in SAG completion payload',
            });
            return;
          }

          if (typeof payload.answer !== 'string' || !payload.metadata || typeof payload.metadata !== 'object' || Array.isArray(payload.metadata)) {
            errorEmitted = true;
            writeSseEvent(res, 'error', {
              code: 'INVALID_SAG_PAYLOAD',
              message: 'Incomplete completion payload from SAG',
            });
            return;
          }

          const rawMetadata = payload.metadata as ResearchChatMetadata;
          const metadataToStore: ResearchChatMetadata = {
            ...rawMetadata,
            mode: rawMetadata.mode || 'RESEARCH_CHAT_V2',
            chat_mode: rawMetadata.chat_mode || chatMode,
            primary_ticker: rawMetadata.primary_ticker || ticker || '',
            as_of_date: rawMetadata.as_of_date ?? input.asOfDate ?? null,
            enable_kg: typeof rawMetadata.enable_kg === 'boolean' ? rawMetadata.enable_kg : enableKg,
          };

          const savedAssistant = await this.prisma.chatMessage.create({
            data: {
              sessionId: sessionId!,
              role: 'assistant',
              content: payload.answer,
              type: 'research_chat_v2',
              metadata: metadataToStore as Prisma.InputJsonValue,
            },
          });

          doneEmitted = true;
          writeSseEvent(res, 'done', {
            messageId: savedAssistant.id,
            sessionId,
            answer: payload.answer,
            metadata: metadataToStore,
          });
          return;
        }

        if (parsed.event === 'error') {
          errorEmitted = true;
        }

        try {
          const parsedData = JSON.parse(parsed.data);
          writeSseEvent(res, parsed.event, parsedData);
        } catch {
          errorEmitted = true;
          writeSseEvent(res, 'error', {
            code: 'INVALID_SAG_EVENT',
            message: `Malformed JSON in SAG ${parsed.event} event`,
          });
        }
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');

        let boundaryIdx = buffer.indexOf('\n\n');
        while (boundaryIdx !== -1) {
          const rawBlock = buffer.slice(0, boundaryIdx);
          buffer = buffer.slice(boundaryIdx + 2);
          const parsed = parseSseBlock(rawBlock);
          if (parsed) {
            await processParsedEvent(parsed);
          }
          boundaryIdx = buffer.indexOf('\n\n');
        }
      }

      const trailing = decoder.decode();
      if (trailing) {
        buffer += trailing.replace(/\r\n/g, '\n');
      }
      if (buffer.trim()) {
        const parsed = parseSseBlock(buffer);
        if (parsed) {
          await processParsedEvent(parsed);
        }
      }

      if (!doneEmitted && !errorEmitted) {
        writeSseEvent(res, 'error', {
          code: 'STREAM_INCOMPLETE',
          message: 'SAG stream terminated before emitting a completion event',
        });
      }
    } catch (err) {
      const error = err as Error;
      if (timedOut) {
        writeSseEvent(res, 'error', {
          code: 'SAG_TIMEOUT',
          message: `Research chat request timed out after ${this.timeoutMs}ms`,
        });
      } else if (error.name !== 'AbortError') {
        writeSseEvent(res, 'error', {
          code: 'SAG_STREAM_FAILED',
          message: error.message || 'Failed to stream research chat response',
        });
      }
    } finally {
      clearTimeout(timeoutHandle);
      res.off('close', onResponseClose);
      if (!res.writableEnded) {
        res.end();
      }
    }
  }

  async submitFeedback(userId: string, input: SubmitFeedbackInput) {
    const message = await this.prisma.chatMessage.findFirst({
      where: {
        id: input.messageId,
        role: 'assistant',
        session: { userId },
      },
      select: { id: true },
    });

    if (!message) {
      throw new SagGatewayError(404, 'MESSAGE_NOT_FOUND', 'Assistant message not found');
    }

    const feedback = await this.prisma.sagChatEvalFeedback.upsert({
      where: {
        messageId_userId: {
          messageId: input.messageId,
          userId,
        },
      },
      create: {
        messageId: input.messageId,
        userId,
        verdict: input.verdict,
        citationOk: input.citationOk ?? true,
        tableCalcOk: input.tableCalcOk ?? true,
        graphContextOk: input.graphContextOk ?? true,
        hallucinationFlag: input.hallucinationFlag ?? false,
        notes: input.notes ?? null,
      },
      update: {
        verdict: input.verdict,
        citationOk: input.citationOk ?? true,
        tableCalcOk: input.tableCalcOk ?? true,
        graphContextOk: input.graphContextOk ?? true,
        hallucinationFlag: input.hallucinationFlag ?? false,
        notes: input.notes ?? null,
      },
    });

    return feedback;
  }

  async getEvalSummary(userId: string) {
    const [totalSessions, assistantMessages, feedbacks] = await Promise.all([
      this.prisma.chatSession.count({ where: { userId } }),
      this.prisma.chatMessage.findMany({
        where: {
          role: 'assistant',
          session: { userId },
        },
        select: {
          id: true,
          type: true,
          metadata: true,
        },
      }),
      this.prisma.sagChatEvalFeedback.findMany({
        where: { userId },
        select: {
          messageId: true,
          verdict: true,
          citationOk: true,
          tableCalcOk: true,
          graphContextOk: true,
          hallucinationFlag: true,
        },
      }),
    ]);

    const researchMessages = assistantMessages.filter((msg) => {
      if (msg.type === 'research_chat_v2') return true;
      const meta = msg.metadata as ResearchChatMetadata | null;
      return meta?.mode === 'RESEARCH_CHAT_V2';
    });

    let totalCitations = 0;
    let verifiedCitations = 0;
    let totalTablesAndCalcs = 0;
    let verifiedTablesAndCalcs = 0;
    let pitLeakCount = 0;
    const autoHallucinatedMessageIds = new Set<string>();

    for (const msg of researchMessages) {
      const meta = (msg.metadata ?? {}) as ResearchChatMetadata;
      const asOfDate = typeof meta.as_of_date === 'string' ? meta.as_of_date : null;
      const tel =
        meta.telemetry && typeof meta.telemetry === 'object' && !Array.isArray(meta.telemetry)
          ? (meta.telemetry as Record<string, unknown>)
          : {};

      if (typeof meta.pit_leak_count === 'number' && meta.pit_leak_count > 0) {
        pitLeakCount += meta.pit_leak_count;
      }

      if (Array.isArray(meta.citations)) {
        for (const citation of meta.citations) {
          totalCitations += 1;
          if (citation?.verified === true) {
            verifiedCitations += 1;
          }
          if (
            asOfDate &&
            typeof citation?.published_date === 'string' &&
            citation.published_date.slice(0, 10) > asOfDate
          ) {
            pitLeakCount += 1;
          }
        }
      }

      // True Gate G1: include Stage 7 rejected/hallucinated citations in denominator so G1 is NEVER inflated to 100%
      const rejectedArrLen = Array.isArray(meta.rejected_citations) ? meta.rejected_citations.length : 0;
      const telRejectedCount =
        typeof tel.rejected_citations_count === 'number' && Number.isFinite(tel.rejected_citations_count)
          ? Math.max(0, Math.floor(tel.rejected_citations_count))
          : 0;
      const telHallucinatedCidCount =
        typeof tel.hallucinated_citation_ids_count === 'number' &&
        Number.isFinite(tel.hallucinated_citation_ids_count)
          ? Math.max(0, Math.floor(tel.hallucinated_citation_ids_count))
          : 0;
      const extraRejectedCount = Math.max(rejectedArrLen, telRejectedCount, telHallucinatedCidCount);
      totalCitations += extraRejectedCount;

      // Automated hallucination signals for true Gate G3 accounting
      const blindspots = Array.isArray(meta.declared_blindspots) ? meta.declared_blindspots : [];
      const hasHallucinationBlindspot = blindspots.some((b) => {
        if (!b || typeof b !== 'object') return false;
        const code = String((b as ResearchChatBlindspot).code ?? '').toUpperCase();
        return code === 'HALLUCINATED_CITATION_ID' || code === 'UNSUPPORTED_NUMERIC_CLAIM';
      });
      const telUnsupportedNumeric =
        typeof tel.unsupported_numeric_claims_count === 'number' && tel.unsupported_numeric_claims_count > 0;
      if (hasHallucinationBlindspot || telUnsupportedNumeric || telHallucinatedCidCount > 0) {
        autoHallucinatedMessageIds.add(msg.id);
      }

      if (Array.isArray(meta.hydrated_tables)) {
        for (const table of meta.hydrated_tables) {
          totalTablesAndCalcs += 1;
          if (table?.verification_status === 'VERIFIED') {
            verifiedTablesAndCalcs += 1;
          }
        }
      }

      if (Array.isArray(meta.calculator_traces)) {
        for (const trace of meta.calculator_traces) {
          totalTablesAndCalcs += 1;
          const hasInputs =
            trace?.inputs !== null &&
            trace?.inputs !== undefined &&
            (Array.isArray(trace.inputs)
              ? trace.inputs.length > 0
              : typeof trace.inputs === 'object' && Object.keys(trace.inputs as Record<string, unknown>).length > 0);
          if (typeof trace?.result === 'number' && Number.isFinite(trace.result) && hasInputs) {
            verifiedTablesAndCalcs += 1;
          }
        }
      }

      if (asOfDate && Array.isArray(meta.graph_hops)) {
        for (const hop of meta.graph_hops) {
          if (
            typeof hop?.effective_from === 'string' &&
            hop.effective_from.slice(0, 10) > asOfDate
          ) {
            pitLeakCount += 1;
          }
        }
      }
    }

    const feedbackCount = feedbacks.length;
    const autoHallucinationCount = autoHallucinatedMessageIds.size;
    const g1CitationVerifiedRate =
      totalCitations > 0
        ? verifiedCitations / totalCitations
        : feedbackCount > 0
          ? feedbacks.filter((f) => f.citationOk).length / feedbackCount
          : 0;

    const g2TableCalcVerifiedRate =
      totalTablesAndCalcs > 0
        ? verifiedTablesAndCalcs / totalTablesAndCalcs
        : feedbackCount > 0
          ? feedbacks.filter((f) => f.tableCalcOk).length / feedbackCount
          : 0;

    // True Gate G3: combine human hallucinationFlag with automated Stage 7 hallucination signals.
    // Never default G3 to fake 1.0 when feedbackCount === 0 (return null if unmeasured and clean).
    const g3AbstentionComplianceRate: number | null =
      feedbackCount > 0
        ? feedbacks.filter(
            (f) => !f.hallucinationFlag && !autoHallucinatedMessageIds.has(f.messageId),
          ).length / feedbackCount
        : autoHallucinationCount > 0 && researchMessages.length > 0
          ? (researchMessages.length - autoHallucinationCount) / researchMessages.length
          : null;

    const g5HumanPassRate =
      feedbackCount > 0
        ? feedbacks.filter((f) => f.verdict === 'PASS').length / feedbackCount
        : 0;

    const readyForPhase2 =
      totalSessions >= 40 &&
      feedbackCount >= 40 &&
      g1CitationVerifiedRate >= 0.98 &&
      g2TableCalcVerifiedRate >= 0.98 &&
      g3AbstentionComplianceRate !== null &&
      g3AbstentionComplianceRate >= 1.0 &&
      autoHallucinationCount === 0 &&
      pitLeakCount === 0 &&
      g5HumanPassRate >= 0.9;

    return {
      totalSessions,
      totalAssistantMessages: researchMessages.length,
      feedbackCount,
      autoHallucinationCount,
      gates: {
        g1CitationVerifiedRate: Number(g1CitationVerifiedRate.toFixed(4)),
        g2TableCalcVerifiedRate: Number(g2TableCalcVerifiedRate.toFixed(4)),
        g3AbstentionComplianceRate:
          g3AbstentionComplianceRate === null ? null : Number(g3AbstentionComplianceRate.toFixed(4)),
        g4PitZeroLeakCount: pitLeakCount,
        g5HumanPassRate: Number(g5HumanPassRate.toFixed(4)),
      },
      thresholds: {
        g1Min: 0.98,
        g2Min: 0.98,
        g3Min: 1.0,
        g4Max: 0,
        g5Min: 0.9,
        minSessions: 40,
      },
      readyForPhase2,
    };
  }

  async getIngestedTickers(userId: string) {
    const headers = this.buildBffHeaders(userId, null);
    return this.fetchSagJson('/api/v1/research-chat/tickers', headers);
  }

  async getKgSubgraph(userId: string, ticker: string, asOfDate?: string) {
    const normalizedTicker = normalizeTicker(ticker);
    const headers = this.buildBffHeaders(userId, normalizedTicker);
    const qs = asOfDate ? `?as_of_date=${encodeURIComponent(asOfDate)}` : '';
    return this.fetchSagJson(`/api/v1/research-chat/kg/${encodeURIComponent(normalizedTicker)}${qs}`, headers);
  }

  async getHydratedTable(userId: string, tableId: string, ticker?: string) {
    const scopeTicker = normalizeOptionalTicker(ticker);
    const headers = this.buildBffHeaders(userId, scopeTicker);
    return this.fetchSagJson(`/api/v1/research-chat/tables/${encodeURIComponent(tableId)}`, headers);
  }

  private async fetchSagJson(path: string, headers: Record<string, string>): Promise<unknown> {
    const abortController = new AbortController();
    const timeoutHandle = setTimeout(() => abortController.abort(), this.timeoutMs);

    try {
      const upstreamRes = await this.fetchImpl(`${this.sagApiUrl}${path}`, {
        method: 'GET',
        headers: {
          ...headers,
          Accept: 'application/json',
        },
        signal: abortController.signal,
      });

      if (!upstreamRes.ok) {
        throw new SagGatewayError(
          upstreamRes.status >= 400 && upstreamRes.status < 500 ? upstreamRes.status : 502,
          'SAG_UPSTREAM_ERROR',
          `SAG upstream returned status ${upstreamRes.status}`,
        );
      }

      const rawText = await upstreamRes.text();
      try {
        return JSON.parse(rawText);
      } catch {
        throw new SagGatewayError(502, 'INVALID_SAG_PAYLOAD', 'SAG returned invalid JSON');
      }
    } catch (err) {
      if (err instanceof SagGatewayError) {
        throw err;
      }
      const error = err as Error;
      if (error.name === 'AbortError') {
        throw new SagGatewayError(504, 'SAG_TIMEOUT', `SAG request timed out after ${this.timeoutMs}ms`);
      }
      throw new SagGatewayError(502, 'SAG_UNREACHABLE', error.message || 'Failed to reach SAG API');
    } finally {
      clearTimeout(timeoutHandle);
    }
  }
}

export const sagResearchChatService = new SagResearchChatService();
