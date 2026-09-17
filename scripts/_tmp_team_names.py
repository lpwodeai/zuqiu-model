# -*- coding: utf-8 -*-
"""临时: 列出各联赛球队中文名（用于构建德比对列表）。只读。"""
import sqlite3, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'odds.db')
conn = sqlite3.connect(DB)
c = conn.cursor()
for lg in ('英超', '西甲', '意甲', '德甲', '法甲'):
    print('==', lg, '==')
    rows = c.execute(
        'SELECT home_team, COUNT(*) FROM matches WHERE league=? GROUP BY home_team ORDER BY 2 DESC',
        (lg,)
    ).fetchall()
    for name, cnt in rows:
        print(f'   {name}: {cnt}')
conn.close()
print('DONE')