import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
conn = sqlite3.connect(BASE_DIR / "data" / "odds.db")

# 检查 WDL 覆盖的实际 match_id 数量
total_wdl = conn.execute("SELECT COUNT(DISTINCT match_id) FROM wdl_history").fetchone()[0]
print(f"wdl_history 去重 match_id: {total_wdl}")

# 检查 23/24 赛季的 WDL 覆盖（按 match_date 在 2023-2024）
wdl_2324 = conn.execute("""
    SELECT COUNT(DISTINCT w.match_id) FROM wdl_history w
    WHERE w.match_id LIKE '2023%' OR w.match_id LIKE '2024%'
""").fetchone()[0]
print(f"wdl_history 2023-2024 match_id: {wdl_2324}")

# 检查刚补充的几场比赛
sample = conn.execute("""
    SELECT match_id, COUNT(*) as cnt FROM wdl_history
    WHERE match_id LIKE '2024-05-19%'
    GROUP BY match_id
""").fetchall()
print(f"\n示例: 2024-05-19 的比赛:")
for r in sample:
    print(f"  {r[0]}: {r[1]} 个时序点")

# 检查 match_id 格式差异
print("\n=== matches 表 match_id 样例 ===")
samples = conn.execute("""
    SELECT match_id FROM matches 
    WHERE match_type LIKE '%2023-2024%' 
    LIMIT 5
""").fetchall()
for r in samples:
    print(f"  {r[0]}")

print("\n=== wdl_history match_id 样例 ===")
samples = conn.execute("""
    SELECT match_id FROM wdl_history
    WHERE match_id LIKE '2023%' OR match_id LIKE '2024%'
    LIMIT 5
""").fetchall()
for r in samples:
    print(f"  {r[0]}")

# 检查 match_id 匹配情况
conn.close()