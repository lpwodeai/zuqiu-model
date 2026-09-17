# -*- coding: utf-8 -*-
"""P1-11 探针2：竞彩(wdl_history) vs 百家欧指(odds500_ouzhi_summary) 关联关系 + 列细节。只读。"""
import sqlite3, os

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'odds.db')
conn = sqlite3.connect(DB)
cur = conn.cursor()

def cols(t):
    cur.execute(f"PRAGMA table_info(\"{t}\")")
    return [c[1] for c in cur.fetchall()]

for t in ('wdl_history', 'odds500_match', 'odds500_ouzhi_summary', 'odds500_betting'):
    print(f"\n[{t}] 列: {cols(t)}")

print("\n" + "=" * 70)
print("wdl_history 前3行（竞彩?）")
cur.execute("SELECT * FROM wdl_history LIMIT 3")
for r in cur.fetchall():
    print("  ", r)

print("\n" + "=" * 70)
print("odds500_ouzhi_summary 前2行")
cur.execute("SELECT match_id, company_count, avg_init_win, avg_init_draw, avg_init_lose, avg_prob_init_win, avg_prob_init_draw, avg_prob_init_lose, disp_init_win, disp_init_draw, disp_init_lose FROM odds500_ouzhi_summary LIMIT 2")
for r in cur.fetchall():
    print("  ", r)

print("\n" + "=" * 70)
print("odds500_match 前2行")
cur.execute("SELECT * FROM odds500_match LIMIT 2")
for r in cur.fetchall():
    print("  ", r)

# 关联性：ousummary.match_id 是否 == matches.match_id
print("\n" + "=" * 70)
print("关联测试：ousummary.match_id 与 matches.match_id 交叠数")
cur.execute("""
  SELECT COUNT(*) FROM odds500_ouzhi_summary s
  JOIN matches m ON s.match_id = m.match_id
""")
print("  ouzhi_summary JOIN matches (match_id==match_id):", cur.fetchone()[0])

cur.execute("""
  SELECT COUNT(*) FROM wdl_history w
  JOIN matches m ON w.match_id = m.match_id
""")
print("  wdl_history JOIN matches (match_id==match_id):", cur.fetchone()[0])

# match_type 结构（赛季/轮次）
print("\n" + "=" * 70)
print("matches.match_type 唯一值采样（含赛季/轮次推断）")
cur.execute("SELECT DISTINCT match_type FROM matches LIMIT 30")
for r in cur.fetchall():
    print("  ", r[0])

conn.close()
print("\nDONE")