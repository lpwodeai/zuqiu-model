import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
db_path = BASE_DIR / "data" / "odds_timing.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT round, match_date, home_team, away_team FROM matches ORDER BY round, match_date")
rows = cursor.fetchall()

print('数据库中所有比赛的轮次和日期分布:')
current_round = None
for row in rows:
    if row[0] != current_round:
        current_round = row[0]
        print(f'\n=== 第{current_round}轮 ===')
    print(f'  {row[1]}: {row[2]} vs {row[3]}')

cursor.execute("SELECT round, COUNT(*) FROM matches GROUP BY round ORDER BY round")
round_counts = cursor.fetchall()

print('\n\n轮次统计:')
for rc in round_counts:
    print(f'第{rc[0]}轮: {rc[1]}场')

conn.close()
