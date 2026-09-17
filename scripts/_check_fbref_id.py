import sqlite3
conn = sqlite3.connect('f:/zuqiu/五大联赛专属模型/五大联赛专属模型/data/odds.db')

# Check fbref_match_id in match_player_stats for the 4 matches
print("=== match_player_stats 的 fbref_match_id ===")
rows = conn.execute("SELECT DISTINCT match_id, fbref_match_id FROM match_player_stats WHERE match_id IN ('2026-08-23_Inter_Monza','2026-08-23_Udinese_Como','2026-08-23_Genoa_SSC Napoli','2026-08-23_Parma_Cagliari')").fetchall()
for r in rows:
    print(f"  {r[0]} -> fbref_match_id={r[1]}")

# Check match_lineups
print("\n=== match_lineups 的 fbref_match_id ===")
rows = conn.execute("SELECT DISTINCT match_id, fbref_match_id FROM match_lineups WHERE match_id IN ('2026-08-23_Inter_Monza','2026-08-23_Udinese_Como','2026-08-23_Genoa_SSC Napoli','2026-08-23_Parma_Cagliari')").fetchall()
for r in rows:
    print(f"  {r[0]} -> fbref_match_id={r[1]}")

# Check sofascore-specific stat columns for the 4 matches (sample)
print("\n=== 4场比赛的 sofascore 字段样本 (1 player) ===")
row = conn.execute("SELECT match_id, team, player_name, rating, expected_goals, expected_assists, accurate_pass_sofa, total_pass_sofa, accurate_long_balls, total_long_balls, accurate_crosses, total_crosses, successful_dribbles, total_dribbles, total_tackles, interceptions, duels_won, duels_total, aerials_won_total, aerials_total, ball_recoveries_sofa, possession_lost, big_chances_created, big_chances_missed, meters_covered_sprinting_km, meters_covered_high_speed_running_km, meters_covered_running_km, meters_covered_jogging_km, meters_covered_walking_km, gk_saves_sofa, goals_prevented, clearances, key_passes_sofa, touches_sofa FROM match_player_stats WHERE match_id='2026-08-23_Inter_Monza' LIMIT 1").fetchone()
if row:
    for i, col in enumerate(['match_id','team','player_name','rating','expected_goals','expected_assists','accurate_pass_sofa','total_pass_sofa','accurate_long_balls','total_long_balls','accurate_crosses','total_crosses','successful_dribbles','total_dribbles','total_tackles','interceptions','duels_won','duels_total','aerials_won_total','aerials_total','ball_recoveries_sofa','possession_lost','big_chances_created','big_chances_missed','meters_covered_sprinting_km','meters_covered_high_speed_running_km','meters_covered_running_km','meters_covered_jogging_km','meters_covered_walking_km','gk_saves_sofa','goals_prevented','clearances','key_passes_sofa','touches_sofa']):
        if i < len(row):
            print(f"  {col}: {row[i]}")

conn.close()