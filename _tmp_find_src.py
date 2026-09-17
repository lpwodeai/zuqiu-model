# -*- coding: utf-8 -*-
"""定位 matches.handicap=-1.75 的来源"""
import sqlite3

conn = sqlite3.connect('data/odds.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 国米这场完整行 + 表结构
print("=== matches 表结构 ===")
cur.execute("PRAGMA table_info(matches)")
cols = [r['name'] for r in cur.fetchall()]
print("  列:", cols)

print("\n=== 国米这场完整行 ===")
cur.execute("SELECT * FROM matches WHERE match_id='2026-09-15_Inter_Udinese'")
r = cur.fetchone()
if r:
    for k in r.keys():
        print(f"  {k} = {r[k]}")

# 看 handicap=-1.75 的所有比赛，观察 match_type/source 特征
print("\n=== handicap=-1.75 的比赛（含来源特征）===")
cur.execute("""
    SELECT match_id, home_team, away_team, match_type, source, created_at, updated_at, actual_score
    FROM matches WHERE handicap=-1.75 LIMIT 15
""")
for r in cur.fetchall():
    print(f"  {r['match_id']} | type={r['match_type']} | src={r['source']} | score={r['actual_score']} | upd={r['updated_at']}")

conn.close()