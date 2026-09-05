import sys
sys.path.insert(0, ".")
from app.infrastructure.database.pg_pool import get_conn

def patch_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 1. Alter cio_resolutions
            cur.execute("ALTER TABLE cio_resolutions ALTER COLUMN final_resolution TYPE VARCHAR(64);")
            cur.execute("ALTER TABLE cio_resolutions ALTER COLUMN thesis_id TYPE VARCHAR(64) USING thesis_id::text;")

            # 2. Add regime_multiplier to counter_thesis_verdicts
            cur.execute("ALTER TABLE counter_thesis_verdicts ADD COLUMN IF NOT EXISTS regime_multiplier NUMERIC(6,2) DEFAULT 1.0;")

            # 3. Alter investment_theses columns that may be VARCHAR(16)
            cur.execute("ALTER TABLE investment_theses ALTER COLUMN catalyst_type TYPE VARCHAR(64);")
            cur.execute("ALTER TABLE investment_theses ALTER COLUMN status TYPE VARCHAR(32);")

        conn.commit()
    print("PATCH DB COMPLETED SUCCESSFULLY")

if __name__ == "__main__":
    patch_db()
