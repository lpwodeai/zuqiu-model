# -*- coding: utf-8 -*-
import sqlite3

c = sqlite3.connect("data/odds.db")
cur = c.cursor()

# 1. 所有表名
tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print("=== ALL TABLES ===")
for t in tables:
    print(t)

# 2. fbref_match_mapping 字段
print("\n=== fbref_match_mapping columns ===")
for r in cur.execute("PRAGMA table_info(fbref_match_mapping)"):
    print(r[1], r[2])

# 3. 09-12 比赛的 match_id 与映射情况
print("\n=== 09-12 matches with mapping ===")
cur.execute("""
    SELECT r.match_id, r.home_team, r.away_team, m.odds_match_id, m.fbref_match_id
    FROM post_match_review r
    LEFT JOIN fbref_match_mapping m ON r.match_id = m.odds_match_id
    WHERE r.match_date = '2026-09-12'
    LIMIT 5
""")
for r in cur.fetchall():
    print(r[0], "|", r[1], "vs", r[2], "| odds_match_id=", r[3], "| fbref_match_id=", r[4])

# 4. match_player_stats 中 stats_source='sofascore' 的 match_id 样例
print("\n=== match_player_stats sofascore match_id sample (09-12) ===")
cur.execute("""
    SELECT DISTINCT match_id FROM match_player_stats
    WHERE stats_source = 'sofascore'
    AND match_id LIKE '%2026-09-12%'
    LIMIT 5
""")
for r in cur.fetchall():
    print(r[0])

c.close()