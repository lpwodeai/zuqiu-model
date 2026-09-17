"""检查odds_timing.db表结构和数据状态"""
import sqlite3

conn = sqlite3.connect('data/odds_timing.db')
cursor = conn.cursor()

# 检查表结构
for table in ['wdl_timing', 'handicap_timing', 'score_timing', 'total_goals_timing']:
    cursor.execute(f'PRAGMA table_info({table})')
    columns = cursor.fetchall()
    print(f'\n{table} 表结构:')
    for col in columns:
        print(f'  {col[1]} {col[2]}')

# 检查意甲比赛的status
cursor.execute("SELECT DISTINCT status FROM matches WHERE league LIKE '%意甲%'")
print('\n意甲比赛状态:')
for row in cursor.fetchall():
    print(f'  {row[0]}')

# 检查is_valid字段
cursor.execute('PRAGMA table_info(wdl_timing)')
columns = [col[1] for col in cursor.fetchall()]
print(f'\nwdl_timing列: {columns}')
print(f'is_valid存在: {"is_valid" in columns}')

# 检查意甲数据量
cursor.execute("SELECT COUNT(*) FROM matches WHERE league LIKE '%意甲%'")
print(f'\n意甲比赛数: {cursor.fetchone()[0]}')

cursor.execute("SELECT COUNT(*) FROM wdl_timing WHERE match_id IN (SELECT match_id FROM matches WHERE league LIKE '%意甲%')")
print(f'意甲WDL赔率数: {cursor.fetchone()[0]}')

conn.close()
