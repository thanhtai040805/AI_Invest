"""Run all migration SQL files in order."""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from app.infrastructure.database.connection import get_raw_connection


def run_all_migrations():
    migrations_dir = BASE_DIR / "app" / "infrastructure" / "database" / "migrations"
    sql_files = sorted(migrations_dir.glob("*.sql"))

    conn = get_raw_connection()
    cur = conn.cursor()

    for sql_file in sql_files:
        print(f"Executing: {sql_file.name}...")
        with open(sql_file, "r", encoding="utf-8") as f:
            script = f.read()
        cur.execute(script)
        conn.commit()
        print(f"✅ Finished: {sql_file.name}")

    cur.close()
    conn.close()
    print("🎉 All migrations completed successfully!")


if __name__ == "__main__":
    run_all_migrations()
