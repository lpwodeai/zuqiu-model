import sqlite3
import pandas as pd
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
base = os.path.dirname(os.getcwd())

print('=' * 70)
print('赔率特征匹配问题诊断')
print('=' * 70)

# 1. 检查 odds.db 的 match_mapping
print('\n📋 odds.db match_mapping 分析:')
conn = sqlite3.connect(os.path.join(base, 'data', 'odds.db'))
df_mapping = pd.read_sql('SELECT * FROM match_mapping LIMIT 20', conn)
print(df_mapping.to_string())

# 统计映射
df_stats = pd.read_sql('''
    SELECT league, COUNT(*) as cnt 
    FROM match_mapping 
    GROUP BY league
    ORDER BY cnt DESC
''', conn)
print('\n联赛映射统计:')
print(df_stats.to_string())

# 检查odds.db的matches表
df_odds_matches = pd.read_sql('''
    SELECT match_type, COUNT(*) as cnt 
    FROM matches 
    GROUP BY match_type
''', conn)
print('\nodds.db 比赛分布:')
print(df_odds_matches.to_string())

conn.close()

# 2. 检查 five_leagues.db 的球队名称
print('\n📋 five_leagues.db 球队名称:')
conn = sqlite3.connect(os.path.join(base, 'data', 'five_leagues.db'))
df_teams = pd.read_sql('''
    SELECT t.name, c.name as competition
    FROM teams t
    LEFT JOIN matches m ON t.id = m.homeTeamId OR t.id = m.awayTeamId
    LEFT JOIN competitions c ON m.competitionId = c.id
    GROUP BY t.name, c.name
    ORDER BY c.name, t.name
''', conn)
print(df_teams.head(30).to_string())

# 检查比赛样本
df_sample = pd.read_sql('''
    SELECT m.date, ht.name as home, at.name as away, c.name as competition
    FROM matches m
    LEFT JOIN teams ht ON m.homeTeamId = ht.id
    LEFT JOIN teams at ON m.awayTeamId = at.id
    LEFT JOIN competitions c ON m.competitionId = c.id
    LIMIT 10
''', conn)
print('\nfive_leagues.db 比赛样本:')
print(df_sample.to_string())
conn.close()

# 3. 尝试手动匹配
print('\n🔍 手动匹配测试:')
conn = sqlite3.connect(os.path.join(base, 'data', 'odds.db'))

# 取一场英超比赛
home = '利物浦'
away = '伯恩茅斯'
date_str = '2025-08-16'

match_id_cn = f"{date_str}_{home}_{away}"
match_id_en = f"{date_str}_{home.replace(' ', '_')}_{away.replace(' ', '_')}"

print(f'  尝试match_id: {match_id_cn}')
df_test = pd.read_sql('SELECT * FROM wdl_history WHERE match_id = ? LIMIT 3', conn, params=(match_id_cn,))
print(f'  结果: {len(df_test)} 条记录')
if len(df_test) > 0:
    print(df_test.to_string())

# 检查有多少比赛能匹配
cursor = conn.cursor()
cursor.execute('SELECT DISTINCT match_id FROM wdl_history LIMIT 50')
available_ids = [r[0] for r in cursor.fetchall()]
print(f'\n  wdl_history中可用的match_id (前50):')
for mid in available_ids[:20]:
    print(f'    {mid}')

# 检查球队名映射
df_team_map = pd.read_sql('SELECT * FROM team_mapping LIMIT 20', conn)
print('\n  team_mapping:')
print(df_team_map.to_string())

conn.close()

# 4. 关键问题诊断
print('\n' + '=' * 70)
print('🎯 问题诊断总结')
print('=' * 70)

# 检查odds.db wdl_history有多少唯一比赛
conn = sqlite3.connect(os.path.join(base, 'data', 'odds.db'))
cursor = conn.cursor()
cursor.execute('SELECT COUNT(DISTINCT match_id) FROM wdl_history')
wdl_matches = cursor.fetchone()[0]
print(f'  wdl_history中有 {wdl_matches} 场比赛的赔率记录')

cursor.execute('SELECT COUNT(*) FROM matches')
odds_matches = cursor.fetchone()[0]
print(f'  odds.db中有 {odds_matches} 场比赛')

# 检查matches表的match_type
cursor.execute('SELECT DISTINCT match_type FROM matches')
leagues = [r[0] for r in cursor.fetchall()]
print(f'  odds.db联赛: {leagues}')

# 检查有多少比赛有wdl数据
cursor.execute('''
    SELECT COUNT(DISTINCT m.id) 
    FROM matches m 
    WHERE m.match_id IN (SELECT DISTINCT match_id FROM wdl_history)
''')
covered = cursor.fetchone()[0]
print(f'  有WDL赔率的比赛: {covered}/{odds_matches}')

# 检查matches表的match_id格式
cursor.execute('SELECT match_id FROM matches LIMIT 10')
match_ids = [r[0] for r in cursor.fetchall()]
print(f'\n  odds.db match_id样本:')
for mid in match_ids:
    print(f'    {mid}')

conn.close()

print('''
核心问题:
1. five_leagues.db 缺少德甲(178场)和法甲(178场)数据 
   - 这两个联赛只存在于 odds.db 和 odds_timing.db
   
2. 赔率特征匹配逻辑:
   - build_odds_features() 用 "日期_主队_客队" 格式匹配
   - odds.db 的 wdl_history 使用相同格式
   - 但 five_leagues.db 的球队名（中文）可能与 odds.db 不一致
   
3. 需要修复:
   a) 将德甲/法甲比赛数据同步到 five_leagues.db
   b) 确保球队名称映射正确（team_mapping表已存在）
   c) 修复日期格式解析问题
''')
