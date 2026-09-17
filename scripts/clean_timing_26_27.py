# -*- coding: utf-8 -*-
"""清理 odds_timing.db 中错误的 26/27 赛季数据"""
import sqlite3
from pathlib import Path

DB = str(Path(__file__).resolve().parent.parent / "data" / "odds_timing.db")
sources = ["英超_26_27", "西甲_26_27", "法甲_26_27", "意甲_26_27"]

conn = sqlite3.connect(DB)
c = conn.cursor()

for table in ["score_timing", "total_goals_timing", "handicap_timing", "wdl_timing"]:
    for src in sources:
        c.execute(f"DELETE FROM {table} WHERE source=?", (src,))
    print(f"{table} deleted: {c.rowcount}")

for src in sources:
    c.execute("DELETE FROM matches WHERE source=?", (src,))
print(f"matches deleted: {c.rowcount}")

conn.commit()
conn.close()
print("Cleaned!")