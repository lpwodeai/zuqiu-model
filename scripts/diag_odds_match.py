import sqlite3
import pandas as pd
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
base = os.path.dirname(os.getcwd())

# 加载odds.db数据
conn = sqlite3.connect(os.path.join(base, 'data', 'odds.db'))

# 检查match_id格式
df_matches = pd.read_sql('SELECT match_id, home_team, away_team, match_date, match_type FROM matches LIMIT 10', conn)
print("odds.db matches表 match_id 样本:")
print(df_matches.to_string())

# 检查wdl_history的match_id
cursor = conn.cursor()
cursor.execute("SELECT DISTINCT match_id FROM wdl_history WHERE match_id IN (SELECT match_id FROM matches) LIMIT 10")
matching_ids = [r[0] for r in cursor.fetchall()]
print(f"\n在wdl_history中存在的match_id (匹配matches表):")
for mid in matching_ids:
    print(f"  {mid}")

# 检查matches表中有多少match_id在wdl_history中
cursor.execute("""
    SELECT COUNT(*) FROM matches m
    WHERE EXISTS (SELECT 1 FROM wdl_history w WHERE w.match_id = m.match_id)
""")
matched = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM matches")
total = cursor.fetchone()[0]
print(f"\n匹配情况: {matched}/{total} ({matched/total*100:.1f}%)")

# 检查不匹配的
cursor.execute("""
    SELECT m.match_id, m.home_team, m.away_team 
    FROM matches m
    WHERE NOT EXISTS (SELECT 1 FROM wdl_history w WHERE w.match_id = m.match_id)
    LIMIT 10
""")
unmatched = cursor.fetchall()
print(f"\n未匹配的match_id样本 ({len(unmatched)} 个):")
for row in unmatched:
    print(f"  {row}")

# 直接测试用matches表的match_id查询wdl_history
test_id = '2025-08-16_利物浦_伯恩茅斯'
cursor.execute("SELECT * FROM wdl_history WHERE match_id = ? LIMIT 3", (test_id,))
result = cursor.fetchall()
print(f"\n测试查询 '{test_id}': {len(result)} 条记录")
if result:
    for r in result:
        print(f"  {r}")

# 检查英文match_id
test_id2 = '2025-08-16_Rennes_Marseille'
cursor.execute("SELECT * FROM wdl_history WHERE match_id = ? LIMIT 3", (test_id2,))
result2 = cursor.fetchall()
print(f"\n测试查询 '{test_id2}': {len(result2)} 条记录")

# 总结
cursor.execute("SELECT COUNT(DISTINCT match_id) FROM wdl_history")
wdl_total = cursor.fetchone()[0]
print(f"\nwdl_history中有 {wdl_total} 个唯一match_id")

conn.close()
