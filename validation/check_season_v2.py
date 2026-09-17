# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
conn = sqlite3.connect(BASE_DIR / "data" / "odds.db")

print("=" * 70)
print("fbref_match_mapping 按 season 字段分布")
print("=" * 70)
rows = conn.execute(
    "SELECT season, league, COUNT(*) FROM fbref_match_mapping "
    "GROUP BY season, league ORDER BY season, league"
).fetchall()
for r in rows:
    print(f"  {r[0]:12s} | {r[1]:25s} | {r[2]:5d}")

print()
totals = conn.execute(
    "SELECT season, COUNT(*) FROM fbref_match_mapping "
    "GROUP BY season ORDER BY season"
).fetchall()
for r in totals:
    print(f"  {r[0]:12s} 总计: {r[1]:5d}")

total_all = conn.execute("SELECT COUNT(*) FROM fbref_match_mapping").fetchone()[0]
print(f"  全表行数: {total_all}")

print()
print("=" * 70)
print("matches 表 vs fbref_match_mapping 赛季对比")
print("=" * 70)
rows = conn.execute("""
    SELECT 
        CASE WHEN m.match_date LIKE '2023%' THEN '2023-2024'
             WHEN m.match_date LIKE '2024%' THEN '2024-2025'
             WHEN m.match_date LIKE '2025%' THEN '2025-2026'
             WHEN m.match_date LIKE '2026%' THEN '2026-2027'
             ELSE '其他' END as season,
        COUNT(DISTINCT m.match_id) as matches_cnt,
        COUNT(DISTINCT f.odds_match_id) as mapped_cnt
    FROM matches m
    LEFT JOIN fbref_match_mapping f ON m.match_id = f.odds_match_id
    GROUP BY season
    ORDER BY season
""").fetchall()
for r in rows:
    pct = r[2] * 100.0 / r[1] if r[1] > 0 else 0
    print(f"  {r[0]:12s} | matches={r[1]:5d} | 已映射={r[2]:5d} ({pct:.0f}%)")

print()
print("=" * 70)
print("fbref_match_mapping season='2023-2024' 联赛明细")
print("=" * 70)
rows = conn.execute("""
    SELECT league, COUNT(*) as cnt, MIN(match_date), MAX(match_date)
    FROM fbref_match_mapping 
    WHERE season = '2023-2024'
    GROUP BY league
    ORDER BY league
""").fetchall()
for r in rows:
    print(f"  {r[0]:25s} | {r[1]:4d} 场 | {r[2]} ~ {r[3]}")

conn.close()