import sys
sys.path.insert(0, ".")
from app.infrastructure.database.pg_pool import get_conn

def migrate_audit_logs():
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Clear old legacy inconsistent audit logs from before the upgrade
            cur.execute("TRUNCATE TABLE audit_logs RESTART IDENTITY;")
            cur.execute("ALTER TABLE audit_logs ALTER COLUMN timestamp TYPE VARCHAR(64) USING timestamp::text;")
        conn.commit()
    print("AUDIT_LOGS MIGRATED TO EXACT STRING TIMESTAMP AND TRUNCATED CLEANLY")

if __name__ == "__main__":
    migrate_audit_logs()
