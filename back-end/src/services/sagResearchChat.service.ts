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
export type ResearchMemoryMode = 'auto' | 'read_only' | 'incognito';

export interface PortfolioPositionSnapshot {
  symbol: string;
  quantity: number;
  avg_cost: number;
}

export interface UserPortfolioSnapshot {
  as_of_timestamp: string;
  positions: PortfolioPositionSnapshot[];
  watchlists: string[];
  screener_presets: Array<{
    id: string;
    name: string;
    filters: unknown;
  }>;
}

export interface MemoryIngestTurnPayload {
  session_id: string;
  parent_session_id: string | null;
  session_title: string | null;
  chat_mode: ResearchChatMode;
  user_message_id: string | null;
  assistant_message_id: string;
  user_content: string;
  assistant_content: string;
  turn_index: number;
  turn_as_of_date: string | null;
  memory_mode: ResearchMemoryMode;
  tickers: string[];
  sectors: string[];
  macro_factors: string[];
  cited_doc_version_ids: string[];
  blindspot_codes: string[];
  stage7_hallucinated: boolean;
  stream_interrupted: boolean;
}

export interface ListUserMemoriesQuery {
  memoryType?: string;
  ticker?: string;
  status?: 'active' | 'stale' | 'superseded' | 'all' | string;
  limit?: number;
  offset?: number;
}

export interface PatchUserMemoryInput {
  content_text?: string;
  pinned_by_user?: boolean;
  reason?: string;
}

export interface DeleteSessionResult {
  deleted: boolean;
  sessionId: string;
  outboxId: string;
  cascadeStatus: 'SENT' | 'PENDING';
}

export interface FlushCascadeOutboxResult {
  processed: number;
  sent: number;
  failed: number;
}

export interface CreateSessionInput {
  ticker?: string | null;
  title?: string;
  mode?: ResearchChatMode;
  asOfDate?: string;
  enableKg?: boolean;
  parentSessionId?: string;
}

export interface StreamResearchChatInput {
  sessionId?: string;
  parentSessionId?: string;
  query: string;
  ticker?: string | null;
  compareTickers?: string[];
  mode?: ResearchChatMode;
  asOfDate?: string;
  enableKg?: boolean;
  memoryMode?: ResearchMemoryMode;
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
  route_intent?: string | null;
  eval_eligible?: boolean;
  primary_ticker?: string;
  expanded_tickers?: string[];
  resolved_query?: string | null;
  sub_queries?: string[];
  as_of_date?: string | null;
  enable_kg?: boolean;
  citations?: ResearchChatCitation[];
  rejected_citations?: ResearchChatCitation[];
  hydrated_tables?: ResearchChatHydratedTable[];
  screening_results?: Array<Record<string, unknown>>;
  tool_traces?: Array<Record<string, unknown>>;
  activity_log?: Array<Record<string, unknown>>;
  calculator_traces?: ResearchChatCalculatorTrace[];
  graph_hops?: ResearchChatGraphHop[];
  discrepancy_alerts?: unknown[];
  declared_blindspots?: Array<ResearchChatBlindspot | string>;
  recalled_memories?: unknown[];
  linked_sessions?: unknown[];
  portfolio_snapshot_used?: boolean;
  pit_leak_count?: number;
  telemetry?: Record<string, unknown>;
  [key: string]: unknown;
}

const MAX_CASCADE_OUTBOX_ATTEMPTS = 20;

const HALLUCINATION_BLINDSPOT_CODES = new Set([
  'HALLUCINATED_CITATION_ID',
  'HALLUCINATED_MEMORY_ID',
  'UNSUPPORTED_NUMERIC_CLAIM',
  'CITATION_CONTENT_MISMATCH',
]);

export function toValidIsoDate(raw?: string | null): string | null {
  if (!raw || !raw.trim()) {
    return null;
  }
  const trimmed = raw.trim();
  // Accept strict YYYY-MM-DD or ISO-8601 datetime prefix (reject trailing garbage like "2026-11-24foo")
  const match = trimmed.match(/^(\d{4})-(\d{2})-(\d{2})(?:$|[T\s])/);
  if (!match) {
    return null;
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  if (
    parsed.getUTCFullYear() !== year ||
    parsed.getUTCMonth() !== month - 1 ||
    parsed.getUTCDate() !== day
  ) {
    return null;
  }
  return `${match[1]}-${match[2]}-${match[3]}`;
}

export function isHistoricalBacktestDate(
  asOfDate?: string | null,
  referenceNow: Date = new Date(),
): boolean {
  if (!asOfDate || !asOfDate.trim()) {
    return false;
  }
  const validIso = toValidIsoDate(asOfDate);
  if (!validIso) {
    // Fail-closed: treat unparseable as_of_date as historical so live data never leaks
    return true;
  }
  const [year, month, day] = validIso.split('-').map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  const todayUtcMs = Date.UTC(
    referenceNow.getUTCFullYear(),
    referenceNow.getUTCMonth(),
    referenceNow.getUTCDate(),
  );
  const diffDays = Math.floor((todayUtcMs - parsed.getTime()) / 86_400_000);
  return diffDays > 1;
}

function extractBlindspotCodes(
  rawBlindspots?: Array<ResearchChatBlindspot | string>,
): string[] {
  if (!Array.isArray(rawBlindspots)) return [];
  const codes = new Set<string>();
  for (const item of rawBlindspots) {
    if (!item) continue;
    if (typeof item === 'string') {
      const clean = item.trim().toUpperCase();
      if (clean) codes.add(clean);
    } else if (typeof item === 'object') {
      const clean = String((item as ResearchChatBlindspot).code ?? '').trim().toUpperCase();
      if (clean) codes.add(clean);
    }
  }
  return Array.from(codes).slice(0, 32);
}

function buildIngestTurnPayload(params: {
  sessionId: string;
  parentSessionId: string | null;
  sessionTitle: string | null;
  chatMode: ResearchChatMode;
  userMessageId: string | null;
  assistantMessageId: string;
  userContent: string;
  assistantContent: string;
  turnIndex: number;
  turnAsOfDate: string | null;
  memoryMode: ResearchMemoryMode;
  ticker: string | null;
  compareTickers: string[];
  metadata: ResearchChatMetadata;
}): MemoryIngestTurnPayload {
  const { metadata } = params;
  const tickerSet = new Set<string>();
  const addTicker = (val: unknown) => {
    if (typeof val === 'string' && val.trim()) {
      tickerSet.add(val.trim().toUpperCase());
    }
  };
  addTicker(metadata.primary_ticker);
  addTicker(params.ticker);
  for (const t of params.compareTickers) addTicker(t);
  if (Array.isArray(metadata.expanded_tickers)) {
    for (const t of metadata.expanded_tickers) addTicker(t);
  }
  const docVersionSet = new Set<string>();
  if (Array.isArray(metadata.citations)) {
    for (const c of metadata.citations) {
      addTicker(c?.ticker);
      if (typeof c?.document_version_id === 'string' && c.document_version_id.trim()) {
        docVersionSet.add(c.document_version_id.trim());
      }
    }
  }

  const sectorSet = new Set<string>();
  if (Array.isArray(metadata.screening_results)) {
    for (const row of metadata.screening_results) {
      if (row && typeof row === 'object' && typeof row.sector === 'string' && row.sector.trim()) {
        sectorSet.add(row.sector.trim());
      }
    }
  }

  const macroSet = new Set<string>();
  if (Array.isArray(metadata.graph_hops)) {
    for (const hop of metadata.graph_hops) {
      if (
        hop &&
        typeof hop === 'object' &&
        String(hop.layer ?? '').toUpperCase() === 'MACRO' &&
        typeof hop.src_label === 'string' &&
        hop.src_label.trim()
      ) {
        macroSet.add(hop.src_label.trim());
      }
    }
  }

  const blindspotCodes = extractBlindspotCodes(metadata.declared_blindspots);
  const tel =
    metadata.telemetry && typeof metadata.telemetry === 'object' && !Array.isArray(metadata.telemetry)
      ? (metadata.telemetry as Record<string, unknown>)
      : {};
  const stage7Hallucinated =
    blindspotCodes.some((c) => HALLUCINATION_BLINDSPOT_CODES.has(c)) ||
    (typeof tel.hallucinated_citation_ids_count === 'number' && tel.hallucinated_citation_ids_count > 0) ||
    (typeof tel.hallucinated_memory_ids_count === 'number' && tel.hallucinated_memory_ids_count > 0) ||
    (typeof tel.unsupported_numeric_claims_count === 'number' && tel.unsupported_numeric_claims_count > 0);

  return {
    session_id: params.sessionId,
    parent_session_id: params.parentSessionId,
    session_title: params.sessionTitle ? params.sessionTitle.slice(0, 256) : null,
    chat_mode: metadata.chat_mode === 'MOAT' ? 'MOAT' : params.chatMode,
    user_message_id: params.userMessageId,
    assistant_message_id: params.assistantMessageId,
    user_content: params.userContent.slice(0, 16_000),
    assistant_content: params.assistantContent.slice(0, 32_000),
    turn_index: Math.max(1, params.turnIndex),
    turn_as_of_date: toValidIsoDate(params.turnAsOfDate),
    memory_mode: params.memoryMode,
    tickers: Array.from(tickerSet).slice(0, 32),
    sectors: Array.from(sectorSet).slice(0, 24),
    macro_factors: Array.from(macroSet).slice(0, 24),
    cited_doc_version_ids: Array.from(docVersionSet).slice(0, 64),
    blindspot_codes: blindspotCodes,
    stage7_hallucinated: stage7Hallucinated,
    stream_interrupted: blindspotCodes.includes('STREAM_INTERRUPTED_UNVERIFIED'),
  };
}

function projectHistoryEntry(msg: {
  id?: string;
  role: string;
  content: string;
  metadata?: unknown;
}): Record<string, unknown> {
  const base: Record<string, unknown> = {
    role: msg.role,
    content: msg.content,
  };
  if (msg.id) {
    base.message_id = msg.id;
  }
  const meta =
    msg.metadata && typeof msg.metadata === 'object' && !Array.isArray(msg.metadata)
      ? (msg.metadata as ResearchChatMetadata)
      : null;
  if (!meta) {
    return base;
  }
  if (typeof meta.as_of_date === 'string' && meta.as_of_date.trim().length >= 10) {
    base.turn_as_of_date = meta.as_of_date.trim().slice(0, 10);
  }
  if (typeof meta.primary_ticker === 'string' && meta.primary_ticker.trim()) {
    base.primary_ticker = meta.primary_ticker.trim().toUpperCase();
  }
  if (Array.isArray(meta.expanded_tickers) && meta.expanded_tickers.length > 0) {
    base.expanded_tickers = meta.expanded_tickers
      .filter((t): t is string => typeof t === 'string' && t.trim().length > 0)
      .slice(0, 24);
  }
  if (Array.isArray(meta.citations) && meta.citations.length > 0) {
    const citedSet = new Set<string>();
    for (const c of meta.citations) {
      if (typeof c?.ticker === 'string' && c.ticker.trim()) {
        citedSet.add(c.ticker.trim().toUpperCase());
      }
    }
    if (citedSet.size > 0) {
      base.cited_tickers = Array.from(citedSet).slice(0, 24);
    }
  }
  if (Array.isArray(meta.screening_results) && meta.screening_results.length > 0) {
    const summary: Array<Record<string, unknown>> = [];
    const screenedTks: string[] = [];
    for (const row of meta.screening_results.slice(0, 15)) {
      if (!row || typeof row !== 'object') continue;
      const tk = typeof row.ticker === 'string' ? row.ticker.trim().toUpperCase() : '';
      if (!tk) continue;
      screenedTks.push(tk);
      summary.push({
        ticker: tk,
        fiscal_year: row.fiscal_year ?? null,
        sector: row.sector ?? null,
        pe: row.pe ?? null,
        pb: row.pb ?? null,
        roe: row.roe ?? null,
        gross_margin: row.gross_margin ?? null,
        debt_to_equity: row.debt_to_equity ?? null,
        quality_score: row.quality_score ?? null,
        source_kind: row.source_kind ?? null,
      });
    }
    if (screenedTks.length > 0) {
      base.screened_tickers = screenedTks;
      base.screening_summary = summary;
    }
  }
  if (typeof meta.route_intent === 'string' && meta.route_intent.trim()) {
    base.route_intent = meta.route_intent.trim();
  }
  if (typeof meta.eval_eligible === 'boolean') {
    base.eval_eligible = meta.eval_eligible;
  }
  const rawBrief =
    meta.research_brief && typeof meta.research_brief === 'object' && !Array.isArray(meta.research_brief)
      ? (meta.research_brief as Record<string, unknown>)
      : null;
  if (rawBrief) {
    if (typeof rawBrief.user_concern === 'string' && rawBrief.user_concern.trim()) {
      base.user_concern = rawBrief.user_concern.trim().slice(0, 400);
    }
    if (typeof rawBrief.answer_shape === 'string' && rawBrief.answer_shape.trim()) {
      base.answer_shape = rawBrief.answer_shape.trim().slice(0, 64);
    }
    if (typeof rawBrief.needs_clarification === 'boolean') {
      base.needs_clarification = rawBrief.needs_clarification;
    }
    if (typeof rawBrief.clarifying_question === 'string' && rawBrief.clarifying_question.trim()) {
      base.clarifying_question = rawBrief.clarifying_question.trim().slice(0, 600);
    }
  }
  if (typeof meta.session_rolling_summary === 'string' && meta.session_rolling_summary.trim()) {
    base.session_rolling_summary = meta.session_rolling_summary.trim().slice(0, 12_000);
  }
  return base;
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
  private readonly inFlightOutboxIds = new Set<string>();

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

  async buildLiveUserPortfolioSnapshot(
    userId: string,
    asOfDate?: string | null,
    memoryMode: ResearchMemoryMode = 'auto',
    referenceNow: Date = new Date(),
  ): Promise<UserPortfolioSnapshot | null> {
    // CRITICAL PIT & PRIVACY INVARIANT:
    // NEVER attach live Core DB portfolio/watchlist/preset state during historical backtests
    // ((todayUTC - asOfDate) > 1 day) or in incognito mode -> 0% PIT leakage!
    if (memoryMode === 'incognito') {
      return null;
    }
    if (isHistoricalBacktestDate(asOfDate, referenceNow)) {
      return null;
    }

    try {
      const [positionsRaw, watchlistsRaw, presetsRaw] = await Promise.all([
        this.prisma.position.findMany({
          where: { userId, quantity: { gt: 0 } },
          select: { symbol: true, quantity: true, avgPrice: true },
          take: 100,
        }),
        this.prisma.watchlist.findMany({
          where: { userId },
          select: { symbols: true },
          take: 50,
        }),
        this.prisma.screenerPreset.findMany({
          where: { userId },
          select: { id: true, name: true, filters: true },
          take: 20,
        }),
      ]);

      const positions: PortfolioPositionSnapshot[] = [];
      for (const row of positionsRaw) {
        const sym = normalizeTicker(row.symbol).slice(0, 32);
        const qty = Number(row.quantity);
        const avgCost = Number(row.avgPrice);
        if (!sym || !Number.isFinite(qty) || qty <= 0) continue;
        positions.push({
          symbol: sym,
          quantity: qty,
          avg_cost: Number.isFinite(avgCost) && avgCost >= 0 ? avgCost : 0,
        });
      }

      const watchlistSet = new Set<string>();
      for (const wl of watchlistsRaw) {
        if (!Array.isArray(wl.symbols)) continue;
        for (const symRaw of wl.symbols) {
          const sym = normalizeTicker(symRaw);
          if (sym) watchlistSet.add(sym);
          if (watchlistSet.size >= 200) break;
        }
        if (watchlistSet.size >= 200) break;
      }
      const watchlists = Array.from(watchlistSet);

      const screener_presets = presetsRaw.slice(0, 20).map((preset) => ({
        id: preset.id,
        name: preset.name,
        filters: preset.filters,
      }));

      if (positions.length === 0 && watchlists.length === 0 && screener_presets.length === 0) {
        return null;
      }

      return {
        as_of_timestamp: referenceNow.toISOString(),
        positions,
        watchlists,
        screener_presets,
      };
    } catch (err) {
      console.warn(
        '[SagResearchChat] Failed to query live user portfolio snapshot; degrading to null:',
        (err as Error)?.message ?? err,
      );
      return null;
    }
  }

  async enqueueMemoryIngestTurn(
    userId: string,
    payload: MemoryIngestTurnPayload,
  ): Promise<boolean> {
    // Only ingest turns in 'auto' memory mode (skip 'read_only' & 'incognito')
    if (payload.memory_mode !== 'auto') {
      return false;
    }

    let headers: Record<string, string>;
    try {
      headers = this.buildBffHeaders(userId);
    } catch (err) {
      console.warn(
        '[SagResearchChat] Skipping async memory ingest-turn (BFF headers unavailable):',
        (err as Error)?.message ?? err,
      );
      return false;
    }

    const abortController = new AbortController();
    const ingestTimeoutMs = Math.min(this.timeoutMs, 15_000);
    const timeoutHandle = setTimeout(() => abortController.abort(), ingestTimeoutMs);
    if (typeof (timeoutHandle as NodeJS.Timeout).unref === 'function') {
      (timeoutHandle as NodeJS.Timeout).unref();
    }

    try {
      const res = await this.fetchImpl(`${this.sagApiUrl}/api/v1/research-chat/memories/ingest-turn`, {
        method: 'POST',
        headers: {
          ...headers,
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify(payload),
        signal: abortController.signal,
      });
      if (!res.ok) {
        const detail = await res.text().catch(() => '');
        console.warn(
          `[SagResearchChat] Async memory ingest-turn returned HTTP ${res.status}${detail ? `: ${detail.slice(0, 200)}` : ''}`,
        );
        return false;
      }
      return true;
    } catch (err) {
      console.warn(
        '[SagResearchChat] Async memory ingest-turn failed:',
        (err as Error)?.message ?? err,
      );
      return false;
    } finally {
      clearTimeout(timeoutHandle);
    }
  }

  async createSession(userId: string, input: CreateSessionInput = {}) {
    const ticker = normalizeOptionalTicker(input.ticker);
    const mode: ResearchChatMode = input.mode === 'MOAT' ? 'MOAT' : 'GENERAL';
    const title = input.title?.trim() || (ticker ? `${ticker} Research` : 'Cuộc trò chuyện nghiên cứu mới');
    let parentSessionId: string | null = null;
    if (input.parentSessionId) {
      const parent = await this.prisma.chatSession.findFirst({
        where: { id: input.parentSessionId, userId },
        select: { id: true },
      });
      if (!parent) {
        throw new SagGatewayError(404, 'PARENT_SESSION_NOT_FOUND', 'Parent research chat session not found');
      }
      parentSessionId = parent.id;
    }

    const session = await this.prisma.chatSession.create({
      data: {
        userId,
        title,
        ...(parentSessionId ? { parentSessionId } : {}),
      },
    });

    return {
      id: session.id,
      title: session.title,
      parentSessionId: session.parentSessionId ?? parentSessionId,
      ticker,
      mode,
      asOfDate: input.asOfDate ?? null,
      enableKg: input.enableKg ?? true,
      createdAt: session.createdAt,
    };
  }

  async listSessions(userId: string) {
    this.scheduleOpportunisticOutboxFlush(userId);
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
        parentSessionId: session.parentSessionId ?? null,
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
        parentSessionId: session.parentSessionId ?? null,
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
    const memoryMode: ResearchMemoryMode = input.memoryMode ?? 'auto';
    const compareTickers = (input.compareTickers ?? [])
      .map(normalizeTicker)
      .filter((t) => t && t !== ticker)
      .slice(0, MAX_COMPARE_TICKERS);
    const bffHeaders = this.buildBffHeaders(userId, ticker, compareTickers);
    const enableKg = input.enableKg ?? true;

    let sessionId = input.sessionId;
    let sessionTitle: string | null = null;
    let sessionParentId: string | null = null;
    if (sessionId) {
      const existing = await this.prisma.chatSession.findFirst({
        where: { id: sessionId, userId },
      });
      if (!existing) {
        throw new SagGatewayError(404, 'SESSION_NOT_FOUND', 'Research chat session not found');
      }
      sessionTitle = existing.title ?? null;
      sessionParentId = existing.parentSessionId ?? null;
    } else {
      if (input.parentSessionId) {
        const parent = await this.prisma.chatSession.findFirst({
          where: { id: input.parentSessionId, userId },
          select: { id: true },
        });
        if (!parent) {
          throw new SagGatewayError(404, 'PARENT_SESSION_NOT_FOUND', 'Parent research chat session not found');
        }
        sessionParentId = parent.id;
      }
      const autoTitle = ticker
        ? `${ticker}: ${input.query.slice(0, 48)}`
        : input.query.slice(0, 60) || 'Cuộc trò chuyện nghiên cứu';
      const created = await this.prisma.chatSession.create({
        data: {
          userId,
          title: autoTitle,
          ...(sessionParentId ? { parentSessionId: sessionParentId } : {}),
        },
      });
      sessionId = created.id;
      sessionTitle = created.title ?? autoTitle;
      sessionParentId = created.parentSessionId ?? sessionParentId;
    }

    const priorMessages = await this.prisma.chatMessage.findMany({
      where: { sessionId },
      orderBy: { createdAt: 'desc' },
      take: 120,
      select: { id: true, role: true, content: true, metadata: true },
    });
    const history = priorMessages
      .reverse()
      .filter((m) => m.role === 'user' || m.role === 'assistant')
      .map((m) => projectHistoryEntry(m));

    const userMetadata: ResearchChatMetadata = {
      mode: 'RESEARCH_CHAT_V2',
      chat_mode: chatMode,
      primary_ticker: ticker ?? '',
      expanded_tickers: ticker ? [ticker, ...compareTickers] : compareTickers,
      as_of_date: input.asOfDate ?? null,
      enable_kg: enableKg,
    };

    const savedUserMessage = await this.prisma.chatMessage.create({
      data: {
        sessionId,
        role: 'user',
        content: input.query,
        type: 'text',
        metadata: userMetadata as Prisma.InputJsonValue,
      },
    });

    // Live-only Core DB Portfolio Snapshot Bridge (strictly null in backtest >1d or incognito)
    const userPortfolioSnapshot = await this.buildLiveUserPortfolioSnapshot(
      userId,
      input.asOfDate,
      memoryMode,
    );

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

    let pendingIngestPayload: MemoryIngestTurnPayload | null = null;

    try {
      const upstreamPayload: Record<string, unknown> = {
        query: input.query,
        session_id: sessionId,
        mode: chatMode,
        compare_tickers: compareTickers.length > 0 ? compareTickers : undefined,
        as_of_date: input.asOfDate || undefined,
        enable_kg: enableKg,
        memory_mode: memoryMode,
        user_portfolio_snapshot: userPortfolioSnapshot,
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
          if (doneEmitted) {
            return;
          }
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

          // ONLY prepare post-commit async memory ingest after BOTH user_msg & assistant_msg are persisted in Core PG
          if (memoryMode === 'auto') {
            const priorAssistantTurns = history.filter((m) => m.role === 'assistant').length;
            pendingIngestPayload = buildIngestTurnPayload({
              sessionId: sessionId!,
              parentSessionId: sessionParentId,
              sessionTitle,
              chatMode,
              userMessageId: savedUserMessage.id,
              assistantMessageId: savedAssistant.id,
              userContent: input.query,
              assistantContent: payload.answer,
              turnIndex: priorAssistantTurns + 1,
              turnAsOfDate: metadataToStore.as_of_date ?? input.asOfDate ?? null,
              memoryMode,
              ticker,
              compareTickers,
              metadata: metadataToStore,
            });
          }

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
      if (pendingIngestPayload) {
        const payloadToSend = pendingIngestPayload;
        setImmediate(() => {
          void this.enqueueMemoryIngestTurn(userId, payloadToSend);
        });
      }
      this.scheduleOpportunisticOutboxFlush(userId);
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
    const [_rawSessionCount, assistantMessages, feedbacks] = await Promise.all([
      this.prisma.chatSession.count({ where: { userId } }),
      this.prisma.chatMessage.findMany({
        where: {
          role: 'assistant',
          session: { userId },
        },
        select: {
          id: true,
          sessionId: true,
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
      const meta = msg.metadata as ResearchChatMetadata | null;
      const isResearch = msg.type === 'research_chat_v2' || meta?.mode === 'RESEARCH_CHAT_V2';
      if (!isResearch) return false;
      // Exclude 0-data short-circuit turns (chitchat/concept/boundary refusal) from G3/session denominators
      if (meta?.eval_eligible === false) return false;
      const tel =
        meta?.telemetry && typeof meta.telemetry === 'object' && !Array.isArray(meta.telemetry)
          ? (meta.telemetry as Record<string, unknown>)
          : null;
      if (tel?.eval_eligible === false) return false;
      return true;
    });

    const eligibleSessionIds = new Set<string>();
    for (const msg of researchMessages) {
      if (typeof msg.sessionId === 'string' && msg.sessionId) {
        eligibleSessionIds.add(msg.sessionId);
      }
    }
    const totalSessions = eligibleSessionIds.size;

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
        if (!b) return false;
        const code =
          typeof b === 'string'
            ? b.trim().toUpperCase()
            : typeof b === 'object'
              ? String((b as ResearchChatBlindspot).code ?? '').trim().toUpperCase()
              : '';
        return HALLUCINATION_BLINDSPOT_CODES.has(code);
      });
      const telUnsupportedNumeric =
        typeof tel.unsupported_numeric_claims_count === 'number' && tel.unsupported_numeric_claims_count > 0;
      const telHallucinatedMemCount =
        typeof tel.hallucinated_memory_ids_count === 'number' &&
        Number.isFinite(tel.hallucinated_memory_ids_count)
          ? Math.max(0, Math.floor(tel.hallucinated_memory_ids_count))
          : 0;
      if (
        hasHallucinationBlindspot ||
        telUnsupportedNumeric ||
        telHallucinatedCidCount > 0 ||
        telHallucinatedMemCount > 0
      ) {
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

      if (asOfDate && Array.isArray(meta.screening_results)) {
        for (const row of meta.screening_results) {
          if (
            row &&
            typeof row === 'object' &&
            typeof row.valid_from === 'string' &&
            row.valid_from.slice(0, 10) > asOfDate
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

    // True Gate G3: combine human hallucinationFlag with automated Stage 7 hallucination signals
    // (including unreviewed auto-hallucinated messages so human PASSes on clean messages never mask them).
    // Never default G3 to fake 1.0 when feedbackCount === 0 (return null if unmeasured and clean).
    const feedbackMessageIds = new Set(feedbacks.map((f) => f.messageId));
    const unreviewedAutoHallucinations = Array.from(autoHallucinatedMessageIds).filter(
      (id) => !feedbackMessageIds.has(id),
    ).length;
    const g3AbstentionComplianceRate: number | null =
      feedbackCount > 0
        ? feedbacks.filter(
            (f) => !f.hallucinationFlag && !autoHallucinatedMessageIds.has(f.messageId),
          ).length /
          (feedbackCount + unreviewedAutoHallucinations)
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

  async deleteResearchSession(
    userId: string,
    sessionId: string,
    reason = 'USER_DELETED_SESSION',
  ): Promise<DeleteSessionResult> {
    const outboxRow = await this.prisma.$transaction(async (tx) => {
      const existing = await tx.chatSession.findFirst({
        where: { id: sessionId, userId },
        select: { id: true },
      });
      if (!existing) {
        throw new SagGatewayError(404, 'SESSION_NOT_FOUND', 'Research chat session not found');
      }
      await tx.chatMessage.deleteMany({ where: { sessionId } });
      const deleted = await tx.chatSession.deleteMany({
        where: { id: sessionId, userId },
      });
      if (deleted.count === 0) {
        throw new SagGatewayError(404, 'SESSION_NOT_FOUND', 'Research chat session not found');
      }
      return tx.sagMemoryCascadeOutbox.create({
        data: {
          sessionId,
          userId,
          reason,
          status: 'PENDING',
          attempts: 0,
        },
      });
    });

    const delivered = await this.deliverCascadeOutboxRow(outboxRow);
    return {
      deleted: true,
      sessionId,
      outboxId: outboxRow.id,
      cascadeStatus: delivered ? 'SENT' : 'PENDING',
    };
  }

  async flushPendingCascadeOutbox(
    userId?: string,
    limit = 20,
  ): Promise<FlushCascadeOutboxResult> {
    let pendingRows: Array<{
      id: string;
      sessionId: string;
      userId: string;
      reason: string;
      attempts: number;
    }>;
    try {
      pendingRows = await this.prisma.sagMemoryCascadeOutbox.findMany({
        where: {
          status: 'PENDING',
          ...(userId ? { userId } : {}),
        },
        orderBy: { createdAt: 'asc' },
        take: limit,
      });
    } catch {
      return { processed: 0, sent: 0, failed: 0 };
    }

    let processed = 0;
    let sent = 0;
    let failed = 0;
    for (const row of pendingRows) {
      if (this.inFlightOutboxIds.has(row.id)) {
        continue;
      }
      processed += 1;
      const ok = await this.deliverCascadeOutboxRow(row);
      if (ok) {
        sent += 1;
      } else {
        failed += 1;
      }
    }

    return {
      processed,
      sent,
      failed,
    };
  }

  private scheduleOpportunisticOutboxFlush(userId: string): void {
    setImmediate(() => {
      void this.flushPendingCascadeOutbox(userId, 10).catch(() => {});
    });
  }

  private async deliverCascadeOutboxRow(row: {
    id: string;
    sessionId: string;
    userId: string;
    reason: string;
    attempts: number;
  }): Promise<boolean> {
    if (this.inFlightOutboxIds.has(row.id)) {
      return false;
    }
    this.inFlightOutboxIds.add(row.id);

    const nextAttempts = (row.attempts ?? 0) + 1;
    let headers: Record<string, string>;
    try {
      headers = this.buildBffHeaders(row.userId);
    } catch (err) {
      await this.markOutboxPendingError(row.id, nextAttempts, (err as Error)?.message ?? 'Missing BFF token');
      this.inFlightOutboxIds.delete(row.id);
      return false;
    }

    const abortController = new AbortController();
    const outboxTimeoutMs = Math.min(this.timeoutMs, 15_000);
    const timeoutHandle = setTimeout(() => abortController.abort(), outboxTimeoutMs);
    if (typeof (timeoutHandle as NodeJS.Timeout).unref === 'function') {
      (timeoutHandle as NodeJS.Timeout).unref();
    }

    try {
      const upstreamRes = await this.fetchImpl(
        `${this.sagApiUrl}/api/v1/research-chat/memories/cascade-delete-session`,
        {
          method: 'POST',
          headers: {
            ...headers,
            'Content-Type': 'application/json',
            Accept: 'application/json',
          },
          body: JSON.stringify({
            session_id: row.sessionId,
            reason: row.reason || 'USER_DELETED_SESSION',
          }),
          signal: abortController.signal,
        },
      );

      if (upstreamRes.ok) {
        await this.prisma.sagMemoryCascadeOutbox.updateMany({
          where: { id: row.id, status: 'PENDING' },
          data: {
            status: 'SENT',
            attempts: nextAttempts,
            lastError: null,
          },
        });
        return true;
      }

      const detail = await upstreamRes.text().catch(() => '');
      await this.markOutboxPendingError(
        row.id,
        nextAttempts,
        `SAG returned HTTP ${upstreamRes.status}${detail ? `: ${detail.slice(0, 200)}` : ''}`,
      );
      return false;
    } catch (err) {
      await this.markOutboxPendingError(
        row.id,
        nextAttempts,
        (err as Error)?.message || 'Failed to deliver cascade delete to SAG',
      );
      return false;
    } finally {
      clearTimeout(timeoutHandle);
      this.inFlightOutboxIds.delete(row.id);
    }
  }

  private async markOutboxPendingError(
    outboxId: string,
    attempts: number,
    lastError: string,
  ): Promise<void> {
    try {
      const nextStatus = attempts >= MAX_CASCADE_OUTBOX_ATTEMPTS ? 'FAILED' : 'PENDING';
      // Conditional on status='PENDING' so a slow concurrent failure can NEVER revert an already-SENT row
      await this.prisma.sagMemoryCascadeOutbox.updateMany({
        where: { id: outboxId, status: 'PENDING' },
        data: {
          status: nextStatus,
          attempts,
          lastError: lastError.slice(0, 500),
        },
      });
    } catch (err) {
      console.warn(
        '[SagResearchChat] Failed to update cascade outbox retry state:',
        (err as Error)?.message ?? err,
      );
    }
  }

  async listUserMemories(userId: string, query: ListUserMemoriesQuery = {}) {
    this.scheduleOpportunisticOutboxFlush(userId);
    const headers = this.buildBffHeaders(userId);
    const params = new URLSearchParams();
    if (query.memoryType) params.set('memory_type', query.memoryType.trim().toUpperCase());
    if (query.ticker) params.set('ticker', normalizeTicker(query.ticker));
    if (query.status) params.set('status', query.status.trim().toLowerCase());
    if (query.limit !== undefined) params.set('limit', String(query.limit));
    if (query.offset !== undefined) params.set('offset', String(query.offset));
    const qs = params.toString();
    return this.fetchSagJson(`/api/v1/research-chat/memories${qs ? `?${qs}` : ''}`, headers);
  }

  async patchUserMemory(userId: string, memoryId: string, input: PatchUserMemoryInput) {
    this.scheduleOpportunisticOutboxFlush(userId);
    const headers = this.buildBffHeaders(userId);
    const body: Record<string, unknown> = {};
    if (input.content_text !== undefined) body.content_text = input.content_text;
    if (input.pinned_by_user !== undefined) body.pinned_by_user = input.pinned_by_user;
    if (input.reason !== undefined) body.reason = input.reason;
    return this.fetchSagJson(
      `/api/v1/research-chat/memories/${encodeURIComponent(memoryId)}`,
      headers,
      { method: 'PATCH', body },
    );
  }

  async deleteUserMemory(userId: string, memoryId: string) {
    this.scheduleOpportunisticOutboxFlush(userId);
    const headers = this.buildBffHeaders(userId);
    return this.fetchSagJson(
      `/api/v1/research-chat/memories/${encodeURIComponent(memoryId)}`,
      headers,
      { method: 'DELETE' },
    );
  }

  async clearAllUserMemories(userId: string) {
    this.scheduleOpportunisticOutboxFlush(userId);
    const headers = this.buildBffHeaders(userId);
    return this.fetchSagJson(
      '/api/v1/research-chat/memories/clear',
      headers,
      { method: 'POST' },
    );
  }

  async getRelatedSessions(userId: string, sessionId: string, limit?: number) {
    // 100% IDOR-proof: verify Core PG session ownership before querying SAG
    const existing = await this.prisma.chatSession.findFirst({
      where: { id: sessionId, userId },
      select: { id: true },
    });
    if (!existing) {
      throw new SagGatewayError(404, 'SESSION_NOT_FOUND', 'Research chat session not found');
    }
    this.scheduleOpportunisticOutboxFlush(userId);
    const headers = this.buildBffHeaders(userId);
    const qs = limit !== undefined ? `?limit=${encodeURIComponent(String(limit))}` : '';
    return this.fetchSagJson(
      `/api/v1/research-chat/sessions/${encodeURIComponent(sessionId)}/related${qs}`,
      headers,
    );
  }

  async getIngestedTickers(userId: string, asOfDate?: string) {
    const headers = this.buildBffHeaders(userId, null);
    const qs = asOfDate ? `?as_of_date=${encodeURIComponent(asOfDate)}` : '';
    return this.fetchSagJson(`/api/v1/research-chat/tickers${qs}`, headers);
  }

  async getKgSubgraph(userId: string, ticker: string, asOfDate?: string) {
    const normalizedTicker = normalizeTicker(ticker);
    const headers = this.buildBffHeaders(userId, normalizedTicker);
    const qs = asOfDate ? `?as_of_date=${encodeURIComponent(asOfDate)}` : '';
    return this.fetchSagJson(`/api/v1/research-chat/kg/${encodeURIComponent(normalizedTicker)}${qs}`, headers);
  }

  async getHydratedTable(userId: string, tableId: string, ticker?: string, asOfDate?: string) {
    const scopeTicker = normalizeOptionalTicker(ticker);
    const headers = this.buildBffHeaders(userId, scopeTicker);
    const qs = asOfDate ? `?as_of_date=${encodeURIComponent(asOfDate)}` : '';
    return this.fetchSagJson(`/api/v1/research-chat/tables/${encodeURIComponent(tableId)}${qs}`, headers);
  }

  private async fetchSagJson(
    path: string,
    headers: Record<string, string>,
    options: { method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'; body?: unknown } = {},
  ): Promise<unknown> {
    const method = options.method ?? 'GET';
    const abortController = new AbortController();
    const timeoutHandle = setTimeout(() => abortController.abort(), this.timeoutMs);

    try {
      const requestHeaders: Record<string, string> = {
        ...headers,
        Accept: 'application/json',
      };
      let serializedBody: string | undefined;
      if (options.body !== undefined) {
        requestHeaders['Content-Type'] = 'application/json';
        serializedBody = JSON.stringify(options.body);
      }

      const upstreamRes = await this.fetchImpl(`${this.sagApiUrl}${path}`, {
        method,
        headers: requestHeaders,
        body: serializedBody,
        signal: abortController.signal,
      });

      if (!upstreamRes.ok) {
        let errorMessage = `SAG upstream returned status ${upstreamRes.status}`;
        const rawErr = await upstreamRes.text().catch(() => '');
        if (rawErr) {
          try {
            const parsedErr = JSON.parse(rawErr) as { detail?: unknown; message?: unknown; error?: unknown };
            const detailText =
              typeof parsedErr.detail === 'string'
                ? parsedErr.detail
                : typeof parsedErr.message === 'string'
                  ? parsedErr.message
                  : typeof parsedErr.error === 'string'
                    ? parsedErr.error
                    : '';
            if (detailText) {
              errorMessage = detailText;
            }
          } catch {
            // Keep default status error message
          }
        }
        throw new SagGatewayError(
          upstreamRes.status >= 400 && upstreamRes.status < 500 ? upstreamRes.status : 502,
          'SAG_UPSTREAM_ERROR',
          errorMessage,
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

export async function flushPendingCascadeOutbox(
  userId?: string,
  limit?: number,
): Promise<FlushCascadeOutboxResult> {
  return sagResearchChatService.flushPendingCascadeOutbox(userId, limit);
}
