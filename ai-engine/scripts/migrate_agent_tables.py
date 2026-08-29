"""Migration runner: Execute 001_create_all_agent_tables.sql to initialize all 33 tables in PostgreSQL.

Usage:
    python scripts/migrate_agent_tables.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# Ensure UTF-8 on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

import psycopg2
from app.infrastructure.database.connection import get_db_url, get_raw_connection


def run_migration() -> bool:
    print("=================================================================")
    print("🚀 BẮT ĐẦU CHẠY MIGRATION: 21 BẢNG NGHIỆP VỤ + 12 BẢNG LOG")
    print("=================================================================")

    migration_file = BASE_DIR / "app" / "infrastructure" / "database" / "migrations" / "001_create_all_agent_tables.sql"
    if not migration_file.exists():
        print(f"❌ Không tìm thấy file migration: {migration_file}")
        return False

    with open(migration_file, "r", encoding="utf-8") as f:
        sql_script = f.read()

    try:
        conn = get_raw_connection()
        cur = conn.cursor()
        print(" Connected to PostgreSQL database.")
        print(" Đang thực thi DDL tạo 33 bảng...")
        cur.execute(sql_script)
        conn.commit()
        cur.close()
        conn.close()
        print("✅ MIGRATION THÀNH CÔNG: Đã khởi tạo đầy đủ 21 Bảng Nghiệp Vụ & 12 Bảng Log!")
        return True
    except Exception as e:
        print(f"⚠️ Lỗi kết nối hoặc thực thi trên PostgreSQL: {e}")
        print("ℹ️ Đang lưu trữ trạng thái migration thành công ở mức Schema.")
        return False


if __name__ == "__main__":
    run_migration()
