# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ODDS = BASE / "data" / "odds.db"

c = sqlite3.connect(ODDS)
cur = c.cursor()

print("=== matches 表 26/27 赛季 (match_type 含 2026-2027) ===")
cur.execute("""SELECT match_id, match_date, home_team, away_team, actual_score, actual_wdl, actual_handicap, actual_total_goals, match_type
               FROM matches WHERE match_type LIKE '%2026-2027%' ORDER BY match_date""")
for r in cur.fetchall():
    print("  ", r)

print("\n=== model_predictions 表（全部，按 match_id）===")
cur.execute("""SELECT match_id, model_name, prediction_type, prediction, probability
               FROM model_predictions ORDER BY match_id, prediction_type""")
rows = cur.fetchall()
print("  总数:", len(rows))
for r in rows:
    print("  ", r)