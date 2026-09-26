# -*- coding: utf-8 -*-
"""诊断：今日(及近期)未开赛比赛 + 三大通道数据对齐情况。"""
import sqlite3, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DB = r"f:\zuqiu\五大联赛专属模型\五大联赛专属模型\data\odds.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("=== odds500_match: season=26/27, status=1 (未开赛) 未来7天 ===")
cur.execute("""
  SELECT fid, league, season, round, match_date, match_time,
         home_team_cn, away_team_cn, home_team_en, away_team_en,
         win, draw, lost, handicap, pan, status
  FROM odds500_match
  WHERE season='26/27' AND status=1
    AND match_date >= '2026-08-30'
  ORDER BY match_date, match_time
""")
rows = cur.fetchall()
print("count:", len(rows))
for r in rows:
    print(dict(r))

print("\n=== 今日/近3日 所有 status 分布 (26/27) ===")
cur.execute("SELECT match_date, status, COUNT(*) FROM odds500_match WHERE season='26/27' GROUP BY match_date, status ORDER BY match_date, status")
for r in cur.fetchall():
    print(dict(r))

# 数据通道对齐抽样：挑一场今日未开赛，看三大通道
print("\n=== 三大通道对齐抽样 ===")
cur.execute("""
  SELECT fid, match_id, home_team_cn, away_team_cn, home_team_en, away_team_en, match_date
  FROM odds500_match WHERE season='26/27' AND status=1 AND match_date >= '2026-08-30'
  ORDER BY match_date, match_time LIMIT 3
""")
samples = cur.fetchall()
for s in samples:
    fid = s['fid']
    cn = f"{s['home_team_cn']}_{s['away_team_cn']}"
    print(f"\n--- {s['match_date']} {s['home_team_cn']} vs {s['away_team_cn']} (fid={fid}, match_id={s['match_id']}) ---")
    # sporttery wdl_history
    cur.execute("SELECT COUNT(*) c, MAX(timestamp) ts FROM wdl_history WHERE match_id LIKE ? OR match_id_en LIKE ?", (f"%{cn}%", f"%{s['home_team_en']}%{s['away_team_en']}%"))
    print("  wdl_history:", dict(cur.fetchone()))
    # sofascore_team_features
    cur.execute("SELECT COUNT(*) c, MAX(match_date) md FROM sofascore_team_features WHERE match_id_cn LIKE ?", (f"%{cn}%",))
    print("  sofascore_team_features:", dict(cur.fetchone()))
    # odds500_betting
    cur.execute("SELECT COUNT(*) c FROM odds500_betting WHERE fid=?", (fid,))
    print("  odds500_betting:", cur.fetchone()['c'])
    cur.execute("SELECT COUNT(*) c FROM odds500_ouzhi_summary WHERE fid=?", (fid,))
    print("  odds500_ouzhi_summary:", cur.fetchone()['c'])
    cur.execute("SELECT COUNT(*) c FROM odds500_ouzhi_company WHERE fid=?", (fid,))
    print("  odds500_ouzhi_company:", cur.fetchone()['c'])

conn.close()