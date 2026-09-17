import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
db_path = BASE_DIR / "data" / "odds.db"
db = sqlite3.connect(db_path)
c = db.cursor()

# Check wdl_history schema and sample
print("=== wdl_history schema ===")
c.execute("PRAGMA table_info(wdl_history)")
for r in c.fetchall():
    print(f"  {r[1]}: {r[2]}")

print("\n=== wdl_history sample (first 5) ===")
c.execute("SELECT * FROM wdl_history LIMIT 5")
for r in c.fetchall():
    print(f"  {r}")

print(f"\n=== wdl_history match_id distribution ===")
c.execute("SELECT COUNT(DISTINCT match_id) FROM wdl_history")
print(f"  Distinct match_ids: {c.fetchone()[0]}")

# Check match_mapping schema
print("\n=== match_mapping schema ===")
c.execute("PRAGMA table_info(match_mapping)")
for r in c.fetchall():
    print(f"  {r[1]}: {r[2]}")

print("\n=== match_mapping sample (first 3) ===")
c.execute("SELECT * FROM match_mapping LIMIT 3")
for r in c.fetchall():
    print(f"  {r}")

# Check handicap_history schema
print("\n=== handicap_history schema ===")
c.execute("PRAGMA table_info(handicap_history)")
for r in c.fetchall():
    print(f"  {r[1]}: {r[2]}")

print("\n=== total_goals_history schema ===")
c.execute("PRAGMA table_info(total_goals_history)")
for r in c.fetchall():
    print(f"  {r[1]}: {r[2]}")

db.close()