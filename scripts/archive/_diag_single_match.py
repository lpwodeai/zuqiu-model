# -*- coding: utf-8 -*-
"""诊断：单场三项时序数据实际格式，用于组装 odds_data。"""
import sqlite3, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
DB = r"f:\zuqiu\五大联赛专属模型\五大联赛专属模型\data\odds.db"
conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 用 Sassuolo vs Torino (中文) 的 match_id
cur.execute("SELECT DISTINCT match_id, match_id_en FROM wdl_history WHERE match_id LIKE '%萨索洛%都灵%' OR match_id LIKE '%萨索洛%' AND match_id LIKE '%都灵%'")
print("wdl_history match_ids:")
for r in cur.fetchall():
    print("  ", dict(r))

print("\n--- wdl_history sample (5 rows) ---")
cur.execute("SELECT * FROM wdl_history WHERE match_id LIKE '%萨索洛%都灵%' ORDER BY timestamp LIMIT 5")
for r in cur.fetchall():
    print("  ", dict(r))

print("\n--- handicap_history sample ---")
cur.execute("SELECT * FROM handicap_history WHERE match_id LIKE '%萨索洛%都灵%' ORDER BY timestamp LIMIT 5")
for r in cur.fetchall():
    print("  ", dict(r))

print("\n--- total_goals_history sample ---")
cur.execute("SELECT * FROM total_goals_history WHERE match_id LIKE '%萨索洛%都灵%' ORDER BY timestamp LIMIT 3")
for r in cur.fetchall():
    print("  ", dict(r))

print("\n--- score_history sample ---")
cur.execute("SELECT * FROM score_history WHERE match_id LIKE '%萨索洛%都灵%' ORDER BY timestamp LIMIT 10")
for r in cur.fetchall():
    print("  ", dict(r))

print("\n--- odds500_match 萨索洛 vs 都灵 ---")
cur.execute("SELECT fid, handicap, pan, win, draw, lost, home_team_en, away_team_en, match_date, match_time, round, league FROM odds500_match WHERE home_team_cn='萨索洛' AND away_team_cn='都灵' AND season='26/27'")
for r in cur.fetchall():
    print("  ", dict(r))

conn.close()