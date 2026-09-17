import sqlite3
conn = sqlite3.connect('f:/zuqiu/五大联赛专属模型/五大联赛专属模型/data/odds.db')

# Check fbref_match_mapping team names for Serie A
print("=== fbref_match_mapping 意甲队名样本 ===")
rows = conn.execute("SELECT league, season, home_team_cn, away_team_cn FROM fbref_match_mapping WHERE league='意甲' ORDER BY match_date LIMIT 10").fetchall()
for r in rows:
    print(f"  {r[0]} {r[1]}: {r[2]} vs {r[3]}")

# Check match_player_stats team names for older Serie A
print("\n=== match_player_stats 意甲队名样本 ===")
rows = conn.execute("SELECT DISTINCT team FROM match_player_stats WHERE match_id LIKE '%_%_%' AND stats_source='sofascore' LIMIT 30").fetchall()
for r in rows:
    print(f"  '{r[0]}'")

# Check match_lineups team names
print("\n=== match_lineups 意甲队名样本 ===")
rows = conn.execute("SELECT DISTINCT team FROM match_lineups WHERE match_id LIKE '%2025%' AND team LIKE '%Inter%' OR team LIKE '%Genoa%' OR team LIKE '%热那亚%' OR team LIKE '%国际米兰%' LIMIT 10").fetchall()
for r in rows:
    print(f"  '{r[0]}'")

conn.close()