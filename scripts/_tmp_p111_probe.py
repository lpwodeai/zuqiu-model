# -*- coding: utf-8 -*-
"""P1-11 前置探针：odds.db 表清单 + 多博彩公司/百家欧指数据可用性。只读，无副作用。"""
import sqlite3, os

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'odds.db')
conn = sqlite3.connect(DB)
cur = conn.cursor()

print("=" * 70)
print("odds.db 表清单")
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
for t in tables:
    try:
        cur.execute(f"SELECT COUNT(*) FROM \"{t}\"")
        n = cur.fetchone()[0]
        print(f"  {t}: {n}")
    except Exception as e:
        print(f"  {t}: ERROR {e}")

print()
print("=" * 70)
print("搜索多博彩公司/百家欧指相关表（含 ouzhi/xbet/market/company/bookmaker/consensus）")
for t in tables:
    low = t.lower()
    if any(k in low for k in ('ouzhi', 'xbet', 'market', 'company', 'bookmaker', 'consensus', 'odd_avg', '欧指')):
        cur.execute(f"PRAGMA table_info(\"{t}\")")
        cols = [c[1] for c in cur.fetchall()]
        print(f"  [{t}] 列: {cols[:40]}")

# matches 表列（确认 league/home/away/date/round 等）
print()
print("=" * 70)
print("matches 表关键列")
cur.execute("PRAGMA table_info(matches)")
for c in cur.fetchall():
    print(f"  {c[1]:24s} {c[2]}")

conn.close()
print()
print("DONE")