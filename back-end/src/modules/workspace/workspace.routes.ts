import { Router, Response, NextFunction } from 'express';
import { z } from 'zod';
import prisma from '../../config/database';
import { authMiddleware, optionalAuth, AuthRequest } from '../../middleware/auth';
import { getSnapshot } from '../../services/portfolio.service';
import { accountLedger, ExecutionReceipt } from '../../services/portfolioAccounting';

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

router.get('/overview', (_req, res, next) => send(res, next, async () => {
  const [regime, factors, news] = await Promise.all([
    db.market_regime.findFirst({ orderBy: { date: 'desc' } }),
    db.factorScore.findMany({ orderBy: { score_date: 'desc' }, take: 20 }),
    db.knowledge_documents.findMany({ orderBy: { published_date: 'desc' }, take: 8 }).catch(() => []),
  ]);
  return { regime, factors, risks: [], news };
}));

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
  const logs = await Promise.all(agentModels.map(async (model) => ({
    agent: model.replace('log_', ''),
    entries: model === 'log_investment_thesis'
      ? await db.$queryRawUnsafe(`SELECT * FROM log_investment_thesis
          WHERE ($1::text IS NULL OR COALESCE(analysis_date, (created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date) = $1::date)
          ORDER BY id DESC LIMIT $2`, date || null, limit)
      : db[model] ? await db[model].findMany({ where: date ? { created_at: dateWindow(date) } : {}, take: limit, orderBy: { id: 'desc' } }).catch(() => []) : [],
  })));
  const [theses, counterTheses, resolutions, mainAccount, dateRows, decisions, positionHealth] = await Promise.all([
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
  ]);
  // Legacy account/health fields are monitoring snapshots. Current valuation is
  // served separately so minute refreshes never reload the entire audit log.
  return { logs, theses, counterTheses, resolutions, decisions, positionHealth,
    dates: dateRows.map((row: any) => row.date), risks: [], account: mainAccount[0] || null, mode: 'SHADOW' };
}));

router.get('/ml-fund', optionalAuth, (req: AuthRequest, res, next) => send(res, next, async () => {
  const { date, from, to } = z.object({ date: calendarDate, from: calendarDate, to: calendarDate })
    .refine(q => !q.from || !q.to || q.from <= q.to, 'Ngày bắt đầu phải trước ngày kết thúc').parse(req.query);
  const accountId = process.env.STANDALONE_ML_ACCOUNT_ID?.trim() || 'standalone-pure-ml-fund-account';
  const { accounts, positions, receipts, snapshots } = await db.$transaction(async (tx: any) => {
    const [accounts, positions, receipts, snapshots] = await Promise.all([
    tx.$queryRawUnsafe(`SELECT a.account_id, u.cash_balance, a.updated_at
      FROM portfolio_account a LEFT JOIN users u ON u.id = a.account_id WHERE a.account_id = $1`, accountId),
    tx.$queryRawUnsafe(`SELECT p.id, p.symbol, p.quantity, p.avg_price,
        md.close_unadj * 1000 AS current_price, to_char(md.date,'YYYY-MM-DD') AS price_date
      FROM positions p LEFT JOIN LATERAL (
        SELECT close_unadj, date FROM market_data_daily
        WHERE ticker = p.symbol AND date <= (now() AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AND close_unadj > 0
        ORDER BY date DESC LIMIT 1
      ) md ON TRUE WHERE p.user_id = $1 AND p.quantity > 0 ORDER BY p.symbol`, accountId),
    tx.$queryRawUnsafe(`SELECT o.symbol, CASE WHEN e.action=o.side OR
        (e.action IN ('SELL','SELL_MP') AND o.side IN ('SELL','SELL_MP')) THEN o.side END AS side,
        e.shares, e.gross_value, e.brokerage_fee, e.transfer_tax, e.cash_delta,
        e.order_id::text AS "executionId", to_char(e.executed_at AT TIME ZONE 'Asia/Ho_Chi_Minh','YYYY-MM-DD') AS "executedDate"
      FROM orders o LEFT JOIN order_executions e ON e.order_id::text=o.id
      WHERE o.user_id=$1 AND (e.order_id IS NOT NULL OR o.status IN ('FILLED','FILLED_REPLAY','EXECUTED','PARTIALLY_EXECUTED'))
      ORDER BY e.executed_at NULLS LAST, o.created_at, o.id`, accountId),
    tx.$queryRawUnsafe(`SELECT to_char(date,'YYYY-MM-DD') AS date, total_nav::float, cash_balance::float
      FROM portfolio_nav_history WHERE account_id=$1 AND date <= (now() AT TIME ZONE 'Asia/Ho_Chi_Minh')::date ORDER BY date`, accountId),
    ]);
    return { accounts, positions, receipts, snapshots };
  }, { isolationLevel: 'RepeatableRead' });
  const [predictions, dateRows, accuracyRows, history, predictionSummary] = await Promise.all([
    db.$queryRawUnsafe(`SELECT p.*, o.status AS order_status, o.price AS order_price
      FROM standalone_ml_predictions p LEFT JOIN orders o ON o.id = p.order_id
      WHERE p.account_id = $1 AND p.predict_date = COALESCE($2::date,
        (SELECT max(predict_date) FROM standalone_ml_predictions WHERE account_id = $1))
      ORDER BY p.rank_pred DESC NULLS LAST, p.ticker`, accountId, date || null),
    db.$queryRawUnsafe("SELECT DISTINCT to_char(predict_date, 'YYYY-MM-DD') AS date FROM standalone_ml_predictions WHERE account_id = $1 ORDER BY date DESC", accountId),
    db.$queryRawUnsafe(`SELECT count(*)::int AS total_evaluated,
        count(*) FILTER (WHERE (accuracy_evaluated_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date = (now() AT TIME ZONE 'Asia/Ho_Chi_Minh')::date)::int AS evaluated_today,
        avg(CASE WHEN survival_outcome THEN 100.0 WHEN survival_outcome = false THEN 0.0 END)::float AS realized_survival_rate,
        (avg(surv_prob) * 100)::float AS predicted_avg_survival_prob,
        avg(CASE WHEN mom_pred IS NOT NULL AND realized_3d_ret IS NOT NULL THEN
          CASE WHEN (mom_pred > 0 AND realized_3d_ret > 0) OR (mom_pred <= 0 AND realized_3d_ret <= 0) THEN 100.0 ELSE 0.0 END END)::float AS directional_hit_rate,
        (avg(realized_3d_ret) * 100)::float AS avg_realized_3d_ret,
        (avg(mom_pred) * 100)::float AS avg_predicted_3d_ret
      FROM standalone_ml_predictions WHERE account_id = $1 AND accuracy_evaluated_at IS NOT NULL
        AND ($2::date IS NULL OR predict_date >= $2::date) AND ($3::date IS NULL OR predict_date <= $3::date)`, accountId, from || null, to || null),
    db.$queryRawUnsafe(`SELECT * FROM standalone_ml_predictions
      WHERE account_id = $1 AND accuracy_evaluated_at IS NOT NULL
        AND ($2::date IS NULL OR predict_date >= $2::date) AND ($3::date IS NULL OR predict_date <= $3::date)
      ORDER BY predict_date DESC, ticker LIMIT 200`, accountId, from || null, to || null),
    db.$queryRawUnsafe(`SELECT to_char(predict_date,'YYYY-MM-DD') AS date,
        to_char(max(feature_date),'YYYY-MM-DD') AS feature_date,
        count(*)::int AS total_predictions, count(accuracy_evaluated_at)::int AS evaluated
      FROM standalone_ml_predictions WHERE account_id=$1
        AND ($2::date IS NULL OR predict_date >= $2::date) AND ($3::date IS NULL OR predict_date <= $3::date)
      GROUP BY predict_date ORDER BY predict_date DESC`, accountId, from || null, to || null),
  ]);
  const cash = accounts[0]?.cash_balance == null ? null : Number(accounts[0].cash_balance);
  const ledger = accountLedger(receipts as ExecutionReceipt[], positions.map((p: any) => ({
    symbol: p.symbol, quantity: p.quantity, avgPrice: p.avg_price,
  })), cash ?? 0, { from, to });
  const ledgerComplete = cash !== null && ledger.complete;
  const markedPositions = positions.map((p: any) => {
    const market_value = p.current_price == null ? null : Number(p.current_price) * p.quantity;
    const cost_basis = ledgerComplete ? ledger.costBySymbol[p.symbol] ?? null : null;
    return { ...p, market_value, cost_basis,
      unrealized_pnl: market_value !== null && cost_basis !== null ? market_value - cost_basis : null };
  });
  const nav = cash == null || markedPositions.some((p: any) => p.market_value == null)
    ? null : cash + markedPositions.reduce((sum: number, p: any) => sum + p.market_value, 0);
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Ho_Chi_Minh' }).format(new Date());
  const marketDates = snapshots.length ? await db.$queryRawUnsafe(`SELECT DISTINCT to_char(date,'YYYY-MM-DD') AS trading_date
      FROM market_data_daily WHERE date BETWEEN $1::date AND $2::date ORDER BY trading_date`, snapshots[0].date, to || today) : [];
  const tradingDates = marketDates.map((row: any) => row.trading_date);
  const snapshotByDate = new Map<string, any>(snapshots.map((s: any) => [s.date, s]));
  const missingDates = tradingDates.filter((date: string) => !snapshotByDate.has(date) && (!from || date >= from) && (!to || date <= to));
  // The first snapshot is the opening baseline, not a performance observation.
  const selected = snapshots.slice(1).filter((s: any) => (!from || s.date >= from) && (!to || s.date <= to));
  const baselineDate = from && snapshots.length && from > snapshots[0].date
    ? tradingDates.filter((date: string) => date < from).at(-1) ?? null : snapshots[0]?.date ?? null;
  const baseline = baselineDate ? snapshotByDate.get(baselineDate) : null;
  const closingDate = to ? tradingDates.filter((date: string) => !from || date >= from).at(-1) ?? null : selected.at(-1)?.date ?? null;
  const last = closingDate && selected.length ? snapshotByDate.get(closingDate) : null;
  const periodReceipts = (receipts as ExecutionReceipt[]).filter(r => r.executedDate &&
    (!from || r.executedDate >= from) && (!to || r.executedDate <= to));
  const executionStats = {
    fills: periodReceipts.length,
    fees: ledgerComplete ? Number(periodReceipts.reduce((sum, r) => sum + Number(r.brokerage_fee) + Number(r.transfer_tax), 0).toFixed(2)) : null,
    missing_receipts: (receipts as ExecutionReceipt[]).filter(r => !r.executedDate ||
      [r.cash_delta, r.gross_value, r.brokerage_fee, r.transfer_tax].some(v => v == null)).length,
  };
  const performance = {
    baselineDate, closingDate, startDate: selected[0]?.date || null, endDate: last?.date || null,
    openingNav: baseline?.total_nav ?? null, closingNav: last?.total_nav ?? null,
    pnl: baseline && last ? last.total_nav - baseline.total_nav : null,
    returnPct: baseline?.total_nav > 0 && last ? (last.total_nav / baseline.total_nav - 1) * 100 : null,
    ...executionStats,
    sessions: selected.map((s: any) => {
      const previousIndex = snapshots.findIndex((point: any) => point.date === s.date) - 1;
      const previous = previousIndex >= 0 ? snapshots[previousIndex] : null;
      const marketIndex = tradingDates.indexOf(s.date);
      const previousSessionDate = marketIndex > 0 ? tradingDates[marketIndex - 1] : null;
      return { ...s, previousNavDate: previous?.date ?? null, previousSessionDate,
        dailyPnl: previous && previous.date === previousSessionDate ? s.total_nav - previous.total_nav : null,
        cumulativePnl: baseline ? s.total_nav - baseline.total_nav : null };
    }),
    missingDates,
    equityCurve: snapshots.filter((s: any) => (!from || s.date >= from) && (!to || s.date <= to))
      .map((s: any) => ({ date: s.date, value: s.total_nav })),
  };
  const sales = ledgerComplete ? ledger.sales!.filter(s => s.date && (!from || s.date >= from) && (!to || s.date <= to)).reverse() : null;
  return {
    account: accounts[0] ? { ...accounts[0], cash_balance: cash,
      total_nav: snapshots.at(-1)?.total_nav ?? null, estimated_nav: nav } : null,
    latestClose: snapshots.at(-1) ?? null,
    trading: { ledgerComplete, realizedPnl: ledgerComplete ? ledger.periodRealizedPnl : null, sales },
    accountId, positions: markedPositions, predictions,
    dates: dateRows.map((row: any) => row.date), selectedDate: date || dateRows[0]?.date || null,
    navDates: snapshots.map((s: any) => s.date), predictionSummary,
    accuracy: accuracyRows[0], history, historyLimit: 200, performance, range: { from: from || null, to: to || null }, mode: 'PAPER',
  };
}));

router.get('/research', (_req, res, next) => send(res, next, async () => ({
  theses: await db.$queryRawUnsafe(`
    SELECT * FROM (
      SELECT DISTINCT ON (t.ticker) t.*, COALESCE(log.generated_at, t.created_at) AS generated_at,
        row_to_json(v) AS counter_verdict
      FROM investment_theses t LEFT JOIN counter_thesis_verdicts v USING (thesis_id)
      LEFT JOIN (
        SELECT thesis_id::text, max(created_at) AS generated_at
        FROM log_investment_thesis GROUP BY thesis_id
      ) log ON log.thesis_id = t.thesis_id
      ORDER BY t.ticker, COALESCE(log.generated_at, t.created_at) DESC NULLS LAST, t.thesis_id DESC
    ) latest ORDER BY generated_at DESC NULLS LAST, thesis_id DESC;
  `),
})));

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

const watchlistSchema = z.object({ name: z.string().min(1).max(80), symbols: z.array(z.string()).default([]) });
router.post('/watchlists', authMiddleware, async (req: AuthRequest, res, next) => {
  try {
    const body = watchlistSchema.parse(req.body);
    const item = await db.watchlist.create({ data: { userId: req.userId!, name: body.name, symbols: body.symbols.map(s => s.toUpperCase()) } });
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
