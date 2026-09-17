# -*- coding: utf-8 -*-
import sqlite3
c = sqlite3.connect(r'f:\zuqiu\五大联赛专属模型\五大联赛专属模型\data\odds.db', timeout=30)
c.row_factory = sqlite3.Row

print("=== 4 支主队最新特征行（match_date >= 2026-09-13） ===")
for t in ["皇家贝蒂斯", "埃尔切", "巴塞罗那", "马拉加"]:
    rows = c.execute(
        "SELECT match_date, home_team_cn, away_team_cn, sofa_rat_5g_home, sofa_rat_5g_away, sofa_xg_5g_home, sofa_xg_5g_away "
        "FROM sofascore_team_features WHERE home_team_cn=? AND match_date>='2026-09-13' "
        "ORDER BY match_date DESC", (t,)).fetchall()
    print(f"\n[{t}] 命中 {len(rows)} 条:")
    for r in rows:
        print(f"  {r['match_date']} | {r['home_team_cn']} vs {r['away_team_cn']} | 评分 {r['sofa_rat_5g_home']:.2f}/{r['sofa_rat_5g_away']:.2f} | xG {r['sofa_xg_5g_home']:.4f}/{r['sofa_xg_5g_away']:.4f}")

print("\n=== 这4场真实对阵（从 matches 表，match_date 含 09-16） ===")
for r in c.execute(
    "SELECT match_id, match_date, home_team, away_team FROM matches "
    "WHERE (home_team LIKE '%贝蒂斯%' OR home_team LIKE '%埃尔切%' OR home_team LIKE '%巴萨%' OR home_team LIKE '%马拉加%') "
    "AND match_date >= '2026-09-13' ORDER BY match_date"):
    print(f"  {r['match_date']} | {r['home_team']} vs {r['away_team']} | id={r['match_id']}")
c.close()