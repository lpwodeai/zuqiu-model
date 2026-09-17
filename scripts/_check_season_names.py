import sqlite3
conn = sqlite3.connect('f:/zuqiu/五大联赛专属模型/五大联赛专属模型/data/odds.db')
# Check team names in matches for 意甲 older seasons
for season in ['意甲2023-2024赛季', '意甲2024-2025赛季', '意甲2025-2026赛季', '意甲2026-2027赛季']:
    rows = conn.execute("SELECT home_team, away_team FROM matches WHERE match_type=? LIMIT 3", (season,)).fetchall()
    print(f"\n=== {season} ===")
    for r in rows:
        print(f"  '{r[0]}' vs '{r[1]}'")
conn.close()