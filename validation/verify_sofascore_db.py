# -*- coding: utf-8 -*-
"""验证 SofaScore 爬虫 DB 写入"""
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB = BASE_DIR / "data" / "odds.db"
conn = sqlite3.connect(DB)
cur = conn.cursor()
EVENT = '14025013'

# 1
cur.execute("SELECT odds_match_id, fbref_match_id, league, season, match_date, home_team_cn, away_team_cn, fbref_score FROM fbref_match_mapping WHERE fbref_match_id=?", (EVENT,))
r = cur.fetchone()
print('=== 1. fbref_match_mapping ===')
print(f"  odds_match_id      : {r[0]}")
print(f"  fbref_match_id(sofascore): {r[1]}")
print(f"  league/season      : {r[2]} {r[3]}")
print(f"  date/homeScore-awayScore: {r[4]} {r[7]}")
print(f"  {r[5]} vs {r[6]}")

# 2 lineups summary
cur.execute("SELECT formation, team, COUNT(1), SUM(is_starter) FROM match_lineups WHERE fbref_match_id=? GROUP BY team, formation", (EVENT,))
print("\n=== 2. match_lineups === 每队阵型/球员数")
for r in cur.fetchall():
    print(f"  team={r[1]:14s}  formation={r[0]:8s}  总球员={r[2]:2d}  首发={r[3]:2d}  替补={r[2]-r[3]:2d}")

cur.execute("SELECT team, jersey_number, position, player_name, is_starter, minutes_played, sub_in_time, sub_out_time, sub_reason FROM match_lineups WHERE fbref_match_id=? AND (sub_in_time IS NOT NULL OR sub_out_time IS NOT NULL) LIMIT 8", (EVENT,))
print("\n  有换人记录的球员示例 (前8个):")
for r in cur.fetchall():
    print(f"    {r[0]} #{r[1] or '':<3} {r[2] or '':<4} {r[3]:<22} starter={r[4]} mins={r[5] or 0:3d} in={str(r[6] or ''):>5} out={str(r[7] or ''):>5} reason={r[8] or ''}")

# 3 player stats top5 by rating
cur.execute("SELECT team, player_name, rating, expected_goals, expected_assists, total_shots, shots_on_target, goals, assists, minutes_played, meters_covered_sprinting_km, accurate_pass_sofa, total_pass_sofa, accurate_long_balls, stats_source FROM match_player_stats WHERE fbref_match_id=? ORDER BY rating DESC LIMIT 5", (EVENT,))
print("\n=== 3. match_player_stats TOP5 评分(SofaScore rating) ===")
cols = ['team','player','rating','xG','xA','shots','SoT','G','A','min','sprint_km','accP','totP','LBacc','src']
print("  " + " | ".join(f"{c:<12}" for c in cols))
for r in cur.fetchall():
    vals = [str(c) if c is not None else "-" for c in r]
    print("  " + " | ".join(f"{v:<12}" for v in vals))

# 4. fbref_players
cur.execute("SELECT COUNT(1) FROM fbref_players WHERE fbref_player_url LIKE '%sofascore%'")
print(f"\n=== 4. fbref_players SofaScore 注册球员累计: {cur.fetchone()[0]} ===")

# 5. stats_source split
cur.execute("SELECT stats_source, COUNT(1) FROM match_player_stats WHERE fbref_match_id=? GROUP BY stats_source", (EVENT,))
print(f"\n=== 5. stats_source 分布: {cur.fetchall()} ===")

conn.close()
print("\n✅ DB 写入验证通过")
