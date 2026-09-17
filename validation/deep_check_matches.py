# -*- coding: utf-8 -*-
"""
深度检查：matches 表数据质量
"""
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
conn = sqlite3.connect(BASE_DIR / "data" / "odds.db")

print("=" * 70)
print("📊 matches 表深度数据质量检查")
print("=" * 70)

# 1. 按正确赛季（基于日期）统计
print("\n1. 按日期正确归类的赛季分布:")
rows = conn.execute("""
    SELECT 
        CASE 
            WHEN match_date >= '2023-08-01' AND match_date < '2024-08-01' THEN '2023-2024'
            WHEN match_date >= '2024-08-01' AND match_date < '2025-08-01' THEN '2024-2025'
            WHEN match_date >= '2025-08-01' AND match_date < '2026-08-01' THEN '2025-2026'
            WHEN match_date >= '2026-08-01' THEN '2026-2027'
            ELSE '其他'
        END as correct_season,
        COUNT(*) as cnt
    FROM matches
    GROUP BY correct_season
    ORDER BY correct_season
""").fetchall()
for r in rows:
    expected = 1752
    status = "✅" if abs(r[1] - expected) < 200 else "⚠️ 异常"
    print(f"  {r[0]:12s}: {r[1]:>5d} 场 (理论 ~{expected}) {status}")

# 2. 检查 match_type 与 正确赛季是否一致
print("\n2. match_type 标签准确性检查:")
errors = conn.execute("""
    SELECT COUNT(*) FROM matches
    WHERE (
        (match_date >= '2023-08-01' AND match_date < '2024-08-01' AND match_type NOT LIKE '%2023-2024%')
        OR
        (match_date >= '2024-08-01' AND match_date < '2025-08-01' AND match_type NOT LIKE '%2024-2025%')
        OR
        (match_date >= '2025-08-01' AND match_date < '2026-08-01' AND match_type NOT LIKE '%2025-2026%')
    )
    AND match_type != ''
""").fetchone()[0]
print(f"  标签错误数: {errors}")

# 3. 如标签正确，检查重复（同一日期+主客队+联赛）
print("\n3. 重复比赛检查 (同日期+同主客队):")
dup = conn.execute("""
    SELECT match_date, home_team, away_team, COUNT(*) as cnt
    FROM matches
    GROUP BY match_date, home_team, away_team
    HAVING cnt > 1
    ORDER BY cnt DESC
    LIMIT 10
""").fetchall()
print(f"  重复组合数: {len(dup)}")
for r in dup:
    print(f"    {r[0]} {r[1]} vs {r[2]}: {r[3]} 条")

# 4. 检查 match_id 重复
dup_id = conn.execute("""
    SELECT match_id, COUNT(*) as cnt
    FROM matches
    GROUP BY match_id
    HAVING cnt > 1
    LIMIT 5
""").fetchall()
print(f"\n4. match_id 重复: {len(dup_id)} 组")

# 5. 按联赛+正确赛季统计
print("\n5. 按联赛+正确赛季明细:")
for league in ["英超", "西甲", "意甲", "德甲", "法甲"]:
    rows = conn.execute("""
        SELECT 
            CASE 
                WHEN match_date >= '2023-08-01' AND match_date < '2024-08-01' THEN '23/24'
                WHEN match_date >= '2024-08-01' AND match_date < '2025-08-01' THEN '24/25'
                WHEN match_date >= '2025-08-01' AND match_date < '2026-08-01' THEN '25/26'
                WHEN match_date >= '2026-08-01' THEN '26/27'
            END as s,
            COUNT(*) as cnt
        FROM matches
        WHERE match_type LIKE ?
        GROUP BY s
        ORDER BY s
    """, (f"{league}%",)).fetchall()
    parts = [f"{r[0]}={r[1]}" for r in rows]
    total = sum(r[1] for r in rows)
    expected = 380 if league in ["英超", "西甲", "意甲"] else 306
    status = "✅" if abs(total - expected) < 20 else "⚠️ 异常"
    print(f"  {league}: {', '.join(parts)} | 总计={total} (预期={expected}) {status}")

conn.close()