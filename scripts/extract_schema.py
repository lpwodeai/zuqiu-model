# -*- coding: utf-8 -*-
"""提取 SQLite 完整 schema（表结构 + 索引）到 JSON，供 PG 迁移脚本分析使用。"""
import json
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "odds.db"

conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row

tables = {}
for r in conn.execute(
    "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
):
    tables[r["name"]] = {"ddl": r["sql"], "indexes": []}

for r in conn.execute(
    "SELECT tbl_name, name, sql FROM sqlite_master "
    "WHERE type='index' AND sql IS NOT NULL ORDER BY tbl_name"
):
    if r["tbl_name"] in tables:
        tables[r["tbl_name"]]["indexes"].append({"name": r["name"], "sql": r["sql"]})

out = PROJECT_ROOT / "scripts" / "pg_migration_schema.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(tables, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"tables: {len(tables)} -> {out}")

# 打印大表结构（迁移分区设计用）
for t in ["match_player_stats", "match_lineups", "score_history",
          "odds500_ouzhi_company", "understat_player_xg", "understat_shots", "matches"]:
    if t in tables:
        print(f"\n===== {t} =====")
        print(tables[t]["ddl"][:1500])
conn.close()
