"""
统计赔率数据库中英超比赛的详细赔率数据
"""
import sqlite3
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
odds_db = BASE_DIR / "data" / "odds.db"

conn = sqlite3.connect(odds_db)

# 查询英超比赛
epl_matches = pd.read_sql_query(
    "SELECT * FROM matches WHERE match_type = 'Premier League'",
    conn
)

print("=" * 70)
print("英超比赛赔率数据统计（赔率数据库）")
print("=" * 70)
print(f"\n英超比赛数: {len(epl_matches)}")

# 获取英超比赛match_id列表
epl_match_ids = epl_matches['match_id'].unique().tolist()
print(f"英超比赛唯一ID数: {len(epl_match_ids)}")

# 统计各类型赔率数据
odds_tables = {
    'wdl_history': '胜平负赔率',
    'handicap_history': '让球胜平负赔率',
    'total_goals_history': '总进球数赔率',
    'score_history': '比分赔率'
}

match_coverage = {}

print("\n" + "=" * 70)
print("各类型赔率数据覆盖情况")
print("=" * 70)

for table_name, desc in odds_tables.items():
    try:
        # 查询该表中英超比赛的记录
        query = f"""
        SELECT match_id, COUNT(*) as record_count
        FROM {table_name}
        WHERE match_id IN ({','.join(['?' for _ in epl_match_ids])})
        GROUP BY match_id
        """
        df = pd.read_sql_query(query, conn, params=epl_match_ids)
        
        # 统计有该类型赔率的比赛数
        matches_with_odds = len(df)
        avg_records = df['record_count'].mean() if len(df) > 0 else 0
        
        print(f"\n{desc}:")
        print(f"  有赔率数据的比赛数: {matches_with_odds}/{len(epl_match_ids)} ({matches_with_odds/len(epl_match_ids):.1%})")
        print(f"  平均记录数/场: {avg_records:.2f}")
        if len(df) > 0:
            print(f"  记录数范围: {df['record_count'].min()} - {df['record_count'].max()}")
        
        # 保存覆盖情况
        match_coverage[table_name] = set(df['match_id'].tolist())
        
    except Exception as e:
        print(f"\n{desc}:")
        print(f"  查询失败: {e}")
        match_coverage[table_name] = set()

# 统计同时拥有多种赔率的比赛数
print("\n" + "=" * 70)
print("综合覆盖情况")
print("=" * 70)

# 统计拥有不同类型赔率组合的比赛数
all_wdl = match_coverage.get('wdl_history', set())
all_handicap = match_coverage.get('handicap_history', set())
all_total_goals = match_coverage.get('total_goals_history', set())
all_score = match_coverage.get('score_history', set())

# 全类型覆盖
full_coverage = all_wdl & all_handicap & all_total_goals & all_score
print(f"\n拥有全部4种赔率数据的比赛数: {len(full_coverage)}/{len(epl_match_ids)} ({len(full_coverage)/len(epl_match_ids):.1%})")

# 至少拥有一种
any_coverage = all_wdl | all_handicap | all_total_goals | all_score
print(f"拥有至少1种赔率数据的比赛数: {len(any_coverage)}/{len(epl_match_ids)} ({len(any_coverage)/len(epl_match_ids):.1%})")

# 拥有3种及以上
three_or_more = set()
for mid in epl_match_ids:
    count = 0
    if mid in all_wdl: count += 1
    if mid in all_handicap: count += 1
    if mid in all_total_goals: count += 1
    if mid in all_score: count += 1
    if count >= 3:
        three_or_more.add(mid)
print(f"拥有3种及以上赔率数据的比赛数: {len(three_or_more)}/{len(epl_match_ids)} ({len(three_or_more)/len(epl_match_ids):.1%})")

# 没有任何赔率数据
no_coverage = set(epl_match_ids) - any_coverage
print(f"没有任何赔率数据的比赛数: {len(no_coverage)}/{len(epl_match_ids)} ({len(no_coverage)/len(epl_match_ids):.1%})")

# 详细统计各类型覆盖组合
print("\n各类型赔率覆盖组合统计:")
combinations = [
    ('仅胜平负', all_wdl - all_handicap - all_total_goals - all_score),
    ('仅让球', all_handicap - all_wdl - all_total_goals - all_score),
    ('仅总进球', all_total_goals - all_wdl - all_handicap - all_score),
    ('仅比分', all_score - all_wdl - all_handicap - all_total_goals),
    ('胜平负+让球', (all_wdl & all_handicap) - all_total_goals - all_score),
    ('胜平负+总进球', (all_wdl & all_total_goals) - all_handicap - all_score),
    ('胜平负+比分', (all_wdl & all_score) - all_handicap - all_total_goals),
    ('让球+总进球', (all_handicap & all_total_goals) - all_wdl - all_score),
    ('让球+比分', (all_handicap & all_score) - all_wdl - all_total_goals),
    ('总进球+比分', (all_total_goals & all_score) - all_wdl - all_handicap),
    ('胜平负+让球+总进球', (all_wdl & all_handicap & all_total_goals) - all_score),
    ('胜平负+让球+比分', (all_wdl & all_handicap & all_score) - all_total_goals),
    ('胜平负+总进球+比分', (all_wdl & all_total_goals & all_score) - all_handicap),
    ('让球+总进球+比分', (all_handicap & all_total_goals & all_score) - all_wdl),
    ('全部4种', full_coverage)
]

for name, matches in combinations:
    if len(matches) > 0:
        print(f"  {name}: {len(matches)}场")

# 查看部分详细数据示例
print("\n" + "=" * 70)
print("赔率数据记录数分布")
print("=" * 70)

for table_name, desc in odds_tables.items():
    try:
        query = f"""
        SELECT COUNT(*) as match_count, record_count
        FROM (
            SELECT match_id, COUNT(*) as record_count
            FROM {table_name}
            WHERE match_id IN ({','.join(['?' for _ in epl_match_ids])})
            GROUP BY match_id
        )
        GROUP BY record_count
        ORDER BY record_count
        """
        df = pd.read_sql_query(query, conn, params=epl_match_ids)
        
        print(f"\n{desc}记录数分布:")
        for _, row in df.iterrows():
            print(f"  {row['record_count']}条记录: {row['match_count']}场")
            
    except Exception as e:
        print(f"\n{desc}记录数分布:")
        print(f"  查询失败: {e}")

# 查看有完整赔率数据的比赛示例
if len(full_coverage) > 0:
    print("\n" + "=" * 70)
    print("拥有完整赔率数据的比赛示例")
    print("=" * 70)
    
    sample_match_id = list(full_coverage)[0]
    print(f"\n比赛: {sample_match_id}")
    
    # 查看该比赛的胜平负赔率历史
    wdl_sample = pd.read_sql_query(
        "SELECT * FROM wdl_history WHERE match_id = ? ORDER BY timestamp",
        conn, params=[sample_match_id]
    )
    print("\n胜平负赔率历史:")
    print(wdl_sample[['timestamp', 'win_a', 'draw', 'win_b']].to_string())
    
    # 查看让球赔率
    hcp_sample = pd.read_sql_query(
        "SELECT * FROM handicap_history WHERE match_id = ? ORDER BY timestamp",
        conn, params=[sample_match_id]
    )
    print("\n让球赔率历史:")
    print(hcp_sample[['timestamp', 'hcp_win', 'hcp_draw', 'hcp_lose']].to_string())
    
    # 查看总进球赔率
    tg_sample = pd.read_sql_query(
        "SELECT * FROM total_goals_history WHERE match_id = ? ORDER BY timestamp",
        conn, params=[sample_match_id]
    )
    print("\n总进球赔率历史:")
    print(tg_sample[['timestamp', 'goals_0', 'goals_1', 'goals_2', 'goals_3', 'goals_4', 'goals_5', 'goals_6', 'goals_7_plus']].to_string())
    
    # 查看比分赔率
    score_sample = pd.read_sql_query(
        "SELECT * FROM score_history WHERE match_id = ? ORDER BY odds LIMIT 20",
        conn, params=[sample_match_id]
    )
    print("\n比分赔率（前20个低赔率比分）:")
    print(score_sample[['score', 'odds']].to_string())

conn.close()

print("\n" + "=" * 70)
print("统计完成")
print("=" * 70)