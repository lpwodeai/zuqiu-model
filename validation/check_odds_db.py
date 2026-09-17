import sqlite3
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
db_path = BASE_DIR / "data" / "odds.db"
print(f"File size: {os.path.getsize(db_path) / 1024 / 1024:.1f} MB")

db = sqlite3.connect(db_path)
c = db.cursor()

# List all tables
tables = c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
print(f"\nTables ({len(tables)}):")
for t in tables:
    c2 = db.cursor()
    cnt = c2.execute(f"SELECT COUNT(*) FROM [{t[0]}]").fetchone()[0]
    print(f"  {t[0]}: {cnt} rows")

# Check timing-related tables
for table_name in ['wdl_timing', 'handicap_timing', 'total_goals_timing', 'score_timing', 'match_mapping', 'wdl_history', 'handicap_history', 'total_goals_history']:
    try:
        cnt = c.execute(f"SELECT COUNT(*) FROM [{table_name}]").fetchone()[0]
        print(f"\n{table_name}: {cnt} rows")
        if cnt > 0:
            c2 = db.cursor()
            # Get sample
            sample = c2.execute(f"SELECT * FROM [{table_name}] LIMIT 1").fetchone()
            print(f"  Sample columns: {len(sample)}")
    except Exception as e:
        print(f"\n{table_name}: {e}")

db.close()