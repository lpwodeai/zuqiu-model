import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
conn = sqlite3.connect(BASE_DIR / "data" / "odds.db")

# 检查 match_type 为 2023-2024 的重复情况
print("=== 23/24 赛季数据质量检查 ===")

# 按联赛统计
for league in ["英超", "西甲", "意甲", "德甲", "法甲"]:
    cnt = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE match_type = ?",
        (f"{league}2023-2024赛季",)
    ).fetchone()[0]
    cnt_distinct = conn.execute(
        "SELECT COUNT(DISTINCT match_id) FROM matches WHERE match_type = ?",
        (f"{league}2023-2024赛季",)
    ).fetchone()[0]
    dup = cnt - cnt_distinct
    print(f"  {league}: {cnt} 行, 去重 {cnt_distinct}, 重复 {dup}")

# 总去重
total = conn.execute(
    "SELECT COUNT(*) FROM matches WHERE match_type LIKE '%2023-2024%'"
).fetchone()[0]
distinct = conn.execute(
    "SELECT COUNT(DISTINCT match_id) FROM matches WHERE match_type LIKE '%2023-2024%'"
).fetchone()[0]
print(f"\n  总计: {total} 行, 去重 {distinct}, 重复 {total - distinct}")

# 检查 wdl_history 中 23/24 赛季的覆盖
wdl_distinct = conn.execute("""
    SELECT COUNT(DISTINCT w.match_id) FROM wdl_history w
    INNER JOIN matches m ON w.match_id = m.match_id
    WHERE m.match_type LIKE '%2023-2024%'
""").fetchone()[0]
print(f"\n  WDL赔率覆盖 (去重后): {wdl_distinct}/{distinct} ({wdl_distinct*100/distinct:.1f}%)" if distinct > 0 else "N/A")

# 检查 sporttery JSON 中的比赛数
import json, os
d = BASE_DIR / "data" / "sporttery_collected"
total_json = 0
for fname in os.listdir(d):
    with open(os.path.join(d, fname), "r", encoding="utf-8") as fh:
        data = json.load(fh)
    total_json += len(data)
print(f"\n  sporttery JSON 总比赛数: {total_json}")

# 按联赛统计 JSON
league_counts = {}
for fname in os.listdir(d):
    parts = fname.replace(".json", "").split("_")
    league = parts[0]
    with open(os.path.join(d, fname), "r", encoding="utf-8") as fh:
        data = json.load(fh)
    league_counts[league] = league_counts.get(league, 0) + len(data)
print("  JSON 联赛分布:")
for l, c in sorted(league_counts.items()):
    print(f"    {l}: {c}")

conn.close()