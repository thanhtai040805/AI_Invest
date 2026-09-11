export interface FundAccount {
  id: "multi_agent" | "ml"
  kind: "multi_agent" | "standalone_ml"
  label: string
  accountId: string
  nav: number
  cash: number
  openPositions: number
  mode: "LIVE" | "SHADOW_RUNNER" | "DISABLED"
}

export interface MLPrediction {
  ticker: string
  tier: "TIER_A_PLUS" | "TIER_A" | "TIER_B" | "TIER_C"
  conviction: "A+" | "A" | "B" | "C"
  survProb: number
  momPred: number
  zScore: number
  predScore: number
  shares: number
  price: number
  positionValue: number
  weight: number
  action: "EXECUTE_LIVE_BROKER" | "SHADOW_PAPER_TRADE_ONLY"
  executionMode: "LIVE" | "SHADOW_RUNNER"
  predictDate: string
  realizedMinLockRet?: number
  realized3dRet?: number
  survivalOutcome?: boolean
  evaluatedAt?: string
}

export interface MLPosition {
  symbol: string
  quantity: number
  avgPrice: number
  currentPrice: number
  marketValue: number
  weight: number
  side: "BUY"
}

export interface MLSession {
  predictDate: string
  predictionsCount: number
  status: "SUCCESS" | "DISABLED" | "FAIL"
}

export interface MLAccuracy {
  lookbackDays: number
  totalEvaluated: number
  newlyEvaluated: number
  realizedSurvivalRate: number
  predictedAvgSurvivalProb: number
  directionalHitRate: number
  avgRealized3dRet: number
  avgPredicted3dRet: number
}

export interface StrategyCompareRow {
  ticker: string
  multiAgent: {
    css: number
    cts: number
    cio: "BUY" | "CONDITIONAL" | "BLOCK"
  }
  standaloneMl: {
    survProb: number
    momPred: number
    z: number
  }
}
