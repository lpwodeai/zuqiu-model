# -*- coding: utf-8 -*-
"""查验今日赛前数据完整性"""
import sqlite3, os, json
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"

# 1. 查询 odds.db 中今天的比赛
print("=" * 60)
print("1. odds.db matches 表 - 2026-08-22/23 比赛")
print("=" * 60)
db_odds = DATA / "odds.db"
conn = sqlite3.connect(str(db_odds))
c = conn.cursor()
c.execute("SELECT match_id, home_team, away_team, match_date, match_type FROM matches WHERE match_date LIKE '2026-08-22%' OR match_date LIKE '2026-08-23%' ORDER BY match_date")
rows = c.fetchall()
print(f"找到 {len(rows)} 场比赛:")
for r in rows:
    print(f"  {r}")

# 查询最新日期
c.execute("SELECT DISTINCT match_date FROM matches ORDER BY match_date DESC LIMIT 10")
print(f"\n最新比赛日期: {[r[0] for r in c.fetchall()]}")

# 2. 查询 odds.db 所有表
print("\n" + "=" * 60)
print("2. odds.db 所有表")
print("=" * 60)
c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
for r in c.fetchall():
    tbl = r[0]
    c.execute(f"SELECT COUNT(*) FROM [{tbl}]")
    cnt = c.fetchone()[0]
    print(f"  {tbl}: {cnt} rows")

conn.close()

# 3. 查询 five_leagues.db
print("\n" + "=" * 60)
print("3. five_leagues.db 所有表")
print("=" * 60)
db_five = DATA / "five_leagues.db"
conn2 = sqlite3.connect(str(db_five))
c2 = conn2.cursor()
c2.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
for r in c2.fetchall():
    tbl = r[0]
    c2.execute(f"SELECT COUNT(*) FROM [{tbl}]")
    cnt = c2.fetchone()[0]
    print(f"  {tbl}: {cnt} rows")

# 查询 five_leagues 中最新比赛日期
try:
    c2.execute("SELECT DISTINCT match_date FROM matches ORDER BY match_date DESC LIMIT 10")
    print(f"\n最新比赛日期: {[r[0] for r in c2.fetchall()]}")
except:
    pass

conn2.close()

# 4. 检查 sofascore_raw 目录
print("\n" + "=" * 60)
print("4. sofascore_raw 目录内容")
print("=" * 60)
srf = DATA / "sofascore_raw"
for d in sorted(srf.iterdir()):
    if d.is_dir():
        files = list(d.glob("*"))
        print(f"  {d.name}/ : {len(files)} 个文件")
        if files:
            for f in sorted(files)[:5]:
                size_kb = f.stat().st_size / 1024
                print(f"    - {f.name} ({size_kb:.1f} KB)")
    else:
        print(f"  {d.name} ({d.stat().st_size/1024:.1f} KB)")

# 5. 检查时序赔率txt文件
print("\n" + "=" * 60)
print("5. 时序赔率txt文件")
print("=" * 60)
for f in sorted(DATA.glob("*时序赔率*")):
    size_kb = f.stat().st_size / 1024
    print(f"  {f.name} ({size_kb:.1f} KB)")

# 6. 检查 odds_timing.db
print("\n" + "=" * 60)
print("6. odds_timing.db")
print("=" * 60)
db_timing = DATA / "odds_timing.db"
if db_timing.exists():
    conn3 = sqlite3.connect(str(db_timing))
    c3 = conn3.cursor()
    c3.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    for r in c3.fetchall():
        tbl = r[0]
        c3.execute(f"SELECT COUNT(*) FROM [{tbl}]")
        cnt = c3.fetchone()[0]
        print(f"  {tbl}: {cnt} rows")
    conn3.close()
else:
    print("  文件不存在")

print("\n完成!")