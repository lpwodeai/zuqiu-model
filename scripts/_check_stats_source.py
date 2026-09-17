import sqlite3
conn = sqlite3.connect('f:/zuqiu/五大联赛专属模型/五大联赛专属模型/data/odds.db')
# Check stats_source for the 4 Serie A matches
print("=== 4场意甲比赛的 stats_source ===")
rows = conn.execute("SELECT match_id, team, stats_source, COUNT(*) FROM match_player_stats WHERE match_id IN ('2026-08-23_Inter_Monza','2026-08-23_Udinese_Como','2026-08-23_Genoa_SSC Napoli','2026-08-23_Parma_Cagliari') GROUP BY match_id, team, stats_source").fetchall()
for r in rows:
    print(f"  {r[0]} | {r[1]} | {r[2]} | {r[3]} rows")

# Also check overall stats_source distribution
print("\n=== 全局 stats_source 分布 ===")
rows = conn.execute("SELECT stats_source, COUNT(*) FROM match_player_stats GROUP BY stats_source").fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1]} rows")

# Check if these 4 matches have sofascore-specific stats (rating, expected_goals, etc.)
print("\n=== 4场比赛的 rating 字段 ===")
rows = conn.execute("SELECT match_id, team, COUNT(*), SUM(CASE WHEN rating IS NOT NULL THEN 1 ELSE 0 END) as has_rating FROM match_player_stats WHERE match_id IN ('2026-08-23_Inter_Monza','2026-08-23_Udinese_Como','2026-08-23_Genoa_SSC Napoli','2026-08-23_Parma_Cagliari') GROUP BY match_id, team").fetchall()
for r in rows:
    print(f"  {r[0]} | {r[1]} | total={r[2]} | rating={r[3]}")

conn.close()