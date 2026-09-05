-- 002_create_portfolio_campaigns.sql
-- Quản trị chiến dịch gom / xả cổ phiếu đa phiên (Execution Horizon & Phased Accumulation / Distribution)

CREATE TABLE IF NOT EXISTS portfolio_campaigns (
    campaign_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker VARCHAR(16) NOT NULL,
    direction VARCHAR(16) NOT NULL, -- 'ACCUMULATION' (BUY) hoặc 'DISTRIBUTION' (SELL)
    final_target_weight NUMERIC(6,4) NOT NULL,
    current_weight NUMERIC(6,4) NOT NULL,
    session_incremental_weight NUMERIC(6,4) NOT NULL,
    remaining_weight NUMERIC(6,4) NOT NULL,
    target_shares INTEGER NOT NULL,
    accumulated_shares INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(16) NOT NULL DEFAULT 'IN_PROGRESS', -- 'IN_PROGRESS', 'COMPLETED', 'CANCELLED'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_campaigns_ticker_status ON portfolio_campaigns (ticker, status);
