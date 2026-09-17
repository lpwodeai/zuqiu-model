# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path

ODDS = Path(__file__).resolve().parent.parent / "data" / "odds.db"
c = sqlite3.connect(str(ODDS))
cur = c.cursor()

print("### odds.db 所有表 ###")
for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
    print("  ", r[0])

print("\n### model_predictions 结构 ###")
cur.execute("PRAGMA table_info(model_predictions)")
for r in cur.fetchall():
    print("  ", r)

print("\n### model_predictions 索引 ###")
for r in cur.execute("PRAGMA index_list(model_predictions)"):
    print("  ", r)

ids = [
    "2026-08-22_Real Betis_Real Sociedad",
    "2026-08-22_Arsenal_Coventry City",
    "2026-08-22_Olympique de Marseille_RC Strasbourg",
]
print("\n### 3 场的 model_predictions 现有内容 ###")
q = ",".join("?" for _ in ids)
for r in cur.execute(
        f"SELECT match_id, model_name, prediction_type, prediction, probability, confidence, timestamp "
        f"FROM model_predictions WHERE match_id IN ({q}) ORDER BY match_id, prediction_type", ids):
    print("  ", r)

print("\n### 3 场 matches 现有内容（actual 字段）###")
for r in cur.execute(
        f"SELECT match_id, handicap, actual_wdl, actual_handicap, actual_score, actual_total_goals "
        f"FROM matches WHERE match_id IN ({q}) ORDER BY match_id", ids):
    print("  ", r)

print("\n### model_predictions 全表 prediction_type 分布 + 计数 ###")
for r in cur.execute("SELECT prediction_type, COUNT(*) FROM model_predictions GROUP BY prediction_type ORDER BY prediction_type"):
    print("  ", r)

print("\n### model_predictions 全表 model_name 分布 ###")
for r in cur.execute("SELECT model_name, COUNT(*) FROM model_predictions GROUP BY model_name"):
    print("  ", r)

c.close()