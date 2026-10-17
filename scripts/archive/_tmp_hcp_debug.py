# -*- coding: utf-8 -*-
import sqlite3

DB = r"F:\zuqiu\五大联赛专属模型\五大联赛专属模型\data\odds.db"
c = sqlite3.connect(DB)
cur = c.cursor()

# 25 场污染的 0.25 档明细
cur.execute("""
SELECT match_id, match_date, home_team, away_team, handicap, actual_handicap, actual_score, updated_at
FROM matches
WHERE handicap IS NOT NULL AND handicap*2 != CAST(handicap*2 AS INTEGER)
ORDER BY match_date
""")
rows = cur.fetchall()
print("=== 污染场次数 ===", len(rows))
for r in rows:
    print(r)

c.close()