# -*- coding: utf-8 -*-
"""关键缺口确认：盘口 line 到底存哪、完赛+三项赔率+line 的可回测样本有多少"""
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"

def q(db, sql, args=()):
    c = sqlite3.connect(str(DATA / db))
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()

print("A) odds.db.matches 完赛+三项历史齐全+handicap非空（25/26）")
rows = q("odds.db", """
    SELECT COUNT(*) FROM matches m
    WHERE m.actual_score IS NOT NULL AND m.actual_score != ''
      AND m.match_type LIKE '%2025-2026%'
      AND m.handicap IS NOT NULL
      AND EXISTS (SELECT 1 FROM wdl_history w WHERE w.match_id=m.match_id)
      AND EXISTS (SELECT 1 FROM handicap_history h WHERE h.match_id=m.match_id)
      AND EXISTS (SELECT 1 FROM total_goals_history t WHERE t.match_id=m.match_id)""")
print("  25/26 完赛+handicap非空+三项齐全:", rows[0][0])

print("\nB) 抽样看这 42/243 场的 match_id 是中文还是英文 + 是否含 line")
rows = q("odds.db", """
    SELECT m.match_id, m.handicap, m.actual_score
    FROM matches m
    WHERE m.actual_score IS NOT NULL AND m.actual_score != ''
      AND m.match_type LIKE '%2025-2026%'
      AND m.handicap IS NOT NULL
      AND EXISTS (SELECT 1 FROM wdl_history w WHERE w.match_id=m.match_id)
    LIMIT 15""")
for r in rows:
    print("  ", r)

print("\nC) 三项历史齐全的 25/26 完赛场（243）里，match_id 是中文吗？")
rows = q("odds.db", """
    SELECT m.match_id, m.handicap, m.actual_score
    FROM matches m
    WHERE m.actual_score IS NOT NULL AND m.actual_score != ''
      AND m.match_type LIKE '%2025-2026%'
      AND EXISTS (SELECT 1 FROM wdl_history w WHERE w.match_id=m.match_id)
      AND EXISTS (SELECT 1 FROM handicap_history h WHERE h.match_id=m.match_id)
      AND EXISTS (SELECT 1 FROM total_goals_history t WHERE t.match_id=m.match_id)
    LIMIT 10""")
for r in rows:
    print("  ", r)

print("\nD) odds_timing.db.match_results 表")
try:
    rows = q("odds_timing.db", "SELECT * FROM match_results LIMIT 20")
    for r in rows:
        print("  ", r)
    print("  match_results 总数:", q("odds_timing.db", "SELECT COUNT(*) FROM match_results")[0][0])
except Exception as e:
    print("  查询失败:", e)

print("\nE) odds_timing.db.handicap_timing 结构 + 样本（应含 line）")
try:
    rows = q("odds_timing.db", "PRAGMA table_info(handicap_timing)")
    for r in rows:
        print("   col:", r[1], r[2])
    rows = q("odds_timing.db", "SELECT * FROM handicap_timing LIMIT 3")
    for r in rows:
        print("   row:", r)
except Exception as e:
    print("  查询失败:", e)

print("\nF) 是否有盘口 line 存于 handicap 相关的 mapping 表")
for tbl in ("match_mapping", "match_id_mapping", "fbref_match_mapping", "team_mapping"):
    try:
        rows = q("odds.db", f"PRAGMA table_info({tbl})")
        print(f"  [{tbl}] 列:", [r[1] for r in rows])
    except Exception as e:
        print(f"  [{tbl}] 无:", e)