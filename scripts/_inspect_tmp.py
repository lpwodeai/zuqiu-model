# -*- coding: utf-8 -*-
import sqlite3
c = sqlite3.connect(r'data/odds.db')
c.row_factory = sqlite3.Row

mid = '2026-08-29_多特蒙德_汉堡'
print('--- total_goals_history for', mid, '---')
rows = c.execute("SELECT timestamp, goals_0, goals_1, goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus FROM total_goals_history WHERE match_id=? ORDER BY timestamp", (mid,)).fetchall()
print('count:', len(rows))
for r in rows[:2]:
    print(dict(r))

print('--- total_goals_history overall count / recent 2026 ---')
print('total rows:', c.execute('SELECT COUNT(*) FROM total_goals_history').fetchone()[0])
for r in c.execute("SELECT SUBSTR(match_id,1,10) d, COUNT(DISTINCT match_id) n FROM total_goals_history WHERE match_id LIKE '2026%' GROUP BY d ORDER BY d DESC LIMIT 8").fetchall():
    print(dict(r))

print('--- score_history for', mid, '---')
rows = c.execute("SELECT timestamp, score, odds FROM score_history WHERE match_id=? ORDER BY timestamp LIMIT 5", (mid,)).fetchall()
print('count:', len(rows))
for r in rows:
    print(dict(r))

print('--- score_history recent 2026 dates ---')
for r in c.execute("SELECT SUBSTR(match_id,1,10) d, COUNT(DISTINCT match_id) n FROM score_history WHERE match_id LIKE '2026%' GROUP BY d ORDER BY d DESC LIMIT 8").fetchall():
    print(dict(r))

print('--- handicap_history for', mid, '---')
rows = c.execute("SELECT timestamp, hcp_win, hcp_draw, hcp_lose FROM handicap_history WHERE match_id=? ORDER BY timestamp LIMIT 3", (mid,)).fetchall()
print('count:', len(rows))
for r in rows:
    print(dict(r))