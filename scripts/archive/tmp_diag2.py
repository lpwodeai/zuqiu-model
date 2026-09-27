# -*- coding: utf-8 -*-
import sqlite3, sys
sys.path.insert(0, "scripts")
from generate_unified_report import discover_matches, find_sporttery_match_id, load_500_data, load_sofascore_data

con = sqlite3.connect("data/odds.db")
con.row_factory = sqlite3.Row
cur = con.cursor()

ms = discover_matches(con, "2026-09-12", "2026-09-15")
rows = []
for m in ms:
    home_cn = m["home_team_cn"]; fid = m["fid"]; md = m["match_date"]
    smid = find_sporttery_match_id(con, home_cn, m["away_team_cn"], md)
    wdl = cur.execute("SELECT COUNT(*) FROM wdl_history WHERE match_id=?", (smid,)).fetchone()[0] if smid else 0
    ouz = load_500_data(con, fid)["ouzhi"]
    cc = int(ouz["company_count"] or 0) if ouz is not None else 0
    has_sofa = load_sofascore_data(con, md, home_cn) is not None
    comp_odds = 1.0 if wdl >= 2 else (0.5 if wdl == 1 else 0.0)
    compl = int(round((comp_odds + (1.0 if cc > 0 else 0.0) + (1.0 if has_sofa else 0.0)) / 3 * 100))
    rows.append((md, m["league"], home_cn, m["away_team_cn"], wdl, cc, int(has_sofa), compl))

for md, lg, h, a, wdl, cc, sofa, compl in rows:
    print(f"{md} [{lg}] {h} vs {a} | wdl={wdl} 500cc={cc} sofa={sofa} => {compl}%")

from collections import Counter
c = Counter(r[7] for r in rows)
print("\n完整度分布:", dict(sorted(c.items())))
print("未达100%:", sum(1 for r in rows if r[7] < 100), "/", len(rows))
# breakdown of what's still missing
print("\n--- 仍缺 500.com (cc=0) ---")
for r in rows:
    if r[5] == 0: print(f"  {r[0]} [{r[1]}] {r[2]} vs {r[3]} fid? wdl={r[4]}")
con.close()