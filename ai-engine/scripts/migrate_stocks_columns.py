"""Migration Script: Finalizing the Security (stocks) table.

Adds missing columns to the stocks table to align with IOS v5.1 Sovereign Engine requirements.
"""
import os
import psycopg2
from dotenv import load_dotenv

def migrate():
    load_dotenv()
    db_url = os.getenv('DATABASE_URL', 'postgresql://postgres:123@localhost:5432/aiinvest')
    
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    
    print("Migrating 'stocks' table to align with Sovereign Engine v5.1...")
    
    columns_to_add = [
        ("universe_group", "VARCHAR(20) DEFAULT 'B'"),
        ("group_updated_at", "TIMESTAMP"),
        ("trading_status", "VARCHAR(20) DEFAULT 'NORMAL'"),
        ("beneish_status", "VARCHAR(20) DEFAULT 'PENDING'"),
        ("beneish_score", "DECIMAL"),
        ("beneish_updated", "DATE"),
        ("gil_flag", "VARCHAR(20) DEFAULT 'PASS'"),
        ("audit_opinion", "VARCHAR(20) DEFAULT 'UNQUALIFIED'"),
        ("audit_year", "INTEGER")
    ]
    
    for col_name, col_type in columns_to_add:
        try:
            cur.execute(f"ALTER TABLE stocks ADD COLUMN {col_name} {col_type}")
            print(f"Added column: {col_name}")
        except psycopg2.errors.DuplicateColumn:
            conn.rollback()
            print(f"Column {col_name} already exists.")
        except Exception as e:
            conn.rollback()
            print(f"Error adding {col_name}: {e}")
            
    conn.commit()
    conn.close()
    print("Stocks table migration complete.")

if __name__ == "__main__":
    migrate()
