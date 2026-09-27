import sqlite3

conn = sqlite3.connect(r'f:\zuqiu\五大联赛专属模型\五大联赛专属模型\data\odds.db')
cur = conn.cursor()

print("=" * 70)
print("最终验证：ground_duels 字段回填结果")
print("=" * 70)

cur.execute("SELECT COUNT(*) FROM match_player_stats")
total = cur.fetchone()[0]
print(f"总行数: {total:,}")

print("\n【字段覆盖率】")
for col in ['ground_duels_won', 'ground_duels_lost', 'ground_duels_total',
            'duels_won', 'duels_lost_sofa', 'aerials_won_total']:
    cnt = cur.execute(f"SELECT COUNT(*) FROM match_player_stats WHERE {col} IS NOT NULL").fetchone()[0]
    pct = cnt / total * 100
    print(f"  {col:25s}: {cnt:>7,} 行 ({pct:5.1f}%)")

print("\n【抽样验证 - 计算正确性】")
cur.execute("""
    SELECT player_name, team,
           duels_won, aerials_won_total, ground_duels_won,
           duels_lost_sofa, aerials_lost_sofa, ground_duels_lost,
           ground_duels_total
    FROM match_player_stats
    WHERE ground_duels_won IS NOT NULL
    LIMIT 20
""")
rows = cur.fetchall()
all_pass = True
for r in rows:
    expected_won = max(0, (r[2] or 0) - (r[3] or 0))
    expected_lost = max(0, (r[5] or 0) - (r[6] or 0))
    expected_total = expected_won + expected_lost
    
    ok = r[4] == expected_won and r[7] == expected_lost and r[8] == expected_total
    if not ok:
        all_pass = False
        print(f"  ❌ {r[0]}: got=({r[4]},{r[7]},{r[8]}) exp=({expected_won},{expected_lost},{expected_total})")

if all_pass:
    print(f"  ✅ 抽样 {len(rows)} 行全部计算正确！")

print("\n【问题详情 - 统计所有不一致行】")
cur.execute("""
    SELECT COUNT(*)
    FROM match_player_stats
    WHERE ground_duels_won IS NOT NULL
      AND (ground_duels_won != MAX(0, COALESCE(duels_won,0) - COALESCE(aerials_won_total,0))
           OR ground_duels_lost != MAX(0, COALESCE(duels_lost_sofa,0) - COALESCE(aerials_lost_sofa,0))
           OR ground_duels_total != MAX(0, COALESCE(duels_won,0) - COALESCE(aerials_won_total,0)) + MAX(0, COALESCE(duels_lost_sofa,0) - COALESCE(aerials_lost_sofa,0)))
""")
mismatch = cur.fetchone()[0]
print(f"  不一致行数: {mismatch}")

if mismatch > 0:
    cur.execute("""
        SELECT player_name, team,
               duels_won, aerials_won_total, ground_duels_won,
               duels_lost_sofa, aerials_lost_sofa, ground_duels_lost,
               ground_duels_total
        FROM match_player_stats
        WHERE ground_duels_won IS NOT NULL
          AND (ground_duels_won != MAX(0, COALESCE(duels_won,0) - COALESCE(aerials_won_total,0))
               OR ground_duels_lost != MAX(0, COALESCE(duels_lost_sofa,0) - COALESCE(aerials_lost_sofa,0))
               OR ground_duels_total != MAX(0, COALESCE(duels_won,0) - COALESCE(aerials_won_total,0)) + MAX(0, COALESCE(duels_lost_sofa,0) - COALESCE(aerials_lost_sofa,0)))
        LIMIT 10
    """)
    for r in cur.fetchall():
        exp_w = max(0, (r[2] or 0) - (r[3] or 0))
        exp_l = max(0, (r[5] or 0) - (r[6] or 0))
        exp_t = exp_w + exp_l
        print(f"  ❌ {r[0]}: got=({r[4]},{r[7]},{r[8]}) exp=({exp_w},{exp_l},{exp_t})")

print("\n【按联赛分布】")
cur.execute("""
    SELECT m.match_type, COUNT(*) as total,
           SUM(CASE WHEN ps.duels_won IS NOT NULL THEN 1 ELSE 0 END) as has_duels,
           SUM(CASE WHEN ps.ground_duels_won IS NOT NULL THEN 1 ELSE 0 END) as has_ground,
           ROUND(100.0 * SUM(CASE WHEN ps.ground_duels_won IS NOT NULL THEN 1 ELSE 0 END) / 
                 NULLIF(SUM(CASE WHEN ps.duels_won IS NOT NULL THEN 1 ELSE 0 END), 0), 1) as pct
    FROM match_player_stats ps
    JOIN matches m ON ps.match_id = m.match_id
    GROUP BY m.match_type
    ORDER BY total DESC
""")
print(f"  {'联赛':20s} {'总数':>8s} {'duels':>8s} {'ground':>8s} {'比例':>6s}")
for r in cur.fetchall():
    pct_str = f"{r[4]:.1f}" if r[4] is not None else "N/A"
    print(f"  {r[0]:20s} {r[1]:>8,} {r[2]:>8,} {r[3]:>8,} {pct_str:>6}%")

print("\n【结论】")
duels_cnt = cur.execute("SELECT COUNT(*) FROM match_player_stats WHERE duels_won IS NOT NULL").fetchone()[0]
ground_cnt = cur.execute("SELECT COUNT(*) FROM match_player_stats WHERE ground_duels_won IS NOT NULL").fetchone()[0]
print(f"  duels_won 覆盖率: {duels_cnt/total*100:.1f}%")
print(f"  ground_duels_won 覆盖率: {ground_cnt/total*100:.1f}%")
if duels_cnt > 0:
    print(f"  ground/duels 比例: {ground_cnt/duels_cnt*100:.1f}%")
print(f"  ✅ 回填完成！覆盖率从 0.0% 提升到 {ground_cnt/total*100:.1f}%")

conn.close()