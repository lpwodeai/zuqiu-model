# -*- coding: utf-8 -*-
"""探查：handicap 盘口/线 在哪里、完赛样本与赔率历史的重叠情况"""
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"

def q(db, sql, args=()):
    c = sqlite3.connect(str(DATA / db))
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()

print("=" * 78)
print("A) 赔率历史表 实际 schema")
print("=" * 78)
for tbl in ("wdl_history", "handicap_history", "total_goals_history", "score_history"):
    rows = q("odds.db", f"PRAGMA table_info({tbl})")
    print(f"  [{tbl}]")
    for r in rows:
        print("     ", r[1], r[2])
    print()

print("=" * 78)
print("B) handicap_history 样本（看是否含 line/盘口）")
print("=" * 78)
rows = q("odds.db", "SELECT * FROM handicap_history LIMIT 3")
for r in rows:
    print("  ", r)

print("\nC) 7 场近期完赛的赔率历史覆盖")
recent = [
    "2026-08-22_Arsenal_Coventry City",
    "2026-08-22_Olympique de Marseille_RC Strasbourg",
    "2026-08-22_Real Betis_Real Sociedad",
    "2026-08-16_Deportivo Alavés_Getafe",
    "2026-08-16_Real Racing Club_Villarreal",
    "2026-08-16_Sevilla_Rayo Vallecano",
    "2026-08-17_Espanyol_Levante UD",
]
for mid in recent:
    w = q("odds.db", "SELECT COUNT(*) FROM wdl_history WHERE match_id=?", (mid,))[0][0]
    h = q("odds.db", "SELECT COUNT(*) FROM handicap_history WHERE match_id=?", (mid,))[0][0]
    t = q("odds.db", "SELECT COUNT(*) FROM total_goals_history WHERE match_id=?", (mid,))[0][0]
    s = q("odds.db", "SELECT COUNT(*) FROM score_history WHERE match_id=?", (mid,))[0][0]
    print(f"  {mid}: wdl={w} hcp={h} tg={t} score={s}")

print("\nD) 25/26 完赛样本与赔率历史重叠（按 match_id）")
rows = q("odds.db", """
    SELECT COUNT(*) FROM matches m
    WHERE m.actual_score IS NOT NULL AND m.actual_score != ''
      AND m.match_type LIKE '%2025-2026%'""")
print("  25/26 完赛总数:", rows[0][0])
rows = q("odds.db", """
    SELECT COUNT(*) FROM matches m
    WHERE m.actual_score IS NOT NULL AND m.actual_score != ''
      AND m.match_type LIKE '%2025-2026%'
      AND EXISTS (SELECT 1 FROM wdl_history w WHERE w.match_id=m.match_id)
      AND EXISTS (SELECT 1 FROM handicap_history h WHERE h.match_id=m.match_id)
      AND EXISTS (SELECT 1 FROM total_goals_history t WHERE t.match_id=m.match_id)""")
print("  25/26 完赛且三项赔率历史齐全:", rows[0][0])
rows = q("odds.db", """
    SELECT COUNT(*) FROM matches m
    WHERE m.actual_score IS NOT NULL AND m.actual_score != ''
      AND m.match_type LIKE '%2025-2026%'
      AND m.handicap IS NOT NULL""")
print("  25/26 完赛且 matches.handicap 非空:", rows[0][0])

print("\nE) handicap_history 是否含 line（列名找 line/handicap）")
rows = q("odds.db", "PRAGMA table_info(handicap_history)")
cols = [r[1] for r in rows]
print("  handicap_history 列:", cols)

print("\nF) total_goals_history 样本")
rows = q("odds.db", "SELECT * FROM total_goals_history LIMIT 2")
for r in rows:
    print("  ", r)

print("\nG) model_predictions 中 4 条 WDL 类型（可能是完整预测 JSON）")
rows = q("odds.db", "SELECT match_id, model_name, prediction_type, prediction, probability FROM model_predictions WHERE prediction_type='WDL'")
for r in rows:
    print("  ", r)

print("\nH) wdl_history / handicap_history 样本")
print("  wdl:", q("odds.db", "SELECT * FROM wdl_history LIMIT 2"))
print("  hcp:", q("odds.db", "SELECT * FROM handicap_history LIMIT 2"))