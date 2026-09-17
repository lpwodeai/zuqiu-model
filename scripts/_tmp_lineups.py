# -*- coding: utf-8 -*-
import sqlite3
c = sqlite3.connect('data/odds.db')
c.row_factory = sqlite3.Row
OLD = '2026-09-07_Deportivo Alavés_Osasuna'

for t in ('match_lineups','match_player_stats','match_missing_players'):
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
    print(f"=== {t} cols: {cols}")
    rows = c.execute(f"SELECT * FROM {t} WHERE match_id=? LIMIT 8", (OLD,)).fetchall()
    for r in rows:
        d = dict(r)
        # 只打印非空、且与队名/球员相关的关键字段
        print("  ", {k: v for k, v in d.items() if v not in (None, '') and k != 'match_id'})
    print()