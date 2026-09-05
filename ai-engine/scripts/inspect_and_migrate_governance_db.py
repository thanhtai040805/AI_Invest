import os
from app.infrastructure.database.pg_pool import get_conn

def inspect_and_migrate():
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 1. Inspect stocks
            cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'stocks';")
            stocks_cols = cur.fetchall()
            print("STOCKS COLS:", stocks_cols)

            # 2. Inspect investment_theses
            cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'investment_theses';")
            theses_cols = dict(cur.fetchall())
            print("THESES COLS:", theses_cols)

            # 3. Inspect counter_thesis_verdicts
            cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'counter_thesis_verdicts';")
            counter_cols = dict(cur.fetchall())
            print("COUNTER COLS:", counter_cols)

            # 4. Migrate investment_theses and counter_thesis_verdicts thesis_id
            print("--- Migrating foreign keys and thesis_id columns ---")
            cur.execute("ALTER TABLE counter_thesis_verdicts DROP CONSTRAINT IF EXISTS counter_thesis_verdicts_thesis_id_fkey;")
            cur.execute("ALTER TABLE investment_theses ALTER COLUMN thesis_id TYPE VARCHAR(64) USING thesis_id::text;")
            cur.execute("ALTER TABLE counter_thesis_verdicts ALTER COLUMN thesis_id TYPE VARCHAR(64) USING thesis_id::text;")
            cur.execute("""
                ALTER TABLE counter_thesis_verdicts 
                ADD CONSTRAINT counter_thesis_verdicts_thesis_id_fkey 
                FOREIGN KEY (thesis_id) REFERENCES investment_theses(thesis_id) ON DELETE CASCADE;
            """)

            if "catalyst_description" not in theses_cols:
                cur.execute("ALTER TABLE investment_theses ADD COLUMN IF NOT EXISTS catalyst_description TEXT;")
            if "timeline_months" not in theses_cols:
                cur.execute("ALTER TABLE investment_theses ADD COLUMN IF NOT EXISTS timeline_months INTEGER DEFAULT 3;")
            if "target_price" not in theses_cols:
                cur.execute("ALTER TABLE investment_theses ADD COLUMN IF NOT EXISTS target_price NUMERIC(15,2);")
            if "entry_price_estimated" not in theses_cols:
                cur.execute("ALTER TABLE investment_theses ADD COLUMN IF NOT EXISTS entry_price_estimated NUMERIC(15,2);")
            if "pre_mortem_scenarios" not in theses_cols:
                cur.execute("ALTER TABLE investment_theses ADD COLUMN IF NOT EXISTS pre_mortem_scenarios JSONB;")

            # 5. Migrate counter_thesis_verdicts other columns
            if "base_cts" not in counter_cols:
                cur.execute("ALTER TABLE counter_thesis_verdicts ADD COLUMN IF NOT EXISTS base_cts NUMERIC(6,2);")
            if "interaction_multiplier" not in counter_cols:
                cur.execute("ALTER TABLE counter_thesis_verdicts ADD COLUMN IF NOT EXISTS interaction_multiplier NUMERIC(6,2);")
            if "ocr_penalty" not in counter_cols:
                cur.execute("ALTER TABLE counter_thesis_verdicts ADD COLUMN IF NOT EXISTS ocr_penalty NUMERIC(6,2);")
            if "macro_penalty" not in counter_cols:
                cur.execute("ALTER TABLE counter_thesis_verdicts ADD COLUMN IF NOT EXISTS macro_penalty NUMERIC(6,2);")

            # 6. Check log_investment_thesis and log_counter_thesis
            cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'log_investment_thesis';")
            log_th_cols = dict(cur.fetchall())
            if log_th_cols and log_th_cols.get("thesis_id") == "uuid":
                print("Altering log_investment_thesis.thesis_id to VARCHAR(64)")
                cur.execute("ALTER TABLE log_investment_thesis ALTER COLUMN thesis_id TYPE VARCHAR(64) USING thesis_id::text;")

            cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'log_counter_thesis';")
            log_ct_cols = dict(cur.fetchall())
            if log_ct_cols and log_ct_cols.get("thesis_id") == "uuid":
                print("Altering log_counter_thesis.thesis_id to VARCHAR(64)")
                cur.execute("ALTER TABLE log_counter_thesis ALTER COLUMN thesis_id TYPE VARCHAR(64) USING thesis_id::text;")

            # 7. Check audit_reports
            cur.execute("""
                CREATE TABLE IF NOT EXISTS audit_reports (
                    report_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    audit_date DATE NOT NULL,
                    integrity_status VARCHAR(16) NOT NULL,
                    violations_count INTEGER DEFAULT 0,
                    summary TEXT NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 8. Check governance_rules
            cur.execute("""
                CREATE TABLE IF NOT EXISTS governance_rules (
                    rule_id VARCHAR(32) PRIMARY KEY,
                    rule_name VARCHAR(128) NOT NULL,
                    rule_category VARCHAR(32) NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    parameters JSONB NOT NULL,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 9. Check violation_reports table for Governance Escalation
            cur.execute("""
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
            """)

            # 10. Check stocks sector column
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'stocks' AND column_name = 'sector';")
            if not cur.fetchall():
                print("Adding sector column to stocks if missing or copying from icb_name2/industry...")
                cur.execute("ALTER TABLE stocks ADD COLUMN IF NOT EXISTS sector VARCHAR(64);")
                # Try to populate sector from icb_name2 or industry if exists
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'stocks' AND column_name = 'icb_name2';")
                if cur.fetchall():
                    cur.execute("UPDATE stocks SET sector = icb_name2 WHERE sector IS NULL;")

        conn.commit()
        print("MIGRATION COMPLETED SUCCESSFULLY.")

if __name__ == "__main__":
    inspect_and_migrate()
