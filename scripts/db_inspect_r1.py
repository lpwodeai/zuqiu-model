# -*- coding: utf-8 -*-
"""临时核验脚本：检查 odds.db 中 4 张表的结构与当前数据"""
import sqlite3
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
ODDS_DB = PROJECT_DIR / "data" / "odds.db"

conn = sqlite3.connect(str(ODDS_DB))
cur = conn.cursor()

print("=== 所有表 ===")
for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
    print(" ", r[0])

print("\n=== 目标表结构 ===")
for tbl in ["fbref_match_mapping", "match_lineups", "match_player_stats", "fbref_players"]:
    print(f"\n--- {tbl} ---")
    try:
        for r in cur.execute(f"PRAGMA table_info({tbl})"):
            # cid, name, type, notnull, dflt, pk
            print(f"  {r[1]:<24} {r[2]:<12} notnull={r[3]} pk={r[5]}")
    except Exception as e:
        print(f"  ERROR: {e}")

print("\n=== fbref_match_mapping 数据 2026-08+ ===")
try:
    for r in cur.execute("SELECT * FROM fbref_match_mapping WHERE match_date >= '2026-08-01'"):
        print(" ", r)
except Exception as e:
    print("  ERROR:", e)

print("\n=== match_lineups 数据量 ===")
try:
    n = cur.execute("SELECT COUNT(*) FROM match_lineups").fetchone()[0]
    print(f"  总行数: {n}")
    for r in cur.execute("SELECT * FROM match_lineups LIMIT 5"):
        print("  ", r)
except Exception as e:
    print("  ERROR:", e)

print("\n=== match_player_stats 数据量 ===")
try:
    n = cur.execute("SELECT COUNT(*) FROM match_player_stats").fetchone()[0]
    print(f"  总行数: {n}")
    for r in cur.execute("SELECT * FROM match_player_stats LIMIT 5"):
        print("  ", r)
except Exception as e:
    print("  ERROR:", e)

print("\n=== fbref_players 数据量 ===")
try:
    n = cur.execute("SELECT COUNT(*) FROM fbref_players").fetchone()[0]
    print(f"  总行数: {n}")
    for r in cur.execute("SELECT * FROM fbref_players LIMIT 5"):
        print("  ", r)
except Exception as e:
    print("  ERROR:", e)

print("\n=== matches 表结构 ===")
for r in cur.execute("PRAGMA table_info(matches)"):
    print(f"  {r[1]:<22} {r[2]:<12} notnull={r[3]}")
print("\n=== matches 2026-08+ 数据 ===")
for r in cur.execute("SELECT * FROM matches WHERE match_date >= '2026-08-01'"):
    print("  ", r)

print("\n=== model_predictions 表结构 ===")
for r in cur.execute("PRAGMA table_info(model_predictions)"):
    print(f"  {r[1]:<22} {r[2]:<12} notnull={r[3]}")
print("\n=== model_predictions 数据 ===")
for r in cur.execute("SELECT * FROM model_predictions"):
    print("  ", r)

conn.close()