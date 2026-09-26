# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path

DB = Path(r"f:\zuqiu\五大联赛专属模型\五大联赛专属模型\data\odds.db")
print("odds.db 存在:", DB.exists(), "| 大小(MB):", round(DB.stat().st_size / 1048576, 1))

conn = sqlite3.connect(str(DB))
cur = conn.cursor()

# 完整性检查（快速模式）
try:
    r = cur.execute("PRAGMA quick_check").fetchone()
    print("PRAGMA quick_check:", r[0])
except Exception as e:
    print("quick_check 失败:", e)

# 关键表是否存在 + 行数
tables = [
    "fbref_match_mapping",
    "match_missing_players",
    "match_predicted_lineups",
    "sofascore_team_features",
    "match_player_stats",
    "post_match_review",
]
print("\n--- 关键表 ---")
for t in tables:
    try:
        n = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"{t}: {n} 行")
    except Exception as e:
        print(f"{t}: 缺失或错误 {e}")

conn.close()