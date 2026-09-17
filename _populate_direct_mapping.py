# -*- coding: utf-8 -*-
"""补齐 match_id_mapping 桥表：把时序 match_id 与 matches.match_id 完全一致的直接对齐写入桥表。

仅 INSERT OR IGNORE，不清空、不改写现有 3,857 行正确映射。
"""
import sqlite3, os
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'odds.db')
c = sqlite3.connect(DB)

before = c.execute("SELECT COUNT(*) FROM match_id_mapping").fetchone()[0]
print(f"映射表现有: {before} 行")

# 直接对齐：时序 match_id == matches.match_id（中文赛季，占 ~71%）
c.execute("""
INSERT OR IGNORE INTO match_id_mapping (sh_match_id, matches_match_id, match_method, confidence)
SELECT DISTINCT h.match_id, h.match_id, 'direct', 1.0
FROM (
    SELECT match_id FROM wdl_history
    UNION SELECT match_id FROM handicap_history
    UNION SELECT match_id FROM total_goals_history
    UNION SELECT match_id FROM score_history
) h
JOIN matches m ON h.match_id = m.match_id
""")
c.commit()

after = c.execute("SELECT COUNT(*) FROM match_id_mapping").fetchone()[0]
added = after - before
print(f"新增直接对齐: {added} 行")
print(f"映射表现有: {after} 行")

# 方法分布
print("\n=== match_method 分布 ===")
for r in c.execute("SELECT match_method, COUNT(*) FROM match_id_mapping GROUP BY match_method ORDER BY 2 DESC"):
    print(f"  {r[0]}: {r[1]}")

c.close()
print("\n完成")