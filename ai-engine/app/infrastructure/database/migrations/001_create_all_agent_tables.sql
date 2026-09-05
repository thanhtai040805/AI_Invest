-- ============================================================================
-- MIGRATION: 001_create_all_agent_tables.sql
-- PURPOSE: 21 Domain State Tables + 12 Isolated Audit Log Tables (IOS v5.1)
-- ARCHITECTURE: 12 Semantic Plug-and-Play Multi-Agents + SAG FastMCP Integration
-- ============================================================================

-- ============================================================================
-- PHẦN I: 21 BẢNG DỮ LIỆU NGHIỆP VỤ (DOMAIN STATE TABLES)
-- ============================================================================

-- 1. market_surveillance
CREATE TABLE IF NOT EXISTS market_regimes (
    date DATE PRIMARY KEY,
    current_regime VARCHAR(32) NOT NULL,
    vix_vn_analog NUMERIC(8,2),
    breadth_above_ma50_pct NUMERIC(6,2),
    hmm_posteriors JSONB NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS market_anomalies (
    id BIGSERIAL PRIMARY KEY,
    date DATE NOT NULL,
    anomaly_type VARCHAR(64) NOT NULL,
    severity VARCHAR(16) NOT NULL,
    description TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 2. universe_discovery
CREATE TABLE IF NOT EXISTS universe_securities (
    ticker VARCHAR(16) PRIMARY KEY,
    universe_group VARCHAR(16) NOT NULL,
    trading_status VARCHAR(16) NOT NULL,
    beneish_status VARCHAR(16) NOT NULL,
    gil_flag VARCHAR(16) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS beneish_results (
    ticker VARCHAR(16) NOT NULL,
    quarter_date DATE NOT NULL,
    dsri NUMERIC(8,4), gmi NUMERIC(8,4), aqi NUMERIC(8,4), sgi NUMERIC(8,4),
    depi NUMERIC(8,4), sgai NUMERIC(8,4), tata NUMERIC(8,4), lvgi NUMERIC(8,4),
    m_score NUMERIC(8,4) NOT NULL,
    status VARCHAR(16) NOT NULL,
    PRIMARY KEY (ticker, quarter_date)
);

-- 3. equity_research
CREATE TABLE IF NOT EXISTS factor_scores (
    ticker VARCHAR(16) NOT NULL,
    date DATE NOT NULL,
    f1_value NUMERIC(6,2), f2_quality NUMERIC(6,2), f3_momentum NUMERIC(6,2),
    f4_earnings NUMERIC(6,2), f5_flow NUMERIC(6,2), f6_technical NUMERIC(6,2),
    css NUMERIC(6,2), conviction VARCHAR(8),
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS moat_profiles (
    ticker VARCHAR(16) PRIMARY KEY,
    fiscal_year INTEGER NOT NULL DEFAULT 2025,
    report_type VARCHAR(32) NOT NULL DEFAULT 'ANNUAL_REPORT',
    moat_score NUMERIC(6,2) NOT NULL,
    intangibles_score NUMERIC(6,2),
    switching_costs_score NUMERIC(6,2),
    network_effect_score NUMERIC(6,2),
    cost_advantage_score NUMERIC(6,2),
    efficient_scale_score NUMERIC(6,2),
    evidence_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_sag_doc_id VARCHAR(128),
    extracted_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    is_stale BOOLEAN DEFAULT FALSE
);

-- 4. investment_thesis
CREATE TABLE IF NOT EXISTS investment_theses (
    thesis_id VARCHAR(64) PRIMARY KEY,
    ticker VARCHAR(16) NOT NULL,
    catalyst_type VARCHAR(64),
    catalyst_description TEXT,
    timeline_months INTEGER DEFAULT 3,
    target_price NUMERIC(15,2),
    entry_price_estimated NUMERIC(15,2),
    confirming_signals JSONB NOT NULL,
    invalidation_conditions JSONB NOT NULL,
    pre_mortem_scenarios JSONB,
    target_price_range JSONB,
    status VARCHAR(16) DEFAULT 'ACTIVE',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. counter_thesis
CREATE TABLE IF NOT EXISTS counter_thesis_verdicts (
    thesis_id VARCHAR(64) PRIMARY KEY REFERENCES investment_theses(thesis_id) ON DELETE CASCADE,
    ticker VARCHAR(16) NOT NULL,
    cts_score NUMERIC(6,2) NOT NULL,
    base_cts NUMERIC(6,2),
    interaction_multiplier NUMERIC(6,2),
    ocr_penalty NUMERIC(6,2),
    macro_penalty NUMERIC(6,2),
    regime_multiplier NUMERIC(6,2) DEFAULT 1.0,
    verdict VARCHAR(16) NOT NULL,
    rule_of_three_passed BOOLEAN DEFAULT TRUE,
    is_capitulation_rebound BOOLEAN DEFAULT FALSE,
    block_reasons JSONB,
    holes JSONB,
    execution_constraints JSONB,
    rationale TEXT,
    evaluated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5.1 governance_escalation
CREATE TABLE IF NOT EXISTS violation_reports (
    report_id VARCHAR(64) PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    ticker VARCHAR(16),
    issuing_agent VARCHAR(64) NOT NULL,
    violated_rule VARCHAR(64) NOT NULL,
    risk_level VARCHAR(32) NOT NULL,
    reason TEXT NOT NULL,
    order_payload JSONB,
    escalated_to VARCHAR(64) DEFAULT 'strategy_cio',
    resolution_status VARCHAR(32) DEFAULT 'PENDING',
    cio_resolution_id VARCHAR(64),
    resolved_at TIMESTAMP WITH TIME ZONE
);

-- 6. portfolio_risk
CREATE TABLE IF NOT EXISTS risk_snapshots (
    date DATE PRIMARY KEY,
    es_97_5 NUMERIC(6,2) NOT NULL,
    garch_cash_target NUMERIC(6,2) NOT NULL,
    drawdown_tier VARCHAR(16) NOT NULL,
    max_drawdown_from_peak NUMERIC(6,2) NOT NULL,
    cdc_active BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS risk_limits (
    limit_type VARCHAR(32) PRIMARY KEY,
    max_single_stock_pct NUMERIC(6,2) NOT NULL DEFAULT 15.0,
    max_sector_pct NUMERIC(6,2) NOT NULL DEFAULT 35.0,
    hard_stop_loss_pct NUMERIC(6,2) NOT NULL DEFAULT 2.0,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 7. portfolio_allocation
CREATE TABLE IF NOT EXISTS portfolio_account (
    account_id VARCHAR(32) PRIMARY KEY DEFAULT 'MAIN_FUND',
    cash_balance NUMERIC(18,2) NOT NULL,
    total_nav NUMERIC(18,2) NOT NULL,
    peak_nav NUMERIC(18,2) NOT NULL,
    drawdown_tier VARCHAR(16) DEFAULT 'GREEN',
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
-- Ghi chú: Danh mục vị thế thực tế được chuẩn hóa lưu trữ duy nhất tại bảng 'positions' (Prisma / T+2.5)

CREATE TABLE IF NOT EXISTS portfolio_decisions (
    decision_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date DATE NOT NULL,
    ticker VARCHAR(16) NOT NULL,
    action VARCHAR(16) NOT NULL,
    target_shares INTEGER NOT NULL,
    allocated_weight_pct NUMERIC(6,2) NOT NULL,
    rationale TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 8. trade_execution
CREATE TABLE IF NOT EXISTS order_executions (
    order_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker VARCHAR(16) NOT NULL,
    action VARCHAR(8) NOT NULL,
    shares INTEGER NOT NULL,
    executed_price NUMERIC(12,2) NOT NULL,
    target_price NUMERIC(12,2) NOT NULL,
    slippage_bps NUMERIC(8,2) NOT NULL,
    execution_mode VARCHAR(16) NOT NULL,
    executed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS slippage_records (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(16) NOT NULL,
    date DATE NOT NULL,
    adtv20_bucket VARCHAR(16) NOT NULL,
    actual_slippage_bps NUMERIC(8,2) NOT NULL,
    expected_slippage_bps NUMERIC(8,2) NOT NULL,
    mode VARCHAR(16) NOT NULL
);

-- 9. position_monitoring
CREATE TABLE IF NOT EXISTS position_health_ticks (
    ticker VARCHAR(16) PRIMARY KEY,
    current_pnl_pct NUMERIC(6,2) NOT NULL,
    distance_to_stop_loss_pct NUMERIC(6,2) NOT NULL,
    thesis_health_status VARCHAR(16) NOT NULL,
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stop_loss_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker VARCHAR(16) NOT NULL,
    triggered_price NUMERIC(12,2) NOT NULL,
    loss_pct_nav NUMERIC(6,2) NOT NULL,
    bypass_order_id UUID,
    triggered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 10. reinforcement_learning
CREATE TABLE IF NOT EXISTS rl_factor_weights (
    regime VARCHAR(32) PRIMARY KEY,
    f1_value_weight NUMERIC(6,4) NOT NULL,
    f2_quality_weight NUMERIC(6,4) NOT NULL,
    f3_momentum_weight NUMERIC(6,4) NOT NULL,
    f4_earnings_weight NUMERIC(6,4) NOT NULL,
    f5_flow_weight NUMERIC(6,4) NOT NULL,
    f6_technical_weight NUMERIC(6,4) NOT NULL,
    learning_epoch INTEGER NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kelly_win_rate_matrix (
    regime VARCHAR(32) NOT NULL,
    conviction_tier VARCHAR(8) NOT NULL,
    win_rate_p NUMERIC(6,4) NOT NULL,
    payoff_ratio_b NUMERIC(6,4) NOT NULL,
    sample_count INTEGER NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (regime, conviction_tier)
);

CREATE TABLE IF NOT EXISTS factor_ic_history (
    date DATE NOT NULL,
    factor_name VARCHAR(32) NOT NULL,
    rolling_20d_ic NUMERIC(8,4) NOT NULL,
    rolling_60d_ic NUMERIC(8,4) NOT NULL,
    cdc_decay_flag BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (date, factor_name)
);

-- 11. system_governance
CREATE TABLE IF NOT EXISTS governance_rules (
    rule_id VARCHAR(32) PRIMARY KEY,
    rule_name VARCHAR(128) NOT NULL,
    rule_category VARCHAR(32) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    parameters JSONB NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_reports (
    report_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    audit_date DATE NOT NULL,
    integrity_status VARCHAR(16) NOT NULL,
    violations_count INTEGER DEFAULT 0,
    summary TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 12. strategy_cio
CREATE TABLE IF NOT EXISTS strategic_allocations (
    allocation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date DATE NOT NULL,
    macro_view TEXT NOT NULL,
    cash_target_override NUMERIC(6,2),
    sector_focus JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cio_resolutions (
    resolution_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thesis_id UUID NOT NULL,
    debate_summary TEXT NOT NULL,
    final_resolution VARCHAR(16) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- PHẦN II: 12 BẢNG LOG TƯ DUY RIÊNG BIỆT (AUDIT LOG TABLES)
-- ============================================================================

CREATE TABLE IF NOT EXISTS log_market_surveillance (
    id BIGSERIAL PRIMARY KEY,
    date DATE NOT NULL,
    inputs JSONB NOT NULL,
    computation_trace JSONB NOT NULL,
    outputs JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS log_universe_discovery (
    id BIGSERIAL PRIMARY KEY,
    date DATE NOT NULL,
    filtered_counts JSONB NOT NULL,
    beneish_trace JSONB NOT NULL,
    exclusion_log JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS log_equity_research (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(16) NOT NULL,
    date DATE NOT NULL,
    factor_raw_metrics JSONB NOT NULL,
    moat_citations_evidence JSONB NOT NULL,
    llm_prompt_tokens INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS log_investment_thesis (
    id BIGSERIAL PRIMARY KEY,
    thesis_id VARCHAR(64) NOT NULL,
    ticker VARCHAR(16) NOT NULL,
    pre_mortem_scenarios JSONB NOT NULL,
    thesis_text TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS log_counter_thesis (
    id BIGSERIAL PRIMARY KEY,
    thesis_id VARCHAR(64) NOT NULL,
    ticker VARCHAR(16) NOT NULL,
    debate_challenge_text TEXT NOT NULL,
    llm_prompt_response JSONB NOT NULL,
    verdict VARCHAR(16) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS log_portfolio_risk (
    id BIGSERIAL PRIMARY KEY,
    date DATE NOT NULL,
    es_97_5_inputs JSONB NOT NULL,
    covariance_matrix JSONB,
    garch_cash_trace JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS log_portfolio_allocation (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(16) NOT NULL,
    kelly_math_steps JSONB NOT NULL,
    allocated_weight_pct NUMERIC(6,2) NOT NULL,
    rationale TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS log_trade_execution (
    id BIGSERIAL PRIMARY KEY,
    order_id UUID NOT NULL,
    ticker VARCHAR(16) NOT NULL,
    slicing_schedule JSONB NOT NULL,
    orderbook_depth_snapshot JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS log_position_monitoring (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(16) NOT NULL,
    pnl_pct NUMERIC(6,2) NOT NULL,
    stop_loss_triggered BOOLEAN DEFAULT FALSE,
    thesis_invalidated BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS log_reinforcement_learning (
    id BIGSERIAL PRIMARY KEY,
    date DATE NOT NULL,
    ic_rolling_scores JSONB NOT NULL,
    reward_signals JSONB NOT NULL,
    policy_weight_updates JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS log_system_governance (
    id BIGSERIAL PRIMARY KEY,
    rule_id VARCHAR(32),
    action_type VARCHAR(64) NOT NULL,
    audit_trail_verification JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS log_strategy_cio (
    id BIGSERIAL PRIMARY KEY,
    trigger_type VARCHAR(64) NOT NULL,
    debate_synthesis TEXT NOT NULL,
    resolution_payload JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- INDEXES CHO TRUY VẤN O(1) TỐI ƯU HIỆU NĂNG
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_factor_scores_symbol_date ON factor_scores (symbol, score_date);
CREATE INDEX IF NOT EXISTS idx_moat_profiles_ticker ON moat_profiles (ticker);
CREATE INDEX IF NOT EXISTS idx_universe_group ON universe_securities (universe_group);
CREATE INDEX IF NOT EXISTS idx_theses_status ON investment_theses (status);
CREATE INDEX IF NOT EXISTS idx_executions_date ON order_executions (executed_at);

-- ============================================================================
-- 13. BCTC PIPELINE RECORDS & R2 DEDUPLICATION TRACKER (IOS v5.1)
-- ============================================================================
CREATE TABLE IF NOT EXISTS bctc_pipeline_records (
    id VARCHAR(64) PRIMARY KEY,
    ticker VARCHAR(16) NOT NULL,
    fiscal_year INT NOT NULL,
    fiscal_quarter INT NOT NULL,
    report_scope VARCHAR(16) NOT NULL DEFAULT 'CONSOLIDATED',
    
    -- Status flags
    is_classified BOOLEAN DEFAULT FALSE,
    classifier_status VARCHAR(32) DEFAULT 'PENDING',
    total_raw_pages INT,
    retained_pages INT,
    
    -- Cloudflare R2 PDF flags
    r2_pdf_uploaded BOOLEAN DEFAULT FALSE,
    r2_pdf_key VARCHAR(256),
    r2_pdf_url TEXT,
    pdf_sha256 VARCHAR(64),
    
    -- SAG OCR flags
    is_ocr_completed BOOLEAN DEFAULT FALSE,
    ocr_status VARCHAR(32) DEFAULT 'PENDING',
    r2_md_uploaded BOOLEAN DEFAULT FALSE,
    r2_md_key VARCHAR(256),
    r2_md_url TEXT,
    
    -- Audit & Temporal details
    is_audited BOOLEAN DEFAULT FALSE,
    auditor_name VARCHAR(128),
    audit_opinion VARCHAR(32),
    announcement_date DATE,
    
    -- SAG Active Window Lifecycle (IOS v5.1)
    is_active_for_sag BOOLEAN DEFAULT FALSE,
    sag_doc_role VARCHAR(32) DEFAULT NULL, -- 'ANNUAL_BACKBONE' | 'LATEST_QUARTER' | 'ARCHIVED'
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE (ticker, fiscal_year, fiscal_quarter, report_scope)
);
CREATE INDEX IF NOT EXISTS idx_bctc_pipeline_ticker ON bctc_pipeline_records(ticker, fiscal_year, fiscal_quarter);
CREATE INDEX IF NOT EXISTS idx_bctc_pipeline_flags ON bctc_pipeline_records(is_classified, r2_pdf_uploaded, is_ocr_completed);
CREATE INDEX IF NOT EXISTS idx_bctc_pipeline_sag_active ON bctc_pipeline_records(ticker, is_active_for_sag);

