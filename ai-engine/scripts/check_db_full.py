import sqlite3
import glob

db_files = glob.glob('d:/AIInvest/**/*.db', recursive=True) + glob.glob('d:/AIInvest/**/*.sqlite', recursive=True)
print("DB Files found:", db_files)

for db_path in db_files:
    print(f"\n=== DB: {db_path} ===")
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        tables = [t[0] for t in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        print("Tables:", tables)
        for tname in tables:
            try:
                count = cur.execute(f"SELECT count(*) FROM {tname}").fetchone()[0]
                cols = [c[1] for c in cur.execute(f"PRAGMA table_info({tname})").fetchall()]
                date_cols = [c for c in cols if 'date' in c.lower() or 'time' in c.lower()]
                min_max = ""
                if date_cols:
                    dcol = date_cols[0]
                    res = cur.execute(f"SELECT min({dcol}), max({dcol}) FROM {tname}").fetchone()
                    min_max = f" | Date Range: {res[0]} to {res[1]}"
                print(f"  - {tname}: {count} rows{min_max} | Cols: {cols}")
            except Exception as e:
                print(f"  - {tname}: error {e}")
        conn.close()
    except Exception as e:
        print(f"Error reading {db_path}: {e}")
