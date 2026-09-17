# -*- coding: utf-8 -*-
import sqlite3
DB = r"f:\zuqiu\五大联赛专属模型\五大联赛专属模型\data\odds.db"
c = sqlite3.connect(DB)
# 主队名归一化：Deportivo de A Coruña -> 拉科鲁尼亚（注意区别于 Deportivo Alavés=阿拉维斯）
cur = c.execute(
    "UPDATE sofascore_team_features SET home_team_cn='拉科鲁尼亚' WHERE home_team_cn='Deportivo de A Coruña'"
)
print("home 修正行数:", cur.rowcount)
cur2 = c.execute(
    "UPDATE sofascore_team_features SET away_team_cn='拉科鲁尼亚' WHERE away_team_cn='Deportivo de A Coruña'"
)
print("away 修正行数:", cur2.rowcount)
c.commit()

# 校验今日拉科鲁尼亚主队特征是否可被中文名匹配
r = c.execute(
    "SELECT match_date, home_team_cn, away_team_cn FROM sofascore_team_features "
    "WHERE match_date='2026-08-31' AND home_team_cn='拉科鲁尼亚'"
).fetchall()
print("修正后 2026-08-31 拉科鲁尼亚主队行:", r)
c.close()