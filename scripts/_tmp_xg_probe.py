# -*- coding: utf-8 -*-
"""临时：确认 xG 深度特征所需字段（P1-8）"""
import sqlite3

DB = r"f:\zuqiu\五大联赛专属模型\五大联赛专属模型\data\odds.db"
c = sqlite3.connect(DB)
cur = c.cursor()

print("=== fbref_match_mapping schema ===")
cur.execute("PRAGMA table_info(fbref_match_mapping)")
for r in cur.fetchall():
    print("  ", r[1], r[2])

print("\n=== fbref_match_mapping 样本 (sofascore) ===")
cur.execute("SELECT fbref_match_id, home_team_cn, away_team_cn, league, match_date FROM fbref_match_mapping WHERE fbref_match_url LIKE '%sofascore%' LIMIT 3")
for r in cur.fetchall():
    print("  ", r)

print("\n=== match_player_stats.team 样本 & expected_goals ===")
cur.execute("SELECT team, player_name, expected_goals FROM match_player_stats WHERE stats_source='sofascore' AND expected_goals IS NOT NULL LIMIT 5")
for r in cur.fetchall():
    print("  ", r)

print("\n=== 有 expected_goals 的场次数 (sofascore) ===")
cur.execute("SELECT COUNT(DISTINCT fbref_match_id) FROM match_player_stats WHERE stats_source='sofascore' AND expected_goals IS NOT NULL")
print("  distinct events with xg:", cur.fetchone()[0])

print("\n=== sofascore 比赛 league 分布 ===")
cur.execute("SELECT league, COUNT(*) FROM fbref_match_mapping WHERE fbref_match_url LIKE '%sofascore%' GROUP BY league ORDER BY 2 DESC LIMIT 15")
for r in cur.fetchall():
    print("  ", r)

c.close()