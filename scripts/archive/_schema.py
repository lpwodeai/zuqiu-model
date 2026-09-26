# -*- coding: utf-8 -*-
import sqlite3
c = sqlite3.connect(r'data/odds.db')
c.row_factory = sqlite3.Row

tables = ['wdl_history','handicap_history','total_goals_history','score_history',
          'odds500_match','odds500_betting','odds500_ouzhi_summary','odds500_ouzhi_company','odds500_stat',
          'sofascore_team_features','match_id_mapping','matches','fbref_match_mapping']
for t in tables:
    try:
        cols = c.execute(f"PRAGMA table_info({t})").fetchall()
        n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"\n=== {t} (rows={n}) ===")
        print('  ' + ', '.join(r['name'] for r in cols))
    except Exception as e:
        print(f"\n=== {t} === ERROR: {e}")

# 抽样看关键表
print("\n\n### wdl_history 样例(今日一场, 按timestamp) ###")
for r in c.execute("SELECT * FROM wdl_history WHERE match_id LIKE '2026-08-2%' ORDER BY timestamp DESC LIMIT 3"):
    print(' ', dict(r))
print("\n### handicap_history 样例 ###")
for r in c.execute("SELECT * FROM handicap_history WHERE match_id LIKE '2026-08-2%' ORDER BY timestamp DESC LIMIT 3"):
    print(' ', dict(r))
print("\n### total_goals_history 样例 ###")
for r in c.execute("SELECT * FROM total_goals_history ORDER BY timestamp DESC LIMIT 2"):
    print(' ', dict(r))
print("\n### score_history 样例 ###")
for r in c.execute("SELECT * FROM score_history LIMIT 3"):
    print(' ', dict(r))