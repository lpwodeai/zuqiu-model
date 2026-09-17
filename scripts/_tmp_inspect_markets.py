# -*- coding: utf-8 -*-
"""临时：检查可用投注市场表结构。"""
import sqlite3
conn = sqlite3.connect(r'F:\zuqiu\五大联赛专属模型\五大联赛专属模型\data\odds.db')
cur = conn.cursor()
for t in ['odds500_ouzhi_company', 'odds500_ouzhi_summary', 'wdl_history']:
    cur.execute("PRAGMA table_info(%s)" % t)
    print(t, 'cols:', [r[1] for r in cur.fetchall()])
cur.execute('SELECT COUNT(*), COUNT(DISTINCT match_id) FROM odds500_ouzhi_company')
print('company rows/matches:', cur.fetchone())
cur.execute('SELECT COUNT(*), COUNT(DISTINCT match_id) FROM wdl_history')
print('wdl rows/matches:', cur.fetchone())
cur.execute('SELECT * FROM odds500_ouzhi_company LIMIT 3')
for r in cur.fetchall():
    print('company sample:', r)
conn.close()
