# -*- coding: utf-8 -*-
import sqlite3
c = sqlite3.connect('data/odds.db')
c.row_factory = sqlite3.Row
OLD = '2026-09-07_Deportivo Alavés_Osasuna'

print("=== score_history 表结构 ===")
cols = [r[1] for r in c.execute("PRAGMA table_info(score_history)")]
print(cols)

print("\n=== score_history 样例(按 match_id) ===")
rows = c.execute("SELECT * FROM score_history WHERE match_id=? LIMIT 5", (OLD,)).fetchall()
for r in rows:
    print(dict(r))

print("\n=== score_history 中 distinct 队名相关字段 ===")
for col in cols:
    if 'team' in col or 'home' in col or 'away' in col:
        vals = c.execute(f"SELECT DISTINCT {col} FROM score_history WHERE match_id=?", (OLD,)).fetchall()
        print(f"  {col}: {[v[0] for v in vals]}")