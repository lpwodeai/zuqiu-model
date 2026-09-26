# -*- coding: utf-8 -*-
"""诊断：SofaScore 与 500.com 单场数据格式，确定报告章节字段来源。"""
import sqlite3, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
DB = r"f:\zuqiu\五大联赛专属模型\五大联赛专属模型\data\odds.db"
conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("=== sofascore_team_features: 萨索洛 vs 都灵 关联 ===")
cur.execute("SELECT event_id, match_id_cn, match_date, league, home_team_cn, away_team_cn FROM sofascore_team_features WHERE (home_team_cn LIKE '%萨索洛%' AND away_team_cn LIKE '%都灵%') OR (home_team_cn LIKE '%都灵%' AND away_team_cn LIKE '%萨索洛%') ORDER BY match_date DESC LIMIT 5")
for r in cur.fetchall():
    print("  ", dict(r))

print("\n=== sofascore_team_features 近3日(>=08-29) 行 ===")
cur.execute("SELECT event_id, match_id_cn, match_date, league, home_team_cn, away_team_cn, sofa_rat_5g_home, sofa_xg_5g_home FROM sofascore_team_features WHERE match_date >= '2026-08-29' ORDER BY match_date LIMIT 40")
for r in cur.fetchall():
    print("  ", dict(r))

print("\n=== odds500_betting 单场(Sassuolo vs Torino fid=1414205) ===")
cur.execute("SELECT * FROM odds500_betting WHERE fid=1414205")
r = cur.fetchone()
if r: print("  ", dict(r))

print("\n=== odds500_ouzhi_summary 单场 ===")
cur.execute("SELECT * FROM odds500_ouzhi_summary WHERE fid=1414205")
r = cur.fetchone()
if r: print("  ", dict(r))

print("\n=== odds500_ouzhi_company 单场(前3家) ===")
cur.execute("SELECT company, live_win, live_draw, live_lose FROM odds500_ouzhi_company WHERE fid=1414205 LIMIT 5")
for r in cur.fetchall():
    print("  ", dict(r))

conn.close()