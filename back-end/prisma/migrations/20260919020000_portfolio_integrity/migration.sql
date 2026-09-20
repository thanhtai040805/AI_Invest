-- Reconciles the checked-in migration history with the complete Prisma schema.
-- Existing databases that already contain these objects must be schema-verified and
-- baseline this migration with `prisma migrate resolve --applied 20260919020000_portfolio_integrity`.

-- AlterTable
ALTER TABLE "users" ADD COLUMN     "win_rate" DOUBLE PRECISION DEFAULT 0;

-- AlterTable
ALTER TABLE "stocks" ADD COLUMN     "audit_opinion" VARCHAR(20) DEFAULT 'UNQUALIFIED',
ADD COLUMN     "audit_year" INTEGER,
ADD COLUMN     "beneish_score" DECIMAL,
ADD COLUMN     "beneish_status" VARCHAR(20) DEFAULT 'PENDING',
ADD COLUMN     "beneish_updated" DATE,
ADD COLUMN     "gil_analysis_status" VARCHAR(32) DEFAULT 'DATA_INSUFFICIENT',
ADD COLUMN     "gil_analysis_version" VARCHAR(64),
ADD COLUMN     "gil_assessed_at" TIMESTAMPTZ(6),
ADD COLUMN     "gil_flag" VARCHAR(32) DEFAULT 'DATA_INSUFFICIENT',
ADD COLUMN     "group_updated_at" TIMESTAMP(6),
ADD COLUMN     "sector" VARCHAR,
ADD COLUMN     "trading_status" VARCHAR(20) DEFAULT 'NORMAL',
ADD COLUMN     "universe_group" VARCHAR(20) DEFAULT 'B';

-- AlterTable
ALTER TABLE "ohlcv" DROP CONSTRAINT "ohlcv_pkey",
ADD COLUMN     "adj_close" DOUBLE PRECISION,
ADD COLUMN     "adj_factor" DOUBLE PRECISION DEFAULT 1.0,
ALTER COLUMN "time" SET DATA TYPE DATE,
ADD CONSTRAINT "ohlcv_pkey" PRIMARY KEY ("time", "symbol");

-- CreateTable
CREATE TABLE "refresh_tokens" (
    "id" TEXT NOT NULL,
    "token_id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "is_revoked" BOOLEAN NOT NULL DEFAULT false,
    "expires_at" TIMESTAMP(3) NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "refresh_tokens_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "posts" (
    "id" TEXT NOT NULL,
    "author_id" TEXT NOT NULL,
    "content" TEXT NOT NULL,
    "tagged_symbols" TEXT[],
    "likes_count" INTEGER NOT NULL DEFAULT 0,
    "comments_count" INTEGER NOT NULL DEFAULT 0,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "posts_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "comments" (
    "id" TEXT NOT NULL,
    "post_id" TEXT NOT NULL,
    "parent_comment_id" TEXT,
    "author_id" TEXT NOT NULL,
    "content" TEXT NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "comments_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "reactions" (
    "id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "post_id" TEXT,
    "comment_id" TEXT,
    "type" TEXT NOT NULL DEFAULT 'LIKE',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "reactions_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "reaction_exactly_one_target" CHECK (("post_id" IS NULL) <> ("comment_id" IS NULL))
);

-- CreateTable
CREATE TABLE "market_session_logs" (
    "id" TEXT NOT NULL,
    "session_date" TIMESTAMP(3) NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'PENDING',
    "stock_count" INTEGER NOT NULL DEFAULT 0,
    "ohlcv_count" INTEGER NOT NULL DEFAULT 0,
    "error" TEXT,
    "started_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "completed_at" TIMESTAMP(3),

    CONSTRAINT "market_session_logs_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "factor_scores" (
    "symbol" TEXT NOT NULL,
    "score_date" DATE NOT NULL,
    "value_score" DOUBLE PRECISION,
    "quality_score" DOUBLE PRECISION,
    "momentum_1m" DOUBLE PRECISION,
    "momentum_3m" DOUBLE PRECISION,
    "momentum_12m" DOUBLE PRECISION,
    "size_score" DOUBLE PRECISION,
    "volatility_score" DOUBLE PRECISION,
    "liquidity_score" DOUBLE PRECISION,
    "composite_score" DOUBLE PRECISION,
    "percentile" DOUBLE PRECISION,
    "updated_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,
    "earnings_yield_score" DOUBLE PRECISION,
    "accrual_score" DOUBLE PRECISION,
    "foreign_flow_score" DOUBLE PRECISION,
    "insider_score" DOUBLE PRECISION,
    "conditional_mom_score" DOUBLE PRECISION,
    "earnings_surprise_score" DOUBLE PRECISION,
    "distress_score" DOUBLE PRECISION,
    "piotroski_score" DOUBLE PRECISION,
    "factor_details" JSONB DEFAULT '{}',

    CONSTRAINT "factor_scores_pkey" PRIMARY KEY ("symbol","score_date")
);

-- CreateTable
CREATE TABLE "alpha_signals" (
    "symbol" TEXT NOT NULL,
    "signal_date" DATE NOT NULL,
    "alpha_id" TEXT NOT NULL,
    "raw_value" DOUBLE PRECISION,
    "ranked_value" DOUBLE PRECISION,
    "ic_trailing_20d" DOUBLE PRECISION,

    CONSTRAINT "alpha_signals_pkey" PRIMARY KEY ("symbol","signal_date","alpha_id")
);

-- CreateTable
CREATE TABLE "corporate_actions" (
    "symbol" TEXT NOT NULL,
    "action_date" DATE NOT NULL,
    "action_type" TEXT NOT NULL,
    "value" DOUBLE PRECISION DEFAULT 0,
    "ratio" DOUBLE PRECISION,
    "currency" TEXT DEFAULT 'VND',
    "source" TEXT DEFAULT 'yfinance',
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,
    "record_date" DATE,
    "note" TEXT,
    "applied" BOOLEAN DEFAULT false,
    "adjustment_factor" DOUBLE PRECISION,

    CONSTRAINT "corporate_actions_pkey" PRIMARY KEY ("symbol","action_date","action_type")
);

-- CreateTable
CREATE TABLE "financial_ratios" (
    "symbol" TEXT NOT NULL,
    "ratio_date" DATE NOT NULL,
    "pe" DOUBLE PRECISION,
    "pb" DOUBLE PRECISION,
    "roe" DOUBLE PRECISION,
    "roa" DOUBLE PRECISION,
    "debt_equity" DOUBLE PRECISION,
    "current_ratio" DOUBLE PRECISION,
    "gross_margin" DOUBLE PRECISION,
    "net_margin" DOUBLE PRECISION,
    "fcf_yield" DOUBLE PRECISION,
    "ev_ebitda" DOUBLE PRECISION,
    "yoy_revenue_growth" DOUBLE PRECISION,
    "yoy_earnings_growth" DOUBLE PRECISION,
    "updated_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,
    "published_date" DATE,

    CONSTRAINT "financial_ratios_pkey" PRIMARY KEY ("symbol","ratio_date")
);

-- CreateTable
CREATE TABLE "financial_statements" (
    "symbol" TEXT NOT NULL,
    "period_end" DATE NOT NULL,
    "statement_type" TEXT NOT NULL,
    "frequency" TEXT NOT NULL,
    "data" JSONB NOT NULL,
    "source" TEXT DEFAULT 'vnstock',
    "fetched_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,
    "published_date" DATE,

    CONSTRAINT "financial_statements_pkey" PRIMARY KEY ("symbol","period_end","statement_type","frequency")
);

-- CreateTable
CREATE TABLE "foreign_flow" (
    "symbol" TEXT NOT NULL,
    "trade_date" DATE NOT NULL,
    "buy_volume" BIGINT DEFAULT 0,
    "sell_volume" BIGINT DEFAULT 0,
    "buy_value" DOUBLE PRECISION DEFAULT 0,
    "sell_value" DOUBLE PRECISION DEFAULT 0,
    "net_volume" BIGINT DEFAULT 0,
    "net_value" DOUBLE PRECISION DEFAULT 0,
    "room_remaining" BIGINT DEFAULT 0,
    "room_limit" BIGINT DEFAULT 0,
    "ownership_pct" DOUBLE PRECISION DEFAULT 0,
    "source" TEXT DEFAULT 'cafef',
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "foreign_flow_pkey" PRIMARY KEY ("symbol","trade_date")
);

-- CreateTable
CREATE TABLE "insider_trades" (
    "id" SERIAL NOT NULL,
    "symbol" TEXT NOT NULL,
    "trade_date" DATE NOT NULL,
    "trader_name" TEXT,
    "trader_position" TEXT,
    "trade_type" TEXT,
    "quantity" BIGINT,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,
    "related_man" TEXT,
    "related_man_position" TEXT,
    "before_volume" BIGINT,
    "after_volume" BIGINT,
    "ownership_pct" DOUBLE PRECISION,
    "plan_buy_volume" BIGINT,
    "plan_sell_volume" BIGINT,
    "plan_begin_date" DATE,
    "plan_end_date" DATE,
    "real_end_date" DATE,

    CONSTRAINT "insider_trades_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "instrument_master" (
    "symbol" TEXT NOT NULL,
    "isin" TEXT,
    "name" TEXT,
    "first_listed" DATE,
    "delist_date" DATE,
    "free_float" DECIMAL,
    "shares_outstanding" DECIMAL,
    "metadata" JSONB,
    "corporate_actions" JSONB,
    "updated_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "instrument_master_pkey" PRIMARY KEY ("symbol")
);

-- CreateTable
CREATE TABLE "job_states" (
    "job_name" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'pending',
    "started_at" TIMESTAMPTZ(6),
    "completed_at" TIMESTAMPTZ(6),
    "metadata" JSONB DEFAULT '{}',
    "error" TEXT,

    CONSTRAINT "job_states_pkey" PRIMARY KEY ("job_name")
);

-- CreateTable
CREATE TABLE "knowledge_documents" (
    "id" SERIAL NOT NULL,
    "symbol" TEXT NOT NULL,
    "published_date" TIMESTAMPTZ(6) NOT NULL,
    "title" TEXT NOT NULL,
    "url" TEXT,
    "source" TEXT DEFAULT 'cafef',
    "config_id" INTEGER DEFAULT 0,
    "sentiment_score" DOUBLE PRECISION,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,
    "article_content" TEXT,
    "content_fetched_at" TIMESTAMPTZ(6),
    "article_pdf_text" TEXT,
    "article_images" TEXT[],
    "article_pdf_urls" TEXT[],
    "doc_type" TEXT DEFAULT 'news',
    "graph_nodes" JSONB,
    "event_type" TEXT,
    "severity" TEXT,
    "ai_sentiment_score" DOUBLE PRECISION,
    "ai_summary" TEXT,
    "triaged_at" TIMESTAMPTZ(6),
    "direction" TEXT,
    "magnitude" INTEGER,
    "investment_impact" DOUBLE PRECISION,
    "materiality" TEXT,
    "materiality_score" DOUBLE PRECISION,
    "surprise_score" DOUBLE PRECISION,
    "business_horizon" TEXT,
    "pricing_horizon" TEXT,
    "persistence" TEXT,
    "persistence_score" DOUBLE PRECISION,
    "reversibility" BOOLEAN,
    "apparent_novelty" TEXT,
    "novelty" DOUBLE PRECISION,
    "evidence_strength" TEXT,
    "credibility" DOUBLE PRECISION,
    "affected_entities" JSONB,
    "novelty_raw_score" DOUBLE PRECISION,
    "prev_similar_docs_ref" TEXT[],

    CONSTRAINT "news_events_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "macro_indicators" (
    "indicator_date" DATE NOT NULL,
    "indicator_name" TEXT NOT NULL,
    "value" DOUBLE PRECISION NOT NULL,
    "unit" TEXT,
    "source" TEXT,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "macro_indicators_pkey" PRIMARY KEY ("indicator_date","indicator_name")
);

-- CreateTable
CREATE TABLE "market_data_daily" (
    "ticker" TEXT NOT NULL,
    "date" DATE NOT NULL,
    "open_adj" DOUBLE PRECISION,
    "high_adj" DOUBLE PRECISION,
    "low_adj" DOUBLE PRECISION,
    "close_adj" DOUBLE PRECISION,
    "close_unadj" DOUBLE PRECISION,
    "vwap" DOUBLE PRECISION,
    "volume_continuous" BIGINT DEFAULT 0,
    "volume_atc" BIGINT DEFAULT 0,
    "volume_ato" BIGINT DEFAULT 0,
    "volume_total" BIGINT DEFAULT 0,
    "foreign_buy_vol" BIGINT DEFAULT 0,
    "foreign_sell_vol" BIGINT DEFAULT 0,
    "foreign_net_vol" BIGINT DEFAULT 0,
    "is_etf_rebalance_day" BOOLEAN DEFAULT false,
    "adtv20_continuous" DOUBLE PRECISION,
    "market_cap" DOUBLE PRECISION,
    "adj_factor" DOUBLE PRECISION DEFAULT 1.0,
    "data_source" TEXT DEFAULT 'manual',
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "market_data_daily_pkey" PRIMARY KEY ("ticker","date")
);

-- CreateTable
CREATE TABLE "market_regime" (
    "date" DATE NOT NULL,
    "breadth_ma50" DOUBLE PRECISION,
    "breadth_ma200" DOUBLE PRECISION,
    "breadth_rsi_oversold" DOUBLE PRECISION,
    "breadth_rsi_overbought" DOUBLE PRECISION,
    "market_volume_sma20_ratio" DOUBLE PRECISION,
    "net_foreign_flow_bil" DOUBLE PRECISION,
    "net_prop_flow_bil" DOUBLE PRECISION,
    "regime_label" VARCHAR(50),
    "created_at" TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "market_regime_pkey" PRIMARY KEY ("date")
);

-- CreateTable
CREATE TABLE "mral_metrics" (
    "id" SERIAL NOT NULL,
    "metric_type" VARCHAR(50) NOT NULL,
    "metric_date" DATE NOT NULL,
    "ticker" VARCHAR(20),
    "predicted_value" VARCHAR(100),
    "realized_value" VARCHAR(100),
    "numeric_value" DOUBLE PRECISION,
    "metadata" JSONB DEFAULT '{}',
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "mral_metrics_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "paper_trades" (
    "id" SERIAL NOT NULL,
    "ticker" TEXT NOT NULL,
    "action" TEXT NOT NULL,
    "price" DOUBLE PRECISION NOT NULL,
    "date" TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "confidence" DOUBLE PRECISION DEFAULT 0.0,
    "thesis" TEXT,
    "pnl" DOUBLE PRECISION,
    "status" TEXT DEFAULT 'OPEN',
    "resolve_price" DOUBLE PRECISION,
    "resolved_at" TIMESTAMP(6),
    "quantity" INTEGER DEFAULT 0,
    "created_at" TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP,
    "account_id" VARCHAR(64) NOT NULL DEFAULT 'MAIN_FUND',

    CONSTRAINT "paper_trades_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "signal_log" (
    "id" SERIAL NOT NULL,
    "symbol" TEXT NOT NULL,
    "signal_date" DATE NOT NULL,
    "direction" TEXT NOT NULL,
    "confidence" REAL,
    "entry_price" REAL,
    "target_price" REAL,
    "stop_loss" REAL,
    "holding_period" INTEGER DEFAULT 5,
    "source" TEXT,
    "source_agents" TEXT[],
    "factors_used" TEXT[],
    "hypothesis_id" TEXT,
    "hypothesis_title" TEXT,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,
    "evaluated_at" TIMESTAMPTZ(6),
    "exit_price" REAL,
    "actual_return" REAL,
    "hit" BOOLEAN,
    "hit_pct" REAL,
    "max_favorable" REAL,
    "max_adverse" REAL,
    "eval_status" TEXT DEFAULT 'pending',

    CONSTRAINT "signal_log_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "signals" (
    "symbol" TEXT NOT NULL,
    "signal_date" DATE NOT NULL,
    "signal" TEXT NOT NULL,
    "composite_rank" REAL,
    "hard_flags" INTEGER DEFAULT 0,
    "soft_flags" INTEGER DEFAULT 0,
    "sector_group" TEXT,
    "created_at" TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "signals_pkey" PRIMARY KEY ("symbol","signal_date")
);

-- CreateTable
CREATE TABLE "technical_indicators" (
    "symbol" TEXT NOT NULL,
    "calc_date" DATE NOT NULL,
    "indicators" JSONB NOT NULL,
    "updated_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "technical_indicators_pkey" PRIMARY KEY ("symbol","calc_date")
);

-- CreateTable
CREATE TABLE "audit_reports" (
    "report_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "audit_date" DATE NOT NULL,
    "integrity_status" VARCHAR(16) NOT NULL,
    "violations_count" INTEGER DEFAULT 0,
    "summary" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "audit_reports_pkey" PRIMARY KEY ("report_id")
);

-- CreateTable
CREATE TABLE "beneish_results" (
    "ticker" VARCHAR(16) NOT NULL,
    "quarter_date" DATE NOT NULL,
    "dsri" DECIMAL(8,4),
    "gmi" DECIMAL(8,4),
    "aqi" DECIMAL(8,4),
    "sgi" DECIMAL(8,4),
    "depi" DECIMAL(8,4),
    "sgai" DECIMAL(8,4),
    "tata" DECIMAL(8,4),
    "lvgi" DECIMAL(8,4),
    "m_score" DECIMAL(8,4) NOT NULL,
    "status" VARCHAR(16) NOT NULL,

    CONSTRAINT "beneish_results_pkey" PRIMARY KEY ("ticker","quarter_date")
);

-- CreateTable
CREATE TABLE "cio_resolutions" (
    "resolution_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "thesis_id" VARCHAR(64),
    "debate_summary" TEXT NOT NULL,
    "final_resolution" VARCHAR(16) NOT NULL,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,
    "decision_type" VARCHAR(64) DEFAULT 'CONFLICT_RESOLUTION',
    "ticker" VARCHAR(16),
    "verdict_payload" JSONB DEFAULT '{}',
    "previous_hash" VARCHAR(64) DEFAULT '0000000000000000000000000000000000000000000000000000000000000000',
    "decision_hash" VARCHAR(64),
    "governance_cosign" BOOLEAN DEFAULT false,

    CONSTRAINT "cio_resolutions_pkey" PRIMARY KEY ("resolution_id")
);

-- CreateTable
CREATE TABLE "counter_thesis_verdicts" (
    "thesis_id" VARCHAR(64) NOT NULL,
    "ticker" VARCHAR(16) NOT NULL,
    "cts_score" DECIMAL(6,2) NOT NULL,
    "verdict" VARCHAR(16) NOT NULL,
    "block_reasons" JSONB,
    "evaluated_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,
    "base_cts" DECIMAL(6,2),
    "interaction_multiplier" DECIMAL(6,2),
    "ocr_penalty" DECIMAL(6,2),
    "macro_penalty" DECIMAL(6,2),
    "regime_multiplier" DECIMAL(6,2) DEFAULT 1.0,
    "rule_of_three_passed" BOOLEAN DEFAULT true,
    "is_capitulation_rebound" BOOLEAN DEFAULT false,
    "holes" JSONB DEFAULT '[]',
    "execution_constraints" JSONB DEFAULT '{}',
    "rationale" TEXT DEFAULT '',

    CONSTRAINT "counter_thesis_verdicts_pkey" PRIMARY KEY ("thesis_id")
);

-- CreateTable
CREATE TABLE "factor_ic_history" (
    "date" DATE NOT NULL,
    "factor_name" VARCHAR(32) NOT NULL,
    "rolling_20d_ic" DECIMAL(8,4) NOT NULL,
    "rolling_60d_ic" DECIMAL(8,4) NOT NULL,
    "cdc_decay_flag" BOOLEAN DEFAULT false,

    CONSTRAINT "factor_ic_history_pkey" PRIMARY KEY ("date","factor_name")
);

-- CreateTable
CREATE TABLE "governance_rules" (
    "rule_id" VARCHAR(32) NOT NULL,
    "rule_name" VARCHAR(128) NOT NULL,
    "rule_category" VARCHAR(32) NOT NULL,
    "is_active" BOOLEAN DEFAULT true,
    "parameters" JSONB NOT NULL,
    "updated_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "governance_rules_pkey" PRIMARY KEY ("rule_id")
);

-- CreateTable
CREATE TABLE "investment_theses" (
    "thesis_id" VARCHAR(64) NOT NULL DEFAULT gen_random_uuid(),
    "ticker" VARCHAR(16) NOT NULL,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,
    "catalyst_type" VARCHAR(64),
    "target_price_range" JSONB,
    "confirming_signals" JSONB NOT NULL,
    "invalidation_conditions" JSONB NOT NULL,
    "status" VARCHAR(64) DEFAULT 'ACTIVE',
    "catalyst_description" TEXT,
    "timeline_months" INTEGER DEFAULT 3,
    "target_price" DECIMAL(15,2),
    "entry_price_estimated" DECIMAL(15,2),
    "pre_mortem_scenarios" JSONB,

    CONSTRAINT "investment_theses_pkey" PRIMARY KEY ("thesis_id")
);

-- CreateTable
CREATE TABLE "kelly_win_rate_matrix" (
    "regime" VARCHAR(32) NOT NULL,
    "conviction_tier" VARCHAR(8) NOT NULL,
    "win_rate_p" DECIMAL(6,4) NOT NULL,
    "payoff_ratio_b" DECIMAL(6,4) NOT NULL,
    "sample_count" INTEGER NOT NULL,
    "updated_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "kelly_win_rate_matrix_pkey" PRIMARY KEY ("regime","conviction_tier")
);

-- CreateTable
CREATE TABLE "log_counter_thesis" (
    "id" BIGSERIAL NOT NULL,
    "thesis_id" UUID NOT NULL,
    "ticker" VARCHAR(16) NOT NULL,
    "debate_challenge_text" TEXT NOT NULL,
    "llm_prompt_response" JSONB NOT NULL,
    "verdict" VARCHAR(16) NOT NULL,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "log_counter_thesis_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "log_equity_research" (
    "id" BIGSERIAL NOT NULL,
    "ticker" VARCHAR(16) NOT NULL,
    "date" DATE NOT NULL,
    "factor_raw_metrics" JSONB NOT NULL,
    "moat_citations_evidence" JSONB NOT NULL,
    "llm_prompt_tokens" INTEGER,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "log_equity_research_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "log_investment_thesis" (
    "id" BIGSERIAL NOT NULL,
    "thesis_id" UUID NOT NULL,
    "ticker" VARCHAR(16) NOT NULL,
    "pre_mortem_scenarios" JSONB NOT NULL,
    "thesis_text" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "log_investment_thesis_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "log_market_surveillance" (
    "id" BIGSERIAL NOT NULL,
    "date" DATE NOT NULL,
    "inputs" JSONB NOT NULL,
    "computation_trace" JSONB NOT NULL,
    "outputs" JSONB NOT NULL,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "log_market_surveillance_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "log_portfolio_allocation" (
    "id" BIGSERIAL NOT NULL,
    "ticker" VARCHAR(16) NOT NULL,
    "kelly_math_steps" JSONB NOT NULL,
    "allocated_weight_pct" DECIMAL(6,2) NOT NULL,
    "rationale" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "log_portfolio_allocation_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "log_portfolio_risk" (
    "id" BIGSERIAL NOT NULL,
    "date" DATE NOT NULL,
    "es_97_5_inputs" JSONB NOT NULL,
    "covariance_matrix" JSONB,
    "garch_cash_trace" JSONB NOT NULL,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "log_portfolio_risk_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "log_position_monitoring" (
    "id" BIGSERIAL NOT NULL,
    "ticker" VARCHAR(16) NOT NULL,
    "pnl_pct" DECIMAL(6,2) NOT NULL,
    "stop_loss_triggered" BOOLEAN DEFAULT false,
    "thesis_invalidated" BOOLEAN DEFAULT false,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "log_position_monitoring_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "log_reinforcement_learning" (
    "id" BIGSERIAL NOT NULL,
    "date" DATE NOT NULL,
    "ic_rolling_scores" JSONB NOT NULL,
    "reward_signals" JSONB NOT NULL,
    "policy_weight_updates" JSONB NOT NULL,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "log_reinforcement_learning_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "log_strategy_cio" (
    "id" BIGSERIAL NOT NULL,
    "trigger_type" VARCHAR(64) NOT NULL,
    "debate_synthesis" TEXT NOT NULL,
    "resolution_payload" JSONB NOT NULL,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "log_strategy_cio_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "log_system_governance" (
    "id" BIGSERIAL NOT NULL,
    "rule_id" VARCHAR(32),
    "action_type" VARCHAR(64) NOT NULL,
    "audit_trail_verification" JSONB NOT NULL,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "log_system_governance_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "log_trade_execution" (
    "id" BIGSERIAL NOT NULL,
    "order_id" UUID NOT NULL,
    "ticker" VARCHAR(16) NOT NULL,
    "slicing_schedule" JSONB NOT NULL,
    "orderbook_depth_snapshot" JSONB,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "log_trade_execution_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "log_universe_discovery" (
    "id" BIGSERIAL NOT NULL,
    "date" DATE NOT NULL,
    "filtered_counts" JSONB NOT NULL,
    "beneish_trace" JSONB NOT NULL,
    "exclusion_log" JSONB NOT NULL,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "log_universe_discovery_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "market_anomalies" (
    "id" BIGSERIAL NOT NULL,
    "date" DATE NOT NULL,
    "anomaly_type" VARCHAR(64) NOT NULL,
    "severity" VARCHAR(16) NOT NULL,
    "description" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "market_anomalies_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "market_regimes" (
    "date" DATE NOT NULL,
    "current_regime" VARCHAR(32) NOT NULL,
    "vix_vn_analog" DECIMAL(8,2),
    "breadth_above_ma50_pct" DECIMAL(6,2),
    "hmm_posteriors" JSONB NOT NULL,
    "updated_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "market_regimes_pkey" PRIMARY KEY ("date")
);

-- CreateTable
CREATE TABLE "moat_profiles" (
    "ticker" VARCHAR(16) NOT NULL,
    "fiscal_year" INTEGER,
    "report_type" VARCHAR(32) NOT NULL DEFAULT 'ANNUAL_REPORT',
    "assessment_status" VARCHAR(32) NOT NULL DEFAULT 'INSUFFICIENT',
    "moat_score" DECIMAL(6,2),
    "intangibles_score" DECIMAL(6,2),
    "switching_costs_score" DECIMAL(6,2),
    "network_effect_score" DECIMAL(6,2),
    "cost_advantage_score" DECIMAL(6,2),
    "efficient_scale_score" DECIMAL(6,2),
    "coverage_ratio" DECIMAL(6,4),
    "policy_version" VARCHAR(64),
    "document_ids" JSONB NOT NULL DEFAULT '[]',
    "evidence_digest" VARCHAR(128),
    "evidence_summary" JSONB NOT NULL DEFAULT '{}',
    "source_sag_doc_id" VARCHAR(128),
    "assessed_at" TIMESTAMPTZ(6),
    "extracted_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,
    "is_stale" BOOLEAN DEFAULT false,

    CONSTRAINT "moat_profiles_pkey" PRIMARY KEY ("ticker")
);

-- CreateTable
CREATE TABLE "order_executions" (
    "order_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "ticker" VARCHAR(16) NOT NULL,
    "action" VARCHAR(8) NOT NULL,
    "shares" INTEGER NOT NULL,
    "executed_price" DECIMAL(12,2) NOT NULL,
    "target_price" DECIMAL(12,2) NOT NULL,
    "slippage_bps" DECIMAL(8,2) NOT NULL,
    "execution_mode" VARCHAR(16) NOT NULL,
    "executed_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "order_executions_pkey" PRIMARY KEY ("order_id")
);

-- CreateTable
CREATE TABLE "portfolio_account" (
    "account_id" VARCHAR(32) NOT NULL DEFAULT 'MAIN_FUND',
    "cash_balance" DECIMAL(18,2) NOT NULL,
    "total_nav" DECIMAL(18,2) NOT NULL,
    "peak_nav" DECIMAL(18,2) NOT NULL,
    "drawdown_tier" VARCHAR(16) DEFAULT 'GREEN',
    "updated_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "portfolio_account_pkey" PRIMARY KEY ("account_id")
);

-- CreateTable
CREATE TABLE "portfolio_decisions" (
    "decision_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "date" DATE NOT NULL,
    "ticker" VARCHAR(16) NOT NULL,
    "action" VARCHAR(16) NOT NULL,
    "target_shares" INTEGER NOT NULL,
    "allocated_weight_pct" DECIMAL(6,2) NOT NULL,
    "rationale" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "portfolio_decisions_pkey" PRIMARY KEY ("decision_id")
);

-- CreateTable
CREATE TABLE "position_health_ticks" (
    "ticker" VARCHAR(16) NOT NULL,
    "current_pnl_pct" DECIMAL(6,2) NOT NULL,
    "distance_to_stop_loss_pct" DECIMAL(6,2) NOT NULL,
    "thesis_health_status" VARCHAR(16) NOT NULL,
    "last_updated" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "position_health_ticks_pkey" PRIMARY KEY ("ticker")
);

-- CreateTable
CREATE TABLE "risk_limits" (
    "limit_type" VARCHAR(32) NOT NULL,
    "max_single_stock_pct" DECIMAL(6,2) NOT NULL DEFAULT 15.0,
    "max_sector_pct" DECIMAL(6,2) NOT NULL DEFAULT 35.0,
    "hard_stop_loss_pct" DECIMAL(6,2) NOT NULL DEFAULT 2.0,
    "updated_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "risk_limits_pkey" PRIMARY KEY ("limit_type")
);

-- CreateTable
CREATE TABLE "risk_snapshots" (
    "date" DATE NOT NULL,
    "es_97_5" DECIMAL(6,2) NOT NULL,
    "garch_cash_target" DECIMAL(6,2) NOT NULL,
    "drawdown_tier" VARCHAR(16) NOT NULL,
    "max_drawdown_from_peak" DECIMAL(6,2) NOT NULL,
    "cdc_active" BOOLEAN DEFAULT false,
    "updated_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "risk_snapshots_pkey" PRIMARY KEY ("date")
);

-- CreateTable
CREATE TABLE "rl_factor_weights" (
    "regime" VARCHAR(32) NOT NULL,
    "f1_value_weight" DECIMAL(6,4) NOT NULL,
    "f2_quality_weight" DECIMAL(6,4) NOT NULL,
    "f3_momentum_weight" DECIMAL(6,4) NOT NULL,
    "f4_earnings_weight" DECIMAL(6,4) NOT NULL,
    "f5_flow_weight" DECIMAL(6,4) NOT NULL,
    "f6_technical_weight" DECIMAL(6,4) NOT NULL,
    "learning_epoch" INTEGER NOT NULL,
    "updated_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "rl_factor_weights_pkey" PRIMARY KEY ("regime")
);

-- CreateTable
CREATE TABLE "slippage_records" (
    "id" BIGSERIAL NOT NULL,
    "ticker" VARCHAR(16) NOT NULL,
    "date" DATE NOT NULL,
    "adtv20_bucket" VARCHAR(16) NOT NULL,
    "actual_slippage_bps" DECIMAL(8,2) NOT NULL,
    "expected_slippage_bps" DECIMAL(8,2) NOT NULL,
    "mode" VARCHAR(16) NOT NULL,

    CONSTRAINT "slippage_records_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "stop_loss_events" (
    "event_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "ticker" VARCHAR(16) NOT NULL,
    "triggered_price" DECIMAL(12,2) NOT NULL,
    "loss_pct_nav" DECIMAL(6,2) NOT NULL,
    "bypass_order_id" UUID,
    "triggered_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "stop_loss_events_pkey" PRIMARY KEY ("event_id")
);

-- CreateTable
CREATE TABLE "strategic_allocations" (
    "allocation_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "date" DATE NOT NULL,
    "macro_view" TEXT NOT NULL,
    "cash_target_override" DECIMAL(6,2),
    "sector_focus" JSONB,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "strategic_allocations_pkey" PRIMARY KEY ("allocation_id")
);

-- CreateTable
CREATE TABLE "universe_securities" (
    "ticker" VARCHAR(16) NOT NULL,
    "universe_group" VARCHAR(16) NOT NULL,
    "trading_status" VARCHAR(16) NOT NULL,
    "beneish_status" VARCHAR(16) NOT NULL,
    "gil_flag" VARCHAR(32) NOT NULL DEFAULT 'DATA_INSUFFICIENT',
    "gil_analysis_status" VARCHAR(32) DEFAULT 'DATA_INSUFFICIENT',
    "gil_analysis_version" VARCHAR(64),
    "gil_assessed_at" TIMESTAMPTZ(6),
    "updated_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "universe_securities_pkey" PRIMARY KEY ("ticker")
);

-- CreateTable
CREATE TABLE "audit_logs" (
    "id" SERIAL NOT NULL,
    "timestamp" VARCHAR DEFAULT CURRENT_TIMESTAMP,
    "agent_id" VARCHAR(64) NOT NULL,
    "event_type" VARCHAR(64) NOT NULL,
    "details" JSONB,
    "previous_hash" VARCHAR(64),
    "current_hash" VARCHAR(64),

    CONSTRAINT "audit_logs_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "bctc_pipeline_records" (
    "id" VARCHAR NOT NULL,
    "ticker" VARCHAR(16) NOT NULL,
    "fiscal_year" INTEGER NOT NULL,
    "fiscal_quarter" INTEGER NOT NULL,
    "report_scope" VARCHAR(32) NOT NULL DEFAULT 'CONSOLIDATED',
    "is_classified" BOOLEAN DEFAULT false,
    "classifier_status" VARCHAR(32) DEFAULT 'PENDING',
    "total_raw_pages" INTEGER,
    "retained_pages" INTEGER,
    "r2_pdf_uploaded" BOOLEAN DEFAULT false,
    "r2_pdf_key" VARCHAR,
    "r2_pdf_url" TEXT,
    "pdf_sha256" VARCHAR,
    "is_ocr_completed" BOOLEAN DEFAULT false,
    "ocr_status" VARCHAR(32) DEFAULT 'PENDING',
    "r2_md_uploaded" BOOLEAN DEFAULT false,
    "r2_md_key" VARCHAR,
    "r2_md_url" TEXT,
    "is_audited" BOOLEAN DEFAULT false,
    "auditor_name" VARCHAR,
    "audit_opinion" VARCHAR,
    "announcement_date" DATE,
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,
    "is_active_for_sag" BOOLEAN DEFAULT false,
    "sag_doc_role" VARCHAR,

    CONSTRAINT "bctc_pipeline_records_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "cio_strategic_directives" (
    "directive_id" VARCHAR(64) NOT NULL,
    "policy_version" VARCHAR(32) NOT NULL DEFAULT 'v5.1_IOS',
    "effective_from" DATE NOT NULL,
    "effective_until" DATE,
    "status" VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    "macro_regime" VARCHAR(32) NOT NULL,
    "risk_appetite" VARCHAR(32) NOT NULL,
    "strategic_cash_target_pct" DECIMAL(5,2) NOT NULL,
    "sector_tilt" JSONB NOT NULL DEFAULT '{}',
    "flash_invalidation_thresholds" JSONB DEFAULT '{}',
    "rationale" TEXT NOT NULL,
    "decision_hash" VARCHAR(64),
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "cio_strategic_directives_pkey" PRIMARY KEY ("directive_id")
);

-- CreateTable
CREATE TABLE "portfolio_campaigns" (
    "campaign_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "ticker" VARCHAR(16) NOT NULL,
    "direction" VARCHAR(16) NOT NULL,
    "final_target_weight" DECIMAL(6,4) NOT NULL,
    "current_weight" DECIMAL(6,4) NOT NULL,
    "session_incremental_weight" DECIMAL(6,4) NOT NULL,
    "remaining_weight" DECIMAL(6,4) NOT NULL,
    "target_shares" INTEGER NOT NULL,
    "accumulated_shares" INTEGER NOT NULL DEFAULT 0,
    "status" VARCHAR(32) NOT NULL DEFAULT 'IN_PROGRESS',
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "portfolio_campaigns_pkey" PRIMARY KEY ("campaign_id")
);

-- CreateTable
CREATE TABLE "standalone_ml_predictions" (
    "id" SERIAL NOT NULL,
    "predict_date" DATE NOT NULL,
    "ticker" VARCHAR(16) NOT NULL,
    "rank_pred" DOUBLE PRECISION,
    "mom_pred" DOUBLE PRECISION,
    "surv_prob" DOUBLE PRECISION,
    "pred_score_z" DOUBLE PRECISION,
    "shares" INTEGER,
    "price" DOUBLE PRECISION,
    "target_weight_pct" DOUBLE PRECISION,
    "execution_mode" VARCHAR(32),
    "realized_min_lock_ret" DOUBLE PRECISION,
    "realized_3d_ret" DOUBLE PRECISION,
    "survival_outcome" BOOLEAN,
    "accuracy_evaluated_at" TIMESTAMPTZ(6),
    "created_at" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,
    "account_id" VARCHAR(64) NOT NULL DEFAULT 'standalone-pure-ml-fund-account',

    CONSTRAINT "standalone_ml_predictions_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "violation_reports" (
    "report_id" VARCHAR(64) NOT NULL,
    "timestamp" TIMESTAMPTZ(6) DEFAULT CURRENT_TIMESTAMP,
    "ticker" VARCHAR(16),
    "issuing_agent" VARCHAR(64) NOT NULL,
    "violated_rule" VARCHAR(64) NOT NULL,
    "risk_level" VARCHAR(16) NOT NULL,
    "reason" TEXT NOT NULL,
    "order_payload" JSONB,
    "escalated_to" VARCHAR(64) DEFAULT 'strategy_cio',
    "resolution_status" VARCHAR(32) DEFAULT 'PENDING',
    "cio_resolution_id" VARCHAR(64),
    "resolved_at" TIMESTAMPTZ(6),

    CONSTRAINT "violation_reports_pkey" PRIMARY KEY ("report_id")
);

-- CreateIndex
CREATE UNIQUE INDEX "refresh_tokens_token_id_key" ON "refresh_tokens"("token_id");

-- CreateIndex
CREATE UNIQUE INDEX "reactions_user_id_post_id_key" ON "reactions"("user_id", "post_id");

-- CreateIndex
CREATE UNIQUE INDEX "reactions_user_id_comment_id_key" ON "reactions"("user_id", "comment_id");

-- CreateIndex
CREATE UNIQUE INDEX "market_session_logs_session_date_key" ON "market_session_logs"("session_date");

-- CreateIndex
CREATE INDEX "idx_factor_scores_symbol_date" ON "factor_scores"("symbol", "score_date");

-- CreateIndex
CREATE UNIQUE INDEX "insider_trades_unique" ON "insider_trades"("symbol", "trade_date", "trader_name", "quantity", "trade_type");

-- CreateIndex
CREATE INDEX "idx_knowledge_symbol_date" ON "knowledge_documents"("symbol", "published_date" DESC);

-- CreateIndex
CREATE INDEX "idx_knowledge_symbol_type" ON "knowledge_documents"("symbol", "doc_type");

-- CreateIndex
CREATE UNIQUE INDEX "news_events_symbol_url_key" ON "knowledge_documents"("symbol", "url");

-- CreateIndex
CREATE INDEX "idx_market_data_daily_date" ON "market_data_daily"("date" DESC);

-- CreateIndex
CREATE INDEX "idx_market_data_daily_ticker" ON "market_data_daily"("ticker", "date" DESC);

-- CreateIndex
CREATE INDEX "idx_mral_metric_date" ON "mral_metrics"("metric_date");

-- CreateIndex
CREATE INDEX "idx_mral_metric_type" ON "mral_metrics"("metric_type");

-- CreateIndex
CREATE INDEX "paper_trades_account_id_ticker_status_idx" ON "paper_trades"("account_id", "ticker", "status");

-- CreateIndex
CREATE INDEX "idx_signal_log_eval_status" ON "signal_log"("eval_status");

-- CreateIndex
CREATE INDEX "idx_signal_log_symbol_date" ON "signal_log"("symbol", "signal_date");

-- CreateIndex
CREATE INDEX "idx_cio_resolutions_created_at" ON "cio_resolutions"("created_at" DESC);

-- CreateIndex
CREATE INDEX "idx_cio_resolutions_hash" ON "cio_resolutions"("decision_hash");

-- CreateIndex
CREATE INDEX "idx_counter_verdicts_ticker_eval" ON "counter_thesis_verdicts"("ticker", "evaluated_at" DESC);

-- CreateIndex
CREATE INDEX "idx_theses_status" ON "investment_theses"("status");

-- CreateIndex
CREATE INDEX "idx_theses_ticker_created" ON "investment_theses"("ticker", "created_at" DESC);

-- CreateIndex
CREATE INDEX "idx_moat_profiles_ticker" ON "moat_profiles"("ticker");

-- CreateIndex
CREATE INDEX "idx_executions_date" ON "order_executions"("executed_at");

-- CreateIndex
CREATE INDEX "idx_universe_group" ON "universe_securities"("universe_group");

-- CreateIndex
CREATE INDEX "idx_bctc_pipeline_flags" ON "bctc_pipeline_records"("is_classified", "r2_pdf_uploaded", "is_ocr_completed");

-- CreateIndex
CREATE INDEX "idx_bctc_pipeline_sag_active" ON "bctc_pipeline_records"("ticker", "is_active_for_sag");

-- CreateIndex
CREATE INDEX "idx_bctc_pipeline_ticker" ON "bctc_pipeline_records"("ticker", "fiscal_year", "fiscal_quarter");

-- CreateIndex
CREATE UNIQUE INDEX "bctc_pipeline_records_ticker_fiscal_year_fiscal_quarter_rep_key" ON "bctc_pipeline_records"("ticker", "fiscal_year", "fiscal_quarter", "report_scope");

-- CreateIndex
CREATE INDEX "idx_cio_directives_effective" ON "cio_strategic_directives"("effective_from", "effective_until");

-- CreateIndex
CREATE INDEX "idx_cio_directives_status" ON "cio_strategic_directives"("status");

-- CreateIndex
CREATE INDEX "idx_campaigns_ticker_status" ON "portfolio_campaigns"("ticker", "status");

-- CreateIndex
CREATE UNIQUE INDEX "standalone_ml_predictions_predict_date_ticker_account_id_key" ON "standalone_ml_predictions"("predict_date", "ticker", "account_id");

-- CreateIndex
CREATE UNIQUE INDEX "positions_user_id_symbol_key" ON "positions"("user_id", "symbol");

-- AddForeignKey
ALTER TABLE "refresh_tokens" ADD CONSTRAINT "refresh_tokens_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "posts" ADD CONSTRAINT "posts_author_id_fkey" FOREIGN KEY ("author_id") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "comments" ADD CONSTRAINT "comments_author_id_fkey" FOREIGN KEY ("author_id") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "comments" ADD CONSTRAINT "comments_parent_comment_id_fkey" FOREIGN KEY ("parent_comment_id") REFERENCES "comments"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "comments" ADD CONSTRAINT "comments_post_id_fkey" FOREIGN KEY ("post_id") REFERENCES "posts"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "reactions" ADD CONSTRAINT "reaction_comment_fkey" FOREIGN KEY ("comment_id") REFERENCES "comments"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "reactions" ADD CONSTRAINT "reaction_post_fkey" FOREIGN KEY ("post_id") REFERENCES "posts"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "reactions" ADD CONSTRAINT "reactions_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "counter_thesis_verdicts" ADD CONSTRAINT "counter_thesis_verdicts_thesis_id_fkey" FOREIGN KEY ("thesis_id") REFERENCES "investment_theses"("thesis_id") ON DELETE CASCADE ON UPDATE NO ACTION;


