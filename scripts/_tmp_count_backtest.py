# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
c = sqlite3.connect(str(BASE / "data" / "odds.db"))
c.row_factory = sqlite3.Row

wdl_en = set(r[0] for r in c.execute("SELECT DISTINCT match_id_en FROM wdl_history WHERE match_id_en IS NOT NULL").fetchall())
hcp_en = set(r[0] for r in c.execute("SELECT DISTINCT match_id_en FROM handicap_history WHERE match_id_en IS NOT NULL").fetchall())
tg_en = set(r[0] for r in c.execute("SELECT DISTINCT match_id_en FROM total_goals_history WHERE match_id_en IS NOT NULL").fetchall())

score_mids = set(r[0] for r in c.execute("SELECT match_id FROM matches WHERE actual_score IS NOT NULL AND actual_score != ''").fetchall())

all3 = wdl_en & hcp_en & tg_en
with_score = all3 & score_mids
print(f"三项齐全: {len(all3)}, 有比分: {len(with_score)}")

for sz in ['2023','2024','2025','2026']:
    n = sum(1 for m in with_score if m.startswith(sz))
    print(f"  {sz}: {n}")

# 按联赛
for lg in ['英超','西甲','意甲','德甲','法甲']:
    n = 0
    for m in with_score:
        r = c.execute("SELECT match_type FROM matches WHERE match_id=?", (m,)).fetchone()
        if r and lg in r[0]:
            n += 1
    print(f"  {lg}: {n}")

c.close()