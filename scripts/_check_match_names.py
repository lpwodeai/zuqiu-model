import sqlite3
conn = sqlite3.connect('f:/zuqiu/五大联赛专属模型/五大联赛专属模型/data/odds.db')
rows = conn.execute("SELECT home_team, away_team, match_id FROM matches WHERE match_type='意甲2026-2027赛季' LIMIT 10").fetchall()
for r in rows:
    print(f"'{r[0]}' vs '{r[1]}'  (match_id={r[2]})")
conn.close()