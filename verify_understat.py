import sqlite3

c = sqlite3.connect(r'data/odds.db')
cur = c.cursor()

print("=== Understat 西甲 26/27 数据查验 ===\n")

for tbl in ["understat_match_team_stats", "understat_player_xg", "understat_shots"]:
    cnt = cur.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    print(f"{tbl}: {cnt} 行")

print("\n--- 比赛级 (understat_match_team_stats) ---")
cur.execute("SELECT match_id, datetime, home_team, away_team, home_goals, away_goals, home_xg, away_xg, is_result FROM understat_match_team_stats ORDER BY datetime LIMIT 20")
for r in cur.fetchall():
    print(f"  {r[0]} {r[1][:16]}  {r[2]} vs {r[3]}  {r[4]}-{r[5]}  xG:{r[6]}-{r[7]}  result={'T' if r[8] else 'F'}")

print("\n--- 球员 xG (understat_player_xg) 样本 ---")
cur.execute("SELECT player_name, team_name, position, time, goals, shots, xg, assists, xa, xg_chain, xg_buildup FROM understat_player_xg WHERE xg > 0.09 ORDER BY xg DESC LIMIT 15")
for r in cur.fetchall():
    print(f"  {r[0]:20s} {r[1]:12s} {r[2]:3s} t={r[3]:3d} g={r[4]} sh={r[5]} xG={r[6]:.3f} a={r[7]} xA={r[8]:.3f} chain={r[9]:.3f} bld={r[10]:.3f}")

print("\n--- 射门级 (understat_shots) 样本 ---")
cur.execute("SELECT shot_id, match_id, minute, result, x, y, xg, player_name, shot_type, situation, player_assisted FROM understat_shots ORDER BY xg DESC LIMIT 15")
for r in cur.fetchall():
    print(f"  m{r[1]} {r[2]}' {r[3]:18s} ({r[4]:.2f},{r[5]:.2f}) xG={r[6]:.3f} {r[7]:18s} {r[8]:12s} {r[9]:10s} assist={r[10]}")

c.close()