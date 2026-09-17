# -*- coding: utf-8 -*-
"""数据底子探查：可用的完赛样本 + 赔率时序 + 模型预测记录"""
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"

def q(db, sql, args=()):
    c = sqlite3.connect(str(DATA / db))
    try:
        rows = c.execute(sql, args).fetchall()
    finally:
        c.close()
    return rows

print("=" * 80)
print("1) odds.db.matches 完赛样本（actual_score 非空）")
print("=" * 80)
rows = q("odds.db", "SELECT COUNT(*) FROM matches WHERE actual_score IS NOT NULL AND actual_score != ''")
print("  完赛场次总数:", rows[0][0])
rows = q("odds.db", "SELECT match_date, COUNT(*) FROM matches WHERE actual_score IS NOT NULL AND actual_score != '' GROUP BY match_date ORDER BY match_date")
for r in rows:
    print("  ", r)

print("\n" + "=" * 80)
print("2) odds.db.matches 完赛样本的 match_id + 关键字段")
print("=" * 80)
rows = q("odds.db", "SELECT match_id, match_date, home_team, away_team, handicap, actual_score, actual_wdl, actual_total_goals, match_type FROM matches WHERE actual_score IS NOT NULL AND actual_score != '' ORDER BY match_date, match_id")
for r in rows:
    print("  ", r)

print("\n" + "=" * 80)
print("3) odds.db 赔率历史表 数据覆盖")
print("=" * 80)
for tbl in ("wdl_history", "handicap_history", "total_goals_history", "score_history"):
    try:
        n = q("odds.db", f"SELECT COUNT(*) FROM {tbl}")[0][0]
        m = q("odds.db", f"SELECT COUNT(DISTINCT match_id) FROM {tbl}")[0][0]
        print(f"  {tbl}: {n} 行, {m} 个 match_id")
    except Exception as e:
        print(f"  {tbl}: 查询失败 {e}")

print("\n" + "=" * 80)
print("4) odds.db.model_predictions 覆盖的 match_id")
print("=" * 80)
rows = q("odds.db", "SELECT match_id, COUNT(*) FROM model_predictions GROUP BY match_id ORDER BY match_id")
for r in rows:
    print("  ", r)

print("\n" + "=" * 80)
print("5) odds_timing.db 表")
print("=" * 80)
c = sqlite3.connect(str(DATA / "odds_timing.db"))
for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
    print("  ", r[0])
c.close()

print("\n" + "=" * 80)
print("6) odds_timing.db.matches 覆盖")
print("=" * 80)
rows = q("odds_timing.db", "SELECT COUNT(*) FROM matches")
print("  matches 总数:", rows[0][0])
rows = q("odds_timing.db", "SELECT match_id, home_team, away_team, match_date, league, source FROM matches ORDER BY match_date LIMIT 50")
for r in rows:
    print("  ", r)
for tbl in ("wdl_timing", "handicap_timing", "total_goals_timing", "score_timing"):
    try:
        n = q("odds_timing.db", f"SELECT COUNT(*) FROM {tbl}")[0][0]
        m = q("odds_timing.db", f"SELECT COUNT(DISTINCT match_id) FROM {tbl}")[0][0]
        print(f"  {tbl}: {n} 行, {m} 个 match_id")
    except Exception as e:
        print(f"  {tbl}: 查询失败 {e}")

print("\n" + "=" * 80)
print("7) five_leagues.db.matches 完赛样本")
print("=" * 80)
c = sqlite3.connect(str(DATA / "five_leagues.db"))
try:
    n = c.execute("SELECT COUNT(*) FROM matches WHERE homeGoals IS NOT NULL").fetchone()[0]
    print("  完赛(homeGoals非空)总数:", n)
    rows = c.execute("SELECT date, competitionId, homeTeamId, awayTeamId, homeGoals, awayGoals FROM matches WHERE homeGoals IS NOT NULL ORDER BY date DESC LIMIT 60").fetchall()
    for r in rows:
        print("  ", r)
finally:
    c.close()