import sqlite3
conn = sqlite3.connect('f:/zuqiu/五大联赛专属模型/五大联赛专属模型/data/odds.db')
rows = conn.execute("SELECT DISTINCT team FROM match_player_stats WHERE match_id IN ('2026-08-23_Inter_Monza','2026-08-23_Udinese_Como','2026-08-23_Genoa_SSC Napoli','2026-08-23_Parma_Cagliari')").fetchall()
print("=== match_player_stats 中的队名 ===")
for r in rows:
    print(f"  '{r[0]}'")

# Also check fbref_match_mapping
rows2 = conn.execute("SELECT home_team_cn, away_team_cn FROM fbref_match_mapping WHERE odds_match_id IN ('2026-08-23_Inter_Monza','2026-08-23_Udinese_Como','2026-08-23_Genoa_SSC Napoli','2026-08-23_Parma_Cagliari')").fetchall()
print("\n=== fbref_match_mapping 中的队名(home_cn, away_cn) ===")
for r in rows2:
    print(f"  '{r[0]}' vs '{r[1]}'")

# Also check match_lineups
rows3 = conn.execute("SELECT DISTINCT team FROM match_lineups WHERE match_id IN ('2026-08-23_Inter_Monza','2026-08-23_Udinese_Como','2026-08-23_Genoa_SSC Napoli','2026-08-23_Parma_Cagliari')").fetchall()
print("\n=== match_lineups 中的队名 ===")
for r in rows3:
    print(f"  '{r[0]}'")
conn.close()