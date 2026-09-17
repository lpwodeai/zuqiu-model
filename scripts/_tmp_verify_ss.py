# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ODDS = BASE / "data" / "odds.db"

c = sqlite3.connect(ODDS)
cur = c.cursor()

print("=== odds.db matches 表 26/27 赛季全部 ===")
cur.execute("""SELECT match_id, match_date, home_team, away_team, actual_score, actual_wdl, actual_total_goals
               FROM matches WHERE match_type LIKE '%2026-2027%' ORDER BY match_date""")
for r in cur.fetchall():
    print("  ", r)

print("\n=== odds.db fbref_match_mapping 西甲26/27（含比分，SofaScore写入）===")
cur.execute("""SELECT fbref_match_id, match_date, home_team_cn, away_team_cn, fbref_score, season
               FROM fbref_match_mapping WHERE league='西甲' AND season IN ('26/27','2026-2027')
               ORDER BY match_date""")
for r in cur.fetchall():
    print("  ", r)

c.close()