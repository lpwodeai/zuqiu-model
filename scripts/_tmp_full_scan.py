# -*- coding: utf-8 -*-
import sqlite3
c = sqlite3.connect('data/odds.db')
c.row_factory = sqlite3.Row
OLD = '2026-09-07_Deportivo Alavés_Osasuna'

tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("=== 全表全列搜索旧 match_id 出现位置 ===")
total = 0
for t in tables:
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
    for col in cols:
        # 只搜文本类列
        try:
            n = c.execute(f"SELECT COUNT(*) FROM {t} WHERE CAST({col} AS TEXT) LIKE ?", ('%' + OLD + '%',)).fetchone()[0]
        except Exception:
            n = 0
        if n:
            total += n
            print(f"  {t}.{col}: {n}")
print("总引用行数:", total)

print("\n=== match_id_mapping / 映射表结构 ===")
for t in ('match_id_mapping','match_mapping','match_mapping_old','match_mapping_sync','fbref_match_mapping'):
    try:
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
        print(f"  {t}: {cols}")
    except Exception as e:
        print(f"  {t}: 无 ({e})")