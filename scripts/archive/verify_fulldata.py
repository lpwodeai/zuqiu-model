import sqlite3

c = sqlite3.connect(r'data/odds.db')
cur = c.cursor()

# 西甲 25/26 前5轮过滤（用 fbref_match_mapping）
FILTER = """
    FROM match_player_stats ps
    JOIN fbref_match_mapping fmm ON ps.match_id = fmm.odds_match_id
    WHERE fmm.league = '西甲' AND fmm.season = '25/26'
      AND CAST(fmm.fbref_week AS INTEGER) BETWEEN 1 AND 5
"""

cur.execute("PRAGMA table_info(match_player_stats)")
cols = [r[1] for r in cur.fetchall()]

# 总行数
cur.execute("SELECT COUNT(*) " + FILTER)
total = cur.fetchone()[0]

# 统计每列非空数
print(f"=== 西甲 25/26 前5轮 赛后数据全字段盘点 ===")
print(f"总行数: {total}\n")

categories = []
results = []
for col in cols:
    cur.execute(f"SELECT COUNT(*) {FILTER} AND ps.{col} IS NOT NULL")
    cnt = cur.fetchone()[0]
    if cnt == 0:
        continue  # 跳过全空列
    results.append((col, cnt))

results.sort(key=lambda x: -x[1])

print(f"非空字段数: {len(results)} / {len(cols)} 列\n")
print(f"{'字段名':35s} {'非空':>6s} {'覆盖率':>8s}")
print("-" * 55)
for col, cnt in results:
    pct = cnt / total * 100
    print(f"{col:35s} {cnt:>6} {pct:>7.1f}%")

c.close()