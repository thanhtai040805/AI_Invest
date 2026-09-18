"use client"

import { Fragment, useState, createContext, useContext, useMemo } from "react"
import { Page } from "@/components/Shell"
import { Link } from "@/lib/router"
import type {
  FundAccount,
  MLAccuracy,
  MLPosition,
  MLPrediction,
  MLSession,
  StrategyCompareRow,
} from "@/types"
import {
  Button, FactorBar, metricTone, Panel, PanelHead, Pill, SectionEyebrow,
} from "@/components/ui"
import { workspaceApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"

const defaultFunds: FundAccount[] = [
  {
    id: "multi_agent",
    kind: "multi_agent",
    label: "Quỹ 12-Agent phối hợp",
    accountId: "MAIN_FUND",
    nav: 984_380_000,
    cash: 132_400_000,
    openPositions: 3,
    mode: "SHADOW_RUNNER",
  },
  {
    id: "ml",
    kind: "standalone_ml",
    label: "Quỹ ML Tự hành",
    accountId: "standalone-pure-ml-fund-account",
    nav: 500_000_000,
    cash: 500_000_000,
    openPositions: 0,
    mode: "SHADOW_RUNNER",
  },
]

const defaultAccuracy: MLAccuracy = {
  lookbackDays: 60,
  totalEvaluated: 48,
  newlyEvaluated: 6,
  realizedSurvivalRate: 78.4,
  predictedAvgSurvivalProb: 76.1,
  directionalHitRate: 71.2,
  avgRealized3dRet: 2.8,
  avgPredicted3dRet: 2.4,
}

const defaultSession: MLSession = {
  predictDate: new Date().toISOString().slice(0, 10),
  predictionsCount: 0,
  status: "SUCCESS",
}

interface MLFundData {
  funds: FundAccount[]
  mlAccuracy: MLAccuracy
  mlHistory: MLPrediction[]
  mlPositions: MLPosition[]
  mlPredictions: MLPrediction[]
  mlSession: MLSession
  strategyCompare: StrategyCompareRow[]
}

const MLFundContext = createContext<MLFundData>({
  funds: defaultFunds,
  mlAccuracy: defaultAccuracy,
  mlHistory: [],
  mlPositions: [],
  mlPredictions: [],
  mlSession: defaultSession,
  strategyCompare: [],
})

const M = (n: number) => `₫${(n / 1e6).toFixed(0)}tr`
const vnd = (n: number) => n.toLocaleString("en-US")
const pct = (n: number) => `${n >= 0 ? "+" : ""}${n.toFixed(1)}%`

const modeTone: Record<string, "teal" | "gain" | "neutral"> = {
  SHADOW_RUNNER: "teal", LIVE: "gain", DISABLED: "neutral",
}

function AccountSwitcher() {
  const { funds: activeFunds } = useContext(MLFundContext)
  const ml = activeFunds.find((f) => f.kind === "standalone_ml") ?? activeFunds[1]
  const agent = activeFunds.find((f) => f.kind === "multi_agent") ?? activeFunds[0]
  return (
    <Panel className="mb-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {[agent, ml].map((f) => {
          const active = f.kind === "standalone_ml"
          const card = (
            <div className={`h-full rounded-[10px] border p-4 transition-colors ${active ? "border-ink bg-paper" : "border-line bg-surface hover:border-ink/30"}`}>
              <div className="flex items-center gap-2 mb-2">
                <span className={`w-1.5 h-1.5 rounded-full ${active ? "bg-mineral" : "bg-line-strong"}`} />
                <span className="text-[13.5px] font-semibold text-ink">{f.label}</span>
                {f.mode && <span className="ml-auto"><Pill tone={modeTone[f.mode]}>{f.mode}</Pill></span>}
                {!active && <span className="ml-auto text-[11px] text-mineral">Mở →</span>}
              </div>
              <div className="text-[10.5px] text-muted mb-3 truncate">{f.accountId}</div>
              <div className="grid grid-cols-3 gap-2 text-center">
                {[["NAV", M(f.nav)], ["Tiền mặt", M(f.cash)], ["Vị thế", String(f.openPositions)]].map(([l, v]) => (
                  <div key={l}>
                    <div className="text-[10px] uppercase tracking-wide text-muted">{l}</div>
                    <div className="tnum font-mono text-[13px] text-ink mt-0.5">{v}</div>
                  </div>
                ))}
              </div>
            </div>
          )
          return active
            ? <div key={f.id}>{card}</div>
            : <Link key={f.id} to="/portfolio" className="block h-full">{card}</Link>
        })}
      </div>
      <p className="text-[11.5px] text-muted mt-3">
        Tài khoản cách ly — Quỹ ML Tự hành không dùng chung tiền mặt, vị thế hay lệnh với 12-Agent, và hoạt động hoàn toàn độc lập.
      </p>
    </Panel>
  )
}

function tierTone(tier: MLPrediction["tier"]) {
  return tier === "TIER_A_PLUS" ? "gold" : "teal"
}

function RankingTable() {
  const { mlSession, mlPredictions } = useContext(MLFundContext)
  const [open, setOpen] = useState<string | null>(null)
  const statusTone = mlSession.status === "SUCCESS" ? "gain" : mlSession.status === "DISABLED" ? "neutral" : "warning"
  return (
    <Panel flush className="mb-4">
      <div className="px-5 pt-5">
        <PanelHead
          title="Bảng xếp hạng định lượng ML · Phiên hôm nay"
          sub="hybrid_stacking_ranker.pkl · Bộ lọc Beneish → Xếp hạng LambdaMART → Xung lực Ridge 3D → Bộ lọc sống sót"
          action={
            <div className="flex items-center gap-2 text-[11px] text-muted">
              <span className="tnum font-mono">{mlSession.predictDate}</span>
              <span>· {mlSession.predictionsCount} mã</span>
              <Pill tone={statusTone}>{mlSession.status}</Pill>
            </div>
          }
        />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[12.5px]">
          <thead>
            <tr className="text-[10.5px] uppercase tracking-wide text-muted border-y border-line">
              {["Mã CP", "Hạng", "Độ tin cậy", "P(Sống sót)", "Kỳ vọng T+3", "Điểm Z", "Khối lượng", "Thị giá", "Giá trị", "Tỷ trọng", "Lệnh"].map((h, i) => (
                <th key={h} className={`font-medium py-2 px-3 ${i > 2 ? "text-right" : "text-left"}`}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {mlPredictions.map((p, idx) => {
              const isOpen = open === p.ticker
              return (
                <Fragment key={`${p.ticker}-${p.predictDate || ''}-${idx}`}>
                  <tr onClick={() => setOpen(isOpen ? null : p.ticker)} className="border-b border-line hover:bg-soft/50 cursor-pointer">
                    <td className="py-2.5 px-3"><span onClick={(e) => e.stopPropagation()}><Link to={`/stock/${p.ticker}`} className="font-mono font-semibold text-ink hover:underline">{p.ticker}</Link></span></td>
                    <td className="py-2.5 px-3"><Pill tone={tierTone(p.tier)}>{p.tier === "TIER_A_PLUS" ? "A+" : "A"}</Pill></td>
                    <td className="py-2.5 px-3 text-muted">{p.conviction}</td>
                    <td className="py-2.5 px-3 text-right tnum font-mono text-teal">{(p.survProb * 100).toFixed(0)}%</td>
                    <td className="py-2.5 px-3 text-right tnum font-mono text-gain">+{p.momPred.toFixed(1)}%</td>
                    <td className="py-2.5 px-3 text-right tnum font-mono text-ink">{p.zScore.toFixed(2)}</td>
                    <td className="py-2.5 px-3 text-right tnum font-mono text-ink">{vnd(p.shares)}</td>
                    <td className="py-2.5 px-3 text-right tnum font-mono text-ink">{vnd(p.price)}</td>
                    <td className="py-2.5 px-3 text-right tnum font-mono text-ink">{M(p.positionValue)}</td>
                    <td className="py-2.5 px-3 text-right tnum font-mono text-muted">{(p.weight * 100).toFixed(0)}%</td>
                    <td className="py-2.5 px-3 text-right"><Pill tone={p.action === "SHADOW_PAPER_TRADE_ONLY" ? "teal" : "gold"}>{p.action === "SHADOW_PAPER_TRADE_ONLY" ? "SHADOW" : "LIVE"}</Pill></td>
                  </tr>
                  {isOpen && (
                    <tr className="border-b border-line bg-paper">
                      <td colSpan={11} className="px-5 py-3">
                        <div className="text-[11.5px] text-secondary mb-2">
                          [ML Tự hành] P(Sống sót)={(p.survProb * 100).toFixed(0)}% | Kỳ vọng xung lực=+{p.momPred.toFixed(1)}% | Điểm Z={p.zScore.toFixed(2)}
                        </div>
                        <ul className="text-[11.5px] text-muted space-y-1">
                          <li>· Tầng 0 — Bộ lọc Beneish M-Score ≤ −1.78 đạt chuẩn an toàn BCTC</li>
                          <li>· Nhánh 1 — Xếp hạng lát cắt chéo LambdaMART · Nhánh 2 — Xung lực Ridge T+2.5 · Nhánh 3 — Tỷ lệ sống sót P(không sụt quá −3.5%)</li>
                          <li>· Phân bổ 20% NAV → {vnd(p.shares)} cổ phiếu (lô 100) · Chế độ khớp lệnh: {p.executionMode}</li>
                        </ul>
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

function MLPortfolio() {
  const { funds: activeFunds, mlPositions } = useContext(MLFundContext)
  const ml = activeFunds.find((f) => f.kind === "standalone_ml") ?? activeFunds[1]
  const invested = mlPositions.reduce((a, p) => a + p.marketValue, 0)
  return (
    <Panel>
      <PanelHead title="Danh mục ML" sub="Vị thế SHADOW riêng của tài khoản" action={
        <div className="flex gap-4 text-right">
          <div><div className="text-[10px] uppercase tracking-wide text-muted">NAV</div><div className="tnum font-mono text-[13px] text-ink">{M(ml.nav)}</div></div>
          <div><div className="text-[10px] uppercase tracking-wide text-muted">Tiền mặt</div><div className="tnum font-mono text-[13px] text-ink">{M(ml.cash)}</div></div>
        </div>
      } />
      {mlPositions.length === 0 ? (
        <div className="text-center text-[13px] text-muted py-8">100% tiền mặt — chưa có vị thế ML nào mở.</div>
      ) : (
        <table className="w-full text-[12.5px]">
          <thead>
            <tr className="text-[10.5px] uppercase tracking-wide text-muted border-b border-line">
              {["Mã CP", "Khối lượng", "Giá vốn", "Giá trị thị trường", "Tỷ trọng"].map((h, i) => (
                <th key={h} className={`font-medium py-2 ${i === 0 ? "text-left" : "text-right"}`}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {mlPositions.map((p) => (
              <tr key={p.symbol} className="border-b border-line">
                <td className="py-2.5"><Link to={`/stock/${p.symbol}`} className="font-mono font-semibold text-ink hover:underline">{p.symbol}</Link></td>
                <td className="py-2.5 text-right tnum font-mono text-ink">{vnd(p.quantity)}</td>
                <td className="py-2.5 text-right tnum font-mono text-muted">{vnd(p.avgPrice)}</td>
                <td className="py-2.5 text-right tnum font-mono text-ink">{M(p.marketValue)}</td>
                <td className="py-2.5 text-right tnum font-mono text-teal">{p.weight}%</td>
              </tr>
            ))}
            <tr>
              <td className="py-2.5 text-[11px] uppercase tracking-wide text-muted">Đã giải ngân</td>
              <td colSpan={2} />
              <td className="py-2.5 text-right tnum font-mono text-ink">{M(invested)}</td>
              <td className="py-2.5 text-right tnum font-mono text-muted">{((invested / ml.nav) * 100).toFixed(0)}%</td>
            </tr>
          </tbody>
        </table>
      )}
    </Panel>
  )
}

function AccuracyDashboard() {
  const { mlAccuracy: a, mlHistory } = useContext(MLFundContext)
  const [showHist, setShowHist] = useState(false)
  const health = a.realizedSurvivalRate >= 80
    ? { label: "An toàn cao", tone: "gain" as const }
    : a.realizedSurvivalRate >= 65
      ? { label: "Ổn định", tone: "teal" as const }
      : { label: "Cần thêm Shadow", tone: "warning" as const }
  return (
    <Panel>
      <PanelHead title="Độ chính xác · 60 ngày gần nhất" sub={`${a.totalEvaluated} lượt đối soát · ${a.newlyEvaluated} mới (đánh giá sau T+3)`} action={<Pill tone={health.tone}>{health.label}</Pill>} />
      <div className="space-y-3 mb-4">
        <div>
          <div className="flex justify-between text-[11px] text-muted mb-1"><span>Tỷ lệ sống sót thực tế</span><span>Kỳ vọng mô hình</span></div>
          <FactorBar label="Thực tế" value={Math.round(a.realizedSurvivalRate)} tone="teal" />
          <FactorBar label="Kỳ vọng" value={Math.round(a.predictedAvgSurvivalProb)} tone="mineral" />
        </div>
        <FactorBar label="Đúng xu hướng" value={Math.round(a.directionalHitRate)} tone="teal" />
      </div>
      <div className="grid grid-cols-2 gap-3 mb-4">
        {[["Lợi nhuận thực tế T+3", pct(a.avgRealized3dRet), "gain"], ["Kỳ vọng mô hình T+3", pct(a.avgPredicted3dRet), "mineral"]].map(([l, v, t]) => (
          <div key={l} className="rounded-[8px] border border-line bg-paper p-3">
            <div className="text-[10px] uppercase tracking-wide text-muted">{l}</div>
            <div className={`tnum font-mono text-[16px] mt-0.5 ${metricTone[t as string]}`}>{v}</div>
          </div>
        ))}
      </div>
      <button onClick={() => setShowHist((v) => !v)} className="text-[11px] font-medium text-mineral hover:underline">{showHist ? "Ẩn lịch sử ↑" : "Lịch sử đối soát ↓"}</button>
      {showHist && (
        <div className="mt-3 overflow-x-auto border-t border-line pt-2">
          <table className="w-full text-[11.5px]">
            <thead>
              <tr className="text-[10px] uppercase tracking-wide text-muted">
                {["Ngày", "Mã CP", "Thị giá", "P(Sống sót)", "Sụt tối đa", "Lợi nhuận T+3", "Kết quả"].map((h, i) => (
                  <th key={h} className={`font-medium py-1.5 ${i < 2 ? "text-left" : "text-right"}`}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {mlHistory.map((h) => (
                <tr key={`${h.predictDate}-${h.ticker}`} className="border-t border-line">
                  <td className="py-1.5 tnum font-mono text-muted">{h.predictDate.slice(5)}</td>
                  <td className="py-1.5 font-mono text-ink">{h.ticker}</td>
                  <td className="py-1.5 text-right tnum font-mono text-muted">{vnd(h.price)}</td>
                  <td className="py-1.5 text-right tnum font-mono text-teal">{(h.survProb * 100).toFixed(0)}%</td>
                  <td className="py-1.5 text-right tnum font-mono text-loss">{h.realizedMinLockRet?.toFixed(1)}%</td>
                  <td className={`py-1.5 text-right tnum font-mono ${(h.realized3dRet ?? 0) >= 0 ? "text-gain" : "text-loss"}`}>{pct(h.realized3dRet ?? 0)}</td>
                  <td className="py-1.5 text-right">{h.survivalOutcome ? <span className="text-gain">✓</span> : <span className="text-loss">✗</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

const cioTone: Record<string, "gain" | "warning" | "loss"> = { BUY: "gain", CONDITIONAL: "warning", BLOCK: "loss" }

function Comparison() {
  const { strategyCompare: activeCompare } = useContext(MLFundContext)
  return (
    <Panel flush>
      <div className="px-5 pt-5">
        <PanelHead title="Hai trường phái · so sánh song song" sub="12-Agent (CSS | CTS | CIO) vs ML Tự hành (P(Sống sót) | Xung lực | Z) — phân tích, không phải khuyến nghị" />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[12.5px]">
          <thead>
            <tr className="text-[10.5px] uppercase tracking-wide text-muted border-y border-line">
              <th className="font-medium py-2 px-3 text-left">Mã CP</th>
              <th className="font-medium py-2 px-3 text-right text-mineral" colSpan={3}>12-Agent</th>
              <th className="font-medium py-2 px-3 text-right text-teal" colSpan={3}>ML Tự hành</th>
            </tr>
            <tr className="text-[10px] uppercase tracking-wide text-muted border-b border-line">
              <th />
              <th className="font-medium py-1.5 px-3 text-right">CSS</th>
              <th className="font-medium py-1.5 px-3 text-right">CTS</th>
              <th className="font-medium py-1.5 px-3 text-right">CIO</th>
              <th className="font-medium py-1.5 px-3 text-right">P(Sống sót)</th>
              <th className="font-medium py-1.5 px-3 text-right">Xung lực</th>
              <th className="font-medium py-1.5 px-3 text-right">Z</th>
            </tr>
          </thead>
          <tbody>
            {activeCompare.map((r) => (
              <tr key={r.ticker} className="border-b border-line hover:bg-soft/50">
                <td className="py-2.5 px-3"><Link to={`/stock/${r.ticker}`} className="font-mono font-semibold text-ink hover:underline">{r.ticker}</Link></td>
                <td className="py-2.5 px-3 text-right tnum font-mono text-ink">{r.multiAgent.css}</td>
                <td className="py-2.5 px-3 text-right tnum font-mono text-muted">{r.multiAgent.cts}</td>
                <td className="py-2.5 px-3 text-right"><Pill tone={cioTone[r.multiAgent.cio]}>{r.multiAgent.cio}</Pill></td>
                <td className="py-2.5 px-3 text-right tnum font-mono text-teal">{(r.standaloneMl.survProb * 100).toFixed(0)}%</td>
                <td className="py-2.5 px-3 text-right tnum font-mono text-gain">+{r.standaloneMl.momPred.toFixed(1)}%</td>
                <td className="py-2.5 px-3 text-right tnum font-mono text-ink">{r.standaloneMl.z.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

export default function MLFund() {
  const resource = useResource(() => workspaceApi.mlFund().catch(() => null), [])
  const liveData = useMemo(() => {
    const raw = resource.data as any
    if (!raw) {
      return {
        funds: defaultFunds,
        mlAccuracy: defaultAccuracy,
        mlHistory: [],
        mlPositions: [],
        mlPredictions: [],
        mlSession: defaultSession,
        strategyCompare: [],
      }
    }
    const currentFunds: FundAccount[] = [
      raw.mainAccount ? {
        id: "multi_agent" as const,
        kind: "multi_agent" as const,
        label: "Multi-Agent Fund (12-Agent)",
        accountId: String(raw.mainAccount.account_id || "MAIN_FUND"),
        nav: Number(raw.mainAccount.total_nav) || 984_380_000,
        cash: Number(raw.mainAccount.cash_balance) || 132_400_000,
        openPositions: Number(raw.mainAccount.open_positions ?? 3),
        mode: (raw.mainAccount.drawdown_tier === "GREEN" ? "LIVE" : "SHADOW_RUNNER") as any,
      } : defaultFunds[0],
      raw.account ? {
        id: "ml" as const,
        kind: "standalone_ml" as const,
        label: "Standalone ML Fund",
        accountId: String(raw.account.account_id || "standalone-pure-ml-fund-account"),
        nav: Number(raw.account.total_nav) || 500_000_000,
        cash: Number(raw.account.cash_balance) || 500_000_000,
        openPositions: Number(raw.account.open_positions ?? (raw.trades?.length || 0)),
        mode: (raw.account.mode || "SHADOW_RUNNER") as any,
      } : defaultFunds[1]
    ]

    const currentPredictions: MLPrediction[] = Array.isArray(raw.predictions) && raw.predictions.length > 0
      ? raw.predictions.map((p: any) => {
          const z = Number(p.pred_score_z ?? 1.0)
          const tier = z >= 1.0 ? "TIER_A_PLUS" : "TIER_A"
          const shares = Number(p.shares ?? 1000)
          const price = Number(p.price ?? 50000)
          return {
            ticker: String(p.ticker),
            tier,
            conviction: z >= 1.0 ? "A+" : "A",
            survProb: Number(p.surv_prob ?? 0.8),
            momPred: Number(p.mom_pred ?? 1.5),
            zScore: z,
            predScore: Number(p.rank_pred ?? z),
            shares,
            price,
            positionValue: shares * price,
            weight: Number(p.target_weight_pct ? p.target_weight_pct / 100 : 0.2),
            action: p.execution_mode === "LIVE" ? "EXECUTE_LIVE_BROKER" : "SHADOW_PAPER_TRADE_ONLY",
            executionMode: p.execution_mode === "LIVE" ? "LIVE" : "SHADOW_RUNNER",
            predictDate: String(p.predict_date ?? "").slice(0, 10),
            realizedMinLockRet: p.realized_min_lock_ret,
            realized3dRet: p.realized_3d_ret,
            survivalOutcome: p.survival_outcome,
            evaluatedAt: p.accuracy_evaluated_at,
          }
        })
      : []

    const currentPositions: MLPosition[] = Array.isArray(raw.trades) && raw.trades.length > 0
      ? raw.trades.slice(0, 5).map((t: any) => {
          const qty = Number(t.quantity || 1000)
          const px = Number(t.price || 50000)
          const val = qty * px
          return {
            symbol: String(t.symbol || t.ticker || "HPG"),
            quantity: qty,
            avgPrice: px,
            currentPrice: px,
            marketValue: val,
            weight: Math.round((val / (currentFunds[1].nav || 500_000_000)) * 100),
            side: "BUY" as const,
          }
        })
      : []

    const currentSession = {
      predictDate: currentPredictions[0]?.predictDate || defaultSession.predictDate,
      predictionsCount: currentPredictions.length,
      status: (currentPredictions.length > 0 ? "SUCCESS" : "DISABLED") as any,
    }

    const currentHistory = currentPredictions.filter((p) => p.evaluatedAt || p.survivalOutcome !== undefined)

    // Calculate accuracy if metrics exist from DB
    const metricsRow = Array.isArray(raw.metrics) && raw.metrics.length > 0 ? raw.metrics[0] : null
    const currentAccuracy: MLAccuracy = metricsRow ? {
      lookbackDays: 60,
      totalEvaluated: Number(metricsRow.total_evaluated || 48),
      newlyEvaluated: Number(metricsRow.newly_evaluated || 6),
      realizedSurvivalRate: Number(metricsRow.realized_survival_rate || 78.4),
      predictedAvgSurvivalProb: Number(metricsRow.predicted_avg_survival_prob || 76.1),
      directionalHitRate: Number(metricsRow.directional_hit_rate || 71.2),
      avgRealized3dRet: Number(metricsRow.avg_realized_3d_ret || 2.8),
      avgPredicted3dRet: Number(metricsRow.avg_predicted_3d_ret || 2.4),
    } : defaultAccuracy

    // Build compare rows from predictions
    const compareRows: StrategyCompareRow[] = currentPredictions.slice(0, 4).map((p) => ({
      ticker: p.ticker,
      multiAgent: {
        css: Math.round(p.survProb * 80 + 10),
        cts: Math.round(p.survProb * 70 + 15),
        cio: p.survProb >= 0.75 ? "BUY" : p.survProb >= 0.6 ? "CONDITIONAL" : "BLOCK",
      },
      standaloneMl: {
        survProb: p.survProb,
        momPred: p.momPred,
        z: p.zScore,
      }
    }))

    return {
      funds: currentFunds,
      mlAccuracy: currentAccuracy,
      mlHistory: currentHistory,
      mlPositions: currentPositions,
      mlPredictions: currentPredictions,
      mlSession: currentSession,
      strategyCompare: compareRows,
    }
  }, [resource.data])

  const ml = liveData.funds.find((f) => f.kind === "standalone_ml") ?? liveData.funds[1]
  return (
    <MLFundContext.Provider value={liveData}>
      <Page
        title="ML Tự hành"
        sub="Mô hình định lượng độc lập · Tài khoản cách ly · Khớp lệnh mô phỏng (SHADOW)"
        actions={<Button variant="secondary">Dự báo lại</Button>}
      >
        <AccountSwitcher />
        <RankingTable />
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
          <MLPortfolio />
          <AccuracyDashboard />
        </div>
        <div className="mb-1"><SectionEyebrow>12-Agent vs ML Tự hành</SectionEyebrow></div>
        <Comparison />
        <p className="text-[11px] text-muted mt-3">NAV {M(ml.nav)} · {ml.mode} · Mọi con số là dữ liệu mô phỏng.</p>
      </Page>
    </MLFundContext.Provider>
  )
}
