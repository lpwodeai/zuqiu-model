# -*- coding: utf-8 -*-
"""回填 matches.league：用 match_type 前缀提取联赛名，覆盖 NULL/空值。"""
import sqlite3, os
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'odds.db')
c = sqlite3.connect(DB)

before = c.execute("SELECT COUNT(*) FROM matches WHERE league IS NULL OR league = ''").fetchone()[0]
print(f"回填前 league 为空: {before}")

# 五大联赛中文名均为 2 字符，substr 提取前 2 位即可
c.execute("""
UPDATE matches
SET league = CASE
    WHEN match_type LIKE '英超%' THEN '英超'
    WHEN match_type LIKE '西甲%' THEN '西甲'
    WHEN match_type LIKE '意甲%' THEN '意甲'
    WHEN match_type LIKE '德甲%' THEN '德甲'
    WHEN match_type LIKE '法甲%' THEN '法甲'
    ELSE league
END
WHERE (league IS NULL OR league = '')
""")
c.commit()

after = c.execute("SELECT COUNT(*) FROM matches WHERE league IS NULL OR league = ''").fetchone()[0]
print(f"回填后 league 为空: {after}  (回填 {before - after} 行)")

print("\n=== league 分布 ===")
for r in c.execute("SELECT league, COUNT(*) FROM matches GROUP BY league ORDER BY 2 DESC"):
    print(f"  {r[0]!r}: {r[1]}")

c.close()
print("\n完成")