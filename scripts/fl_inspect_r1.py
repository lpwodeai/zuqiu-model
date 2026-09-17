# -*- coding: utf-8 -*-
"""检查 five_leagues.db 的 teams / matches 表结构与当前 4 场西甲数据"""
import sqlite3
from pathlib import Path

FL_DB = Path(__file__).resolve().parent.parent / "data" / "five_leagues.db"
conn = sqlite3.connect(str(FL_DB))
cur = conn.cursor()

print("=== 所有表 ===")
for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
    print("  ", r[0])

print("\n=== teams 表结构 ===")
for r in cur.execute("PRAGMA table_info(teams)"):
    print(f"  {r[1]:<20} {r[2]:<12} notnull={r[3]} pk={r[5]}")

print("\n=== teams 中西甲相关 ===")
for r in cur.execute("SELECT * FROM teams WHERE league='LL' ORDER BY id"):
    print("  ", r)

print("\n=== matches 表结构 ===")
for r in cur.execute("PRAGMA table_info(matches)"):
    print(f"  {r[1]:<24} {r[2]:<12} notnull={r[3]}")

print("\n=== matches 2026-08+ ===")
for r in cur.execute("SELECT * FROM matches WHERE date >= '2026-08-01'"):
    print("  ", r)

conn.close()