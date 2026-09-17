import sqlite3, pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
conn = sqlite3.connect(BASE_DIR / "data" / "odds.db")

print('=== match_player_stats 表结构 ===')
schema = pd.read_sql('PRAGMA table_info(match_player_stats)', conn)
for _, row in schema.iterrows():
    print(f'  {row["name"]:45s} {str(row["type"]):15s}')

total = pd.read_sql('SELECT COUNT(*) as cnt FROM match_player_stats', conn).iloc[0,0]
matches = pd.read_sql('SELECT COUNT(DISTINCT match_id) as cnt FROM match_player_stats', conn).iloc[0,0]
players = pd.read_sql('SELECT COUNT(DISTINCT fbref_player_id) as cnt FROM match_player_stats', conn).iloc[0,0]
print(f'\n总行数: {total}')
print(f'唯一比赛数: {matches}')
print(f'唯一球员数: {players}')

per_match = pd.read_sql('SELECT match_id, COUNT(*) as players FROM match_player_stats GROUP BY match_id', conn)
print(f'每场球员数: 均值={per_match["players"].mean():.1f}, 中位数={per_match["players"].median():.0f}, 范围=[{per_match["players"].min()}, {per_match["players"].max()}]')

print('\n=== 关键字段覆盖率 ===')
key_cols = ['expected_goals', 'total_shots', 'shots_on_target', 'rating', 'goals', 'assists',
            'accurate_passes', 'total_passes', 'touches', 'total_tackles', 'interceptions',
            'clearances', 'total_duels_won', 'total_duels', 'aerial_duels_won', 'aerial_duels',
            'dispossessed', 'was_fouled', 'fouls', 'key_passes', 'big_chances_created',
            'big_chances_missed', 'blocked_shots', 'hit_woodwork', 'penalty_won', 'penalty_conceded',
            'own_goals', 'error_lead_to_goal', 'error_lead_to_shot']

for col in key_cols:
    try:
        cnt = pd.read_sql(f'SELECT COUNT({col}) as cnt FROM match_player_stats WHERE {col} IS NOT NULL', conn).iloc[0,0]
        avg = pd.read_sql(f'SELECT AVG(CAST({col} AS FLOAT)) as avg FROM match_player_stats WHERE {col} IS NOT NULL', conn).iloc[0,0]
        print(f'  {col:25s}: {cnt:6d}/{total} ({cnt/total*100:5.1f}%), 均值={avg:.2f}')
    except Exception as e:
        print(f'  {col:25s}: 列不存在或查询失败')

# match_id 格式
print('\n=== match_id 格式 ===')
samples = pd.read_sql('SELECT DISTINCT match_id FROM match_player_stats LIMIT 10', conn)
for s in samples['match_id'].values:
    print(f'  [{s}]')

# 与 matches 表关联
print('\n=== 与 matches 表关联 ===')
joined = pd.read_sql("""
    SELECT COUNT(*) as cnt FROM match_player_stats mps
    INNER JOIN matches mt ON mps.match_id = mt.match_id
""", conn).iloc[0,0]
print(f'  直接关联: {joined}/{total} ({joined/total*100:.1f}%)')

# 按联赛看
print('\n=== 按联赛/赛季分布 ===')
by_league = pd.read_sql("""
    SELECT mt.match_type as league, COUNT(*) as cnt
    FROM match_player_stats mps
    INNER JOIN matches mt ON mps.match_id = mt.match_id
    GROUP BY mt.match_type
    ORDER BY cnt DESC
""", conn)
for _, row in by_league.iterrows():
    print(f'  {row["league"]:30s}: {row["cnt"]:6d} 条记录')

# 按比赛日期
print('\n=== 按赛季分布 ===')
by_season = pd.read_sql("""
    SELECT 
        CASE 
            WHEN mt.match_date BETWEEN '2023-08-01' AND '2024-07-31' THEN '2023-2024'
            WHEN mt.match_date BETWEEN '2024-08-01' AND '2025-07-31' THEN '2024-2025'
            WHEN mt.match_date BETWEEN '2025-08-01' AND '2026-07-31' THEN '2025-2026'
            ELSE '其他'
        END as season,
        COUNT(DISTINCT mps.match_id) as matches
    FROM match_player_stats mps
    INNER JOIN matches mt ON mps.match_id = mt.match_id
    GROUP BY season
    ORDER BY season
""", conn)
for _, row in by_season.iterrows():
    print(f'  {row["season"]:10s}: {row["matches"]:4d} 场')

# 比赛级聚合示例
print('\n=== 比赛级 expected_goals 聚合示例 (前5场) ===')
agg = pd.read_sql("""
    SELECT mps.match_id, mt.match_date, mt.home_team, mt.away_team,
           SUM(mps.expected_goals) as match_xg_total,
           COUNT(*) as player_count
    FROM match_player_stats mps
    INNER JOIN matches mt ON mps.match_id = mt.match_id
    WHERE mps.expected_goals IS NOT NULL
    GROUP BY mps.match_id
    ORDER BY mt.match_date
    LIMIT 5
""", conn)
print(agg.to_string())

# 看看 match_id 格式是否与 matches 不同
print('\n=== matches 表 match_id 示例 ===')
m_samples = pd.read_sql('SELECT DISTINCT match_id FROM matches LIMIT 5', conn)
for s in m_samples['match_id'].values:
    print(f'  [{s}]')

conn.close()
print('\n探索完成！')