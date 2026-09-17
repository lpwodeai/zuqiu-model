# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'f:\zuqiu\五大联赛专属模型\五大联赛专属模型')
from db_utils import connect, read_sql

c = connect()

print("=== fbref_match_mapping (sofascore) 2026-08-30/31 比赛 ===")
df = read_sql("""
SELECT match_date, home_team_cn, away_team_cn, league, fbref_match_id, odds_match_id
FROM fbref_match_mapping
WHERE fbref_match_url LIKE '%sofascore%'
  AND match_date IN ('2026-08-30','2026-08-31')
ORDER BY match_date, home_team_cn
""", c)
print(df.to_string() if len(df) else "(无 SofaScore 8/30-31 比赛)")

print("\n=== sofascore_team_features 覆盖 8/30-31 ===")
df2 = read_sql("""
SELECT match_date, home_team_cn, away_team_cn
FROM sofascore_team_features
WHERE match_date IN ('2026-08-30','2026-08-31')
ORDER BY match_date, home_team_cn
""", c)
print(df2.to_string() if len(df2) else "(无)")

print("\n=== sofascore_team_features 总行数 ===")
df3 = read_sql("SELECT COUNT(1) n FROM sofascore_team_features", c)
print(df3.to_string())