# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path
BASE = Path(__file__).resolve().parent.parent
c = sqlite3.connect(str(BASE / "data" / "odds.db")); c.row_factory = sqlite3.Row

print("### matches(odds.db) 各比赛日：总数 / 有比分 / 有赛果WDL")
for d in ["2026-08-30","2026-08-31","2026-09-01","2026-09-06","2026-09-07","2026-09-12","2026-09-13","2026-09-14","2026-09-15"]:
    rows = c.execute("SELECT match_id, home_team, away_team, actual_score, actual_wdl FROM matches WHERE match_date=? ORDER BY match_id", (d,)).fetchall()
    n = len(rows)
    with_score = sum(1 for r in rows if r["actual_score"])
    print(f"\n== {d}: 共 {n} 场，有比分 {with_score} 场 ==")
    for r in rows:
        print(f"  {r['match_id']:<55} score={r['actual_score']!r:<8} wdl={r['actual_wdl']!r}")

c.close()