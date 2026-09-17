import sqlite3
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
db_path = BASE_DIR / "data" / "odds_timing.db"
print(f"File exists: {os.path.exists(db_path)}")
print(f"File size: {os.path.getsize(db_path) / 1024 / 1024:.1f} MB")

db = sqlite3.connect(db_path)
c = db.cursor()

# List all tables
tables = c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
print(f"\nTables ({len(tables)}):")
for t in tables:
    c2 = db.cursor()
    cnt = c2.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
    print(f"  {t[0]}: {cnt} rows")

# Check wdl_timing specifically
try:
    cnt = c.execute("SELECT COUNT(*) FROM wdl_timing").fetchone()[0]
    print(f"\nwdl_timing rows: {cnt}")
except Exception as e:
    print(f"\nwdl_timing: {e}")

# Check match_results for timing data
try:
    cnt = c.execute("SELECT COUNT(*) FROM match_results").fetchone()[0]
    print(f"match_results rows: {cnt}")
except Exception as e:
    print(f"match_results: {e}")

# Check match_mapping
try:
    cnt = c.execute("SELECT COUNT(*) FROM match_mapping").fetchone()[0]
    print(f"match_mapping rows: {cnt}")
except Exception as e:
    print(f"match_mapping: {e}")

db.close()