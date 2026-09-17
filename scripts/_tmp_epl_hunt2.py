# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
c = sqlite3.connect(str(BASE / "data" / "odds.db"))
c.row_factory = sqlite3.Row

teams = ['阿森纳','利物浦','曼城','切尔西','热刺','曼联','纽卡斯尔','阿斯顿维拉','西汉姆','布莱顿','狼队','水晶宫','埃弗顿','布伦特福德','伯恩茅斯','诺丁汉','富勒姆','伯恩利','利兹联','莱斯特','南安普顿','伊普斯维奇']

for tbl in ['wdl_history','handicap_history','total_goals_history']:
    total = 0
    example = None
    for t in teams:
        n = c.execute(f"SELECT COUNT(*) FROM {tbl} WHERE match_id LIKE ?", (f"%{t}%",)).fetchone()[0]
        total += n
        if not example and n > 0:
            row = c.execute(f"SELECT match_id FROM {tbl} WHERE match_id LIKE ? LIMIT 1", (f"%{t}%",)).fetchone()
            example = row[0]
    print(f"{tbl}: {total} 条, 示例: {example}")

# 统计 distinct match_id
print()
for tbl in ['wdl_history','handicap_history','total_goals_history']:
    ids = set()
    for t in teams:
        rows = c.execute(f"SELECT DISTINCT match_id FROM {tbl} WHERE match_id LIKE ?", (f"%{t}%",)).fetchall()
        ids.update(r[0] for r in rows)
    print(f"{tbl} distinct match_id: {len(ids)} 个")

c.close()