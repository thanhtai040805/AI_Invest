import { Router, Response, NextFunction } from 'express';
import { z } from 'zod';
import prisma from '../../config/database';
import { authMiddleware, AuthRequest } from '../../middleware/auth';

const router = Router();
const db = prisma as any;
router.use(authMiddleware);

const send = async (res: Response, next: NextFunction, work: () => Promise<unknown>) => {
  try { res.json(await work()); } catch (error) { next(error); }
};

router.get('/overview', (_req, res, next) => send(res, next, async () => {
  const [regime, signals, factors, news] = await Promise.all([
    db.market_regime.findFirst({ orderBy: { date: 'desc' } }),
    db.signals.findMany({ orderBy: { signal_date: 'desc' }, take: 20 }),
    db.factorScore.findMany({ orderBy: { score_date: 'desc' }, take: 20 }),
    db.knowledge_documents.findMany({ orderBy: { published_date: 'desc' }, take: 8 }),
  ]);
  return { regime, signals, factors, risks: [], news };
}));

router.get('/signals', (_req, res, next) => send(res, next, async () => {
  const current = await db.$queryRawUnsafe(`
    SELECT s.symbol, s.signal_date, s.signal, s.composite_rank, s.hard_flags, s.soft_flags, s.sector_group,
           COALESCE((d.foreign_net_vol * d.close_adj * 1000 / 1e9)::float8, 0) AS foreign_flow,
           (d.close_adj * 1000)::float8 AS price,
           CASE WHEN d.open_adj IS NOT NULL AND d.open_adj <> 0
             THEN ((d.close_adj - d.open_adj) / d.open_adj) * 100 ELSE 0 END AS change_pct
    FROM signals s
    LEFT JOIN LATERAL (
      SELECT close_adj, open_adj, foreign_net_vol
      FROM market_data_daily
      WHERE ticker = s.symbol
      ORDER BY date DESC
      LIMIT 1
    ) d ON true
    ORDER BY s.signal_date DESC
    LIMIT 100;
  `).catch(async () => db.signals.findMany({ orderBy: { signal_date: 'desc' }, take: 100 }));

  const history = await db.signal_log.findMany({ orderBy: { signal_date: 'desc' }, take: 100 }).catch(() => []);
  return { current, history };
}));

router.get('/agent', (_req, res, next) => send(res, next, async () => {
  const agentModels = [
    'log_market_surveillance', 'log_universe_discovery', 'log_equity_research',
    'log_investment_thesis', 'log_counter_thesis', 'log_portfolio_risk',
    'log_portfolio_allocation', 'log_trade_execution', 'log_position_monitoring',
    'log_reinforcement_learning', 'log_strategy_cio', 'log_system_governance',
  ];
  const logs = await Promise.all(agentModels.map(async (model) => ({
    agent: model.replace('log_', ''),
    entries: db[model] ? await db[model].findMany({ take: 10, orderBy: { id: 'desc' } }).catch(() => []) : [],
  })));
  const [theses, counterTheses, resolutions, mainAccount] = await Promise.all([
    db.$queryRawUnsafe('SELECT * FROM investment_theses ORDER BY created_at DESC LIMIT 30;').catch(() => []),
    db.$queryRawUnsafe('SELECT * FROM counter_thesis_verdicts ORDER BY evaluated_at DESC LIMIT 30;').catch(() => []),
    db.$queryRawUnsafe('SELECT * FROM cio_resolutions ORDER BY created_at DESC LIMIT 30;').catch(() => []),
    db.$queryRawUnsafe("SELECT * FROM portfolio_account WHERE account_id = 'MAIN_FUND' LIMIT 1;").catch(() => []),
  ]);
  return { logs, theses, counterTheses, resolutions, risks: [], account: mainAccount[0] || null, mode: 'SHADOW' };
}));

router.get('/ml-fund', (req: AuthRequest, res, next) => send(res, next, async () => {
  const [mlAccountRows, mainAccountRows, userRows, predictions, metrics, trades] = await Promise.all([
    db.$queryRawUnsafe("SELECT * FROM portfolio_account WHERE account_id = 'standalone-pure-ml-fund-account' LIMIT 1;").catch(() => []),
    db.$queryRawUnsafe("SELECT * FROM portfolio_account WHERE account_id = 'MAIN_FUND' LIMIT 1;").catch(() => []),
    req.userId ? db.$queryRawUnsafe('SELECT id, cash_balance, display_name FROM users WHERE id = $1 LIMIT 1;', req.userId).catch(() => []) : [],
    db.$queryRawUnsafe('SELECT * FROM standalone_ml_predictions ORDER BY predict_date DESC LIMIT 100;').catch(() => []),
    db.$queryRawUnsafe('SELECT * FROM mral_metrics ORDER BY metric_date DESC LIMIT 100;').catch(() => []),
    db.$queryRawUnsafe('SELECT * FROM paper_trades ORDER BY created_at DESC LIMIT 100;').catch(() => []),
  ]);
  return {
    account: mlAccountRows[0] || null,
    mainAccount: mainAccountRows[0] || null,
    userAccount: userRows[0] || null,
    predictions,
    metrics,
    trades,
    mode: 'PAPER'
  };
}));

router.get('/research', (_req, res, next) => send(res, next, async () => ({
  documents: await db.knowledge_documents.findMany({ take: 100, orderBy: { published_date: 'desc' } }).catch(() => []),
  theses: await db.$queryRawUnsafe('SELECT * FROM investment_theses ORDER BY created_at DESC LIMIT 100;').catch(() => []),
})));

router.get('/watchlists', (req: AuthRequest, res, next) => send(res, next, () =>
  db.watchlist.findMany({ where: { userId: req.userId }, orderBy: { createdAt: 'desc' } }),
));

const watchlistSchema = z.object({ name: z.string().min(1).max(80), symbols: z.array(z.string()).default([]) });
router.post('/watchlists', async (req: AuthRequest, res, next) => {
  try {
    const body = watchlistSchema.parse(req.body);
    const item = await db.watchlist.create({ data: { userId: req.userId!, name: body.name, symbols: body.symbols.map(s => s.toUpperCase()) } });
    res.status(201).json(item);
  } catch (error) { next(error); }
});
router.patch('/watchlists/:id', async (req: AuthRequest, res, next) => {
  try {
    const body = watchlistSchema.partial().parse(req.body);
    const owned = await db.watchlist.findFirst({ where: { id: req.params.id, userId: req.userId } });
    if (!owned) { res.status(404).json({ code: 'NOT_FOUND', message: 'Không tìm thấy danh sách theo dõi.' }); return; }
    res.json(await db.watchlist.update({ where: { id: owned.id }, data: { ...body, symbols: body.symbols?.map(s => s.toUpperCase()) } }));
  } catch (error) { next(error); }
});
router.delete('/watchlists/:id', async (req: AuthRequest, res, next) => {
  try {
    const result = await db.watchlist.deleteMany({ where: { id: req.params.id, userId: req.userId } });
    if (!result.count) { res.status(404).json({ code: 'NOT_FOUND', message: 'Không tìm thấy danh sách theo dõi.' }); return; }
    res.status(204).send();
  } catch (error) { next(error); }
});

export default router;
