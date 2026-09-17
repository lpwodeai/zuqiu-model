"""
重复样本检测脚本
检测 odds.db 中 matches 表是否存在重复样本
检测维度：
  1. 完全重复 (same home_team, away_team, match_date)
  2. 近重复 (同球队+同日期，不同match_id)
  3. 按联赛统计重复分布
"""
import sqlite3, os, json
from datetime import datetime
from collections import Counter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'odds.db')
OUTPUT_DIR = os.path.join(PROJECT_DIR, 'assets')
os.makedirs(OUTPUT_DIR, exist_ok=True)

print(f"数据库: {DB_PATH}")
print(f"数据库存在: {os.path.exists(DB_PATH)}")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# 1. 总比赛数
total = conn.execute("SELECT COUNT(*) as cnt FROM matches").fetchone()['cnt']
print(f"\n=== 总比赛数: {total} ===")

# 2. 检查 (home_team, away_team, match_date) 完全重复
print("\n--- 检测1: (home_team, away_team, match_date) 完全重复 ---")
dup_query = """
SELECT home_team, away_team, match_date, COUNT(*) as cnt
FROM matches
GROUP BY home_team, away_team, match_date
HAVING COUNT(*) > 1
ORDER BY cnt DESC
"""
dups = conn.execute(dup_query).fetchall()
print(f"重复组数: {len(dups)}")

if dups:
    total_dup = sum(r['cnt'] - 1 for r in dups)  # 多余的样本数
    dup_rate = total_dup / total * 100
    print(f"多余样本数: {total_dup} ({dup_rate:.2f}%)")
    print(f"\nTop 10 重复组:")
    for r in dups[:10]:
        print(f"  {r['home_team']} vs {r['away_team']} on {r['match_date']}: {r['cnt']} 次")
    
    # 获取重复样本的详细信息
    dup_detail_query = """
    SELECT m.* FROM matches m
    WHERE (m.home_team, m.away_team, m.match_date) IN (
        SELECT home_team, away_team, match_date
        FROM matches
        GROUP BY home_team, away_team, match_date
        HAVING COUNT(*) > 1
    )
    ORDER BY m.home_team, m.away_team, m.match_date
    """
    dup_details = conn.execute(dup_detail_query).fetchall()
    print(f"\n重复样本详细列表 (前20条):")
    for r in dup_details[:20]:
        print(f"  [{r['match_id']}] {r['home_team']} vs {r['away_team']} | {r['match_date']} | {r['match_type']} | score: {r.get('actual_score','?')}")
else:
    dup_rate = 0
    print("✅ 无完全重复样本")

# 3. 按联赛统计
print("\n--- 检测2: 按联赛统计重复 ---")
league_query = """
SELECT match_type as league, COUNT(*) as total, 
       COUNT(DISTINCT home_team || '|' || away_team || '|' || match_date) as unique_matches,
       COUNT(*) - COUNT(DISTINCT home_team || '|' || away_team || '|' || match_date) as dup_count
FROM matches
GROUP BY match_type
ORDER BY dup_count DESC
"""
league_stats = conn.execute(league_query).fetchall()
for r in league_stats:
    print(f"  {r['league']}: total={r['total']}, unique={r['unique_matches']}, dups={r['dup_count']}")

# 4. 检查 match_id 是否有重复
print("\n--- 检测3: match_id 重复 ---")
mid_dup = conn.execute("SELECT match_id, COUNT(*) as cnt FROM matches GROUP BY match_id HAVING cnt > 1").fetchall()
print(f"match_id 重复: {len(mid_dup)} 组")
if mid_dup:
    for r in mid_dup:
        print(f"  {r['match_id']}: {r['cnt']} 次")

# 5. 跨赛季检查 (同球队+同赛季日期)
print("\n--- 检测4: 跨赛季近重复 (同球队+近似日期) ---")
season_query = """
SELECT m1.match_id as id1, m2.match_id as id2, m1.home_team, m1.away_team, 
       m1.match_date as date1, m2.match_date as date2, m1.match_type as league
FROM matches m1
JOIN matches m2 ON m1.home_team = m2.home_team 
    AND m1.away_team = m2.away_team
    AND m1.match_id < m2.match_id
    AND m1.match_date != m2.match_date
    AND ABS(julianday(m1.match_date) - julianday(m2.match_date)) <= 3
LIMIT 20
"""
near_dups = conn.execute(season_query).fetchall()
print(f"3天内近似重复: {len(near_dups)} 组")
for r in near_dups[:10]:
    print(f"  {r['home_team']} vs {r['away_team']}: {r['date1']} 和 {r['date2']} ({r['league']})")

# 6. 汇总
print("\n" + "=" * 60)
print("重复样本检测汇总")
print("=" * 60)
print(f"总样本数: {total}")
print(f"完全重复组: {len(dups)}")
print(f"多余样本数: {sum(r['cnt']-1 for r in dups) if dups else 0}")
print(f"重复率: {dup_rate:.2f}%")
print(f"match_id 重复: {len(mid_dup)} 组")
print(f"3天内近似重复: {len(near_dups)} 组")

# 判定
if dup_rate > 5:
    verdict = "❌ 重复率 > 5%，建议先去重后再训练"
elif dup_rate > 1:
    verdict = "⚠️ 重复率 1-5%，建议排查后决定是否去重"
else:
    verdict = "✅ 重复率 < 1%，数据质量良好"

print(f"\n判定: {verdict}")

# 保存报告
report = {
    'timestamp': datetime.now().isoformat(),
    'total_matches': total,
    'duplicate_groups': len(dups),
    'excess_samples': sum(r['cnt']-1 for r in dups) if dups else 0,
    'duplicate_rate_percent': round(dup_rate, 2),
    'match_id_duplicates': len(mid_dup),
    'near_duplicates_3days': len(near_dups),
    'verdict': verdict,
    'duplicate_details': [
        {'home': r['home_team'], 'away': r['away_team'], 'date': r['match_date'], 'count': r['cnt']}
        for r in dups[:20]
    ],
    'league_stats': [
        {'league': r['league'], 'total': r['total'], 'unique': r['unique_matches'], 'dups': r['dup_count']}
        for r in league_stats
    ],
}
report_path = os.path.join(OUTPUT_DIR, f"dedup_analysis_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
with open(report_path, 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print(f"\n报告已保存: {report_path}")

conn.close()