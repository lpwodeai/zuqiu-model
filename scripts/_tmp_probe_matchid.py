# -*- coding: utf-8 -*-
import sqlite3
c = sqlite3.connect('data/odds.db')
c.row_factory = sqlite3.Row
mid = '2026-09-07_Deportivo Alavés_Osasuna'

print("=== matches 表 ===")
r = c.execute("SELECT * FROM matches WHERE match_id=?", (mid,)).fetchone()
if r:
    for k in r.keys():
        print(f"  {k}: {r[k]}")
else:
    print("  (无)")

print("\n=== post_match_review 表 ===")
r = c.execute("SELECT * FROM post_match_review WHERE match_id=?", (mid,)).fetchone()
if r:
    for k in r.keys():
        print(f"  {k}: {r[k]}")

print("\n=== match_mapping 表结构及匹配 ===")
cols = [r[1] for r in c.execute("PRAGMA table_info(match_mapping)")]
print("  cols:", cols)
for k in ('match_id','home_team','away_team'):
    if k in cols:
        rows = c.execute(f"SELECT * FROM match_mapping WHERE {k}=?", (mid,)).fetchall() if k=='match_id' else c.execute(f"SELECT * FROM match_mapping WHERE {k} LIKE ?", ('%拉科%',)).fetchall()
        for rr in rows:
            print("  ", dict(rr))
        break

print("\n=== 是否有 Deportivo La Coruña / 拉科鲁尼亚 相关 match_id ===")
for pat in ('%Deportivo%','%Coru%','%Alav%'):
    rows = c.execute("SELECT DISTINCT match_id FROM matches WHERE match_id LIKE ?", (pat,)).fetchall()
    print(f"  matches LIKE {pat}: {[r[0] for r in rows]}")