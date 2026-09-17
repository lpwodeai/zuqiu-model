import sqlite3

c = sqlite3.connect(r'data/odds.db')
cur = c.cursor()

print("=== 西甲 25/26 赛季 前5轮 赛后数据查验 ===")

# 先看表结构
cur.execute("PRAGMA table_info(fbref_match_mapping)")
fmm_cols = [r[1] for r in cur.fetchall()]
print("fbref_match_mapping 列:", fmm_cols)

cur.execute("PRAGMA table_info(match_player_stats)")
mps_cols = [r[1] for r in cur.fetchall()]
print("match_player_stats 列数:", len(mps_cols))

# 只靠 fbref_match_mapping 过滤（含 league/season/轮次）
cur.execute("""
    SELECT COUNT(*) FROM match_player_stats ps
    JOIN fbref_match_mapping fmm ON ps.match_id = fmm.odds_match_id
    WHERE fmm.league = '西甲' AND fmm.season = '25/26'
      AND CAST(fmm.fbref_week AS INTEGER) BETWEEN 1 AND 5
""")
tot = cur.fetchone()[0]
print(f"\n球员统计总行数: {tot}")

cols = ['goals', 'assists', 'yellow_cards', 'red_cards', 'rating',
        'duels_won', 'ground_duels_won', 'pass_completion_pct',
        'big_chances_created']

print("\n关键字段覆盖率:")
for col in cols:
    cnt = cur.execute(f"""
        SELECT COUNT(*) FROM match_player_stats ps
        JOIN fbref_match_mapping fmm ON ps.match_id = fmm.odds_match_id
        WHERE fmm.league = '西甲' AND fmm.season = '25/26'
          AND CAST(fmm.fbref_week AS INTEGER) BETWEEN 1 AND 5
          AND ps.{col} IS NOT NULL
    """).fetchone()[0]
    pct = cnt / tot * 100 if tot > 0 else 0
    print(f"  {col:22s}: {cnt:>5} / {tot} ({pct:5.1f}%)")

# 按轮次统计
print("\n按轮次球员统计分布:")
cur.execute("""
    SELECT fmm.fbref_week, COUNT(*) FROM match_player_stats ps
    JOIN fbref_match_mapping fmm ON ps.match_id = fmm.odds_match_id
    WHERE fmm.league = '西甲' AND fmm.season = '25/26'
      AND CAST(fmm.fbref_week AS INTEGER) BETWEEN 1 AND 5
    GROUP BY fmm.fbref_week ORDER BY CAST(fmm.fbref_week AS INTEGER)
""")
for r in cur.fetchall():
    print(f"  第{r[0]}轮: {r[1]} 行")

c.close()