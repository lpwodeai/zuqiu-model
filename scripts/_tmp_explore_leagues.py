# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
c = sqlite3.connect(str(BASE / "data" / "odds.db"))
c.row_factory = sqlite3.Row

print("=== handicap_history 表结构 ===")
cols = c.execute("PRAGMA table_info(handicap_history)").fetchall()
for col in cols:
    print(f"  {col['name']:<20} {col['type']}")

print("\n=== handicap_history 意甲样本(看有无盘口线) ===")
rows = c.execute("""
    SELECT h.* FROM handicap_history h
    JOIN matches m ON m.match_id = h.match_id
    WHERE m.match_type='意甲2025-2026赛季'
    LIMIT 3
""").fetchall()
for r in rows:
    print(dict(r))

print("\n=== 意甲25/26 handicap_history 里 handicap字段非空的数量 ===")
n = c.execute("""
    SELECT COUNT(*) FROM handicap_history h
    JOIN matches m ON m.match_id = h.match_id
    WHERE m.match_type='意甲2025-2026赛季' AND h.handicap IS NOT NULL
""").fetchone()[0]
print(f"  handicap非空: {n}")

print("\n=== 意甲25/26 三项齐全且handicap_history.handicap非空 的场数(可跑B/C) ===")
n2 = c.execute("""
    SELECT COUNT(*) FROM matches m
    WHERE m.match_type='意甲2025-2026赛季' AND m.actual_score IS NOT NULL AND m.actual_score!=''
      AND EXISTS(SELECT 1 FROM wdl_history w WHERE w.match_id=m.match_id)
      AND EXISTS(SELECT 1 FROM handicap_history h WHERE h.match_id=m.match_id AND h.handicap IS NOT NULL)
      AND EXISTS(SELECT 1 FROM total_goals_history g WHERE g.match_id=m.match_id)
""").fetchone()[0]
print(f"  可跑三项联动(B/C): {n2}")

print("\n=== 法甲25/26 handicap_history.handicap 字段情况 ===")
n3 = c.execute("""
    SELECT COUNT(*) FROM handicap_history h
    JOIN matches m ON m.match_id = h.match_id
    WHERE m.match_type='法甲2025-2026赛季' AND h.handicap IS NOT NULL
""").fetchone()[0]
print(f"  法甲 hcp_history.handicap非空: {n3}")

c.close()
