import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
conn = sqlite3.connect(BASE_DIR / "data" / "odds.db")

total = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
print(f"matches 总行数: {total}")

seasons = conn.execute("""
    SELECT 
        CASE WHEN match_date LIKE '2023%' THEN '2023-2024'
             WHEN match_date LIKE '2024%' THEN '2024-2025'
             WHEN match_date LIKE '2025%' THEN '2025-2026'
             WHEN match_date LIKE '2026%' THEN '2026-2027'
             ELSE 'other' END as s,
        match_type,
        COUNT(*) as cnt
    FROM matches
    GROUP BY s, match_type
    ORDER BY s, match_type
""").fetchall()

for r in seasons:
    print(f"  {r[0]:12s} | {r[1]:30s} | {r[2]:4d}")

# check if 23/24 has enough data
cnt_2324 = conn.execute(
    "SELECT COUNT(*) FROM matches WHERE match_date LIKE '2023%'"
).fetchone()[0]
print(f"\n23/24 赛季总计: {cnt_2324} 场")

# Check fbref_match_mapping for 23/24
cnt_fbref = conn.execute(
    "SELECT COUNT(*) FROM fbref_match_mapping WHERE season='23/24'"
).fetchone()[0]
print(f"fbref_match_mapping 23/24: {cnt_fbref} 行")

# Check mapping rate
mapped = conn.execute("""
    SELECT COUNT(DISTINCT m.id) FROM matches m
    INNER JOIN fbref_match_mapping f ON m.match_id = f.odds_match_id
    WHERE m.match_date LIKE '2023%'
""").fetchone()[0]
print(f"23/24 已映射到 fbref: {mapped} 场")

conn.close()