# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
tc = sqlite3.connect(str(BASE / "data" / "odds_timing.db"))
tc.row_factory = sqlite3.Row

# 表结构
print("=== 表结构 ===")
for t in tc.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
    tn = t['name']
    cols = tc.execute(f"PRAGMA table_info({tn})").fetchall()
    n = tc.execute(f"SELECT COUNT(*) FROM {tn}").fetchone()[0]
    print(f"  {tn} ({n} rows): {[c[1] for c in cols]}")

# matches 表数据
print("\n=== matches 表 ===")
for r in tc.execute("SELECT * FROM matches").fetchall():
    print(dict(r))

# wdl_timing 样本
print("\n=== wdl_timing 前3条 ===")
for r in tc.execute("SELECT * FROM wdl_timing LIMIT 3").fetchall():
    print(dict(r))

# handicap_timing
print("\n=== handicap_timing 前3条 ===")
for r in tc.execute("SELECT * FROM handicap_timing LIMIT 3").fetchall():
    print(dict(r))

# total_goals_timing
print("\n=== total_goals_timing 前3条 ===")
for r in tc.execute("SELECT * FROM total_goals_timing LIMIT 3").fetchall():
    print(dict(r))

# match_results
print("\n=== match_results ===")
for r in tc.execute("SELECT * FROM match_results").fetchall():
    print(dict(r))

# 统计 distinct match_id
print("\n=== 各表 distinct match_id 数 ===")
for tbl in ['wdl_timing', 'handicap_timing', 'total_goals_timing']:
    n = tc.execute(f"SELECT COUNT(DISTINCT match_id) FROM {tbl}").fetchone()[0]
    print(f"  {tbl}: {n}")

tc.close()