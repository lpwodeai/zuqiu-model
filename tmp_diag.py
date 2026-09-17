# -*- coding: utf-8 -*-
import sqlite3
con = sqlite3.connect("data/odds.db")
con.row_factory = sqlite3.Row
cur = con.cursor()
cur.execute("""SELECT league, round, COUNT(*) c FROM odds500_match
WHERE season='26/27' AND status=1 AND match_date BETWEEN '2026-09-12' AND '2026-09-15'
GROUP BY league, round ORDER BY league, round""")
print("round distribution (09-12~09-15):")
for r in cur.fetchall():
    print(f"  {r['league']} 第{r['round']}轮: {r['c']}场")

# how many missing 500 (company_count=0) grouped by round
print("\n500-missing count by round:")
cur.execute("""SELECT m.league, m.round, COUNT(*) c FROM odds500_match m
LEFT JOIN odds500_ouzhi_summary s ON s.fid=m.fid AND (s.company_count>0)
WHERE m.season='26/27' AND m.status=1 AND m.match_date BETWEEN '2026-09-12' AND '2026-09-15'
AND s.company_count IS NULL
GROUP BY m.league, m.round ORDER BY m.league, m.round""")
for r in cur.fetchall():
    print(f"  {r['league']} 第{r['round']}轮: {r['c']}场缺失")
con.close()