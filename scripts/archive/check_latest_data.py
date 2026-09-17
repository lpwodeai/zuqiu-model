import sqlite3
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
db_path = BASE_DIR / "data" / "odds_timing.db"
batch_path = BASE_DIR / "data" / "batch_import.py"

print('=' * 60)
print('数据库最新比赛数据检查')
print('=' * 60)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT match_id, home_team, away_team, match_date, match_time, round FROM matches ORDER BY match_date DESC, match_time DESC LIMIT 10")
latest_db_matches = cursor.fetchall()

print('\n📊 数据库中最新的10场比赛:')
for i, match in enumerate(latest_db_matches, 1):
    print(f"{i:2d}. {match[3]} {match[4]} - 第{match[5]}轮: {match[1]} vs {match[2]}")

cursor.execute("SELECT MAX(match_date) FROM matches")
latest_date = cursor.fetchone()[0]
print(f"\n📅 数据库中最新比赛日期: {latest_date}")

cursor.execute("SELECT round, COUNT(*) FROM matches WHERE match_date = ? GROUP BY round ORDER BY round", (latest_date,))
latest_round_counts = cursor.fetchall()
print(f"当天比赛分布: {latest_round_counts}")

conn.close()

print('\n' + '=' * 60)
print('batch_import.py 最新比赛数据检查')
print('=' * 60)

with open(batch_path, 'r', encoding='utf-8') as f:
    content = f.read()

match_ids = re.findall(r"'match_id': '([^']+)'", content)
match_dates = re.findall(r"'match_date': '([^']+)'", content)
match_rounds = re.findall(r"'round': (\d+)", content)

matches = list(zip(match_ids, match_dates, match_rounds))
matches.sort(key=lambda x: (x[1], x[0]), reverse=True)

print('\n📊 batch_import.py中最新的10场比赛:')
for i, (mid, mdate, mround) in enumerate(matches[:10], 1):
    teams = mid.replace(mdate + '_', '')
    print(f"{i:2d}. {mdate} - 第{mround}轮: {teams}")

latest_batch_date = matches[0][1] if matches else '无数据'
print(f"\n📅 batch_import.py中最新比赛日期: {latest_batch_date}")

batch_round_counts = {}
for _, mdate, mround in matches:
    if mdate == latest_batch_date:
        batch_round_counts[mround] = batch_round_counts.get(mround, 0) + 1
print(f"当天比赛分布: {batch_round_counts}")

print('\n' + '=' * 60)
print('数据同步对比')
print('=' * 60)

db_match_ids = set()
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("SELECT match_id FROM matches")
for row in cursor.fetchall():
    db_match_ids.add(row[0])
conn.close()

batch_match_ids = {mid for mid, _, _ in matches}

missing_in_db = batch_match_ids - db_match_ids
missing_in_batch = db_match_ids - batch_match_ids

print(f"\n📝 batch_import.py中共有: {len(batch_match_ids)} 场比赛")
print(f"📝 数据库中共有: {len(db_match_ids)} 场比赛")
print(f"\n⚠️ 存在于batch_import.py但未导入数据库的比赛: {len(missing_in_db)} 场")
if missing_in_db:
    for mid in sorted(missing_in_db)[:5]:
        print(f"   - {mid}")
    if len(missing_in_db) > 5:
        print(f"   ... 还有 {len(missing_in_db) - 5} 场")

print(f"\n⚠️ 存在于数据库但不在batch_import.py的比赛: {len(missing_in_batch)} 场")
if missing_in_batch:
    for mid in sorted(missing_in_batch)[:5]:
        print(f"   - {mid}")
    if len(missing_in_batch) > 5:
        print(f"   ... 还有 {len(missing_in_batch) - 5} 场")

if not missing_in_db and not missing_in_batch:
    print("\n✅ 数据库和batch_import.py的数据完全同步！")
