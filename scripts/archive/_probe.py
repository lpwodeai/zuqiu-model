# -*- coding: utf-8 -*-
import sqlite3
from collections import Counter
c = sqlite3.connect(r'data/odds.db')
c.row_factory = sqlite3.Row

print('=== model_predictions: model_name x prediction_type 分布 ===')
for r in c.execute("SELECT model_name, prediction_type, COUNT(*) n FROM model_predictions GROUP BY model_name, prediction_type ORDER BY model_name, prediction_type"):
    print(f"  {r['model_name']:20s} | {r['prediction_type']:8s} | {r['n']}")

print()
print('=== model_predictions 样例(近5条 WDL/SCORE) ===')
for r in c.execute("SELECT * FROM model_predictions WHERE prediction_type IN ('WDL','HCP','SCORE','TOTAL_GOALS','OU') ORDER BY id DESC LIMIT 8"):
    d = dict(r)
    print(' ', d)

print()
print('=== 今日未开赛比赛(matches 表 match_date>=2026-08-29) ===')
cols = [x[1] for x in c.execute("PRAGMA table_info(matches)")]
print('matches 列:', cols)
for r in c.execute("SELECT * FROM matches WHERE match_date >= '2026-08-29' ORDER BY match_date, home_team"):
    print(' ', dict(r))