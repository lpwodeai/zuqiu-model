# -*- coding: utf-8 -*-
"""分析 odds.db 表结构、队名映射关系、中文 match_id 特征"""
import sqlite3, json
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).resolve().parent.parent
c = sqlite3.connect(str(BASE / "data" / "odds.db"))
c.row_factory = sqlite3.Row

# 1. 表结构
print("=== 赔率时序表结构 ===")
for tbl in ['wdl_history', 'handicap_history', 'total_goals_history']:
    cols = c.execute(f"PRAGMA table_info({tbl})").fetchall()
    n = c.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    print(f"  {tbl} ({n} rows): {[(c[1], c[2]) for c in cols]}")

# 2. matches 表结构
print("\n=== matches 表结构 ===")
cols = c.execute("PRAGMA table_info(matches)").fetchall()
print(f"  {[(c[1], c[2]) for c in cols]}")

# 3. 抽取中文 match_id 样本，分析格式
print("\n=== 中文 match_id 样本 ===")
for r in c.execute("SELECT DISTINCT match_id FROM wdl_history WHERE match_id LIKE '%阿森纳%' LIMIT 5").fetchall():
    print(f"  {r[0]}")
for r in c.execute("SELECT DISTINCT match_id FROM wdl_history WHERE match_id LIKE '%皇马%' LIMIT 5").fetchall():
    print(f"  {r[0]}")
for r in c.execute("SELECT DISTINCT match_id FROM wdl_history WHERE match_id LIKE '%拜仁%' LIMIT 5").fetchall():
    print(f"  {r[0]}")

# 4. matches 表英文 match_id 样本
print("\n=== matches 表英文 match_id 样本 ===")
for r in c.execute("SELECT match_id, match_type FROM matches WHERE match_type LIKE '英超%' AND match_id LIKE '%Arsenal%' LIMIT 3").fetchall():
    print(f"  {r[0]} | {r[1]}")
for r in c.execute("SELECT match_id, match_type FROM matches WHERE match_type LIKE '西甲%' AND match_id LIKE '%Real Madrid%' LIMIT 3").fetchall():
    print(f"  {r[0]} | {r[1]}")

# 5. 统计中文 match_id 中出现的所有球队名
print("\n=== 中文 match_id 队名统计（前30）===")
teams = defaultdict(int)
for r in c.execute("SELECT match_id FROM wdl_history WHERE match_id GLOB '*-*-*_*_*'").fetchall():
    parts = r[0].split('_')
    if len(parts) >= 3:
        for p in parts[1:]:
            teams[p] += 1
for t, n in sorted(teams.items(), key=lambda x: -x[1])[:30]:
    print(f"  {t}: {n}")

c.close()