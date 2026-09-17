# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'f:\zuqiu\五大联赛专属模型\五大联赛专属模型')
from db_utils import connect, read_sql

c = connect()
print("=== 13场(8/30-31) odds500 各表覆盖情况 ===")
df = read_sql("""
SELECT m.match_date, m.match_time, m.league, m.home_team_cn, m.away_team_cn,
       (SELECT COUNT(1) FROM odds500_betting b WHERE b.fid=m.fid) AS betting,
       (SELECT COUNT(1) FROM odds500_ouzhi_summary o WHERE o.fid=m.fid) AS ouzhi_sum
FROM odds500_match m
WHERE m.status=1 AND m.match_date IN ('2026-08-30','2026-08-31')
  AND m.match_time BETWEEN '21:00' AND '23:59'
ORDER BY m.match_date, m.match_time
""", c)
print(df.to_string())
print("\n(注: 8/31 00:30-03:30 场次未在此列表, 需单独看)")
df2 = read_sql("""
SELECT m.match_date, m.match_time, m.home_team_cn, m.away_team_cn,
       (SELECT COUNT(1) FROM odds500_betting b WHERE b.fid=m.fid) AS betting,
       (SELECT COUNT(1) FROM odds500_ouzhi_summary o WHERE o.fid=m.fid) AS ouzhi_sum
FROM odds500_match m
WHERE m.status=1 AND m.match_date='2026-08-31'
ORDER BY m.match_time
""", c)
print(df2.to_string())