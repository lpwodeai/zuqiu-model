"""最终验证导入结果"""
import sqlite3
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ODDS_DB = os.path.join(DATA_DIR, "odds.db")
FIVE_LEAGUES_DB = os.path.join(DATA_DIR, "five_leagues.db")

print('='*70)
print('数据导入最终验证报告')
print('='*70)

# === odds.db ===
conn = sqlite3.connect(ODDS_DB)
cursor = conn.cursor()

print('\n【odds.db 数据总览】')
print('-'*50)

# matches表
cursor.execute('SELECT COUNT(*) FROM matches')
total_matches = cursor.fetchone()[0]
print(f'总比赛数: {total_matches} 场')

cursor.execute('SELECT match_type, COUNT(*) FROM matches GROUP BY match_type ORDER BY COUNT(*) DESC')
print('\n按联赛分布:')
for row in cursor.fetchall():
    print(f'  {row[0]}: {row[1]}场')

# 赔率表
print('\n赔率历史记录:')
for table, desc in [
    ('wdl_history', '胜平负赔率'),
    ('handicap_history', '让球赔率'),
    ('total_goals_history', '总进球赔率'),
    ('score_history', '比分赔率'),
]:
    cursor.execute(f'SELECT COUNT(*) FROM {table}')
    count = cursor.fetchone()[0]
    # 计算有数据的比赛数
    cursor.execute(f'SELECT COUNT(DISTINCT match_id) FROM {table}')
    match_count = cursor.fetchone()[0]
    print(f'  {desc}: {count} 条记录 / {match_count} 场比赛')

# match_mapping
cursor.execute('SELECT COUNT(*) FROM match_mapping')
mapping_count = cursor.fetchone()[0]
cursor.execute('SELECT COUNT(DISTINCT five_leagues_match_id) FROM match_mapping')
mapped_fl = cursor.fetchone()[0]
print(f'\n比赛映射: {mapping_count} 条 / 关联five_leagues比赛 {mapped_fl} 场')

# 数据覆盖率
cursor.execute('SELECT COUNT(DISTINCT m.match_id) FROM matches m')
total = cursor.fetchone()[0]
cursor.execute('SELECT COUNT(DISTINCT match_id) FROM wdl_history')
wdl_cov = cursor.fetchone()[0]
cursor.execute('SELECT COUNT(DISTINCT match_id) FROM handicap_history')
hcp_cov = cursor.fetchone()[0]
cursor.execute('SELECT COUNT(DISTINCT match_id) FROM total_goals_history')
tg_cov = cursor.fetchone()[0]

print(f'\n赔率覆盖率:')
print(f'  胜平负: {wdl_cov}/{total} = {wdl_cov/total*100:.1f}%')
print(f'  让球: {hcp_cov}/{total} = {hcp_cov/total*100:.1f}%')
print(f'  总进球: {tg_cov}/{total} = {tg_cov/total*100:.1f}%')

conn.close()

# === five_leagues.db ===
print('\n' + '='*70)
print('【five_leagues.db 数据总览】')
print('-'*50)

conn2 = sqlite3.connect(FIVE_LEAGUES_DB)
cursor2 = conn2.cursor()

cursor2.execute('SELECT COUNT(*) FROM matches WHERE homeGoals IS NOT NULL')
fl_total = cursor2.fetchone()[0]
print(f'总比赛数: {fl_total} 场')

cursor2.execute('SELECT competitionId, COUNT(*) FROM matches WHERE homeGoals IS NOT NULL GROUP BY competitionId')
print('\n按联赛分布:')
league_names = {1: '英超', 2: '西甲', 3: '德甲', 4: '意甲', 5: '法甲'}
for row in cursor2.fetchall():
    name = league_names.get(row[0], f'联赛{row[0]}')
    print(f'  {name}: {row[1]}场')

conn2.close()

# === 综合统计 ===
print('\n' + '='*70)
print('【数据补充前后对比】')
print('-'*50)
print(f'赔率比赛数: 132场 → {total_matches}场 (增长 {total_matches-132}场, +{(total_matches-132)/132*100:.0f}%)')
print(f'赔率覆盖率: 约15% → {wdl_cov/total*100:.1f}%')
print(f'胜平负记录: 2270条 → ', end='')
conn3 = sqlite3.connect(ODDS_DB)
cursor3 = conn3.cursor()
cursor3.execute('SELECT COUNT(*) FROM wdl_history')
print(f'{cursor3.fetchone()[0]}条')
conn3.close()
print('='*70)
