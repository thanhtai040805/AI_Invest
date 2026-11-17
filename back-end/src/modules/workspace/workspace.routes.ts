import { Router, Response, NextFunction } from 'express';
import { z } from 'zod';
import prisma from '../../config/database';
import { authMiddleware, optionalAuth, AuthRequest } from '../../middleware/auth';
import { getSnapshot } from '../../services/portfolio.service';
import { cached } from '../../utils/cache';

const router = Router();
const db = prisma as any;
const multiAgentAccountId = process.env.MULTI_AGENT_ACCOUNT_ID?.trim() || '940b0c70-2010-42f3-b947-797e6419b794';
const calendarDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/).refine(value => {
    const parsed = new Date(`${value}T00:00:00Z`);
    return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
  }).optional();
const dateQuery = z.object({ date: calendarDate });
function dateWindow(date: string) {
  const gte = new Date(`${date}T00:00:00+07:00`);
  return { gte, lt: new Date(gte.getTime() + 86400000) };
}

const send = async (res: Response, next: NextFunction, work: () => Promise<unknown>) => {
  try { res.json(await work()); } catch (error) { next(error); }
};

router.get('/overview', (_req, res, next) => send(res, next, () =>
  cached('workspace:overview', 60, async () => {
    const [regime, factors, news] = await Promise.all([
      db.market_regime.findFirst({ orderBy: { date: 'desc' } }),
      db.factorScore.findMany({ orderBy: { score_date: 'desc' }, take: 20 }),
      db.knowledge_documents.findMany({ orderBy: { published_date: 'desc' }, take: 8 }).catch(() => []),
    ]);
    return { regime, factors, risks: [], news };
  }),
));

router.get('/agent/portfolio', optionalAuth, (_req, res, next) =>
  send(res, next, () => getSnapshot(multiAgentAccountId)),
);

router.get('/agent', optionalAuth, (req: AuthRequest, res, next) => send(res, next, async () => {
  const { date } = dateQuery.parse(req.query);
  const limit = Math.min(Number(req.query.limit) || 200, 500);
  const agentModels = [
    'log_market_surveillance', 'log_universe_discovery', 'log_equity_research',
    'log_investment_thesis', 'log_counter_thesis', 'log_portfolio_risk',
    'log_portfolio_allocation', 'log_trade_execution', 'log_position_monitoring',
    'log_reinforcement_learning', 'log_strategy_cio', 'log_system_governance',
  ];
  // Audit timestamps are authoritative for legacy state rows with shifted timestamps.
  // Replay analysis dates remain separate from the actual write time.
  const thesisSource = `SELECT t.*, l.thesis_text AS thesis_snapshot, COALESCE(l.created_at, t.created_at) AS generated_at,
      COALESCE(l.analysis_date, (COALESCE(l.created_at, t.created_at) AT TIME ZONE 'Asia/Ho_Chi_Minh')::date) AS analysis_date
    FROM investment_theses t LEFT JOIN LATERAL (
      SELECT created_at, analysis_date, thesis_text FROM log_investment_thesis
      WHERE thesis_id::text = t.thesis_id ORDER BY created_at DESC NULLS LAST, id DESC LIMIT 1
    ) l ON TRUE`;
  const counterSource = `SELECT v.*, COALESCE(l.created_at, v.evaluated_at) AS generated_at,
      COALESCE(t.analysis_date, (COALESCE(l.created_at, v.evaluated_at) AT TIME ZONE 'Asia/Ho_Chi_Minh')::date) AS analysis_date
    FROM counter_thesis_verdicts v LEFT JOIN (${thesisSource}) t USING (thesis_id)
    LEFT JOIN LATERAL (
      SELECT created_at FROM log_counter_thesis WHERE thesis_id::text = v.thesis_id
      ORDER BY created_at DESC NULLS LAST, id DESC LIMIT 1
    ) l ON TRUE`;
  const dateColumnModels = new Set([
    'log_market_surveillance', 'log_universe_discovery', 'log_equity_research',
    'log_portfolio_risk', 'log_reinforcement_learning',
  ]);
  const logs = await Promise.all(agentModels.map(async (model) => {
    const rowLimit = model === 'log_portfolio_risk' ? Math.max(limit, 200) : limit;
    let entries: unknown[] = [];
    if (model === 'log_investment_thesis') {
      entries = await db.$queryRawUnsafe(`SELECT * FROM log_investment_thesis
          WHERE ($1::text IS NULL OR COALESCE(analysis_date, (created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date) = $1::date)
          ORDER BY id DESC LIMIT $2`, date || null, rowLimit);
    } else if (dateColumnModels.has(model)) {
      entries = await db.$queryRawUnsafe(`SELECT * FROM ${model}
          WHERE ($1::text IS NULL OR COALESCE(date, (created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date) = $1::date)
          ORDER BY date DESC NULLS LAST, id DESC LIMIT $2`, date || null, rowLimit).catch(() => []);
    } else if (model === 'log_counter_thesis') {
      entries = await db.$queryRawUnsafe(`SELECT c.* FROM log_counter_thesis c
          LEFT JOIN LATERAL (
            SELECT analysis_date FROM log_investment_thesis t
            WHERE t.thesis_id::text = c.thesis_id::text
            ORDER BY t.created_at DESC NULLS LAST, t.id DESC LIMIT 1
          ) lt ON TRUE
          WHERE ($1::text IS NULL OR COALESCE(lt.analysis_date, (c.created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date) = $1::date)
          ORDER BY c.id DESC LIMIT $2`, date || null, rowLimit).catch(() => []);
    } else if (db[model]) {
      entries = await db[model].findMany({
        where: date ? { created_at: dateWindow(date) } : {},
        take: rowLimit,
        orderBy: { id: 'desc' },
      }).catch(() => []);
    }
    return { agent: model.replace('log_', ''), entries };
  }));
  const [theses, counterTheses, resolutions, mainAccount, dateRows, decisions, positionHealth, msHistory, rlHistory] = await Promise.all([
    db.$queryRawUnsafe(`SELECT * FROM (${thesisSource}) records WHERE ($1::text IS NULL OR analysis_date = $1::date) ORDER BY generated_at DESC NULLS LAST LIMIT 200`, date || null),
    db.$queryRawUnsafe(`SELECT * FROM (${counterSource}) records WHERE ($1::text IS NULL OR analysis_date = $1::date) ORDER BY generated_at DESC NULLS LAST LIMIT 200`, date || null),
    db.$queryRawUnsafe(`SELECT c.*, COALESCE((c.verdict_payload->>'target_date')::date, t.analysis_date) AS analysis_date
      FROM cio_resolutions c LEFT JOIN log_investment_thesis t ON t.thesis_id::text = c.thesis_id::text
      WHERE ($1::text IS NULL OR COALESCE((c.verdict_payload->>'target_date')::date, t.analysis_date, (c.created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date) = $1::date)
      ORDER BY c.created_at DESC LIMIT 200;`, date || null),
    db.$queryRawUnsafe('SELECT * FROM portfolio_account WHERE account_id = $1 LIMIT 1;', multiAgentAccountId),
    db.$queryRawUnsafe(`SELECT DISTINCT to_char(analysis_date, 'YYYY-MM-DD') AS date FROM (
      SELECT date AS analysis_date FROM log_market_surveillance
      UNION ALL SELECT date AS analysis_date FROM log_universe_discovery
      UNION ALL SELECT date AS analysis_date FROM log_equity_research
      UNION ALL SELECT date AS analysis_date FROM log_portfolio_risk
      UNION ALL SELECT date AS analysis_date FROM log_reinforcement_learning
      UNION ALL SELECT COALESCE(analysis_date, (created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date) AS analysis_date FROM log_investment_thesis
      UNION ALL SELECT (created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS analysis_date FROM log_counter_thesis
      UNION ALL SELECT (created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS analysis_date FROM log_portfolio_allocation
      UNION ALL SELECT (created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS analysis_date FROM log_trade_execution
      UNION ALL SELECT (created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS analysis_date FROM log_position_monitoring
      UNION ALL SELECT (created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS analysis_date FROM log_strategy_cio
      UNION ALL SELECT COALESCE((c.verdict_payload->>'target_date')::date, t.analysis_date, (c.created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date) AS analysis_date
        FROM cio_resolutions c LEFT JOIN log_investment_thesis t ON t.thesis_id::text = c.thesis_id::text
      UNION ALL SELECT analysis_date FROM (${thesisSource}) theses
      UNION ALL SELECT analysis_date FROM (${counterSource}) verdicts
      UNION ALL SELECT date AS analysis_date FROM portfolio_decisions
    ) records WHERE analysis_date IS NOT NULL ORDER BY date DESC`),
    db.$queryRawUnsafe("SELECT * FROM portfolio_decisions WHERE ($1::text IS NULL OR date = $1::date) ORDER BY created_at DESC, decision_id DESC LIMIT 200", date || null),
    db.$queryRawUnsafe(`SELECT h.* FROM position_health_ticks h JOIN positions p ON p.symbol=h.ticker
      WHERE p.user_id=$1 AND p.quantity>0 ORDER BY h.ticker`, multiAgentAccountId),
    db.$queryRawUnsafe(
      `SELECT DISTINCT ON (COALESCE(date, (created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date)) *
       FROM log_market_surveillance
       WHERE ($1::text IS NULL OR COALESCE(date, (created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date) <= $1::date)
       ORDER BY COALESCE(date, (created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date) DESC, id DESC
       LIMIT 15`,
      date || null
    ).catch(() => []),
    db.$queryRawUnsafe(
      `SELECT DISTINCT ON (COALESCE(date, (created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date)) *
       FROM log_reinforcement_learning
       WHERE ($1::text IS NULL OR COALESCE(date, (created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date) <= $1::date)
       ORDER BY COALESCE(date, (created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date) DESC, id DESC
       LIMIT 5`,
      date || null
    ).catch(() => []),
  ]);
  // Legacy account/health fields are monitoring snapshots. Current valuation is
  // served separately so minute refreshes never reload the entire audit log.
  return { logs, theses, counterTheses, resolutions, decisions, positionHealth, msHistory, rlHistory,
    dates: dateRows.map((row: any) => row.date), risks: [], account: mainAccount[0] || null, mode: 'SHADOW' };
}));

router.get('/research', (_req, res, next) => send(res, next, async () => {
  const rows = await db.$queryRawUnsafe(`
    SELECT * FROM (
      SELECT DISTINCT ON (t.ticker) t.*, COALESCE(log.generated_at, t.created_at) AS generated_at,
        log.thesis_snapshot, log.analysis_date,
        row_to_json(v) AS counter_verdict
      FROM investment_theses t LEFT JOIN counter_thesis_verdicts v USING (thesis_id)
      LEFT JOIN (
        SELECT thesis_id::text, max(created_at) AS generated_at,
          (array_agg(thesis_text ORDER BY created_at DESC NULLS LAST, id DESC))[1] AS thesis_snapshot,
          (array_agg(analysis_date ORDER BY created_at DESC NULLS LAST, id DESC))[1] AS analysis_date
        FROM log_investment_thesis GROUP BY thesis_id
      ) log ON log.thesis_id = t.thesis_id
      ORDER BY t.ticker, COALESCE(log.generated_at, t.created_at) DESC NULLS LAST, t.thesis_id DESC
    ) latest ORDER BY generated_at DESC NULLS LAST, thesis_id DESC;
  `);
  const theses = rows.map((row: any) => {
    let snapshot: any = {};
    if (typeof row.thesis_snapshot === 'string') {
      try { snapshot = JSON.parse(row.thesis_snapshot); } catch { /* Legacy free-text log. */ }
    } else if (row.thesis_snapshot && typeof row.thesis_snapshot === 'object') {
      snapshot = row.thesis_snapshot;
    }
    const body = snapshot?.thesis_body ?? {};
    const { thesis_snapshot: _dropSnapshot, ...rest } = row;
    return {
      ...rest,
      why_now: row.why_now ?? body.why_now ?? snapshot?.why_now ?? null,
      why_this_stock: row.why_this_stock ?? body.why_this_stock ?? snapshot?.why_this_stock ?? null,
      catalyst_type: row.catalyst_type ?? body.catalyst?.primary_type ?? snapshot?.catalyst_type,
      catalyst_description: row.catalyst_description ?? body.catalyst?.description ?? snapshot?.catalyst_description,
    };
  });
  return { theses };
}));

router.get('/watchlists', authMiddleware, (req: AuthRequest, res, next) => send(res, next, () =>
  db.watchlist.findMany({ where: { userId: req.userId }, orderBy: { createdAt: 'desc' } }),
));

const thesisHistoryQuery = z.object({
  page: z.coerce.number().int().min(1).max(100000).default(1),
  ticker: z.string().trim().max(16).default(''),
  date: calendarDate,
});

router.get('/research/thesis-history', (req, res, next) => send(res, next, async () => {
  const query = thesisHistoryQuery.parse(req.query);
  // Legacy rows can only be grouped by actual write day; never infer replay dates.
  const effectiveDate = "COALESCE(analysis_date, (created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date)";
  const filter = `($1 = '' OR ticker = $1) AND ($2::date IS NULL OR ${effectiveDate} = $2::date)`;
  const ticker = query.ticker.toUpperCase();
  const pageSize = 20;
  const [rows, counts, dateRows] = await Promise.all([
    db.$queryRawUnsafe(`SELECT * FROM log_investment_thesis WHERE ${filter}
      ORDER BY ${effectiveDate} DESC NULLS LAST, created_at DESC, id DESC LIMIT $3 OFFSET $4`, ticker, query.date || null, pageSize, (query.page - 1) * pageSize),
    db.$queryRawUnsafe(`SELECT count(*)::int AS total FROM log_investment_thesis WHERE ${filter}`, ticker, query.date || null),
    db.$queryRawUnsafe(`SELECT DISTINCT to_char(${effectiveDate}, 'YYYY-MM-DD') AS date
      FROM log_investment_thesis WHERE ${effectiveDate} IS NOT NULL AND ($1 = '' OR ticker = $1) ORDER BY date DESC`, ticker),
  ]);
  const total = counts[0]?.total ?? 0;
  const theses = rows.map((row: any) => {
    let snapshot: any = {};
    try { snapshot = JSON.parse(row.thesis_text); } catch { /* Legacy free-text log. */ }
    const body = snapshot?.thesis_body ?? {};
    return {
      history_id: String(row.id), thesis_id: row.thesis_id, ticker: row.ticker,
      created_at: row.created_at, analysis_date: row.analysis_date ? new Date(row.analysis_date).toISOString().slice(0, 10) : null,
      is_replay: row.is_replay, status: snapshot?.status ?? 'UNKNOWN',
      catalyst_type: body.catalyst?.primary_type ?? snapshot?.catalyst_type,
      catalyst_description: body.catalyst?.description ?? snapshot?.catalyst_description,
      why_now: body.why_now, why_this_stock: body.why_this_stock,
      target_price_range: body.price_target?.target_range ?? snapshot?.target_price_range,
      confirming_signals: snapshot?.input_validation?.independent_signals ?? snapshot?.confirming_signals,
      invalidation_conditions: body.exit_conditions?.invalidation_triggers ?? snapshot?.invalidation_conditions,
      pre_mortem_scenarios: row.pre_mortem_scenarios,
    };
  });
  return { theses, total, page: query.page, pageSize, dates: dateRows.map((row: any) => row.date) };
}));

const watchlistSchema = z.object({ name: z.string().min(1).max(80), symbols: z.array(z.string()).optional() });
router.post('/watchlists', authMiddleware, async (req: AuthRequest, res, next) => {
  try {
    const body = watchlistSchema.parse(req.body);
    const item = await db.watchlist.create({ data: { userId: req.userId!, name: body.name, symbols: (body.symbols ?? []).map(s => s.toUpperCase()) } });
    res.status(201).json(item);
  } catch (error) { next(error); }
});
router.patch('/watchlists/:id', authMiddleware, async (req: AuthRequest, res, next) => {
  try {
    const body = watchlistSchema.partial().parse(req.body);
    const owned = await db.watchlist.findFirst({ where: { id: req.params.id, userId: req.userId } });
    if (!owned) { res.status(404).json({ code: 'NOT_FOUND', message: 'Không tìm thấy danh sách theo dõi.' }); return; }
    res.json(await db.watchlist.update({ where: { id: owned.id }, data: { ...body, symbols: body.symbols?.map(s => s.toUpperCase()) } }));
  } catch (error) { next(error); }
});
router.delete('/watchlists/:id', authMiddleware, async (req: AuthRequest, res, next) => {
  try {
    const result = await db.watchlist.deleteMany({ where: { id: req.params.id, userId: req.userId } });
    if (!result.count) { res.status(404).json({ code: 'NOT_FOUND', message: 'Không tìm thấy danh sách theo dõi.' }); return; }
    res.status(204).send();
  } catch (error) { next(error); }
});

export default router;
