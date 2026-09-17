# -*- coding: utf-8 -*-
import sqlite3
c = sqlite3.connect('data/odds.db')
c.row_factory = sqlite3.Row

# 所有表
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("=== 所有表 ===")
for t in tables:
    print(t)

print("\n=== 搜索 match_id='2026-09-07_Deportivo Alavés_Osasuna' 出现在哪些表 ===")
for t in tables:
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
    for col in cols:
        if col.lower() in ('match_id', 'matchid', 'mid'):
            try:
                n = c.execute(f"SELECT COUNT(*) FROM {t} WHERE {col}=?", ('2026-09-07_Deportivo Alavés_Osasuna',)).fetchone()[0]
                if n:
                    print(f"  {t}.{col}: {n} 行")
            except Exception as e:
                pass