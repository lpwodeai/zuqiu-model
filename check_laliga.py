import sqlite3

conn = sqlite3.connect(r'f:\zuqiu\五大联赛专属模型\五大联赛专属模型\data\odds.db')
cur = conn.cursor()

print("=" * 70)
print("查验：西甲 25/26 赛季 前5轮 数据抓取情况")
print("=" * 70)

# 1. 比赛总数（通过 fbref_match_mapping 关联）
cur.execute("""
    SELECT COUNT(*) FROM matches m
    JOIN fbref_match_mapping fmm ON m.match_id = fmm.odds_match_id
    WHERE fmm.league = '西甲' AND fmm.season = '25/26'
""")
total_matches = cur.fetchone()[0]
print(f"\n1. 西甲 25/26 总比赛数: {total_matches}")

# 2. 各轮次分布
print("\n2. 各轮次比赛分布:")
cur.execute("""
    SELECT fmm.fbref_week as round, COUNT(*) as cnt
    FROM matches m
    JOIN fbref_match_mapping fmm ON m.match_id = fmm.odds_match_id
    WHERE fmm.league = '西甲' AND fmm.season = '25/26'
    GROUP BY fmm.fbref_week
    ORDER BY CAST(fmm.fbref_week AS INTEGER)
""")
for r in cur.fetchall():
    print(f"  第 {r[0]} 轮: {r[1]} 场")

# 3. 前5轮数据明细
print("\n3. 前5轮比赛明细:")
cur.execute("""
    SELECT m.id, fmm.fbref_week, m.home_team, m.away_team, 
           m.actual_score, m.match_date, fmm.fbref_score, m.match_id
    FROM matches m
    JOIN fbref_match_mapping fmm ON m.match_id = fmm.odds_match_id
    WHERE fmm.league = '西甲' AND fmm.season = '25/26'
    AND CAST(fmm.fbref_week AS INTEGER) <= 5
    ORDER BY CAST(fmm.fbref_week AS INTEGER), m.match_date
""")
matches = cur.fetchall()
print(f"  共 {len(matches)} 场比赛")
for r in matches:
    score_display = r[4] if r[4] else r[6]
    print(f"  ID={r[0]} R{r[1]} | {r[2]} vs {r[3]} | score={score_display} | date={r[5]} | match_id={r[7]}")

# 4. lineups / player_stats 数据完整性
print("\n4. 数据完整性检查:")
match_ids = [r[7] for r in matches]
if match_ids:
    placeholders = ','.join(['?'] * len(match_ids))
    
    cur.execute(f"""
        SELECT 
            COUNT(DISTINCT ps.match_id) as matches_with_stats,
            COUNT(*) as total_player_stats
        FROM match_player_stats ps
        WHERE ps.match_id IN ({placeholders})
    """, match_ids)
    r = cur.fetchone()
    print(f"  有球员统计的比赛: {r[0]} 场 / {len(matches)} 场")
    print(f"  球员统计总行数: {r[1]} 行")

    cur.execute(f"""
        SELECT 
            COUNT(DISTINCT lu.match_id) as matches_with_lineups,
            COUNT(*) as total_lineups
        FROM match_lineups lu
        WHERE lu.match_id IN ({placeholders})
    """, match_ids)
    r2 = cur.fetchone()
    print(f"  有阵容的比赛: {r2[0]} 场 / {len(matches)} 场")
    print(f"  阵容总行数: {r2[1]} 行")
else:
    print("  无比赛数据")

# 5. 关键字段覆盖率
print("\n5. 球员统计字段覆盖率:")
fields = ['goals', 'assists', 'yellow_cards', 'red_cards', 
          'rating', 'duels_won', 'ground_duels_won', 
          'big_chances_created', 'pass_accuracy']
# 先检查哪些字段实际存在
cur.execute("PRAGMA table_info(match_player_stats)")
existing_columns = {r[1] for r in cur.fetchall()}
for field in fields:
    if field not in existing_columns:
        # 尝试找替代字段
        alt = None
        if field == 'pass_accuracy':
            for c in ['pass_completion_pct', 'accurate_pass_sofa']:
                if c in existing_columns:
                    alt = c
                    break
        if alt:
            print(f"  {field:25s}: 字段不存在, 使用替代字段 {alt}")
            field = alt
        else:
            print(f"  {field:25s}: 字段不存在 (已跳过)")
            continue
    
    if match_ids:
        placeholders = ','.join(['?'] * len(match_ids))
        try:
            cnt = cur.execute(f"""
                SELECT COUNT(*) FROM match_player_stats ps
                WHERE ps.match_id IN ({placeholders})
                AND ps.{field} IS NOT NULL
            """, match_ids).fetchone()[0]
            total = cur.execute(f"""
                SELECT COUNT(*) FROM match_player_stats ps
                WHERE ps.match_id IN ({placeholders})
            """, match_ids).fetchone()[0]
            pct = cnt / total * 100 if total > 0 else 0
            print(f"  {field:25s}: {cnt:>5}/{total:>5} ({pct:5.1f}%)")
        except Exception as e:
            print(f"  {field:25s}: 错误 - {e}")

# 6. 抽样验证（带 ground_duels 的球员）
print("\n6. 抽样验证 - 有 ground_duels 的球员 (前 10 行):")
if match_ids:
    placeholders = ','.join(['?'] * len(match_ids))
    cur.execute(f"""
        SELECT ps.match_id, ps.player_name, ps.team, ps.duels_won, 
               ps.aerials_won_total, ps.ground_duels_won, ps.goals, ps.rating
        FROM match_player_stats ps
        WHERE ps.match_id IN ({placeholders})
        AND ps.ground_duels_won IS NOT NULL
        ORDER BY ps.player_name
        LIMIT 10
    """, match_ids)
    for r in cur.fetchall():
        expected = max(0, (r[3] or 0) - (r[4] or 0))
        ok = "OK" if r[5] == expected else "WARN"
        print(f"  match={r[0]} {r[1]:18s} {r[2]:10s} duels={r[3]} aerials={r[4]} ground={r[5]}(exp={expected}) goals={r[6]} rating={r[7]} {ok}")

# 7. 伤停数据
print("\n7. 伤停球员数据:")
if match_ids:
    placeholders = ','.join(['?'] * len(match_ids))
    cur.execute(f"""
        SELECT COUNT(*) FROM match_missing_players mmp
        WHERE mmp.match_id IN ({placeholders})
    """, match_ids)
    missing_cnt = cur.fetchone()[0]
    print(f"  伤停球员记录: {missing_cnt} 条")

    if missing_cnt > 0:
        cur.execute(f"""
            SELECT mmp.player_name, mmp.team, mmp.reason, mmp.expected_end_date
            FROM match_missing_players mmp
            WHERE mmp.match_id IN ({placeholders})
            LIMIT 10
        """, match_ids)
        for r in cur.fetchall():
            print(f"  {r[0]:18s} {r[1]:10s} | {r[2]:20s} | 预计复出: {r[3]}")
else:
    print("  无比赛数据")

conn.close()
print("\n" + "=" * 70)
print("查验完成")
print("=" * 70)