# -*- coding: utf-8 -*-
"""诊断：三大数据通道表结构 + 今日未开赛比赛，为 generate_unified_report.py 提供依据。"""
import sqlite3, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DB = r"f:\zuqiu\五大联赛专属模型\五大联赛专属模型\data\odds.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 1. 表清单
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
print("=== TABLES (%d) ===" % len(tables))
for t in tables:
    print("  ", t)

# 了解关键表结构
print("\n=== KEY TABLE SCHEMAS ===")
for t in ["matches", "wdl_history", "handicap_history", "total_goals_history",
          "score_history", "odds500_match", "odds500_betting", "odds500_ouzhi_summary",
          "odds500_ouzhi_company", "odds500_stat", "sofascore_team_features",
          "match_id_mapping", "fbref_match_mapping", "match_lineups",
          "understat_match_team_stats", "model_predictions"]:
    if t in tables:
        cur.execute(f"PRAGMA table_info('{t}')")
        cols = [r[1] for r in cur.fetchall()]
        print(f"\n[{t}] ({len(cols)} cols): {', '.join(cols)}")

conn.close()