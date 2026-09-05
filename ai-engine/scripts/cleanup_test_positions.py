import sys
sys.path.insert(0, ".")
from app.infrastructure.database.pg_pool import get_conn

def clean():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM positions WHERE symbol in ('FPT', 'NON_EXISTENT_TICKER_123');")
            cur.execute("DELETE FROM paper_trades WHERE ticker in ('FPT', 'NON_EXISTENT_TICKER_123');")
            cur.execute("DELETE FROM order_executions WHERE ticker in ('FPT', 'NON_EXISTENT_TICKER_123');")
            cur.execute("DELETE FROM investment_theses WHERE ticker in ('FPT', 'NON_EXISTENT_TICKER_123');")
        conn.commit()
    print("CLEARED TEST POSITIONS SUCCESSFULLY")

if __name__ == "__main__":
    clean()
