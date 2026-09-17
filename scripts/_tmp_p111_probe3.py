# -*- coding: utf-8 -*-
"""P1-11 探针3：match_id_mapping 结构 + cn/en 双重解析 + ouzhi 可对齐率。只读。"""
import sqlite3, os

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'odds.db')
conn = sqlite3.connect(DB)
cur = conn.cursor()

print("match_id_mapping 列:")
cur.execute("PRAGMA table_info(match_id_mapping)")
for c in cur.fetchall():
    print("  ", c[1], c[2])

print("\nmatch_id_mapping 前5行:")
cur.execute("SELECT * FROM match_id_mapping LIMIT 5")
for r in cur.fetchall():
    print("  ", r)

print("\nmatches.match_id 中文 vs 英文 数量分布:")
cur.execute("SELECT COUNT(*) FROM matches WHERE match_id LIKE '%_%'")
total = cur.fetchone()[0]
# 中文赛季：match_id 含中文
cur.execute("SELECT COUNT(*) FROM matches WHERE match_id GLOB '*[盃甲联英超西德法意]*'")
cn_hit = cur.fetchone()[0]
print(f"  matches 总 {total}, 含中文队名(近似cn) {cn_hit}")

print("\n竞彩 wdl_history 全部为中文 match_id? 前5取样:")
cur.execute("SELECT DISTINCT match_id FROM wdl_history LIMIT 5")
for r in cur.fetchall():
    print("  ", r[0])

print("\n关键对齐率测算（最终目标：df 每场能否对齐到 ouzhi_summary）:")
# 用 match_id_mapping 的 matches_match_id（matches 表）反查英文 ouzhi
# match_id_mapping 列名待确认，先用候选列名探测
cur.execute("PRAGMA table_info(match_id_mapping)")
cols = [c[1] for c in cur.fetchall()]
print("  列名:", cols)

conn.close()
print("\nDONE")