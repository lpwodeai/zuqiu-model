# -*- coding: utf-8 -*-
"""临时：补抓今日13场的 500.com 投注分析(touzhu)+百家欧指(ouzhi)"""
import sys
from pathlib import Path
import sqlite3
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "collection"))
import final_500_collector as fc

TEAMS = ["桑德兰", "利兹联", "巴黎FC", "弗赖堡", "皇家马德里", "曼彻斯特联", "奥格斯堡",
         "那不勒斯", "拉科鲁尼亚", "拉齐奥", "卡利亚里", "摩纳哥", "维戈塞尔塔"]

conn = sqlite3.connect(str(fc.DB_PATH))
conn.execute("PRAGMA busy_timeout=30000")
fc.create_tables(conn)

ph = ",".join("?" * len(TEAMS))
rows = conn.execute(
    "SELECT fid, match_id, home_team_cn, away_team_cn FROM odds500_match "
    f"WHERE home_team_cn IN ({ph}) AND (match_date LIKE '2026-08-30%' OR match_date LIKE '2026-08-31%')",
    TEAMS,
).fetchall()

print(f"目标场次: {len(rows)}")
client = fc.Client(delay=0.5)
ok_bet = ok_oz = 0
for fid, match_id, h, a in rows:
    print(f"\n[{fid}] {h} vs {a}  ({match_id})")
    r1 = client.get(fc.FENXI_URL.format(page="touzhu", fid=fid), referer="https://odds.500.com/fenxi/")
    bet = fc.parse_touzhu(BeautifulSoup(r1.text, "html.parser")) if r1.status_code == 200 else None
    r2 = client.get(fc.FENXI_URL.format(page="ouzhi", fid=fid), referer="https://odds.500.com/fenxi/")
    oz = fc.parse_ouzhi(BeautifulSoup(r2.text, "html.parser")) if r2.status_code == 200 else None
    if bet:
        fc.write_betting(conn, fid, match_id, bet)
        ok_bet += 1
    if oz:
        fc.write_ouzhi(conn, fid, match_id, oz)
        ok_oz += 1
    conn.commit()
    print(f"  投注分析={'已写' if bet else '无数据'}  百家欧指={'已写' if oz else '无数据'}")

conn.close()
print(f"\n完成: 投注 {ok_bet}/{len(rows)}, 欧指 {ok_oz}/{len(rows)}")