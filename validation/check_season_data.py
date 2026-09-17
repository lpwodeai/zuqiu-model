# -*- coding: utf-8 -*-
"""检查 odds.db 中各赛季数据分布"""
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"

conn = sqlite3.connect(DB_PATH)

print("=" * 70)
print("📊 odds.db 比赛数据赛季分布")
print("=" * 70)

# 1. matches 表结构
print("\n📋 matches 表结构:")
cols = conn.execute("PRAGMA table_info(matches)").fetchall()
for c in cols:
    print(f"  {c[1]:25s} {c[2]:15s}")

# 2. matches 表赛季分布 (按 match_date)
print(f"\n{'='*70}")
print("📋 matches 表 - 按 match_date 赛季分布")
print("=" * 70)

query = """
    SELECT 
        CASE 
            WHEN match_date LIKE '2023%' THEN '2023-2024'
            WHEN match_date LIKE '2024%' THEN '2024-2025'
            WHEN match_date LIKE '2025%' THEN '2025-2026'
            WHEN match_date LIKE '2026%' THEN '2026-2027'
            ELSE '其他'
        END as season,
        match_type,
        COUNT(*) as cnt
    FROM matches
    GROUP BY season, match_type
    ORDER BY season, match_type
"""
rows = conn.execute(query).fetchall()

if rows:
    current_season = None
    season_total = 0
    for row in rows:
        if row[0] != current_season:
            if current_season is not None:
                print(f"  小计: {season_total} 场\n")
            current_season = row[0]
            season_total = 0
            print(f"【{row[0]}】")
        print(f"  {row[1]:30s} {row[2]:>5d} 场")
        season_total += row[2]
    print(f"  小计: {season_total} 场\n")

# 3. 日期范围
print(f"{'='*70}")
print("📋 日期范围")
print("=" * 70)
dr = conn.execute("SELECT MIN(match_date), MAX(match_date) FROM matches").fetchone()
print(f"  matches 表: {dr[0]} ~ {dr[1]}")

# 4. 汇总
print(f"\n{'='*70}")
print("📊 各赛季汇总")
print("=" * 70)
total = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
seasons = conn.execute("""
    SELECT 
        CASE 
            WHEN match_date LIKE '2023%' THEN '2023-2024'
            WHEN match_date LIKE '2024%' THEN '2024-2025'
            WHEN match_date LIKE '2025%' THEN '2025-2026'
            WHEN match_date LIKE '2026%' THEN '2026-2027'
            ELSE '其他'
        END as season,
        COUNT(*) as cnt
    FROM matches
    GROUP BY season
    ORDER BY season
""").fetchall()

print(f"  总比赛数: {total} 场")
for s in seasons:
    pct = s[1] / total * 100
    print(f"  {s[0]:12s}: {s[1]:>5d} 场 ({pct:.1f}%)")

# 5. 结论
has_2324 = any(s[0] == '2023-2024' for s in seasons)
print(f"\n{'='*70}")
if has_2324:
    cnt = [s[1] for s in seasons if s[0] == '2023-2024'][0]
    print(f"✅ 已有 2023-2024 赛季数据: {cnt} 场")
else:
    print(f"❌ 无 2023-2024 赛季数据，需要爬取")
    print(f"   当前数据集中在 {dr[0]} ~ {dr[1]}")

conn.close()